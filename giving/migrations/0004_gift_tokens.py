import secrets

from django.db import migrations, models


def give_existing_gifts_a_link(apps, schema_editor):
    MonetaryGift = apps.get_model("giving", "MonetaryGift")
    for gift in MonetaryGift.objects.filter(gift_token=""):
        gift.gift_token = secrets.token_urlsafe(18)
        gift.save(update_fields=["gift_token"])


class Migration(migrations.Migration):

    dependencies = [
        ('giving', '0003_guest_email_thanks'),
    ]

    operations = [
        migrations.AddField(
            model_name='monetarygift',
            name='gift_token',
            field=models.CharField(blank=True, db_index=True, max_length=40),
        ),
        migrations.RunPython(give_existing_gifts_a_link, migrations.RunPython.noop),
    ]
