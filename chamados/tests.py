import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Requisition, RequisitionBudget, RequisitionUpdate, Ticket, TicketAttendance, TicketPending


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

    def test_only_ti_can_access_pending_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_pending_list'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_pending_list'))
        self.assertEqual(response.status_code, 200)

    def test_ti_pending_is_individual_and_can_be_deleted(self):
        own_pending = TicketPending.objects.create(
            attendant=self.ti_user,
            content='Revisar backup do servidor legado.',
        )
        TicketPending.objects.create(
            attendant=self.other_ti_user,
            content='Validar impressora do setor comercial.',
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_pending_list'))
        self.assertContains(response, own_pending.content)
        self.assertNotContains(response, 'Validar impressora do setor comercial.')

        delete_response = self.client.post(reverse('chamados_pending_delete', args=[own_pending.id]))
        self.assertRedirects(delete_response, reverse('chamados_pending_list'))
        self.assertFalse(TicketPending.objects.filter(id=own_pending.id).exists())

    def test_create_ticket_from_pending_starts_attendance_with_programmed_priority(self):
        pending = TicketPending.objects.create(
            attendant=self.ti_user,
            content='Atualizar permissoes de acesso da pasta financeira.',
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_pending_create_ticket', args=[pending.id]))
        self.assertRedirects(response, reverse('chamados_list'))

        ticket = Ticket.objects.get()
        self.assertEqual(ticket.created_by, self.ti_user)
        self.assertEqual(ticket.priority, Ticket.Priority.PROGRAMADA)
        self.assertEqual(ticket.status, Ticket.Status.EM_ATENDIMENTO)
        self.assertIn('Atualizar permissoes de acesso da pasta financeira.', ticket.description)

        running = TicketAttendance.objects.get(ticket=ticket, attendant=self.ti_user)
        self.assertIsNone(running.ended_at)
        self.assertFalse(TicketPending.objects.filter(id=pending.id).exists())

    def test_only_ti_can_access_requisicoes_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Requisicoes TI')

    def test_ti_can_create_and_edit_requisition(self):
        self.client.login(username='usuario.ti', password='senha@123')
        payload_create = json.dumps(
            [
                {
                    'id': '',
                    'temp_key': 'tmp_root_1',
                    'parent_ref': '',
                    'title': 'Orcamento principal',
                    'amount': '1500.00',
                    'notes': 'Fornecedor A',
                    'file_key': 'budget_file_tmp_root_1',
                    'clear_file': False,
                },
                {
                    'id': '',
                    'temp_key': 'tmp_sub_1',
                    'parent_ref': 'tmp:tmp_root_1',
                    'title': 'Suborcamento de instalacao',
                    'amount': '300.00',
                    'notes': '',
                    'file_key': 'budget_file_tmp_sub_1',
                    'clear_file': False,
                },
            ]
        )
        create_response = self.client.post(
            reverse('chamados_requisicoes_save'),
            data={
                'title': 'Compra de notebook para diretoria',
                'kind': Requisition.Kind.FISICA,
                'request_text': 'Necessario para substituicao do equipamento atual.',
                'budgets_payload': payload_create,
            },
        )
        self.assertRedirects(create_response, reverse('chamados_requisicoes'))
        requisition = Requisition.objects.get()
        self.assertEqual(requisition.requested_by, self.ti_user)
        self.assertEqual(requisition.status, Requisition.Status.PENDENTE_APROVACAO)
        self.assertTrue(requisition.code.startswith('REQ-'))
        self.assertEqual(RequisitionBudget.objects.filter(requisition=requisition).count(), 2)
        root_budget = RequisitionBudget.objects.get(requisition=requisition, parent_budget__isnull=True)
        sub_budget = RequisitionBudget.objects.get(requisition=requisition, parent_budget__isnull=False)
        self.assertEqual(sub_budget.parent_budget_id, root_budget.id)

        payload_edit = json.dumps(
            [
                {
                    'id': str(root_budget.id),
                    'temp_key': 'tmp_root_1',
                    'parent_ref': '',
                    'title': 'Orcamento principal atualizado',
                    'amount': '2000.00',
                    'notes': 'Fornecedor B',
                    'file_key': 'budget_file_tmp_root_1',
                    'clear_file': False,
                }
            ]
        )
        edit_response = self.client.post(
            reverse('chamados_requisicoes_save'),
            data={
                'requisition_id': requisition.id,
                'title': 'Compra de notebook para presidencia',
                'kind': Requisition.Kind.FISICA,
                'request_text': 'Atualizacao da requisicao com especificacao de memoria.',
                'budgets_payload': payload_edit,
            },
        )
        self.assertRedirects(edit_response, reverse('chamados_requisicoes'))
        requisition.refresh_from_db()
        self.assertEqual(requisition.title, 'Compra de notebook para presidencia')
        self.assertEqual(RequisitionUpdate.objects.filter(requisition=requisition).count(), 2)
        self.assertEqual(RequisitionBudget.objects.filter(requisition=requisition).count(), 1)
        root_budget.refresh_from_db()
        self.assertEqual(root_budget.title, 'Orcamento principal atualizado')
        self.assertEqual(str(root_budget.amount), '2000.00')

    def test_ti_can_update_requisition_status(self):
        requisition = Requisition.objects.create(
            title='Licenca de software de design',
            kind=Requisition.Kind.DIGITAL,
            request_text='Aquisicao anual para equipe de marketing.',
            requested_by=self.ti_user,
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_requisicoes_status', args=[requisition.id]),
            data={
                'status': Requisition.Status.APROVADA,
                'note': 'Aprovado em reuniao mensal.',
            },
        )
        self.assertRedirects(response, reverse('chamados_requisicoes'))
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertIsNotNone(requisition.approved_at)
        self.assertTrue(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                status_to=Requisition.Status.APROVADA,
            ).exists()
        )

    def test_requisicoes_page_has_copy_buttons(self):
        requisition = Requisition.objects.create(
            title='Compra de cadeira ergonomica',
            kind=Requisition.Kind.FISICA,
            request_text='Apoio para colaborador com recomendacao medica.',
            requested_by=self.ti_user,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            title='Orcamento principal',
            amount='980.00',
            notes='Fornecedor C',
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertContains(response, 'Copiar para Email')
        self.assertContains(response, 'Copiar para WhatsApp')
