"""Gateway webhook + NIN verification endpoints."""
import hashlib
import hmac

from django.conf import settings
from rest_framework import decorators, response, status
from rest_framework.permissions import AllowAny, IsAuthenticated

from giving.models import MonetaryGift
from giving.services import confirm_gift
from .gateways import payment_gateway, identity_verifier


def _signature_ok(request) -> bool:
    """Verify the gateway's webhook signature.

    Paystack signs the raw request body with HMAC-SHA512 using the secret key
    and sends it as `X-Paystack-Signature`. When no secret is configured we're
    in stub/dev mode and skip the check (the reference is still verified below).
    """
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        return True  # stub/dev — no live gateway configured
    signature = request.headers.get("X-Paystack-Signature", "")
    expected = hmac.new(secret.encode(), request.body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


@decorators.api_view(["POST"])
@decorators.permission_classes([AllowAny])
def payment_webhook(request):
    """
    Gateway calls this on a confirmed charge. We verify the signature header
    first (when a live secret is set), then verify the reference via the gateway
    and confirm the gift idempotently.
    """
    if not _signature_ok(request):
        return response.Response({"detail": "invalid signature"},
                                 status=status.HTTP_401_UNAUTHORIZED)
    reference = request.data.get("reference", "")
    if not payment_gateway.verify(reference):
        return response.Response({"detail": "unverified"},
                                 status=status.HTTP_400_BAD_REQUEST)
    gift = MonetaryGift.objects.filter(gateway_reference=reference).first()
    if gift:
        confirm_gift(gift)
        return response.Response({"status": "ok"})
    # Not a gift — maybe a wallet top-up.
    from wallet.models import TopUp
    from wallet.services import confirm_topup
    topup = TopUp.objects.filter(gateway_reference=reference).first()
    if topup:
        confirm_topup(topup)
        return response.Response({"status": "ok"})
    return response.Response(status=status.HTTP_404_NOT_FOUND)


@decorators.api_view(["GET"])
@decorators.permission_classes([AllowAny])
def gift_status(request, gift_id):
    """Public: a giver's client can poll whether their gift has confirmed."""
    gift = MonetaryGift.objects.filter(id=gift_id).only("status").first()
    if gift is None:
        return response.Response(status=status.HTTP_404_NOT_FOUND)
    return response.Response({"status": gift.status})


@decorators.api_view(["POST"])
@decorators.permission_classes([IsAuthenticated])
def verify_nin(request):
    """Host submits NIN -> on success the receiving cap lifts."""
    ok = identity_verifier.verify_nin(
        nin=request.data.get("nin", ""),
        first_name=request.user.first_name,
        last_name=request.user.last_name,
    )
    if not ok:
        return response.Response({"detail": "Verification failed."},
                                 status=status.HTTP_400_BAD_REQUEST)
    request.user.mark_nin_verified()
    return response.Response({"nin_verified": True})
