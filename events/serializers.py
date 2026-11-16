from rest_framework import serializers

from config.constants import KYC_CAP_KOBO
from .images import StoredImageField, image_url
from .models import Event, RSVP, WishlistItem, ContributionGoal


class WishlistItemSerializer(serializers.ModelSerializer):
    image = StoredImageField("item")
    reserver_display = serializers.ReadOnlyField()
    raised_kobo = serializers.ReadOnlyField()
    remaining_kobo = serializers.ReadOnlyField()
    availability = serializers.ReadOnlyField()

    class Meta:
        model = WishlistItem
        fields = ["id", "name", "description", "image", "link", "price_kobo",
                  "status", "reserver_display", "raised_kobo", "remaining_kobo",
                  "availability"]
        read_only_fields = ["status", "reserver_display", "raised_kobo",
                            "remaining_kobo", "availability"]


class ContributionGoalSerializer(serializers.ModelSerializer):
    progress_pct = serializers.ReadOnlyField()

    class Meta:
        model = ContributionGoal
        fields = ["id", "name", "target_kobo", "raised_kobo", "progress_pct",
                  "hide_contributions", "hide_total"]
        read_only_fields = ["raised_kobo", "progress_pct",
                            "hide_contributions", "hide_total"]


class PublicEventSerializer(serializers.ModelSerializer):
    """What a guest sees on the page — host-private fields excluded.

    Mirrors the frontend `PublicEvent` type (src/lib/types.ts): module data
    (wishlist/goals/wishes/recent gifts), social proof (rsvp_yes_count) and the
    A3 'gift box is full' flag (gifts_paused).
    """
    wishlist_items = WishlistItemSerializer(many=True, read_only=True)
    cover_image = StoredImageField("cover", read_only=True)
    goals = ContributionGoalSerializer(many=True, read_only=True)
    host_verified = serializers.BooleanField(source="host.nin_verified", read_only=True)
    host_first_name = serializers.CharField(source="host.first_name", read_only=True)
    wishes = serializers.SerializerMethodField()
    recent_gifts = serializers.SerializerMethodField()
    rsvp_yes_count = serializers.SerializerMethodField()
    gifts_paused = serializers.SerializerMethodField()
    gallery = serializers.SerializerMethodField()
    # The host has a bank account, so guests can pay cash gifts by transfer.
    # (The account itself is only revealed once a guest starts a gift.)
    accepts_transfers = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "slug", "title", "description", "occasion", "cover_image", "cover_position",
                  "starts_at", "venue", "dress_code", "host_verified",
                  "host_first_name",
                  "accepts_money", "accepts_wishlist", "accepts_wishes",
                  "wishlist_items", "goals", "wishes", "recent_gifts",
                  "rsvp_yes_count", "rsvp_enabled", "rsvp_party_max",
                  "gifts_paused", "gallery", "accepts_transfers"]

    def get_gallery(self, obj):
        req = self.context.get("request")
        return [image_url(req, "photo", p.id, p.image) for p in obj.photos.all()]

    def get_wishes(self, obj):
        # Well wishes are notes to the host, read on their own page; they're
        # not published to everyone with the invite link.
        return []

    def get_recent_gifts(self, obj):
        from giving.models import MonetaryGift
        from giving.serializers import PublicGiftSerializer
        qs = (obj.gifts.filter(status=MonetaryGift.Status.PAID)
              .order_by("-paid_at", "-created_at")[:20])
        return PublicGiftSerializer(qs, many=True).data

    def get_rsvp_yes_count(self, obj):
        return obj.rsvps.filter(status=RSVP.Status.YES).count()

    def get_accepts_transfers(self, obj) -> bool:
        return hasattr(obj.host, "bank_account")

    def get_gifts_paused(self, obj):
        """A3: unverified host already over the receiving cap — new gifts blocked."""
        host = obj.host
        return (not host.nin_verified) and host.cumulative_received_kobo >= KYC_CAP_KOBO


class RSVPSerializer(serializers.ModelSerializer):
    """A guest's reply. Email and phone go to the host only (write-only here)."""
    guest_name = serializers.CharField(max_length=120)
    party_size = serializers.IntegerField(min_value=1, max_value=20, default=1)
    guest_email = serializers.EmailField(required=False, allow_blank=True, write_only=True)
    guest_phone = serializers.CharField(required=False, allow_blank=True, max_length=30, write_only=True)

    class Meta:
        model = RSVP
        fields = ["id", "guest_name", "status", "party_size", "guest_email", "guest_phone", "table_number"]
        read_only_fields = ["table_number"]

    def validate_guest_name(self, v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Add your name.")
        return v

    def validate_guest_email(self, v):
        return (v or "").strip().lower()

    def validate(self, data):
        event = self.context.get("event")
        if data.get("status") == RSVP.Status.NO:
            data["party_size"] = 1  # a "no" is one reply, not a headcount
        elif event is not None and data.get("party_size", 1) > event.rsvp_party_max:
            extra = event.rsvp_party_max - 1
            msg = ("This celebration doesn't have room for plus-ones." if extra == 0 else
                   "You can bring a plus-one." if extra == 1 else
                   f"You can bring up to {extra} plus-ones.")
            raise serializers.ValidationError({"party_size": [msg]})
        return data
