from django.db import migrations


REPLACEMENTS = {
    'servi?o': 'servi\u00e7o',
    'Gon?alves': 'Gon\u00e7alves',
    'Expedi??o': 'Expedi\u00e7\u00e3o',
    'J?nior': 'J\u00fanior',
    'Manuten??o': 'Manuten\u00e7\u00e3o',
    'Mat?ria': 'Mat\u00e9ria',
    'Or?amentos': 'Or\u00e7amentos',
    'Jo?o': 'Jo\u00e3o',
    'Produ??o': 'Produ\u00e7\u00e3o',
    'Rog?rio': 'Rog\u00e9rio',
    'Inspe??o': 'Inspe\u00e7\u00e3o',
    'Reuni?o': 'Reuni\u00e3o',
    'Seguran?a': 'Seguran\u00e7a',
    'Fabrica??o': 'Fabrica\u00e7\u00e3o',
}


def fix_phone_extension_accents(apps, schema_editor):
    phone_extension = apps.get_model('chamados', 'PhoneExtension')
    fields = ('department', 'name', 'phone', 'extension', 'email')
    for extension in phone_extension.objects.all():
        changed_fields = []
        for field in fields:
            value = getattr(extension, field, '')
            if not isinstance(value, str):
                continue

            cleaned = value
            for old, new in REPLACEMENTS.items():
                cleaned = cleaned.replace(old, new)

            if cleaned != value:
                setattr(extension, field, cleaned)
                changed_fields.append(field)

        if changed_fields:
            extension.save(update_fields=[*changed_fields, 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0045_alter_phoneextension_email'),
    ]

    operations = [
        migrations.RunPython(fix_phone_extension_accents, migrations.RunPython.noop),
    ]
