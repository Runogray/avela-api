"""
Events — the page, its RSVP list, wishlist, and contribution goals.
"""
import uuid
from django.conf import settings
from django.db import models


class Event(models.Model):
    class Occasion(models.TextChoices):
        WEDDING = "wedding", "Wedding"
        BIRTHDAY = "birthday", "Birthday"
        BABY_SHOWER = "baby_shower", "Baby shower"

    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public (anyone with the link)"
        INVITE = "invite", "Invite-only"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="events")
    slug = models.SlugField(unique=True, max_length=80)

    title = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    occasion = models.CharField(max_length=20, choices=Occasion.choices)
    # Free-form sub-type of the occasion (e.g. "White wedding", "Traditional").
    category = models.CharField(max_length=60, blank=True)
    # TextField (not URLField) so it can hold an uploaded image as a data URL
    # as well as an external URL (no media server in this build).
    cover_image = models.TextField(blank=True)
    # Focus point for the cover as a CSS object-position ("50% 30%"), set by
    # the host's "Adjust cover". Every frame crops around it. Blank = centre.
    cover_position = models.CharField(max_length=20, blank=True, default="")

    starts_at = models.DateTimeField()
    venue = models.CharField(max_length=200, blank=True)
    dress_code = models.CharField(max_length=120, blank=True)

    visibility = models.CharField(max_length=10, choices=Visibility.choices,
                                  default=Visibility.PUBLIC)

    # host toggles which modules the page shows
    accepts_money = models.BooleanField(default=True)
    accepts_wishlist = models.BooleanField(default=True)
    accepts_wishes = models.BooleanField(default=True)

    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class RSVP(models.Model):
    class Status(models.TextChoices):
        YES = "yes", "Attending"
        MAYBE = "maybe", "Maybe"
        NO = "no", "Not attending"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rsvps")
    guest_name = models.CharField(max_length=120)
    status = models.CharField(max_length=6, choices=Status.choices)
    # optional table assignment (P1 — kept simple as a nullable field, no model)
    table_number = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WishlistItem(models.Model):
    """A physical gift. Reserving it locks it so two guests never double up."""
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE,
                              related_name="wishlist_items")
    name = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    image = models.TextField(blank=True)            # data URL or external URL
    link = models.URLField(blank=True)              # optional external store link
    price_kobo = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.AVAILABLE)
    reserved_by_name = models.CharField(max_length=120, blank=True)
    reserved_show_name = models.BooleanField(default=True)
    reserved_at = models.DateTimeField(null=True, blank=True)
    # "Buy the gift" holds the item for HOLD_MINUTES so the guest can go buy
    # it; "I've bought it" (proved with the hold token) makes it bought for
    # good. An unconfirmed hold simply lapses and the item is free again.
    reserved_until = models.DateTimeField(null=True, blank=True)
    reserve_token = models.CharField(max_length=64, blank=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    # The host confirmed the bought gift arrived (until then it's "pending").
    owner_confirmed_at = models.DateTimeField(null=True, blank=True)

    HOLD_MINUTES = 10

    @property
    def reserver_display(self) -> str:
        if self.status != self.Status.RESERVED:
            return ""
        return self.reserved_by_name if self.reserved_show_name else "Anonymous"

    @property
    def raised_kobo(self) -> int:
        """Confirmed cash gifts toward this item (guests can chip in on a
        physical gift as well as reserve it). Derived, not stored: transfers
        are few per item and this keeps one source of truth."""
        # gifts.all() reuses a prefetch (wishlist_items__gifts) when present,
        # so listing an event's items doesn't run several queries per item.
        return sum(g.amount_kobo for g in self.gifts.all() if g.status == "paid")

    @property
    def awaiting_kobo(self) -> int:
        """Gifts a guest says they've sent that the host hasn't confirmed yet."""
        return sum(g.amount_kobo for g in self.gifts.all()
                   if g.status == "initiated" and g.marked_sent_at is not None)

    @property
    def remaining_kobo(self) -> int | None:
        """What guests can still give (None = no price): the price less
        confirmed gifts and ones awaiting the host's confirmation."""
        if not self.price_kobo:
            return None
        return max(0, self.price_kobo - self.raised_kobo - self.awaiting_kobo)

    @property
    def has_committed_contributions(self) -> bool:
        """Money confirmed, or a guest who says they've sent it."""
        return any(g.status == "paid" or (g.status == "initiated" and g.marked_sent_at is not None)
                   for g in self.gifts.all())

    @property
    def is_held(self) -> bool:
        """A guest is mid-purchase: reserved, not yet bought, hold not lapsed."""
        from django.utils import timezone
        return (self.status == self.Status.RESERVED and self.purchased_at is None
                and self.reserved_until is not None and self.reserved_until > timezone.now())

    @property
    def availability(self) -> str:
        """"bought" (the host confirmed a guest bought it), "pending" (bought,
        or covered by gifts, but awaiting the host's confirmation), "held" (a
        guest has it for a few minutes while they buy it), "funded" (confirmed
        gifts cover the price) or "available". Only "available" items can be
        bought or given toward."""
        if self.status == self.Status.RESERVED and self.purchased_at is not None:
            return "bought" if self.owner_confirmed_at else "pending"
        if self.is_held:
            return "held"
        if self.price_kobo:
            if self.raised_kobo >= self.price_kobo:
                return "funded"
            if self.raised_kobo + self.awaiting_kobo >= self.price_kobo:
                return "pending"  # covered, but the host hasn't confirmed it all
        return "available"

    def release(self):
        """Back on the list (hold lapsed or cancelled, or the host undid it)."""
        self.status = self.Status.AVAILABLE
        self.reserved_by_name = ""
        self.reserved_at = None
        self.reserved_until = None
        self.reserve_token = ""
        self.purchased_at = None
        self.owner_confirmed_at = None
        self.save(update_fields=["status", "reserved_by_name", "reserved_at",
                                 "reserved_until", "reserve_token", "purchased_at",
                                 "owner_confirmed_at"])


class EventPhoto(models.Model):
    """A gallery photo on an event (stored as a data URL or external URL)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE,
                              related_name="photos")
    image = models.TextField()
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]


class Invite(models.Model):
    """A celebration a user has been invited to (their 'Invites received')."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name="invites")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE,
                              related_name="invites")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("recipient", "event")
        ordering = ["-created_at"]


class ContributionGoal(models.Model):
    """
    A money target (e.g. 'Honeymoon fund'). The progress bar is MOTIVATIONAL:
    funds reach the host regardless of whether the target is met (locked decision).
    `raised_kobo` is a denormalised running total updated as gifts confirm.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="goals")
    name = models.CharField(max_length=140)
    target_kobo = models.BigIntegerField()
    raised_kobo = models.BigIntegerField(default=0)
    # Guest-facing privacy: hide per-person contribution amounts and/or the
    # running total raised on the public page.
    hide_contributions = models.BooleanField(default=False)
    hide_total = models.BooleanField(default=False)

    @property
    def progress_pct(self) -> float:
        if not self.target_kobo:
            return 0.0
        return min(100.0, round(self.raised_kobo / self.target_kobo * 100, 2))
