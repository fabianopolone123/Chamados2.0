from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Ticket, TicketAttendance


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
        self.other_ti_user = user_model.objects.create_user(
            username='outro.ti',
            password='senha@123',
        )
        ti_group, _ = Group.objects.get_or_create(name='TI')
        self.ti_user.groups.add(ti_group)
        self.other_ti_user.groups.add(ti_group)

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

    def test_ti_user_can_play_and_pause_ticket(self):
        ticket = Ticket.objects.create(
            title='VPN caiu',
            description='Sem acesso remoto.',
            priority=Ticket.Priority.CRITICA,
            created_by=self.normal_user,
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_action', args=[ticket.id]), data={'action': 'play', 'next': reverse('chamados_list')})
        self.assertRedirects(response, reverse('chamados_list'))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.EM_ATENDIMENTO)
        running = TicketAttendance.objects.get(ticket=ticket, attendant=self.ti_user)
        self.assertIsNone(running.ended_at)

        response = self.client.post(
            reverse('chamados_action', args=[ticket.id]),
            data={'action': 'pause', 'next': reverse('chamados_list')},
            follow=True,
        )
        self.assertContains(response, 'Informe o que foi feito antes de pausar/parar.')
        running.refresh_from_db()
        self.assertIsNone(running.ended_at)

        response = self.client.post(
            reverse('chamados_action', args=[ticket.id]),
            data={
                'action': 'pause',
                'note': 'Rede estabilizada e usuario orientado.',
                'next': reverse('chamados_list'),
            },
        )
        self.assertRedirects(response, reverse('chamados_list'))
        ticket.refresh_from_db()
        running.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.AGUARDANDO_USUARIO)
        self.assertIsNotNone(running.ended_at)
        self.assertEqual(running.end_action, TicketAttendance.EndAction.PAUSE)
        self.assertEqual(running.note, 'Rede estabilizada e usuario orientado.')

    def test_ti_user_cannot_view_ticket_of_other_attendant(self):
        free_ticket = Ticket.objects.create(
            title='Chamado livre',
            description='Aguardando primeiro atendimento.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
        )
        own_ticket = Ticket.objects.create(
            title='Meu chamado TI',
            description='Atendimento do usuario.ti.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
        )
        locked_ticket = Ticket.objects.create(
            title='Chamado de outro TI',
            description='Este chamado ja foi iniciado por outro atendente.',
            priority=Ticket.Priority.BAIXA,
            created_by=self.normal_user,
        )
        TicketAttendance.objects.create(
            ticket=own_ticket,
            attendant=self.ti_user,
            started_at=own_ticket.created_at,
        )
        TicketAttendance.objects.create(
            ticket=locked_ticket,
            attendant=self.other_ti_user,
            started_at=locked_ticket.created_at,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_list'))
        self.assertContains(response, free_ticket.title)
        self.assertContains(response, own_ticket.title)
        self.assertNotContains(response, locked_ticket.title)

        response = self.client.get(reverse('chamados_detail', args=[locked_ticket.id]))
        self.assertRedirects(response, reverse('chamados_list'))
