from __future__ import annotations

import socket
import ssl
from getpass import getpass
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ldap3 import Connection, Server, SUBTREE, Tls
from ldap3.utils.conv import escape_filter_chars


class Command(BaseCommand):
    help = 'Verifica a configuracao de autenticacao no Active Directory sem expor senhas.'

    def add_arguments(self, parser):
        parser.add_argument('--username', help='Usuario AD para testar busca/autenticacao.')
        parser.add_argument('--bind-dn', help='Sobrescreve o usuario/conta de bind apenas neste teste.')
        parser.add_argument(
            '--prompt-bind-password',
            action='store_true',
            help='Pergunta a senha da conta de bind para testar sem editar o .env.',
        )
        parser.add_argument(
            '--prompt-password',
            action='store_true',
            help='Pergunta a senha do usuario informado para testar o login completo.',
        )

    def handle(self, *args, **options):
        username = (options.get('username') or '').strip()
        bind_dn = (options.get('bind_dn') or '').strip()
        prompt_bind_password = bool(options.get('prompt_bind_password'))
        prompt_password = bool(options.get('prompt_password'))

        config = self._get_config()
        if bind_dn:
            config['bind_dn'] = bind_dn
        if prompt_bind_password:
            config['bind_password'] = getpass(f"Senha da conta de bind {config['bind_dn']}: ")

        self._print_config(config)
        self._test_tcp(config)
        server = self._build_server(config)
        bind_conn = self._test_service_bind(server, config)

        if username:
            user_dn = self._test_user_search(bind_conn, config, username)
            if prompt_password:
                self._test_user_bind(server, user_dn, username)

        bind_conn.unbind()
        self.stdout.write(self.style.SUCCESS('Diagnostico AD finalizado com sucesso.'))

    def _get_config(self):
        server_uri = getattr(settings, 'AD_LDAP_SERVER_URI', '')
        parsed = urlparse(server_uri)
        if parsed.scheme in {'ldap', 'ldaps'}:
            host = parsed.hostname or server_uri
            port = parsed.port or (636 if parsed.scheme == 'ldaps' else 389)
            use_ssl = parsed.scheme == 'ldaps'
        else:
            host = server_uri
            port = 636
            use_ssl = True

        return {
            'server_uri': server_uri,
            'host': host,
            'port': port,
            'use_ssl': use_ssl,
            'base_dn': getattr(settings, 'AD_LDAP_BASE_DN', ''),
            'bind_dn': getattr(settings, 'AD_LDAP_BIND_DN', ''),
            'bind_password': getattr(settings, 'AD_LDAP_BIND_PASSWORD', ''),
            'user_filter': getattr(settings, 'AD_LDAP_USER_FILTER', ''),
            'user_attr_map': getattr(settings, 'AD_LDAP_USER_ATTR_MAP', {}),
            'validate_cert': bool(getattr(settings, 'AD_LDAP_VALIDATE_CERT', True)),
            'ca_cert_file': (getattr(settings, 'AD_LDAP_CA_CERT_FILE', '') or '').strip(),
            'connect_timeout': int(getattr(settings, 'AD_LDAP_CONNECT_TIMEOUT', 5) or 5),
        }

    def _print_config(self, config):
        self.stdout.write('Configuracao carregada:')
        self.stdout.write(f"  AD_LDAP_SERVER_URI: {config['server_uri']}")
        self.stdout.write(f"  Host/porta: {config['host']}:{config['port']} ssl={config['use_ssl']}")
        self.stdout.write(f"  AD_LDAP_BASE_DN: {config['base_dn']}")
        self.stdout.write(f"  AD_LDAP_BIND_DN: {config['bind_dn']}")
        self.stdout.write(f"  Senha bind configurada: {'sim' if config['bind_password'] else 'nao'}")
        self.stdout.write(f"  Validar certificado: {'sim' if config['validate_cert'] else 'nao'}")
        ca_cert_file = config['ca_cert_file'] or '(nao informado)'
        self.stdout.write(f"  CA/certificado: {ca_cert_file}")

    def _test_tcp(self, config):
        self.stdout.write('Testando conexao TCP...')
        try:
            with socket.create_connection(
                (config['host'], config['port']),
                timeout=config['connect_timeout'],
            ):
                pass
        except OSError as exc:
            raise CommandError(f"Falha TCP ate o AD: {exc}") from exc
        self.stdout.write(self.style.SUCCESS('  TCP OK'))

    def _build_server(self, config):
        tls = Tls(
            validate=ssl.CERT_REQUIRED if config['validate_cert'] else ssl.CERT_NONE,
            ca_certs_file=config['ca_cert_file'] or None,
        )
        return Server(
            host=config['host'],
            port=config['port'],
            use_ssl=config['use_ssl'],
            tls=tls,
            get_info=None,
            connect_timeout=config['connect_timeout'],
        )

    def _test_service_bind(self, server, config):
        self.stdout.write('Testando bind da conta de servico...')
        if not config['bind_dn'] or not config['bind_password']:
            raise CommandError('AD_LDAP_BIND_DN ou AD_LDAP_BIND_PASSWORD nao configurado.')

        try:
            conn = Connection(
                server,
                user=config['bind_dn'],
                password=config['bind_password'],
                auto_bind=True,
                raise_exceptions=True,
                read_only=True,
            )
        except Exception as exc:
            hint = self._ldap_error_hint(exc)
            raise CommandError(f'Falha no bind da conta de servico: {exc}{hint}') from exc

        self.stdout.write(self.style.SUCCESS('  Bind da conta de servico OK'))
        return conn

    def _test_user_search(self, conn, config, username):
        self.stdout.write(f'Testando busca do usuario {username}...')
        escaped_username = escape_filter_chars(username)
        search_filter = config['user_filter'].replace('%(user)s', escaped_username)
        attributes = sorted(set(config['user_attr_map'].values())) or ['sAMAccountName']
        conn.search(
            search_base=config['base_dn'],
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=attributes,
        )
        if not conn.entries:
            raise CommandError('Usuario nao encontrado no AD com o filtro configurado.')

        entry = conn.entries[0]
        self.stdout.write(self.style.SUCCESS(f'  Usuario encontrado: {entry.entry_dn}'))
        return entry.entry_dn

    def _test_user_bind(self, server, user_dn, username):
        password = getpass(f'Senha AD de {username}: ')
        self.stdout.write('Testando bind do usuario...')
        try:
            conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
                raise_exceptions=True,
                read_only=True,
            )
            conn.unbind()
        except Exception as exc:
            hint = self._ldap_error_hint(exc)
            raise CommandError(f'Falha no login do usuario: {exc}{hint}') from exc
        self.stdout.write(self.style.SUCCESS('  Login do usuario OK'))

    def _ldap_error_hint(self, exc):
        message = str(exc).lower()
        if 'data 52e' in message or 'invalidcredentials' in message:
            return (
                '\nDica: o AD respondeu credencial invalida. Confira a senha da conta '
                'de bind em AD_LDAP_BIND_PASSWORD/ERP_LDAP_BIND_PASSWORD.'
            )
        if 'certificate' in message or 'certificate_verify_failed' in message:
            return (
                '\nDica: falha de certificado. No Linux, confira AD_LDAP_CA_CERT_FILE '
                'ou use AD_LDAP_VALIDATE_CERT=False apenas se aceitar esse risco na intranet.'
            )
        if 'invalid server address' in message or 'name or service not known' in message:
            return '\nDica: confira DNS/host em AD_LDAP_SERVER_URI.'
        return ''
