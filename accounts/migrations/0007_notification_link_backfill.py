"""
Give notifications created before links existed somewhere to go.

Older notifications only have their words, so the link is worked out from
them: a transfer's reference (AV-XXXXXX) finds the exact contribution, a
bought gift's name and event title find the gift, an event title finds the
event. Anything that can't be matched is left without a link.
"""
import re

from django.db import migrations

TRANSFER_REF = re.compile(r"\(ref (AV-[A-Z0-9]+)\)")
BOUGHT = re.compile(r" says they bought (.+) for (.+?)\. Mark it as received")
SENT_ON = re.compile(r" sent .+ on (.+)\.$")
NOTE_ON = re.compile(r" left you a note on (.+)\.$")


def _gift_link(event_id, target_id=None, contribution_id=None):
    link = f"/events/{event_id}"
    if target_id:
        link += f"?gift={target_id}"
        if contribution_id:
            link += f"&contribution={contribution_id}"
    return link


def backfill(apps, schema_editor):
    Notification = apps.get_model("accounts", "Notification")
    MonetaryGift = apps.get_model("giving", "MonetaryGift")
    WishlistItem = apps.get_model("events", "WishlistItem")
    Event = apps.get_model("events", "Event")

    def event_titled(user_id, title):
        # Newest first, in case a host reused a title.
        return Event.objects.filter(host_id=user_id, title=title).order_by("-created_at").first()

    for n in Notification.objects.filter(link=""):
        link = ""
        if n.kind == "contribution":
            m = TRANSFER_REF.search(n.body)
            if m:
                gift = MonetaryGift.objects.filter(gateway_reference=m.group(1),
                                                   event__host_id=n.user_id).first()
                if gift:
                    link = _gift_link(gift.event_id, gift.wishlist_item_id or gift.goal_id, gift.id)
            elif (m := BOUGHT.search(n.body)):
                item = (WishlistItem.objects
                        .filter(name=m.group(1), event__title=m.group(2), event__host_id=n.user_id)
                        .order_by("-purchased_at").first())
                if item:
                    link = _gift_link(item.event_id, item.id)
            elif (m := SENT_ON.search(n.body)):
                ev = event_titled(n.user_id, m.group(1))
                if ev:
                    link = _gift_link(ev.id)
        elif n.kind == "wish":
            m = NOTE_ON.search(n.body)
            ev = event_titled(n.user_id, m.group(1)) if m else None
            if ev:
                link = f"/events/{ev.id}#well-wishes"
        elif n.kind == "invite":
            link = "/invites"
        elif n.kind == "wallet":
            link = "/wallet"
        elif n.kind == "system" and n.title.startswith("Welcome"):
            link = "/create"
        if link:
            n.link = link
            n.save(update_fields=["link"])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_notification_link'),
        ('events', '0011_gift_tokens'),
        ('giving', '0005_wish_guest_email'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
