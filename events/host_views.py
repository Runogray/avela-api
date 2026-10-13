"""Authenticated host-side CRUD — an owner managing their own events and the
nested wishlist items, goals, RSVPs and wishes. All querysets are scoped to
``request.user`` so a host can only ever see or touch their own data.

Routes (wired in config/urls.py):
  /api/host/events/                         list + create my events
  /api/host/events/{id}/                    retrieve / update / delete
  /api/host/events/{id}/publish/            publish (unpublish/ to reverse)
  /api/host/events/{id}/wishlist-items/     list + add wishlist items
  /api/host/events/{id}/goals/              list + add contribution goals
  /api/host/events/{id}/rsvps/              list RSVPs (host view)
  /api/host/events/{id}/wishes/             list wishes (host view)
  /api/host/wishlist-items/{id}/            update / delete an item
  /api/host/goals/{id}/                     update / delete a goal
  /api/host/wishes/{id}/                    reply / hide / delete a wish
  /api/host/rsvps/{id}/                     assign a table number
  /api/host/events/{id}/transfers/          list bank-transfer gifts
  /api/host/events/{id}/transfers/{gift}/confirm/   money arrived -> counts
  /api/host/events/{id}/transfers/{gift}/decline/   never arrived
"""
import uuid

from django.utils.text import slugify
from rest_framework import decorators, mixins, response, status, viewsets
from rest_framework.permissions import IsAuthenticated

from giving.models import MonetaryGift, Wish
from giving.services import confirm_gift, decline_transfer
from .host_serializers import (EventPhotoSerializer,
                               HostContributionGoalSerializer,
                               HostEventSerializer, HostRSVPSerializer,
                               HostTransferGiftSerializer,
                               HostWishSerializer, HostWishlistItemSerializer,
                               InviteSerializer)
from .models import (ContributionGoal, Event, EventPhoto, Invite, RSVP,
                     WishlistItem)


def _unique_slug(title: str) -> str:
    base = slugify(title)[:70] or "event"
    return f"{base}-{uuid.uuid4().hex[:6]}"


