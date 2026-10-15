"""Host-facing serializers — the authenticated owner's writable view of an
event and its wishlist / goals / RSVPs / wishes. Separate from the public
(guest) serializers in events/serializers.py, which hide host-private data."""
from django.db.models import Sum
from rest_framework import serializers

from giving.models import MonetaryGift, Wish
from .models import Event, EventPhoto, Invite, RSVP, WishlistItem, ContributionGoal


class EventPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventPhoto
        fields = ["id", "image", "order"]


def _has_contributions(obj) -> bool:
    return obj.gifts.filter(status__in=[MonetaryGift.Status.PAID,
                                        MonetaryGift.Status.INITIATED]).exists()


class HostWishlistItemSerializer(serializers.ModelSerializer):
    reserver_display = serializers.ReadOnlyField()
    raised_kobo = serializers.ReadOnlyField()
    remaining_kobo = serializers.ReadOnlyField()
    availability = serializers.ReadOnlyField()
    # Gifts with money on them can't be removed (see _GiftHolderViewSet).
    has_contributions = serializers.SerializerMethodField()

    def get_has_contributions(self, obj) -> bool:
        return _has_contributions(obj)

    class Meta:
        model = WishlistItem
        fields = ["id", "name", "description", "image", "link", "price_kobo",
                  "status", "reserved_by_name", "reserved_show_name",
                  "reserved_at", "reserved_until", "purchased_at", "owner_confirmed_at",
                  "reserver_display",
                  "raised_kobo", "remaining_kobo", "availability", "has_contributions"]
        # Reservation state is driven by guests reserving, not by the host.
        read_only_fields = ["status", "reserved_by_name", "reserved_show_name",
                            "reserved_at", "reserved_until", "purchased_at",
                            "owner_confirmed_at", "reserver_display", "raised_kobo", "remaining_kobo",
                            "availability", "has_contributions"]


class HostContributionGoalSerializer(serializers.ModelSerializer):
    progress_pct = serializers.ReadOnlyField()
    has_contributions = serializers.SerializerMethodField()

    def get_has_contributions(self, obj) -> bool:
        return _has_contributions(obj)

    class Meta:
        model = ContributionGoal
        fields = ["id", "name", "target_kobo", "raised_kobo", "progress_pct",
                  "hide_contributions", "hide_total", "has_contributions"]
        read_only_fields = ["raised_kobo", "progress_pct", "has_contributions"]


class HostRSVPSerializer(serializers.ModelSerializer):
    """Guests create RSVPs on the public endpoint; the host may assign a table."""

    class Meta:
        model = RSVP
        fields = ["id", "guest_name", "status", "table_number", "created_at"]
        read_only_fields = ["id", "guest_name", "status", "created_at"]


class HostWishSerializer(serializers.ModelSerializer):
    """Guests leave wishes; the host may reply or hide (moderation)."""

    class Meta:
        model = Wish
        fields = ["id", "guest_name", "message", "host_reply", "is_hidden",
                  "created_at"]
        read_only_fields = ["id", "guest_name", "message", "created_at"]


class HostEventSerializer(serializers.ModelSerializer):
    """The host's own event — full read + create/update of the editable fields."""
    wishlist_items = HostWishlistItemSerializer(many=True, read_only=True)
    goals = HostContributionGoalSerializer(many=True, read_only=True)
    photos = EventPhotoSerializer(many=True, read_only=True)

    def validate_cover_position(self, value):
        return _clean_cover_position(value)
    rsvp_yes_count = serializers.SerializerMethodField()
    gift_total_kobo = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "slug", "title", "description", "occasion", "category",
                  "cover_image", "cover_position", "starts_at", "venue", "dress_code", "visibility",
                  "accepts_money", "accepts_wishlist", "accepts_wishes",
                  "is_published", "created_at",
                  "wishlist_items", "goals", "photos", "rsvp_yes_count",
                  "gift_total_kobo"]
        read_only_fields = ["id", "slug", "created_at", "wishlist_items", "goals",
                            "photos", "rsvp_yes_count", "gift_total_kobo"]

    def get_rsvp_yes_count(self, obj) -> int:
        return obj.rsvps.filter(status=RSVP.Status.YES).count()

    def get_gift_total_kobo(self, obj) -> int:
        agg = (obj.gifts.filter(status=MonetaryGift.Status.PAID)
               .aggregate(total=Sum("amount_kobo")))
        return agg["total"] or 0


_POS = __import__("re").compile(r"^(\d{1,3}(?:\.\d+)?)% (\d{1,3}(?:\.\d+)?)%$")


def _clean_cover_position(value: str) -> str:
    """Accept "" (centre) or "X% Y%" with both in 0..100."""
    if not value:
        return ""
    m = _POS.match(value.strip())
    if not m or not all(0 <= float(v) <= 100 for v in m.groups()):
        raise serializers.ValidationError('Use "X% Y%" with values from 0 to 100.')
    return value.strip()


class InviteEventSummarySerializer(serializers.ModelSerializer):
    """Compact event fields for an invite card (the invitee isn't the host)."""
    host_first_name = serializers.CharField(source="host.first_name", read_only=True)
    wishlist_count = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "slug", "title", "occasion", "starts_at", "venue",
                  "cover_image", "cover_position", "host_first_name", "wishlist_count"]

    def get_wishlist_count(self, obj) -> int:
        return obj.wishlist_items.count()


class InviteSerializer(serializers.ModelSerializer):
    event = InviteEventSummarySerializer(read_only=True)

    class Meta:
        model = Invite
        fields = ["id", "created_at", "event"]


class HostTransferGiftSerializer(serializers.ModelSerializer):
    """A bank-transfer gift as the host sees it (always shows name + amount)."""
    reference = serializers.CharField(source="gateway_reference", read_only=True)
    for_name = serializers.SerializerMethodField()

    class Meta:
        model = MonetaryGift
        fields = ["id", "giver_name", "amount_kobo", "reference", "status",
                  "goal_id", "wishlist_item_id", "for_name", "message",
                  "marked_sent_at", "created_at", "paid_at"]
        read_only_fields = fields

    def get_for_name(self, obj) -> str:
        if obj.goal_id:
            return obj.goal.name
        if obj.wishlist_item_id:
            return obj.wishlist_item.name
        return ""
