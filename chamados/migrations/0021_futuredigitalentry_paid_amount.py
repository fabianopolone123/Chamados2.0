from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0020_alter_contractentry_payment_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='futuradigitalentry',
            name='paid_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12),
        ),
    ]
