"""Account-side services — email OTP + notification creation."""
from django.conf import settings

from .emails import send_password_reset_code, send_verification_code
from .models import EmailOTP, Notification


def notify(user, kind: str, title: str, body: str = "", link: str = "") -> Notification:
    """Create an in-app notification for `user`. `link` is the app path it opens."""
    return Notification.objects.create(user=user, kind=kind, title=title, body=body, link=link)


def gift_link(event_id, target_id=None, contribution_id=None) -> str:
    """The host's event page, opened on one gift's contributions when given,
    with one contribution picked out (highlighted) when that's given too."""
    link = f"/events/{event_id}"
    if target_id:
        link += f"?gift={target_id}"
        if contribution_id:
            link += f"&contribution={contribution_id}"
    return link


def send_password_reset_otp(user) -> EmailOTP:
    """Issue + email a one-time code for resetting a forgotten password."""
    ttl = settings.EMAIL_OTP_TTL_MINUTES
    otp = EmailOTP.issue(user, ttl_minutes=ttl)
    send_password_reset_code(user, otp.code, ttl)
    return otp


def send_email_otp(user) -> EmailOTP:
    """Issue a fresh OTP for `user` and email it.

    Uses `fail_silently=True` so a mail backend hiccup never breaks signup; in
    dev the console email backend prints the code to the server log.
    """
    ttl = settings.EMAIL_OTP_TTL_MINUTES
    otp = EmailOTP.issue(user, ttl_minutes=ttl)
    send_verification_code(user, otp.code, ttl)
    return otp
