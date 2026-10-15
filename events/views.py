import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import viewsets, decorators, response, status
from rest_framework.permissions import AllowAny

from .models import Event, WishlistItem
from .serializers import (PublicEventSerializer, RSVPSerializer,
                          WishlistItemSerializer)
from giving.models import MonetaryGift, Wish
from giving.serializers import (CreateGiftSerializer, CreateTransferGiftSerializer,
                                PublicGiftSerializer, WishSerializer)
from giving.services import (create_gift, create_transfer_gift, mark_transfer_sent,
                             GiftBlocked, GiftUnavailable, NoBankAccount)
from events.models import ContributionGoal


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    """Public read of a published event by slug, plus the guest action verbs."""
    queryset = Event.objects.filter(is_published=True)
    serializer_class = PublicEventSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    @decorators.action(detail=True, methods=["post"])
    def rsvp(self, request, slug=None):
        event = self.get_object()
        ser = RSVPSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(event=event)
        return response.Response(ser.data, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"])
    def wishes(self, request, slug=None):
        event = self.get_object()
        ser = WishSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(event=event)
        return response.Response(ser.data, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"], url_path="gifts")
    def send_gift(self, request, slug=None):
        event = self.get_object()
        ser = CreateGiftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        goal = None
        if data.get("goal_id"):
            goal = ContributionGoal.objects.filter(
                id=data["goal_id"], event=event).first()
        try:
            result = create_gift(
                event=event, amount_kobo=data["amount_kobo"], goal=goal,
                giver_name=data.get("giver_name", ""),
                message=data.get("message", ""),
                show_name=data["show_name"], show_amount=data["show_amount"],
            )
        except GiftBlocked as e:
            # graceful giver-side block (Epic A3)
            return response.Response({"detail": str(e), "code": "host_capped"},
                                     status=status.HTTP_409_CONFLICT)
        return response.Response(
            {"gift_id": str(result["gift"].id),
             "checkout_url": result["checkout_url"]},
            status=status.HTTP_201_CREATED)


    @decorators.action(detail=True, methods=["post"], url_path="transfers")
    def start_transfer(self, request, slug=None):
        """Guest picked an amount: record a pending bank-transfer gift and
        return the host's account + a reference for the narration."""
        event = self.get_object()
        ser = CreateTransferGiftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        goal = item = None
        if data.get("goal_id"):
            goal = ContributionGoal.objects.filter(id=data["goal_id"], event=event).first()
        if data.get("wishlist_item_id"):
            item = WishlistItem.objects.filter(id=data["wishlist_item_id"], event=event).first()
        try:
            result = create_transfer_gift(
                event=event, amount_kobo=data["amount_kobo"], goal=goal,
                wishlist_item=item, giver_name=data["giver_name"].strip(),
                message=data.get("message", ""),
                show_name=data["show_name"], show_amount=data["show_amount"],
            )
        except NoBankAccount as e:
            return response.Response({"detail": str(e), "code": "no_bank_account"},
                                     status=status.HTTP_409_CONFLICT)
        except GiftUnavailable as e:
            return response.Response({"detail": str(e), "code": e.code},
                                     status=status.HTTP_409_CONFLICT)
        except GiftBlocked as e:
            return response.Response({"detail": str(e), "code": "host_capped"},
                                     status=status.HTTP_409_CONFLICT)
        gift, bank = result["gift"], result["bank"]
        return response.Response({
            "gift_id": str(gift.id),
            "reference": gift.gateway_reference,
            "amount_kobo": gift.amount_kobo,
            "bank_name": bank.bank_name,
            "account_number": bank.account_number,
            "account_name": bank.account_name,
        }, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"],
                       url_path=r"transfers/(?P<gift_id>[0-9a-f-]{36})/sent")
    def transfer_sent(self, request, slug=None, gift_id=None):
        """Guest tapped "I've sent it"."""
        event = self.get_object()
        gift = MonetaryGift.objects.filter(
            id=gift_id, event=event, method=MonetaryGift.Method.BANK_TRANSFER).first()
        if gift is None:
            return response.Response(status=status.HTTP_404_NOT_FOUND)
        mark_transfer_sent(gift)
        return response.Response({"status": gift.status,
                                  "marked_sent": bool(gift.marked_sent_at)})


def _gone(item):
    """A 409 for an item that's bought, held by someone else, or funded."""
    state = item.availability
    if state == "bought":
        return {"detail": f"Someone has already bought {item.name}.", "code": "item_bought"}
    if state == "held":
        return {"detail": f"Someone is buying {item.name} right now. Check back in a few minutes.",
                "code": "item_held"}
    if state == "funded":
        return {"detail": f"{item.name} is already fully funded.", "code": "item_funded"}
    return None


class ReserveView(viewsets.ViewSet):
    """Guest "Buy the gift": hold the item for HOLD_MINUTES, then confirm with
    "I've bought it" (bought for good) or let the hold lapse / cancel it.
    Rows are locked FOR UPDATE so two guests can't both win the same item."""
    permission_classes = [AllowAny]

    def create(self, request, item_id=None):
        try:
            with transaction.atomic():
                item = WishlistItem.objects.select_for_update().get(id=item_id)
                gone = _gone(item)
                if gone:
                    return response.Response(gone, status=status.HTTP_409_CONFLICT)
                # Friends are already paying toward it: buying it outright
                # as well would double up, so they contribute instead.
                if item.has_committed_contributions:
                    return response.Response(
                        {"detail": f"Friends are already chipping in on {item.name}, "
                                   "so it can't be bought outright. You can contribute instead.",
                         "code": "has_contributions"},
                        status=status.HTTP_409_CONFLICT)
                now = timezone.now()
                item.status = WishlistItem.Status.RESERVED
                item.reserved_by_name = (request.data.get("guest_name") or "").strip()[:120]
                item.reserved_show_name = request.data.get("show_name", True)
                item.reserved_at = now
                item.reserved_until = now + timedelta(minutes=WishlistItem.HOLD_MINUTES)
                item.reserve_token = secrets.token_urlsafe(24)
                item.purchased_at = None
                item.save(update_fields=["status", "reserved_by_name", "reserved_show_name",
                                         "reserved_at", "reserved_until", "reserve_token",
                                         "purchased_at"])
        except WishlistItem.DoesNotExist:
            return response.Response({"detail": "Item not found."},
                                     status=status.HTTP_404_NOT_FOUND)
        data = WishlistItemSerializer(item).data
        # Only the guest holding it gets the token that confirms or cancels.
        data.update(hold_token=item.reserve_token, reserved_until=item.reserved_until)
        return response.Response(data)

    def _held_by(self, request, item_id):
        """The item, locked, if the caller's hold token still owns it."""
        item = WishlistItem.objects.select_for_update().filter(id=item_id).first()
        token = request.data.get("hold_token") or ""
        if item is None:
            return None, response.Response({"detail": "Item not found."},
                                            status=status.HTTP_404_NOT_FOUND)
        owns = (token and item.status == WishlistItem.Status.RESERVED
                and secrets.compare_digest(token, item.reserve_token))
        if not owns:
            gone = _gone(item) or {"detail": f"Your hold on {item.name} has ended. "
                                             "You can reserve it again.", "code": "hold_ended"}
            return None, response.Response(gone, status=status.HTTP_409_CONFLICT)
        return item, None

    def bought(self, request, item_id=None):
        """Guest tapped "I've bought it". Accepted after the 10 minutes too,
        as long as nobody else has taken the item since."""
        with transaction.atomic():
            item, error = self._held_by(request, item_id)
            if error:
                return error
            if item.purchased_at is None:
                item.purchased_at = timezone.now()
                item.save(update_fields=["purchased_at"])
                from accounts.services import notify
                who = item.reserved_by_name or "A guest"
                notify(item.event.host, "contribution", "A gift was bought",
                       f"{who} bought {item.name} for {item.event.title}. "
                       "If that's not right, you can release it from the gift.")
        return response.Response(WishlistItemSerializer(item).data)

    def release_hold(self, request, item_id=None):
        """Guest changed their mind before buying: put it back."""
        with transaction.atomic():
            item, error = self._held_by(request, item_id)
            if error:
                return error
            if item.purchased_at is None:
                item.release()
        return response.Response(WishlistItemSerializer(item).data)
