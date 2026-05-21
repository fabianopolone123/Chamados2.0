from django.db import migrations


def uppercase_equipment_identifiers(apps, schema_editor):
    equipment_loan = apps.get_model('chamados', 'EquipmentLoan')
    equipment_loan_item = apps.get_model('chamados', 'EquipmentLoanItem')

    for model in (equipment_loan, equipment_loan_item):
        for item in model.objects.all().only('id', 'equipment_model', 'equipment_serial'):
            model_value = (item.equipment_model or '').strip().upper()
            serial_value = (item.equipment_serial or '').strip().upper()
            if item.equipment_model != model_value or item.equipment_serial != serial_value:
                item.equipment_model = model_value
                item.equipment_serial = serial_value
                item.save(update_fields=['equipment_model', 'equipment_serial'])


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0052_equipmentloanphoto_item'),
    ]

    operations = [
        migrations.RunPython(uppercase_equipment_identifiers, migrations.RunPython.noop),
    ]
