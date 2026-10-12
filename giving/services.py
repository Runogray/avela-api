"""
The core giving flow — where the cap, the gateway, and the wallet meet.

create_gift()  : called when a guest starts a gift. Runs the receiving-cap
                 gate BEFORE taking payment, records whether this gift crosses
                 the cap, and opens a gateway checkout.
confirm_gift() : called by the gateway webhook on confirmed payment. This is
                 the only place money becomes real: it increments the host's
                 cumulative_received, credits the wallet's pending balance, and
                 bumps the goal's raised total.
"""
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from wallet.models import Wallet
from integrations.gateways import payment_gateway
from .models import MonetaryGift


class GiftBlocked(Exception):
    """Raised when an unverified host is already over the receiving cap."""


def create_gift(*, event, amount_kobo, giver_name="", message="",
                show_name=True, show_amount=True, goal=None) -> dict:
    host = event.host
    allowed, crossing = host.can_receive(amount_kobo)
    if not allowed:
        # host is over the cap and unverified -> guest sees the graceful block
        raise GiftBlocked(
            f"{event.title} can't receive gifts right now. "
            "The host has been notified to verify and unlock them."
        )

    gift = MonetaryGift.objects.create(
        event=event, goal=goal, amount_kobo=amount_kobo, message=message,
        giver_name=giver_name, show_name=show_name, show_amount=show_amount,
        is_crossing=crossing,
    )
    init = payment_gateway.init_transaction(
        amount_kobo=amount_kobo, email=host.email,
        metadata={"gift_id": str(gift.id)},
    )
    gift.gateway_reference = init["reference"]
    gift.save(update_fields=["gateway_reference"])
    return {"gift": gift, "checkout_url": init["checkout_url"]}


@transaction.atomic
def confirm_gift(gift: MonetaryGift) -> MonetaryGift:
    if gift.status == MonetaryGift.Status.PAID:
        return gift                                  # idempotent for webhooks

    gift.status = MonetaryGift.Status.PAID
    gift.paid_at = timezone.now()
    gift.save(update_fields=["status", "paid_at"])

    host = gift.event.host
    host.cumulative_received_kobo += gift.amount_kobo
    host.save(update_fields=["cumulative_received_kobo"])

    wallet, _ = Wallet.objects.get_or_create(host=host)
    wallet.credit_pending(gift.amount_kobo, memo=f"Gift on {gift.event.title}")
    # DEV: settle immediately so dev wallets have a spendable balance. In
    # production funds stay pending until a real settlement webhook clears them.
    if settings.DEBUG:
        wallet.settle(gift.amount_kobo, memo=f"Settled: {gift.event.title}")

    if gift.goal:
        gift.goal.raised_kobo += gift.amount_kobo
        gift.goal.save(update_fields=["raised_kobo"])

    # Notify the host (lazy import avoids an accounts↔giving import cycle).
    from accounts.services import notify
    from config.constants import naira
    notify(host, "contribution", "New contribution",
           f"{gift.public_giver} sent {naira(gift.amount_kobo)} on {gift.event.title}.")
    return gift
