# Generated manually for contract date/card update

from django.db import migrations, models


def copy_validity_date_to_contract_end(apps, schema_editor):
    ContractEntry = apps.get_model('chamados', 'ContractEntry')
    for item in ContractEntry.objects.all():
        if item.validity_date and not item.contract_end:
            item.contract_end = item.validity_date
            item.save(update_fields=['contract_end'])


def reverse_copy_contract_end_to_validity_date(apps, schema_editor):
    ContractEntry = apps.get_model('chamados', 'ContractEntry')
    for item in ContractEntry.objects.all():
        if item.contract_end and not item.validity_date:
            item.validity_date = item.contract_end
            item.save(update_fields=['validity_date'])


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0015_documententry_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='contractentry',
            name='card_final',
            field=models.CharField(blank=True, default='', max_length=4),
        ),
        migrations.AddField(
            model_name='contractentry',
            name='contract_end',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='contractentry',
            name='contract_start',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(
            copy_validity_date_to_contract_end,
            reverse_copy_contract_end_to_validity_date,
        ),
        migrations.RemoveField(
            model_name='contractentry',
            name='duration_unit',
        ),
        migrations.RemoveField(
            model_name='contractentry',
            name='duration_value',
        ),
        migrations.RemoveField(
            model_name='contractentry',
            name='validity_date',
        ),
    ]
