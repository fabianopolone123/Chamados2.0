from django.db import migrations


def add_marcelo_to_ti(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    UserGroups = User.groups.through

    ti_group, _ = Group.objects.get_or_create(name='TI')
    marcelo = User.objects.filter(username='marcelo.sorigotti').first()
    if marcelo is not None:
        UserGroups.objects.get_or_create(user_id=marcelo.id, group_id=ti_group.id)


def remove_marcelo_from_ti(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('auth', 'User')
    UserGroups = User.groups.through

    ti_group = Group.objects.filter(name='TI').first()
    marcelo = User.objects.filter(username='marcelo.sorigotti').first()
    if ti_group is not None and marcelo is not None:
        UserGroups.objects.filter(user_id=marcelo.id, group_id=ti_group.id).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0006_insumo'),
    ]

    operations = [
        migrations.RunPython(add_marcelo_to_ti, remove_marcelo_from_ti),
    ]
