"""Branded transactional emails (HTML + plain-text fallback)."""
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

SITE_URL = "https://avelaevent.com"
LOGO_URL = f"{SITE_URL}/apple-icon.png"
TEAL = "#00829f"
INK = "#1c1611"
MUTED = "#5c5c5c"
CREAM = "#FFFEF7"


def _layout(*, preheader: str, body_html: str) -> str:
    """Wrap `body_html` in the shared Avela card. Inline styles only — email
    clients strip <style> blocks and external CSS."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>Avela</title></head>
<body style="margin:0;padding:0;background:{CREAM};-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM};">
<tr><td align="center" style="padding:40px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">
  <tr><td style="padding:0 4px 20px;">
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      <td style="vertical-align:middle;"><img src="{LOGO_URL}" width="36" height="36" alt="" style="display:block;border:0;border-radius:10px;"></td>
      <td style="vertical-align:middle;padding-left:10px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:20px;font-weight:600;letter-spacing:-0.3px;color:{TEAL};">Avela</td>
    </tr></table>
  </td></tr>
  <tr><td style="background:#ffffff;border:1px solid #e8f1f3;border-radius:20px;padding:32px 24px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{INK};">
    {body_html}
  </td></tr>
  <tr><td style="padding:20px 4px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:12px;line-height:1.6;color:#9a9a9a;">
    Avela · Celebrate the people you love with gifts they actually want.<br>
    <a href="{SITE_URL}" style="color:#9a9a9a;text-decoration:underline;">avelaevent.com</a>
  </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _code_email(*, heading: str, greeting: str, intro: str, code: str, ttl: int, outro: str) -> str:
    return _layout(
        preheader=f"{code} is your code. It expires in {ttl} minutes.",
        body_html=f"""
    <h1 style="margin:0 0 10px;font-size:22px;line-height:1.3;font-weight:600;letter-spacing:-0.5px;color:{INK};">{escape(heading)}</h1>
    <p style="margin:0 0 6px;font-size:15px;line-height:1.6;color:{INK};">{escape(greeting)}</p>
    <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:{MUTED};">{escape(intro)}</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td align="center" style="background:#f0f8fa;border-radius:14px;padding:20px 12px;">
        <span style="font-family:'SF Mono',Menlo,Consolas,'Courier New',monospace;font-size:32px;font-weight:700;letter-spacing:10px;white-space:nowrap;color:{TEAL};">{escape(code)}</span>
      </td>
    </tr></table>
    <p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:{MUTED};text-align:center;">This code expires in <strong style="color:{INK};">{ttl} minutes</strong>.</p>
    <hr style="border:0;border-top:1px solid #eef3f4;margin:28px 0 20px;">
    <p style="margin:0;font-size:13px;line-height:1.6;color:#8a8a8a;">{escape(outro)} Avela will never ask you to share this code.</p>""",
    )


def _send(*, to: str, subject: str, text: str, html: str) -> None:
    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=True)


def _greeting(user) -> str:
    name = (getattr(user, "first_name", "") or "").strip()
    return f"Hi {name}," if name else "Hi there,"


def send_verification_code(user, code: str, ttl: int) -> None:
    intro = "Enter this code in Avela to confirm your email address and finish setting up your account."
    outro = "If you didn't create an Avela account, you can safely ignore this email."
    text = (
        f"{_greeting(user)}\n\n"
        f"Your Avela verification code is: {code}\n\n"
        f"{intro}\nIt expires in {ttl} minutes.\n\n"
        f"{outro} Avela will never ask you to share this code.\n\n"
        f"The Avela team\n{SITE_URL}\n"
    )
    html = _code_email(heading="Confirm your email", greeting=_greeting(user), intro=intro,
                       code=code, ttl=ttl, outro=outro)
    _send(to=user.email, subject=f"{code} is your Avela verification code", text=text, html=html)


def _naira(kobo: int) -> str:
    """₦25,000 for whole amounts, ₦25,000.50 otherwise."""
    value = kobo / 100
    return f"₦{value:,.0f}" if kobo % 100 == 0 else f"₦{value:,.2f}"


def send_contribution_to_confirm(gift) -> None:
    """Tell the host a guest says they've transferred a cash gift, so they
    check their bank and confirm (or decline) it in Avela."""
    event = gift.event
    host = event.host
    if not host.email:
        return
    who = (gift.giver_name or "A guest").strip()
    amount = _naira(gift.amount_kobo)
    toward = (gift.goal.name if gift.goal_id else
              gift.wishlist_item.name if gift.wishlist_item_id else "")
    ref = gift.gateway_reference
    bank = getattr(host, "bank_account", None)
    account = f"{bank.bank_name} ••{bank.account_number[-4:]}" if bank else ""
    url = f"{SITE_URL}/events/{event.id}"

    rows = [("Amount", amount), ("From", who)]
    if toward:
        rows.append(("Toward", toward))
    rows.append(("Reference", ref))
    if account:
        rows.append(("Paid into", account))
    rows_html = "".join(
        f'<tr><td style="padding:10px 0;border-bottom:1px solid #eef3f4;font-size:13px;color:{MUTED};">{escape(k)}</td>'
        f'<td align="right" style="padding:10px 0;border-bottom:1px solid #eef3f4;font-size:15px;font-weight:600;'
        f'color:{TEAL if k == "Reference" else INK};{"letter-spacing:1px;" if k == "Reference" else ""}">{escape(v)}</td></tr>'
        for k, v in rows
    )
    intro = f"{who} says they've sent you {amount} for {event.title}."
    check = (f"Check your bank account for a transfer with reference {ref}. "
             "Confirm it once it has arrived, so it counts toward your gift totals.")

    html = _layout(
        preheader=f"{who} sent {amount}. Confirm it once it's in your account.",
        body_html=f"""
    <h1 style="margin:0 0 10px;font-size:22px;line-height:1.3;font-weight:600;letter-spacing:-0.5px;color:{INK};">Confirm a gift</h1>
    <p style="margin:0 0 6px;font-size:15px;line-height:1.6;color:{INK};">{escape(_greeting(host))}</p>
    <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:{MUTED};">{escape(intro)}</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;">{rows_html}</table>
    <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:{MUTED};">{escape(check)}</p>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center">
      <a href="{url}" style="display:inline-block;background:{TEAL};color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:14px 28px;border-radius:50px;">Review and confirm</a>
    </td></tr></table>
    <hr style="border:0;border-top:1px solid #eef3f4;margin:28px 0 20px;">
    <p style="margin:0;font-size:13px;line-height:1.6;color:#8a8a8a;">Didn't receive it? Open the gift in Avela and choose “Not received”.</p>""",
    )
    text = (
        f"{_greeting(host)}\n\n{intro}\n\n"
        + "".join(f"{k}: {v}\n" for k, v in rows)
        + f"\n{check}\n\nReview and confirm: {url}\n\n"
        "Didn't receive it? Open the gift in Avela and choose \"Not received\".\n\n"
        f"The Avela team\n{SITE_URL}\n"
    )
    _send(to=host.email, subject=f"Confirm a {amount} gift from {who}", text=text, html=html)


