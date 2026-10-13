from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0005_event_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="contributiongoal",
            name="hide_contributions",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="contributiongoal",
            name="hide_total",
            field=models.BooleanField(default=False),
        ),
    ]
