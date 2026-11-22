"""Gateway webhook + NIN verification endpoints."""
import hashlib
import hmac

from django.conf import settings
from rest_framework import decorators, response, status
from rest_framework.permissions import AllowAny, IsAuthenticated

from giving.models import MonetaryGift
from giving.services import confirm_gift, settle_card_gift
from .gateways import payment_gateway, identity_verifier


WEBHOOK_TOLERANCE_SECONDS = 300


def _new_signature_ok(header: str, body: bytes, secret: str, now: int) -> bool:
    """Paystack-Signature: t=<unix>,v1=<key id>:<hex HMAC-SHA512 of "t." + body>.
    Signed with the webhook signing secret (whsec_…); any matching v1 passes."""
    t, sigs = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            t = int(value)
        elif key == "v1" and ":" in value:
            sigs.append(value.split(":", 1)[1])
    if t is None or not sigs or abs(now - t) > WEBHOOK_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha512).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def _signature_ok(request) -> bool:
    """Verify the gateway's webhook signature on the raw body.

    Paystack's current scheme signs with a webhook secret (Paystack-Signature);
    the older one signs the body with the secret key (x-paystack-signature).
    With no live key we're in stub/dev mode and skip the check; the payment is
    still verified with the gateway before anything is confirmed.
    """
    import time
    body = request.body
    new_header = request.headers.get("Paystack-Signature", "")
    if settings.PAYSTACK_WEBHOOK_SECRET and new_header:
        return _new_signature_ok(new_header, body, settings.PAYSTACK_WEBHOOK_SECRET, int(time.time()))
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        return True  # stub/dev — no live gateway configured
    signature = request.headers.get("X-Paystack-Signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


@decorators.api_view(["POST"])
@decorators.permission_classes([AllowAny])
def payment_webhook(request):
    """
    Paystack calls this with {"event": "charge.success", "data": {...}}. We
    check the signature, then verify the reference with Paystack ourselves and
    confirm the gift (or wallet top-up) idempotently. Other events are
    acknowledged and ignored, so Paystack doesn't keep retrying them.
    """
    if not _signature_ok(request):
        return response.Response({"detail": "invalid signature"},
                                 status=status.HTTP_401_UNAUTHORIZED)
    payload = request.data if isinstance(request.data, dict) else {}
    event = payload.get("event", "charge.success")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if event != "charge.success":
        return response.Response({"status": "ignored"})
    reference = (data.get("reference") or "").strip()
    if not reference:
        return response.Response({"status": "ignored"})

    gift = MonetaryGift.objects.filter(gateway_reference=reference).first()
    if gift:
        if gift.paid_direct:
            settle_card_gift(gift)  # verifies the amount with Paystack itself
        elif payment_gateway.verify(reference):
            confirm_gift(gift)
        return response.Response({"status": "ok"})
    # Not a gift — maybe a wallet top-up.
    from wallet.models import TopUp
    from wallet.services import confirm_topup
    topup = TopUp.objects.filter(gateway_reference=reference).first()
    if topup and payment_gateway.verify(reference):
        confirm_topup(topup)
    # Always 200 for references we don't know, so Paystack stops retrying.
    return response.Response({"status": "ok"})


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
