from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0021_futuredigitalentry_paid_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='requisitionbudget',
            name='quantity',
            field=models.PositiveIntegerField(default=1),
        ),
    ]
