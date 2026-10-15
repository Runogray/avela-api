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


class ReserveView(viewsets.ViewSet):
    permission_classes = [AllowAny]

    def create(self, request, item_id=None):
        """Reserve a wishlist item, locking the row so two simultaneous
        reservations can't both win (D2 reserve-lock). The row is selected
        FOR UPDATE inside the transaction; the second caller then sees it as
        already reserved and gets a 409."""
        try:
            with transaction.atomic():
                item = (WishlistItem.objects
                        .select_for_update()
                        .get(id=item_id))
                if item.status == WishlistItem.Status.RESERVED:
                    return response.Response(
                        {"detail": f"Someone has already bought {item.name}.",
                         "code": "item_bought"},
                        status=status.HTTP_409_CONFLICT)
                if item.availability == "funded":
                    return response.Response(
                        {"detail": f"{item.name} is already fully funded.",
                         "code": "item_funded"},
                        status=status.HTTP_409_CONFLICT)
                # Friends are already paying toward it: buying it outright
                # as well would double up, so they contribute instead.
                if item.has_committed_contributions:
                    return response.Response(
                        {"detail": f"Friends are already chipping in on {item.name}, "
                                   "so it can't be bought outright. You can contribute instead.",
                         "code": "has_contributions"},
                        status=status.HTTP_409_CONFLICT)
                item.status = WishlistItem.Status.RESERVED
                item.reserved_by_name = request.data.get("guest_name", "")
                item.reserved_show_name = request.data.get("show_name", True)
                item.reserved_at = timezone.now()
                item.save(update_fields=["status", "reserved_by_name",
                                         "reserved_show_name", "reserved_at"])
        except WishlistItem.DoesNotExist:
            return response.Response({"detail": "Item not found."},
                                     status=status.HTTP_404_NOT_FOUND)
        return response.Response(WishlistItemSerializer(item).data)
