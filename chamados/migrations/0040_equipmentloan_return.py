from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0039_ticketfailuretype_remove_ticket_category_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='equipmentloan',
            name='returned',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='equipmentloan',
            name='returned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='equipmentloan',
            name='returned_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='returned_equipment_loans',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
