"""
Wallet — held balance and withdrawals.

available_kobo : cleared funds the host can withdraw now.
pending_kobo   : funds received but still in the gateway's settlement /
                 chargeback window. They move available <- pending when the
                 settlement webhook confirms (protects against early withdrawal
                 of money that could still be reversed).

NOTE on custody (Open Decision D2): in production the cleanest design routes
settlement through the gateway (sub-accounts / dedicated virtual accounts) so
the platform never legally holds pooled funds. This model represents the
host's *view* of their balance regardless of where the money legally rests.
"""
import uuid
from django.conf import settings
from django.db import models, transaction

from config.constants import MIN_WITHDRAWAL_KOBO
from .fees import quote_withdrawal


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    host = models.OneToOneField(settings.AUTH_USER_MODEL,
                                on_delete=models.CASCADE, related_name="wallet")
    available_kobo = models.BigIntegerField(default=0)
    pending_kobo = models.BigIntegerField(default=0)

    def credit_pending(self, amount_kobo: int, *, memo=""):
        with transaction.atomic():
            self.pending_kobo += amount_kobo
            self.save(update_fields=["pending_kobo"])
            LedgerEntry.objects.create(wallet=self, kind=LedgerEntry.Kind.GIFT_IN,
                                       amount_kobo=amount_kobo, memo=memo)

    def settle(self, amount_kobo: int, *, memo=""):
        """Move funds from pending -> available once settlement clears."""
        with transaction.atomic():
            self.pending_kobo -= amount_kobo
            self.available_kobo += amount_kobo
            self.save(update_fields=["pending_kobo", "available_kobo"])
            LedgerEntry.objects.create(wallet=self, kind=LedgerEntry.Kind.SETTLE,
                                       amount_kobo=amount_kobo, memo=memo)

    def credit_available(self, amount_kobo: int, *, memo="", kind=None):
        """Credit the available balance directly (e.g. a confirmed top-up)."""
        with transaction.atomic():
            self.available_kobo += amount_kobo
            self.save(update_fields=["available_kobo"])
            LedgerEntry.objects.create(
                wallet=self, kind=kind or LedgerEntry.Kind.TOPUP,
                amount_kobo=amount_kobo, memo=memo)


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        GIFT_IN = "gift_in", "Gift received (pending)"
        SETTLE = "settle", "Settled to available"
        WITHDRAWAL = "withdrawal", "Withdrawal"
        FEE = "fee", "Platform fee"
        REVERSAL = "reversal", "Chargeback reversal"
        TOPUP = "topup", "Wallet top-up"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount_kobo = models.BigIntegerField()
    memo = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WithdrawalError(Exception):
    pass


class WithdrawalRequest(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PAID = "paid", "Paid out"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE,
                               related_name="withdrawals")
    amount_kobo = models.BigIntegerField()
    fee_kobo = models.BigIntegerField()
    net_kobo = models.BigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.REQUESTED)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def open(cls, wallet: Wallet, amount_kobo: int) -> "WithdrawalRequest":
        """
        Validate and create a withdrawal. Enforces: NIN verification, the
        minimum withdrawal floor, and sufficient available balance.
        """
        host = wallet.host
        if not host.nin_verified:
            raise WithdrawalError("Identity verification required before withdrawing.")
        if amount_kobo < MIN_WITHDRAWAL_KOBO:
            raise WithdrawalError("Below the minimum withdrawal amount.")
        if amount_kobo > wallet.available_kobo:
            raise WithdrawalError("Amount exceeds your available balance.")

        q = quote_withdrawal(amount_kobo)
        with transaction.atomic():
            wallet.available_kobo -= amount_kobo
            wallet.save(update_fields=["available_kobo"])
            LedgerEntry.objects.create(wallet=wallet, kind=LedgerEntry.Kind.WITHDRAWAL,
                                       amount_kobo=-amount_kobo)
            LedgerEntry.objects.create(wallet=wallet, kind=LedgerEntry.Kind.FEE,
                                       amount_kobo=-q["fee_kobo"], memo="Platform fee")
            wr = cls.objects.create(wallet=wallet, amount_kobo=amount_kobo,
                                    fee_kobo=q["fee_kobo"], net_kobo=q["net_kobo"])
        # Notify the host (lazy import avoids an accounts↔wallet import cycle).
        from accounts.services import notify
        from config.constants import naira
        notify(host, "wallet", "Withdrawal successful",
               f"{naira(q['net_kobo'])} is on its way to your bank.")
        return wr


class TopUp(models.Model):
    """A host funding their own wallet via the payment gateway (Paystack)."""
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="topups")
    amount_kobo = models.BigIntegerField()
    gateway_reference = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.INITIATED)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
