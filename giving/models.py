"""
Giving — monetary gifts (direct or toward a goal) and well-wishes.

Privacy is two independent axes on a money gift:
  - show_name:   name shown vs anonymous
  - show_amount: amount shown vs hidden
The public serializer honours both (see giving/serializers.py).
"""
import uuid
from django.db import models

from events.models import Event, ContributionGoal, WishlistItem


class MonetaryGift(models.Model):
    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"     # checkout started
        PAID = "paid", "Paid"                     # gateway confirmed (webhook)
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"         # chargeback

    class Method(models.TextChoices):
        GATEWAY = "gateway", "Card / gateway"        # Paystack checkout
        BANK_TRANSFER = "transfer", "Bank transfer"  # guest pays the host's account directly

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="gifts")
    goal = models.ForeignKey(ContributionGoal, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="gifts")
    # Cash toward a physical wishlist item (no raised total; shown to the host).
    wishlist_item = models.ForeignKey(WishlistItem, null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="gifts")
    method = models.CharField(max_length=10, choices=Method.choices,
                              default=Method.GATEWAY)

    amount_kobo = models.BigIntegerField()
    message = models.TextField(blank=True)

    giver_name = models.CharField(max_length=120, blank=True)
    # Optional: lets us tell the guest when the host confirms it, and lets
    # the host send a thank-you note.
    giver_email = models.EmailField(blank=True)
    thank_you_message = models.TextField(blank=True)
    thanked_at = models.DateTimeField(null=True, blank=True)
    show_name = models.BooleanField(default=True)
    show_amount = models.BooleanField(default=True)

    # `is_crossing` records that this was the gift that took the host over the
    # cap; everything after a host hits the cap is blocked until NIN verify.
    is_crossing = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.INITIATED)
    # Gateway reference, or for bank transfers the code the guest puts in
    # the transfer narration so the host can match it (e.g. AV-7K3Q9P).
    gateway_reference = models.CharField(max_length=120, blank=True, db_index=True)
    # Bank transfer: when the guest tapped "I've sent it" (host still confirms).
    marked_sent_at = models.DateTimeField(null=True, blank=True)
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
