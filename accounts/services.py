"""Account-side services — email OTP + notification creation."""
from django.conf import settings
from django.core.mail import send_mail

from .models import EmailOTP, Notification


def notify(user, kind: str, title: str, body: str = "") -> Notification:
    """Create an in-app notification for `user`."""
    return Notification.objects.create(user=user, kind=kind, title=title, body=body)


def send_password_reset_otp(user) -> EmailOTP:
    """Issue + email a one-time code for resetting a forgotten password."""
    ttl = settings.EMAIL_OTP_TTL_MINUTES
    otp = EmailOTP.issue(user, ttl_minutes=ttl)
    send_mail(
        subject="Your Avela password reset code",
        message=(
            f"Your Avela password reset code is {otp.code}.\n"
            f"It expires in {ttl} minutes.\n\n"
            "If you didn't request this, you can safely ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    return otp


def send_email_otp(user) -> EmailOTP:
    """Issue a fresh OTP for `user` and email it.

    Uses `fail_silently=True` so a mail backend hiccup never breaks signup; in
    dev the console email backend prints the code to the server log.
    """
    ttl = settings.EMAIL_OTP_TTL_MINUTES
    otp = EmailOTP.issue(user, ttl_minutes=ttl)
    send_mail(
        subject="Your Avela verification code",
        message=(
            f"Your Avela verification code is {otp.code}.\n"
            f"It expires in {ttl} minutes.\n\n"
            "If you didn't request this, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
    return otp
