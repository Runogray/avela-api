"""Wallet services — confirm a gateway-funded top-up."""
from django.utils import timezone

from config.constants import naira
from .models import TopUp


def confirm_topup(topup: TopUp) -> TopUp:
    """Mark a top-up paid and credit the host's available balance (idempotent)."""
    if topup.status == TopUp.Status.PAID:
        return topup
    topup.status = TopUp.Status.PAID
    topup.paid_at = timezone.now()
    topup.save(update_fields=["status", "paid_at"])
    topup.wallet.credit_available(topup.amount_kobo, memo="Wallet top-up")

    from accounts.services import notify
    notify(topup.wallet.host, "wallet", "Wallet funded",
           f"{naira(topup.amount_kobo)} was added to your wallet.")
    return topup
