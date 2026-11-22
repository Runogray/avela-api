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


class PaymentUnavailable(Exception):
    """The gateway couldn't start a payment."""


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


def _check_item_open(wishlist_item, amount_kobo):
    """A gift toward an item must fit what's still open on it."""
    if wishlist_item is not None:
        state = wishlist_item.availability
        if state == "bought":
            raise GiftUnavailable(f"Someone has already bought {wishlist_item.name}.", "item_bought")
        if state == "pending":
            raise GiftUnavailable(
                f"{wishlist_item.name} is waiting for the host to confirm it. Check back later.",
                "item_pending")
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


def create_transfer_gift(*, event, amount_kobo, giver_name="", giver_email="", message="",
                         show_name=True, show_amount=True, goal=None,
                         wishlist_item=None) -> dict:
    host = event.host
    bank = getattr(host, "bank_account", None)
    if bank is None:
        raise NoBankAccount(
            f"{event.title} isn't set up to receive transfers yet. "
            "The host needs to add their bank account."
        )
    _check_item_open(wishlist_item, amount_kobo)
    allowed, crossing = host.can_receive(amount_kobo)
    if not allowed:
        raise GiftBlocked(
            f"{event.title} can't receive gifts right now. "
            "The host has been notified to verify and unlock them."
        )
    gift = MonetaryGift.objects.create(
        event=event, goal=goal, wishlist_item=wishlist_item,
        amount_kobo=amount_kobo, message=message, giver_name=giver_name,
        giver_email=giver_email,
        show_name=show_name, show_amount=show_amount, is_crossing=crossing,
        method=MonetaryGift.Method.BANK_TRANSFER,
        gateway_reference=_transfer_reference(),
        gift_token=secrets.token_urlsafe(18),
    )
    return {"gift": gift, "bank": bank}


