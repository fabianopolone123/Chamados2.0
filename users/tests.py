from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .auth_backend import ActiveDirectoryBackend


class LoginFlowTests(TestCase):
    def test_login_page_loads(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sidertec')

    def test_success_requires_authentication(self):
        response = self.client.get(reverse('login_success'))
        self.assertEqual(response.status_code, 302)

    @override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'])
    def test_local_login_redirects_to_success_page(self):
        User = get_user_model()
        User.objects.create_user(username='usuario.teste', password='senha@123')

        response = self.client.post(
            reverse('login'),
            data={'username': 'usuario.teste', 'password': 'senha@123'},
        )
        self.assertRedirects(response, reverse('chamados_list'))

    @override_settings(
        AD_LDAP_SERVER_URI='ldaps://srv-ad.sidertec.intra.net:636',
        AD_LDAP_BASE_DN='dc=sidertec,dc=intra,dc=net',
        AD_LDAP_BIND_DN='glpi_ldap@sidertec.intra.net',
        AD_LDAP_BIND_PASSWORD='segredo',
        AD_LDAP_USER_FILTER='(&(objectCategory=person)(objectclass=user)(sAMAccountName=%(user)s))',
    )
    def test_ad_backend_returns_none_when_local_sync_fails(self):
        User = get_user_model()
        User.objects.create_user(username='usuario.ad', password='senha@123')

        class FakeAttribute:
            def __init__(self, value):
                self.value = value

        class FakeEntry:
            entry_dn = 'CN=Usuario AD,DC=sidertec,DC=intra,DC=net'

            def __contains__(self, key):
                return key in {'givenName', 'sn', 'mail'}

            def __getitem__(self, key):
                values = {
                    'givenName': FakeAttribute('Usuario'),
                    'sn': FakeAttribute('Comum'),
                    'mail': FakeAttribute('usuario.ad@sidertec.intra.net'),
                }
                return values[key]

        bind_conn = MagicMock()
        bind_conn.entries = [FakeEntry()]
        user_conn = MagicMock()

        with (
            patch('users.auth_backend.Server', return_value=object()),
            patch('users.auth_backend.Connection', side_effect=[bind_conn, user_conn]),
            patch.object(User, 'save', side_effect=RuntimeError('falha ao salvar usuario')),
        ):
            backend = ActiveDirectoryBackend()
            user = backend.authenticate(None, username='usuario.ad', password='senha@123')

        self.assertIsNone(user)

    @override_settings(
        AD_LDAP_SERVER_URI='ldaps://srv-ad.sidertec.intra.net:636',
        AD_LDAP_BASE_DN='dc=sidertec,dc=intra,dc=net',
        AD_LDAP_BIND_DN='glpi_ldap@sidertec.intra.net',
        AD_LDAP_BIND_PASSWORD='segredo',
        AD_LDAP_USER_FILTER='(&(objectCategory=person)(objectclass=user)(sAMAccountName=%(user)s))',
    )
    def test_ad_backend_updates_existing_user_without_rewriting_username(self):
        User = get_user_model()
        existing = User.objects.create_user(username='luana.keren', password='senha@123')

        class FakeAttribute:
            def __init__(self, value):
                self.value = value

        class FakeEntry:
            entry_dn = 'CN=Luana Keren,DC=sidertec,DC=intra,DC=net'

            def __contains__(self, key):
                return key in {'sAMAccountName', 'givenName', 'sn', 'mail'}

            def __getitem__(self, key):
                values = {
                    'sAMAccountName': FakeAttribute('luana.keren'),
                    'givenName': FakeAttribute('Luana'),
                    'sn': FakeAttribute('Keren'),
                    'mail': FakeAttribute('luana.keren@sidertec.intra.net'),
                }
                return values[key]

        bind_conn = MagicMock()
        bind_conn.entries = [FakeEntry()]
        user_conn = MagicMock()

        with (
            patch('users.auth_backend.Server', return_value=object()),
            patch('users.auth_backend.Connection', side_effect=[bind_conn, user_conn]),
        ):
            backend = ActiveDirectoryBackend()
            user = backend.authenticate(None, username='luana.keren', password='senha@123')

        self.assertIsNotNone(user)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, 'Luana')
        self.assertEqual(existing.last_name, 'Keren')
        self.assertEqual(existing.email, 'luana.keren@sidertec.intra.net')
