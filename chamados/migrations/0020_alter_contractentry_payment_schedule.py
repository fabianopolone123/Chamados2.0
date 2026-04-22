from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0019_futuredigitalentry'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contractentry',
            name='payment_schedule',
            field=models.CharField(
                choices=[
                    ('mensal', 'Mensal'),
                    ('anual', 'Anual'),
                    ('pagamento_unico', 'Pagamento unico'),
                ],
                default='mensal',
                max_length=20,
            ),
        ),
    ]
