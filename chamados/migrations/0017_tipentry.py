# Generated manually from legacy ERP-TI tips

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


LEGACY_TIPS = [
    {
        'legacy_id': 1,
        'category': 'configuracao',
        'title': 'M-Files - Valmet',
        'content': 'Segue as configurações na foto',
        'legacy_attachment_path': 'dicas/image007.png',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 2,
        'category': 'resolucao',
        'title': 'Power Fab nao conecta',
        'content': 'Verificar serviço ativo no SRV-ERP Teckla Power Fab Remote Server',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 3,
        'category': 'geral',
        'title': 'Copiar acessos de um usuário no AD para outro',
        'content': 'Import-Module ActiveDirectory\r\n\r\n$usuarioOrigem = "dayara.toledo"\r\n$usuarioDestino = "joana.pessoa"\r\n\r\n$grupos = Get-ADUser $usuarioOrigem -Properties MemberOf | \r\n          Select-Object -ExpandProperty MemberOf\r\n\r\nforeach ($grupo in $grupos) {\r\n    Add-ADGroupMember -Identity $grupo -Members $usuarioDestino\r\n}\r\n\r\nWrite-Host "Grupos copiados com sucesso!"',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 4,
        'category': 'geral',
        'title': 'Distribuidores autorizados microsoft',
        'content': 'https://partner.microsoft.com/pt-br/Licensing/distribuidores-Autorizados',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 5,
        'category': 'configuracao',
        'title': 'Cadastro de Biometria para Catraca do Restaurante',
        'content': 'Configuração de cadastro em anexo.',
        'legacy_attachment_path': 'dicas/Cadastro_de_biometria.pdf',
        'created_by_username': 'fabio.generoso',
    },
    {
        'legacy_id': 6,
        'category': 'resolucao',
        'title': 'Captura de tela das requisições não funciona',
        'content': 'chrome://flags/#unsafely-treat-insecure-origin-as-secure\r\n\r\nDepois:\r\n\r\nEm Insecure origins treated as secure, coloque:\r\nhttp://192.168.22.17:8000\r\nClique em Relaunch para reiniciar o Chrome.',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 7,
        'category': 'geral',
        'title': 'Migrar emails de uma conta para outra',
        'content': 'https://docs.google.com/document/u/0/d/e/2PACX-1vSwNK108zFFae0TKbwA2PLeoFHgWxewNxzyknoWZ8or4RhuvhAoNqHsIuhksHR0r5ffoDGdIdReWMhh/pub?pli=1',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 8,
        'category': 'geral',
        'title': 'Mover todos emails para um marcador',
        'content': '>> Criar um marcador\r\n>> Ir em todos e-mails\r\n>> Selecionar todos\r\n>> clicar em selecionar todos na opção que aparecer\r\n>> Clicar em em marcadores e selecionar um marcador e aplicar\r\n>> Ir em todos emails e selecionar todos\r\n>> clicar em arquivar',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 9,
        'category': 'geral',
        'title': 'Problema multi usuario Tekla',
        'content': 'Verificar serviço no servidor Tekla Structures Multiuser Server tem que estar em execução',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 10,
        'category': 'resolucao',
        'title': 'Lantek não conecta no banco',
        'content': 'Inicar serviço Lantek SQL no servidor PJ 192.168.22.6',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
    {
        'legacy_id': 11,
        'category': 'geral',
        'title': 'Suporte Free Trimble Connect',
        'content': 'Enviar e-mail para\r\nconnect-support@trimble.com',
        'legacy_attachment_path': '',
        'created_by_username': 'fabiano.polone',
    },
]


def seed_legacy_tips(apps, schema_editor):
    TipEntry = apps.get_model('chamados', 'TipEntry')
    User = apps.get_model('auth', 'User')

    for item in LEGACY_TIPS:
        if TipEntry.objects.filter(legacy_id=item['legacy_id']).exists():
            continue
        created_by = User.objects.filter(username=item['created_by_username']).first()
        TipEntry.objects.create(
            legacy_id=item['legacy_id'],
            category=item['category'],
            title=item['title'],
            content=item['content'],
            legacy_attachment_path=item['legacy_attachment_path'],
            created_by=created_by,
        )


def unseed_legacy_tips(apps, schema_editor):
    TipEntry = apps.get_model('chamados', 'TipEntry')
    TipEntry.objects.filter(legacy_id__in=[item['legacy_id'] for item in LEGACY_TIPS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chamados', '0016_contract_dates_and_card_final'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TipEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[('geral', 'Geral'), ('configuracao', 'Configuracao'), ('resolucao', 'Resolucao')], default='geral', max_length=20)),
                ('title', models.CharField(max_length=200)),
                ('content', models.TextField()),
                ('attachment', models.FileField(blank=True, null=True, upload_to='tips/')),
                ('legacy_attachment_path', models.CharField(blank=True, default='', max_length=255)),
                ('legacy_id', models.PositiveIntegerField(blank=True, db_index=True, null=True, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='created_tips', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Dica',
                'verbose_name_plural': 'Dicas',
                'ordering': ['category', 'title', 'id'],
            },
        ),
        migrations.RunPython(seed_legacy_tips, unseed_legacy_tips),
    ]
