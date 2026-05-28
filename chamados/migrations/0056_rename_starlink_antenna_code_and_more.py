from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0055_starlink_antenna_code'),
    ]

    operations = [
        migrations.RenameField(
            model_name='starlink',
            old_name='antenna_code',
            new_name='starlink_identifier',
        ),
        migrations.AddField(
            model_name='starlink',
            name='software_version',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='starlink',
            name='serial_number',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='starlink',
            name='kit_number',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]