class HostEventViewSet(viewsets.ModelViewSet):
    """A host's own events — full CRUD, scoped to the authenticated user."""
    serializer_class = HostEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (Event.objects.filter(host=self.request.user)
                .prefetch_related("wishlist_items", "goals")
                .order_by("-created_at"))

    def perform_create(self, serializer):
        serializer.save(
            host=self.request.user,
            slug=_unique_slug(serializer.validated_data.get("title", "event")),
        )

    @decorators.action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        event = self.get_object()
        event.is_published = True
        event.save(update_fields=["is_published"])
        return response.Response(self.get_serializer(event).data)

    @decorators.action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        event = self.get_object()
        event.is_published = False
        event.save(update_fields=["is_published"])
        return response.Response(self.get_serializer(event).data)

    @decorators.action(detail=True, methods=["get"])
    def transfers(self, request, pk=None):
        """Bank-transfer gifts on this event, pending first, newest first."""
        event = self.get_object()
        qs = (event.gifts.filter(method=MonetaryGift.Method.BANK_TRANSFER)
              .exclude(status=MonetaryGift.Status.FAILED)
              .select_related("goal", "wishlist_item"))
        order = {MonetaryGift.Status.INITIATED: 0}
        rows = sorted(qs, key=lambda g: (order.get(g.status, 1), -g.created_at.timestamp()))
        return response.Response(HostTransferGiftSerializer(rows, many=True).data)

    def _transfer(self, pk, gift_id):
        event = self.get_object()
        return event.gifts.filter(
            id=gift_id, method=MonetaryGift.Method.BANK_TRANSFER).first()

    @decorators.action(detail=True, methods=["post"],
                       url_path=r"transfers/(?P<gift_id>[0-9a-f-]{36})/confirm")
    def confirm_transfer(self, request, pk=None, gift_id=None):
        gift = self._transfer(pk, gift_id)
        if gift is None:
            return response.Response(status=status.HTTP_404_NOT_FOUND)
        if gift.status != MonetaryGift.Status.PAID:
            gift = confirm_gift(gift)
        return response.Response(HostTransferGiftSerializer(gift).data)

    @decorators.action(detail=True, methods=["post"],
                       url_path=r"transfers/(?P<gift_id>[0-9a-f-]{36})/decline")
    def decline_transfer(self, request, pk=None, gift_id=None):
        gift = self._transfer(pk, gift_id)
        if gift is None:
            return response.Response(status=status.HTTP_404_NOT_FOUND)
        if gift.status == MonetaryGift.Status.PAID:
            return response.Response({"detail": "Already confirmed."},
                                     status=status.HTTP_409_CONFLICT)
        return response.Response(HostTransferGiftSerializer(decline_transfer(gift)).data)

    @decorators.action(detail=True, methods=["get", "post"], url_path="wishlist-items")
    def wishlist_items(self, request, pk=None):
        event = self.get_object()
        if request.method == "POST":
            ser = HostWishlistItemSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            ser.save(event=event)
            return response.Response(ser.data, status=status.HTTP_201_CREATED)
        return response.Response(
            HostWishlistItemSerializer(event.wishlist_items.all(), many=True).data)

    @decorators.action(detail=True, methods=["get", "post"])
    def goals(self, request, pk=None):
        event = self.get_object()
        if request.method == "POST":
            ser = HostContributionGoalSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            ser.save(event=event)
            return response.Response(ser.data, status=status.HTTP_201_CREATED)
        return response.Response(
            HostContributionGoalSerializer(event.goals.all(), many=True).data)

    @decorators.action(detail=True, methods=["get", "post"])
    def photos(self, request, pk=None):
        event = self.get_object()
        if request.method == "POST":
            ser = EventPhotoSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            ser.save(event=event)
            return response.Response(ser.data, status=status.HTTP_201_CREATED)
        return response.Response(
            EventPhotoSerializer(event.photos.all(), many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def rsvps(self, request, pk=None):
        event = self.get_object()
        qs = event.rsvps.order_by("-created_at")
        return response.Response(HostRSVPSerializer(qs, many=True).data)

    @decorators.action(detail=True, methods=["get"])
    def wishes(self, request, pk=None):
        event = self.get_object()
        qs = event.wishes.order_by("-created_at")
        return response.Response(HostWishSerializer(qs, many=True).data)


class _OwnedDetailViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Retrieve / update / delete a single nested object the host owns."""
    permission_classes = [IsAuthenticated]


class HostWishlistItemViewSet(_OwnedDetailViewSet):
    serializer_class = HostWishlistItemSerializer

    def get_queryset(self):
        return WishlistItem.objects.filter(event__host=self.request.user)


class HostGoalViewSet(_OwnedDetailViewSet):
    serializer_class = HostContributionGoalSerializer

    def get_queryset(self):
        return ContributionGoal.objects.filter(event__host=self.request.user)


class HostWishViewSet(_OwnedDetailViewSet):
    serializer_class = HostWishSerializer

    def get_queryset(self):
        return Wish.objects.filter(event__host=self.request.user)


class HostRSVPViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      viewsets.GenericViewSet):
    """Host can view and assign a table number on an RSVP (no create/delete)."""
    serializer_class = HostRSVPSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return RSVP.objects.filter(event__host=self.request.user)


class InviteViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """The current user's received invites.

    GET  /api/invites/            list my invites (with event summary)
    POST /api/invites/  {event}   add one by event slug or id (idempotent)
    DELETE /api/invites/{id}/     remove one
    """
    serializer_class = InviteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (Invite.objects.filter(recipient=self.request.user)
                .select_related("event", "event__host")
                .prefetch_related("event__wishlist_items"))

    def create(self, request, *args, **kwargs):
        ref = str(request.data.get("event", "")).strip()
        event = Event.objects.filter(slug=ref).first()
        if event is None:
            try:
                event = Event.objects.filter(id=uuid.UUID(ref)).first()
            except (ValueError, AttributeError):
                event = None
        if event is None:
            return response.Response({"detail": "Event not found."},
                                     status=status.HTTP_404_NOT_FOUND)
        invite, created = Invite.objects.get_or_create(
            recipient=request.user, event=event)
        if created:
            from accounts.services import notify
            notify(request.user, "invite", "Invite added",
                   f"You joined {event.title}.")
        return response.Response(self.get_serializer(invite).data,
                                 status=status.HTTP_201_CREATED)


class HostPhotoViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Delete a gallery photo the host owns."""
    serializer_class = EventPhotoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return EventPhoto.objects.filter(event__host=self.request.user)
