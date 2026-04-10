from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import VaultAuditLog, VaultCredential, VaultSettings


@override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'])
class VaultFlowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.authorized_user = user_model.objects.create_user(
            username='usuario.autorizado',
            password='senha@123',
        )
        self.unauthorized_user = user_model.objects.create_user(
            username='usuario.sem.permissao',
            password='senha@123',
        )

        settings_obj = VaultSettings.load()
        settings_obj.set_master_password('senha-mestra')
        settings_obj.save(update_fields=['password_hash', 'updated_at'])
        settings_obj.authorized_users.add(self.authorized_user)

        self.credential = VaultCredential(
            label='Firewall',
            account_username='admin.firewall',
            notes='Acesso principal',
            created_by=self.authorized_user,
            password_encrypted='',
        )
        self.credential.set_secret_password('P@ssw0rd!Segura')
        self.credential.save()

    def test_menu_shows_vault_button_only_for_authorized_user(self):
        self.client.login(username='usuario.autorizado', password='senha@123')
        authorized_response = self.client.get(reverse('login_success'))
        self.assertContains(authorized_response, 'Abrir Cofre')

        self.client.logout()

        self.client.login(username='usuario.sem.permissao', password='senha@123')
        unauthorized_response = self.client.get(reverse('login_success'))
        self.assertContains(unauthorized_response, 'Cofre indisponivel')

    def test_vault_home_requires_unlock(self):
        self.client.login(username='usuario.autorizado', password='senha@123')
        response = self.client.get(reverse('cofre_home'))
        self.assertRedirects(response, reverse('cofre_unlock'))

    def test_unlock_and_copy_password_flow(self):
        self.client.login(username='usuario.autorizado', password='senha@123')

        unlock_response = self.client.post(
            reverse('cofre_unlock'),
            data={'password': 'senha-mestra'},
        )
        self.assertRedirects(unlock_response, reverse('cofre_home'))

        home_response = self.client.get(reverse('cofre_home'))
        self.assertEqual(home_response.status_code, 200)
        self.assertContains(home_response, 'Firewall')

        copy_response = self.client.post(reverse('cofre_copy_password', args=[self.credential.pk]))
        self.assertEqual(copy_response.status_code, 200)
        self.assertEqual(copy_response.json()['password'], 'P@ssw0rd!Segura')
        self.assertTrue(
            VaultAuditLog.objects.filter(action=VaultAuditLog.ACTION_CREDENTIAL_COPIED).exists()
        )

    def test_unauthorized_user_cannot_access_unlock(self):
        self.client.login(username='usuario.sem.permissao', password='senha@123')
        response = self.client.get(reverse('cofre_unlock'))
        self.assertRedirects(response, reverse('login_success'))

    @override_settings(VAULT_MAX_FAILED_ATTEMPTS=3, VAULT_LOCKOUT_SECONDS=120)
    def test_unlock_lockout_after_repeated_failures(self):
        self.client.login(username='usuario.autorizado', password='senha@123')

        for _ in range(3):
            self.client.post(
                reverse('cofre_unlock'),
                data={'password': 'senha-errada'},
            )

        response = self.client.post(
            reverse('cofre_unlock'),
            data={'password': 'senha-mestra'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Muitas tentativas invalidas')
        self.assertTrue(
            VaultAuditLog.objects.filter(action=VaultAuditLog.ACTION_UNLOCK_LOCKOUT).exists()
        )
