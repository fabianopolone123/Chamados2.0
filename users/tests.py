from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse


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
        self.assertRedirects(response, reverse('login_success'))
