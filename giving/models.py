"""
Giving — monetary gifts (direct or toward a goal) and well-wishes.

Privacy is two independent axes on a money gift:
  - show_name:   name shown vs anonymous
  - show_amount: amount shown vs hidden
The public serializer honours both (see giving/serializers.py).
"""
import uuid
from django.db import models

from events.models import Event, ContributionGoal


class MonetaryGift(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"     # checkout started
        PAID = "paid", "Paid"                     # gateway confirmed (webhook)
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"         # chargeback

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="gifts")
    goal = models.ForeignKey(ContributionGoal, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="gifts")

    amount_kobo = models.BigIntegerField()
    message = models.TextField(blank=True)

    giver_name = models.CharField(max_length=120, blank=True)
    show_name = models.BooleanField(default=True)
    show_amount = models.BooleanField(default=True)

    # `is_crossing` records that this was the gift that took the host over the
    # cap; everything after a host hits the cap is blocked until NIN verify.
    is_crossing = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.INITIATED)
    gateway_reference = models.CharField(max_length=120, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    @property
    def public_giver(self) -> str:
        return self.giver_name if self.show_name and self.giver_name else "Anonymous"


class Wish(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="wishes")
    guest_name = models.CharField(max_length=120)
    message = models.TextField()

    host_reply = models.TextField(blank=True)
    is_hidden = models.BooleanField(default=False)        # moderation
    created_at = models.DateTimeField(auto_now_add=True)
