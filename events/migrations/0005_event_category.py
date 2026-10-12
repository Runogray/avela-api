from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0004_eventphoto"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="category",
            field=models.CharField(blank=True, max_length=60),
        ),
    ]
