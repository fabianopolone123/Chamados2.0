from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0017_tipentry'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticketattendance',
            name='exported_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticketattendance',
            name='exported_path',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
