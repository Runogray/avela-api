from rest_framework import viewsets, decorators, response, status
from rest_framework.permissions import IsAuthenticated

from accounts.models import BankAccount
from django.core.cache import cache

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

    @decorators.action(detail=False, methods=["get"])
    def banks(self, request):
        """Nigerian banks with their codes (from Paystack), cached for a day."""
        banks = cache.get("paystack_banks")
        if not banks:
            banks = sorted(payment_gateway.list_banks(), key=lambda b: b["name"].lower())
            if banks:
                cache.set("paystack_banks", banks, 60 * 60 * 24)
        return response.Response(banks or [])

    @decorators.action(detail=False, methods=["post"], url_path="resolve-account")
    def resolve_account(self, request):
        """Look up the name on an account before the host saves it."""
        number = (request.data.get("account_number") or "").strip()
        code = (request.data.get("bank_code") or "").strip()
        if not (number.isdigit() and len(number) == 10 and code):
            return response.Response({"detail": "Choose a bank and enter the 10-digit account number."},
                                     status=status.HTTP_400_BAD_REQUEST)
        name = payment_gateway.resolve_account(account_number=number, bank_code=code)
        if not name:
            return response.Response({"detail": "We couldn't find that account. Check the number and the bank."},
                                     status=status.HTTP_404_NOT_FOUND)
        return response.Response({"account_name": name})

    @decorators.action(detail=False, methods=["get", "put"], url_path="bank-account")
    def bank_account(self, request):
        """GET the saved payout account, or PUT to set/replace it. Saving looks
        the account up (so the name is the bank's, not typed) and sets up the
        Paystack subaccount that card gifts settle into."""
        if request.method == "PUT":
            ser = BankAccountSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            data = ser.validated_data
            code = data.get("bank_code", "")
            if not code:
                return response.Response({"bank_code": ["Choose your bank."]},
                                         status=status.HTTP_400_BAD_REQUEST)
            name = payment_gateway.resolve_account(account_number=data["account_number"], bank_code=code)
            if not name:
                return response.Response(
                    {"detail": "We couldn't find that account. Check the number and the bank."},
                    status=status.HTTP_400_BAD_REQUEST)
            old = getattr(request.user, "bank_account", None)
            same = bool(old and old.paystack_subaccount and old.bank_code == code
                        and old.account_number == data["account_number"])
            sub = old.paystack_subaccount if same else payment_gateway.create_subaccount(
                business_name=(request.user.first_name or name).strip(), bank_code=code,
                account_number=data["account_number"], email=request.user.email)
            BankAccount.objects.update_or_create(user=request.user, defaults={
                "bank_name": data["bank_name"], "bank_code": code,
                "account_number": data["account_number"], "account_name": name,
                "paystack_subaccount": sub or "",
            })
            request.user.refresh_from_db()
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
