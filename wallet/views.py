from rest_framework import viewsets, decorators, response, status
from rest_framework.permissions import IsAuthenticated

from accounts.models import BankAccount
from integrations.gateways import payment_gateway
from .models import TopUp, Wallet, WithdrawalRequest, WithdrawalError
from .serializers import (BankAccountSerializer, LedgerEntrySerializer,
                          WithdrawalRequestSerializer)
from .fees import quote_withdrawal


class WalletViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        wallet, _ = Wallet.objects.get_or_create(host=request.user)
        return response.Response({
            "available_kobo": wallet.available_kobo,
            "pending_kobo": wallet.pending_kobo,
            "nin_verified": request.user.nin_verified,
            "should_nudge_verification": request.user.should_nudge_verification,
            "has_pin": request.user.has_withdrawal_pin,
            "has_bank": hasattr(request.user, "bank_account"),
        })

    @decorators.action(detail=False, methods=["get"])
    def quote(self, request):
        amount = int(request.query_params.get("amount_kobo", 0))
        return response.Response(quote_withdrawal(amount))

    @decorators.action(detail=False, methods=["get"])
    def transactions(self, request):
        """Ledger history for the Transactions screen (newest first)."""
        wallet, _ = Wallet.objects.get_or_create(host=request.user)
        qs = wallet.entries.order_by("-created_at")[:200]
        return response.Response(LedgerEntrySerializer(qs, many=True).data)

    @decorators.action(detail=False, methods=["get"])
    def withdrawals(self, request):
        wallet, _ = Wallet.objects.get_or_create(host=request.user)
        qs = wallet.withdrawals.order_by("-created_at")
        return response.Response(WithdrawalRequestSerializer(qs, many=True).data)

    @decorators.action(detail=False, methods=["get", "put"], url_path="bank-account")
    def bank_account(self, request):
        """GET the saved payout account, or PUT to set/replace it."""
        if request.method == "PUT":
            ser = BankAccountSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            BankAccount.objects.update_or_create(
                user=request.user, defaults=ser.validated_data)
        acct = getattr(request.user, "bank_account", None)
        return response.Response(BankAccountSerializer(acct).data if acct else None)

    @decorators.action(detail=False, methods=["post"], url_path="top-up")
    def top_up(self, request):
        """Start a wallet top-up via the gateway (Paystack). Returns the
        checkout URL + reference; the gateway webhook credits the wallet."""
        amount = int(request.data.get("amount_kobo", 0))
        if amount < 100:
            return response.Response({"detail": "Enter an amount."},
                                     status=status.HTTP_400_BAD_REQUEST)
        wallet, _ = Wallet.objects.get_or_create(host=request.user)
        init = payment_gateway.init_transaction(
            amount_kobo=amount, email=request.user.email,
            metadata={"kind": "topup", "user_id": str(request.user.id)})
        topup = TopUp.objects.create(
            wallet=wallet, amount_kobo=amount,
            gateway_reference=init["reference"])
        return response.Response(
            {"reference": init["reference"], "checkout_url": init["checkout_url"],
             "topup_id": str(topup.id)},
            status=status.HTTP_201_CREATED)

    @decorators.action(detail=False, methods=["post"], url_path="set-pin")
    def set_pin(self, request):
        pin = str(request.data.get("pin", ""))
        if not (pin.isdigit() and len(pin) == 4):
            return response.Response({"detail": "PIN must be 4 digits."},
                                     status=status.HTTP_400_BAD_REQUEST)
        request.user.set_withdrawal_pin(pin)
        return response.Response({"has_pin": True})

    @decorators.action(detail=False, methods=["post"])
    def withdraw(self, request):
        user = request.user
        # Require the withdrawal PIN once one has been set.
        if user.has_withdrawal_pin:
            if not user.check_withdrawal_pin(str(request.data.get("pin", ""))):
                return response.Response({"detail": "Incorrect PIN."},
                                         status=status.HTTP_400_BAD_REQUEST)
        wallet, _ = Wallet.objects.get_or_create(host=user)
        try:
            wr = WithdrawalRequest.open(wallet, int(request.data["amount_kobo"]))
        except WithdrawalError as e:
            return response.Response({"detail": str(e)},
                                     status=status.HTTP_400_BAD_REQUEST)
        return response.Response(
            {"id": str(wr.id), "amount_kobo": wr.amount_kobo,
             "fee_kobo": wr.fee_kobo, "net_kobo": wr.net_kobo, "status": wr.status},
            status=status.HTTP_201_CREATED)
