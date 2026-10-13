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

    @property
    def reserver_display(self) -> str:
        if self.status != self.Status.RESERVED:
            return ""
        return self.reserved_by_name if self.reserved_show_name else "Anonymous"


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
