import secrets

from django.db import migrations, models


def give_existing_buyers_a_link(apps, schema_editor):
    # Items a guest is holding or has bought get a gift page link too.
    WishlistItem = apps.get_model("events", "WishlistItem")
    for item in WishlistItem.objects.filter(status="reserved", gift_token=""):
        item.gift_token = secrets.token_urlsafe(18)
        item.save(update_fields=["gift_token"])


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0010_guest_email_thanks'),
    ]

    operations = [
        migrations.AddField(
            model_name='wishlistitem',
            name='gift_token',
            field=models.CharField(blank=True, db_index=True, max_length=40),
        ),
        migrations.RunPython(give_existing_buyers_a_link, migrations.RunPython.noop),
    ]