# Paystack's local fee: 1.5% + ₦100 (the ₦100 waived under ₦2,500), capped at ₦2,000.
def gateway_fee_kobo(total_kobo: int) -> int:
    fee = -(-total_kobo * 15 // 1000)  # 1.5%, rounded up
    if total_kobo >= 250_000:
        fee += 10_000
    return min(fee, 200_000)


def card_fee_kobo(gift_kobo: int) -> int:
    """What the guest pays on top so the host receives the whole gift: the
    total is grossed up until Paystack's fee on it is covered, then rounded up
    to the whole naira."""
    total = gift_kobo
    for _ in range(10):
        nxt = gift_kobo + gateway_fee_kobo(total)
        if nxt == total:
            break
        total = nxt
    total = -(-total // 100) * 100
    while total - gift_kobo < gateway_fee_kobo(total):  # rounding can tip it over
        total += 100
    return total - gift_kobo


def create_card_gift(*, event, amount_kobo, callback_url, giver_name="", giver_email="",
                     message="", show_name=True, show_amount=True, goal=None,
                     wishlist_item=None) -> dict:
    """Start a card/USSD/bank payment that settles straight into the host's
    bank (their Paystack subaccount). The guest pays the gift plus the fee;
    we keep exactly the fee, which covers Paystack's charge."""
    from integrations.gateways import card_payments_enabled
    host = event.host
    bank = getattr(host, "bank_account", None)
    if not card_payments_enabled() or bank is None or not bank.paystack_subaccount:
        raise NoBankAccount(f"{event.title} can't take card payments yet. You can send a bank transfer instead.")
    _check_item_open(wishlist_item, amount_kobo)
    allowed, crossing = host.can_receive(amount_kobo)
    if not allowed:
        raise GiftBlocked(
            f"{event.title} can't receive gifts right now. "
            "The host has been notified to verify and unlock them."
        )
    fee = card_fee_kobo(amount_kobo)
    gift = MonetaryGift.objects.create(
        event=event, goal=goal, wishlist_item=wishlist_item,
        amount_kobo=amount_kobo, fee_kobo=fee, paid_direct=True,
        message=message, giver_name=giver_name, giver_email=giver_email,
        show_name=show_name, show_amount=show_amount, is_crossing=crossing,
        method=MonetaryGift.Method.GATEWAY, gift_token=secrets.token_urlsafe(18),
    )
    init = payment_gateway.init_transaction(
        amount_kobo=amount_kobo + fee, email=giver_email,
        metadata={"gift_id": str(gift.id), "event": event.slug},
        subaccount=bank.paystack_subaccount, transaction_charge=fee,
        callback_url=callback_url.replace("{token}", gift.gift_token),
        # Local channels only: international cards cost more than the fee covers.
        channels=["card", "bank", "ussd", "bank_transfer"],
    )
    if not init.get("reference") or not init.get("checkout_url"):
        gift.status = MonetaryGift.Status.FAILED
        gift.save(update_fields=["status"])
        raise PaymentUnavailable("We couldn't start the card payment. Please try again, or send a bank transfer.")
    gift.gateway_reference = init["reference"]
    gift.save(update_fields=["gateway_reference"])
    return {"gift": gift, "checkout_url": init["checkout_url"]}


def settle_card_gift(gift: MonetaryGift) -> MonetaryGift:
    """Confirm a card gift once the gateway says the full amount was paid.
    Safe to call repeatedly (webhook, the guest's return, a retry)."""
    if gift.method != MonetaryGift.Method.GATEWAY or gift.status != MonetaryGift.Status.INITIATED:
        return gift
    if not gift.gateway_reference:
        return gift
    tx = payment_gateway.fetch(gift.gateway_reference)
    if tx.get("status") != "success":
        return gift
    paid = tx.get("amount")
    if paid is not None and int(paid) < gift.amount_kobo + gift.fee_kobo:
        logging.getLogger(__name__).warning("Gift %s underpaid: %s", gift.id, paid)
        return gift
    return confirm_gift(gift)


def mark_transfer_sent(gift: MonetaryGift) -> MonetaryGift:
    """Guest tapped "I've sent it": stamp it and nudge the host to confirm."""
    if gift.marked_sent_at or gift.status != MonetaryGift.Status.INITIATED:
        return gift
    gift.marked_sent_at = timezone.now()
    gift.save(update_fields=["marked_sent_at"])
    from accounts.services import gift_link, notify
    who = gift.giver_name or "A guest"
    notify(gift.event.host, "contribution", "Confirm a transfer",
           f"{who} says they sent {naira_short(gift.amount_kobo)} for {gift.event.title} "
           f"(ref {gift.gateway_reference}). Confirm it once it's in your account.",
           link=gift_link(gift.event_id, gift.wishlist_item_id or gift.goal_id, gift.id))
    # And an email, so the host knows to check their bank even when they're
    # not in the app. Best-effort: a mail hiccup never fails the guest's tap.
    from accounts.emails import send_contribution_to_confirm
    try:
        send_contribution_to_confirm(gift)
    except Exception:
        logging.getLogger(__name__).exception("Couldn't email host about gift %s", gift.id)
    return gift


def _email_guest(send, *args):
    """Guest emails are best-effort: never fail the host's action over them."""
    try:
        send(*args)
    except Exception:
        logging.getLogger(__name__).exception("Couldn't email guest")


def decline_transfer(gift: MonetaryGift) -> MonetaryGift:
    """Host says the transfer never arrived."""
    if gift.status == MonetaryGift.Status.INITIATED:
        gift.status = MonetaryGift.Status.FAILED
        gift.save(update_fields=["status"])
        if gift.giver_email and gift.method == MonetaryGift.Method.BANK_TRANSFER:
            from accounts.emails import send_gift_not_received
            _email_guest(send_gift_not_received, gift)
    return gift


def gift_page_url(token: str) -> str:
    from accounts.emails import SITE_URL
    return f"{SITE_URL}/gift/{token}" if token else ""


def send_gift_thank_you(gift: MonetaryGift, message: str) -> MonetaryGift:
    """The note always shows on the guest's gift page; it's emailed too when
    they left an address."""
    gift.thank_you_message = message
    gift.thanked_at = timezone.now()
    gift.save(update_fields=["thank_you_message", "thanked_at"])
    if gift.giver_email:
        from accounts.emails import send_thank_you
        _email_guest(send_thank_you, gift.giver_email, gift.giver_name, gift.event, message,
                     gift_page_url(gift.gift_token))
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

    if gift.method == MonetaryGift.Method.GATEWAY and not gift.paid_direct:
        # Bank transfers and card gifts paid straight to the host's bank
        # (subaccount) never touch the wallet.
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
    from accounts.services import gift_link, notify
    if gift.method == MonetaryGift.Method.GATEWAY:
        who = gift.giver_name or "A guest"
        toward = (gift.goal.name if gift.goal_id else
                  gift.wishlist_item.name if gift.wishlist_item_id else gift.event.title)
        notify(host, "contribution", "New gift",
               f"{who} paid {naira_short(gift.amount_kobo)} by card toward {toward}. "
               "It's on its way to your bank.",
               link=gift_link(gift.event_id, gift.wishlist_item_id or gift.goal_id, gift.id))
    if gift.giver_email:
        from accounts.emails import send_gift_received
        _email_guest(send_gift_received, gift.giver_email, gift.giver_name, gift.event,
                     f"your {naira_short(gift.amount_kobo)} gift", gift_page_url(gift.gift_token))
    return gift
