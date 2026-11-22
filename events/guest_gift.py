"""
A guest's private gift page: avelaevent.com/gift/<token>.

Guests don't have accounts, so each gift carries an unguessable token. The
guest gets the link after giving (and in their emails); it shows what they
gave, where it is (sent, received) and the host's thank-you note. Nobody else
sees the note.
"""
from rest_framework import decorators, response, status
from rest_framework.permissions import AllowAny

from giving.models import MonetaryGift
from .images import image_url
from .models import WishlistItem


def _event(request, event) -> dict:
    return {
        "slug": event.slug,
        "title": event.title,
        "occasion": event.occasion,
        "category": event.category,
        "host_first_name": (event.host.first_name or "").strip(),
        "cover_image": image_url(request, "cover", event.pk, event.cover_image),
        "starts_at": event.starts_at,
    }


def _item(request, item) -> dict | None:
    if item is None:
        return None
    return {"id": str(item.id), "name": item.name, "price_kobo": item.price_kobo,
            "image": image_url(request, "item", item.pk, item.image), "link": item.link}


def _cash(request, gift: MonetaryGift) -> dict:
    if gift.status == MonetaryGift.Status.PAID:
        stage = "received"
    elif gift.status in (MonetaryGift.Status.FAILED, MonetaryGift.Status.REVERSED):
        stage = "not_received"
    else:
        stage = "sent" if gift.marked_sent_at else "started"
    if gift.method == MonetaryGift.Method.GATEWAY and gift.status == MonetaryGift.Status.INITIATED:
        stage = "paying"  # left for the payment page and hasn't paid (yet)
    return {
        "kind": "cash",
        "method": "card" if gift.method == MonetaryGift.Method.GATEWAY else "transfer",
        "fee_kobo": gift.fee_kobo,
        "stage": stage,
        "giver_name": gift.giver_name,
        "amount_kobo": gift.amount_kobo,
        "reference": gift.gateway_reference,
        "item": _item(request, gift.wishlist_item),
        "goal": ({"id": str(gift.goal.id), "name": gift.goal.name} if gift.goal else None),
        "event": _event(request, gift.event),
        "created_at": gift.created_at,
        # A card gift is "sent" the moment it's paid.
        "sent_at": gift.marked_sent_at or (gift.paid_at if gift.method == MonetaryGift.Method.GATEWAY else None),
        "received_at": gift.paid_at,
        "thank_you_message": gift.thank_you_message,
        "thanked_at": gift.thanked_at,
    }


def _bought(request, item: WishlistItem) -> dict:
    if item.purchased_at is None:
        stage = "held"
    else:
        stage = "received" if item.owner_confirmed_at else "sent"
    return {
        "kind": "item",
        "stage": stage,
        "giver_name": item.reserved_by_name,
        "amount_kobo": None,
        "reference": "",
        "item": _item(request, item),
        "goal": None,
        "event": _event(request, item.event),
        "created_at": item.reserved_at,
        "sent_at": item.purchased_at,
        "received_at": item.owner_confirmed_at,
        "thank_you_message": item.thank_you_message,
        "thanked_at": item.thanked_at,
    }


@decorators.api_view(["GET"])
@decorators.permission_classes([AllowAny])
def guest_gift(request, token):
    if len(token) < 16:
        return response.Response(status=status.HTTP_404_NOT_FOUND)
    gift = (MonetaryGift.objects.select_related("event__host", "wishlist_item", "goal")
            .filter(gift_token=token).first())
    if gift is not None:
        # Back from paying by card: check with the gateway now rather than
        # waiting for its webhook, so the guest sees "Received" straight away.
        if gift.method == MonetaryGift.Method.GATEWAY and gift.status == MonetaryGift.Status.INITIATED:
            from giving.services import settle_card_gift
            gift = settle_card_gift(gift)
        return response.Response(_cash(request, gift))
    item = (WishlistItem.objects.select_related("event__host")
            .filter(gift_token=token).first())
    if item is not None:
        return response.Response(_bought(request, item))
    return response.Response(
        {"detail": "We couldn't find this gift. The host may have put it back on their list."},
        status=status.HTTP_404_NOT_FOUND)
