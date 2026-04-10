from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Ticket


@override_settings(AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'])
class TicketAccessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.normal_user = user_model.objects.create_user(
            username='usuario.comum',
            password='senha@123',
        )
        self.other_user = user_model.objects.create_user(
            username='outro.usuario',
            password='senha@123',
        )
        self.ti_user = user_model.objects.create_user(
            username='usuario.ti',
            password='senha@123',
        )
        ti_group, _ = Group.objects.get_or_create(name='TI')
        self.ti_user.groups.add(ti_group)

    def test_normal_user_creates_ticket_and_sees_own_only(self):
        self.client.login(username='usuario.comum', password='senha@123')
        self.client.post(
            reverse('chamados_new'),
            data={
                'title': 'Notebook sem rede',
                'description': 'Nao conecta na rede corporativa.',
                'priority': Ticket.Priority.ALTA,
            },
        )
        self.assertEqual(Ticket.objects.count(), 1)
        ticket = Ticket.objects.first()
        self.assertEqual(ticket.created_by, self.normal_user)

        Ticket.objects.create(
            title='Teste externo',
            description='Outro chamado',
            priority=Ticket.Priority.BAIXA,
            created_by=self.other_user,
        )

        response = self.client.get(reverse('chamados_list'))
        self.assertContains(response, 'Notebook sem rede')
        self.assertNotContains(response, 'Teste externo')

    def test_normal_user_cannot_access_other_ticket(self):
        ticket = Ticket.objects.create(
            title='Problema de impressora',
            description='Falha ao imprimir.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.other_user,
        )
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_detail', args=[ticket.id]))
        self.assertRedirects(response, reverse('chamados_list'))

    def test_ti_user_can_update_status(self):
        ticket = Ticket.objects.create(
            title='VPN caiu',
            description='Sem acesso remoto.',
            priority=Ticket.Priority.CRITICA,
            created_by=self.normal_user,
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_update', args=[ticket.id]),
            data={
                'status': Ticket.Status.EM_ATENDIMENTO,
                'assigned_to': self.ti_user.id,
                'response_message': 'Atendimento iniciado.',
            },
        )
        self.assertRedirects(response, reverse('chamados_detail', args=[ticket.id]))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.EM_ATENDIMENTO)
        self.assertEqual(ticket.assigned_to, self.ti_user)
