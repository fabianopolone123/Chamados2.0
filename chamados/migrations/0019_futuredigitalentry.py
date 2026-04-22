from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0018_ticketattendance_export_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FuturaDigitalEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=180)),
                ('invoice', models.CharField(max_length=80)),
                ('reference_month', models.DateField()),
                ('copies_count', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_futura_digital_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Futura Digital',
                'verbose_name_plural': 'Futura Digital',
                'ordering': ['-reference_month', 'name', '-id'],
            },
        ),
    ]
