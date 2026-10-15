"""
The core giving flow — where the cap, the gateway, and the wallet meet.

create_gift()  : called when a guest starts a gift. Runs the receiving-cap
                 gate BEFORE taking payment, records whether this gift crosses
                 the cap, and opens a gateway checkout.
confirm_gift() : called by the gateway webhook on confirmed payment, or by
                 the host confirming a bank transfer arrived. This is the
                 only place money becomes real: it increments the host's
                 cumulative_received, bumps the goal's raised total, and (for
                 gateway gifts only) credits the wallet's pending balance.

Bank transfers: create_transfer_gift() records a pending gift with a short
reference and returns the host's bank account for the guest to pay into.
The money never touches Avela, so confirming one doesn't credit the wallet.
"""
import logging
import secrets

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from wallet.models import Wallet
from integrations.gateways import payment_gateway
from .models import MonetaryGift


class GiftBlocked(Exception):
    """Raised when an unverified host is already over the receiving cap."""


class NoBankAccount(Exception):
    """The host hasn't added the bank account guests transfer into."""


class GiftUnavailable(Exception):
    """The wishlist item is already bought, fully funded, or the amount is
    more than what's left to fund it."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def naira_short(kobo: int) -> str:
    value = kobo / 100
    return f"₦{value:,.0f}" if kobo % 100 == 0 else f"₦{value:,.2f}"


# No 0/O/1/I/L so the code survives being read aloud or typed into a bank app.
_REF_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _transfer_reference() -> str:
    while True:
        ref = "AV-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))
        if not MonetaryGift.objects.filter(gateway_reference=ref).exists():
            return ref


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


def create_transfer_gift(*, event, amount_kobo, giver_name="", message="",
                         show_name=True, show_amount=True, goal=None,
                         wishlist_item=None) -> dict:
    host = event.host
    bank = getattr(host, "bank_account", None)
    if bank is None:
        raise NoBankAccount(
            f"{event.title} isn't set up to receive transfers yet. "
            "The host needs to add their bank account."
        )
    if wishlist_item is not None:
        state = wishlist_item.availability
        if state == "bought":
            raise GiftUnavailable(f"Someone has already bought {wishlist_item.name}.", "item_bought")
        if state == "held":
            raise GiftUnavailable(
                f"Someone is buying {wishlist_item.name} right now. Check back in a few minutes.",
                "item_held")
        if state == "funded":
            raise GiftUnavailable(f"{wishlist_item.name} is already fully funded.", "item_funded")
        remaining = wishlist_item.remaining_kobo
        if remaining is not None and amount_kobo > remaining:
            raise GiftUnavailable(
                f"Only {naira_short(remaining)} is left to fully fund {wishlist_item.name}.",
                "over_remaining")
    allowed, crossing = host.can_receive(amount_kobo)
    if not allowed:
        raise GiftBlocked(
            f"{event.title} can't receive gifts right now. "
            "The host has been notified to verify and unlock them."
        )
    gift = MonetaryGift.objects.create(
        event=event, goal=goal, wishlist_item=wishlist_item,
        amount_kobo=amount_kobo, message=message, giver_name=giver_name,
        show_name=show_name, show_amount=show_amount, is_crossing=crossing,
        method=MonetaryGift.Method.BANK_TRANSFER,
        gateway_reference=_transfer_reference(),
    )
    return {"gift": gift, "bank": bank}


def mark_transfer_sent(gift: MonetaryGift) -> MonetaryGift:
    """Guest tapped "I've sent it": stamp it and nudge the host to confirm."""
    if gift.marked_sent_at or gift.status != MonetaryGift.Status.INITIATED:
        return gift
    gift.marked_sent_at = timezone.now()
    gift.save(update_fields=["marked_sent_at"])
    from accounts.services import notify
    from config.constants import naira
    who = gift.giver_name or "A guest"
    notify(gift.event.host, "contribution", "Confirm a transfer",
           f"{who} says they sent {naira(gift.amount_kobo)} for {gift.event.title} "
           f"(ref {gift.gateway_reference}). Confirm it once it's in your account.")
    # And an email, so the host knows to check their bank even when they're
    # not in the app. Best-effort: a mail hiccup never fails the guest's tap.
    from accounts.emails import send_contribution_to_confirm
    try:
        send_contribution_to_confirm(gift)
    except Exception:
        logging.getLogger(__name__).exception("Couldn't email host about gift %s", gift.id)
    return gift


def decline_transfer(gift: MonetaryGift) -> MonetaryGift:
    """Host says the transfer never arrived."""
    if gift.status == MonetaryGift.Status.INITIATED:
        gift.status = MonetaryGift.Status.FAILED
        gift.save(update_fields=["status"])
    return gift


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

    if gift.method == MonetaryGift.Method.GATEWAY:
        # Bank transfers land in the host's own account, never the wallet.
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
    if gift.method == MonetaryGift.Method.GATEWAY:
        notify(host, "contribution", "New contribution",
               f"{gift.public_giver} sent {naira(gift.amount_kobo)} on {gift.event.title}.")
    return gift
