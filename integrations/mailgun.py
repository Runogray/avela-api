"""
Django email backend that sends through Mailgun's HTTP API.

Enable with DJANGO_EMAIL_BACKEND=integrations.mailgun.MailgunEmailBackend and
set MAILGUN_API_KEY + MAILGUN_DOMAIN. Uses only the standard library so the
image needs no extra dependency.
"""
import base64
import logging
from urllib import error, parse, request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class MailgunEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = settings.MAILGUN_API_KEY
        self.domain = settings.MAILGUN_DOMAIN
        self.base_url = settings.MAILGUN_API_BASE.rstrip("/")

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
                sent += 1
            except Exception:
                logger.exception("Mailgun send failed")
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        if not (self.api_key and self.domain):
            raise RuntimeError("MAILGUN_API_KEY and MAILGUN_DOMAIN must be set")
        fields = [
            ("from", message.from_email or settings.DEFAULT_FROM_EMAIL),
            ("subject", message.subject),
            ("text", message.body),
        ]
        fields += [("to", addr) for addr in message.to]
        fields += [("cc", addr) for addr in message.cc]
        fields += [("bcc", addr) for addr in message.bcc]
        if message.reply_to:
            fields.append(("h:Reply-To", ", ".join(message.reply_to)))
        for content, mimetype in getattr(message, "alternatives", []):
            if mimetype == "text/html":
                fields.append(("html", content))

        auth = base64.b64encode(f"api:{self.api_key}".encode()).decode()
        req = request.Request(
            f"{self.base_url}/v3/{self.domain}/messages",
            data=parse.urlencode(fields).encode(),
            headers={"Authorization": f"Basic {auth}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"Mailgun HTTP {exc.code}: {detail}") from exc
