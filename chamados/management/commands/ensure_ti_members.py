from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Garante que os usuarios informados facam parte do grupo TI.'

    def add_arguments(self, parser):
        parser.add_argument('usernames', nargs='+', help='Usernames que devem pertencer ao grupo TI.')
        parser.add_argument('--group', default='TI', help='Nome do grupo TI. Padrao: TI.')

    def handle(self, *args, **options):
        usernames = [item.strip() for item in options['usernames'] if item.strip()]
        group_name = (options.get('group') or 'TI').strip() or 'TI'
        user_model = get_user_model()
        ti_group, _ = Group.objects.get_or_create(name=group_name)

        added = []
        missing = []
        already = []

        for username in usernames:
            user = user_model.objects.filter(username=username).first()
            if user is None:
                missing.append(username)
                continue
            if user.groups.filter(id=ti_group.id).exists():
                already.append(username)
                continue
            user.groups.add(ti_group)
            added.append(username)

        if added:
            self.stdout.write(self.style.SUCCESS(f'Usuarios adicionados ao grupo {group_name}: {", ".join(added)}'))
        if already:
            self.stdout.write(f'Usuarios ja pertenciam ao grupo {group_name}: {", ".join(already)}')
        if missing:
            self.stdout.write(self.style.WARNING(f'Usuarios nao encontrados: {", ".join(missing)}'))