def send_purchase_to_confirm(item) -> None:
    """Tell the host a guest says they bought a wishlist gift, so they can
    mark it as received once it arrives (or put it back on the list)."""
    event = item.event
    host = event.host
    if not host.email:
        return
    who = (item.reserved_by_name or "A guest").strip()
    url = f"{SITE_URL}/events/{event.id}"
    intro = f"{who} says they bought {item.name} for {event.title}."
    check = ("It's off your list for now so nobody buys it twice. Mark it as received once it "
             "arrives, or put it back on your list if it doesn't.")
    html = _layout(
        preheader=f"{who} bought {item.name}. Mark it as received once it arrives.",
        body_html=f"""
    <h1 style="margin:0 0 10px;font-size:22px;line-height:1.3;font-weight:600;letter-spacing:-0.5px;color:{INK};">Confirm a bought gift</h1>
    <p style="margin:0 0 6px;font-size:15px;line-height:1.6;color:{INK};">{escape(_greeting(host))}</p>
    <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:{MUTED};">{escape(intro)}</p>
    <p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:{MUTED};">{escape(check)}</p>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr><td align="center">
      <a href="{url}" style="display:inline-block;background:{TEAL};color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:14px 28px;border-radius:50px;">Review the gift</a>
    </td></tr></table>""",
    )
    text = (f"{_greeting(host)}\n\n{intro}\n\n{check}\n\nReview the gift: {url}\n\n"
            f"The Avela team\n{SITE_URL}\n")
    _send(to=host.email, subject=f"{who} bought {item.name}: confirm when it arrives", text=text, html=html)


def send_password_reset_code(user, code: str, ttl: int) -> None:
    intro = "We received a request to reset your Avela password. Enter this code to choose a new one."
    outro = "If you didn't ask to reset your password, you can ignore this email. Your password won't change."
    text = (
        f"{_greeting(user)}\n\n"
        f"Your Avela password reset code is: {code}\n\n"
        f"{intro}\nIt expires in {ttl} minutes.\n\n"
        f"{outro} Avela will never ask you to share this code.\n\n"
        f"The Avela team\n{SITE_URL}\n"
    )
    html = _code_email(heading="Reset your password", greeting=_greeting(user), intro=intro,
                       code=code, ttl=ttl, outro=outro)
    _send(to=user.email, subject=f"{code} is your Avela password reset code", text=text, html=html)
