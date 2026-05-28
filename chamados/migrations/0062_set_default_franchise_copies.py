from django.db import migrations


def set_default_franchise_copies(apps, schema_editor):
    FuturaDigitalEntry = apps.get_model('chamados', 'FuturaDigitalEntry')
    FuturaDigitalEntry.objects.filter(franchise_copies=0).update(franchise_copies=23000)


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0061_futuradigitalentry_franchise_amount'),
    ]

    operations = [
        migrations.RunPython(
            set_default_franchise_copies,
            migrations.RunPython.noop,
        ),
    ]
