"""
Accounts — the host/account owner.

v1 is single-owner: one user owns a page and its funds (a couple nominates one
person). Guests do NOT need an account to gift, wish or RSVP, so there is no
guest model here — guest identity on a gift is just an optional display name.
"""
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

from config.constants import KYC_CAP_KOBO, KYC_NUDGE_KOBO


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    """Host / account owner. Login is by email; phone is captured for OTP."""

    # we use email as the identifier, drop the username field
    username = None
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    avatar = models.CharField(max_length=40, blank=True)  # avatar preset id, e.g. "female-2"

    # --- verification state (Epic A) ---
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    nin_verified = models.BooleanField(default=False)
    nin_verified_at = models.DateTimeField(null=True, blank=True)

    # Hashed 4-digit withdrawal PIN (set the first time the host withdraws).
    withdrawal_pin = models.CharField(max_length=128, blank=True)

    # running total of everything this host has ever RECEIVED, in kobo.
    # drives the receiving cap. Incremented when a gift is confirmed paid.
    cumulative_received_kobo = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    # --- KYC helpers -------------------------------------------------------
    @property
    def kyc_tier(self) -> int:
        """0 = phone+email only (capped). 1 = NIN-verified (uncapped)."""
        return 1 if self.nin_verified else 0

    @property
    def should_nudge_verification(self) -> bool:
        """True once the host is close to the cap but not yet verified."""
        return not self.nin_verified and self.cumulative_received_kobo >= KYC_NUDGE_KOBO

    def can_receive(self, amount_kobo: int) -> tuple[bool, bool]:
        """
        Gate an *incoming* gift of `amount_kobo`.

        Returns (allowed, is_crossing):
          - allowed:     may this gift be accepted at all?
          - is_crossing: does this gift take the host from under the cap to over it?
                         (the crossing gift is allowed; everything after it is not)

        Verified hosts are always allowed. Unverified hosts are allowed while
        they are still under the cap — including the single gift that crosses it.
        """
        if self.nin_verified:
            return True, False
        already = self.cumulative_received_kobo
        if already >= KYC_CAP_KOBO:
            return False, False          # already over and unverified -> blocked
        crossing = (already + amount_kobo) > KYC_CAP_KOBO
        return True, crossing

    def mark_nin_verified(self):
        self.nin_verified = True
        self.nin_verified_at = timezone.now()
        self.save(update_fields=["nin_verified", "nin_verified_at"])

    # --- withdrawal PIN ----------------------------------------------------
    @property
    def has_withdrawal_pin(self) -> bool:
        return bool(self.withdrawal_pin)

    def set_withdrawal_pin(self, pin: str):
        self.withdrawal_pin = make_password(str(pin))
        self.save(update_fields=["withdrawal_pin"])

    def check_withdrawal_pin(self, pin: str) -> bool:
        return bool(self.withdrawal_pin) and check_password(str(pin), self.withdrawal_pin)


class BankAccount(models.Model):
    """The host's payout bank account (one per user)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="bank_account")
    bank_name = models.CharField(max_length=80)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=140, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EmailOTP(models.Model):
    """A one-time 6-digit code emailed to verify a user's address.

    `issue()` invalidates any outstanding codes and mints a fresh one;
    `verify()` checks a submitted code and consumes it on success.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="email_otps")
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self) -> bool:
        return self.consumed_at is None and timezone.now() < self.expires_at

    @classmethod
    def issue(cls, user, ttl_minutes: int = 10) -> "EmailOTP":
        # Supersede any outstanding codes so only the newest is accepted.
        cls.objects.filter(user=user, consumed_at__isnull=True).update(
            consumed_at=timezone.now())
        code = f"{secrets.randbelow(1_000_000):06d}"
        return cls.objects.create(
            user=user, code=code,
            expires_at=timezone.now() + timedelta(minutes=ttl_minutes),
        )

    @classmethod
    def verify(cls, user, code: str) -> bool:
        otp = (cls.objects.filter(user=user, code=str(code),
                                  consumed_at__isnull=True)
               .order_by("-created_at").first())
        if not otp or not otp.is_valid():
            return False
        otp.consumed_at = timezone.now()
        otp.save(update_fields=["consumed_at"])
        return True


class Notification(models.Model):
    """An in-app notification for a user (gift received, withdrawal, etc.)."""
    class Kind(models.TextChoices):
        CONTRIBUTION = "contribution", "Contribution"
        WISH = "wish", "Wish"
        WALLET = "wallet", "Wallet"
        INVITE = "invite", "Invite"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="notifications")
    kind = models.CharField(max_length=16, choices=Kind.choices,
                            default=Kind.SYSTEM)
    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
