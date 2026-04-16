import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Insumo, Requisition, RequisitionBudget, RequisitionUpdate, Starlink, Ticket, TicketAttendance, TicketPending, TicketUpdate


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
        self.fabiano_user = user_model.objects.create_user(
            username='fabiano.polone',
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

    def test_only_fabiano_can_delete_ticket(self):
        ticket = Ticket.objects.create(
            title='Chamado descartavel',
            description='Exclusao permitida apenas para o usuario definido.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
        )
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.post(reverse('chamados_delete', args=[ticket.id]), follow=True)
        self.assertContains(response, 'Voce nao possui permissao para excluir este chamado.')
        self.assertTrue(Ticket.objects.filter(id=ticket.id).exists())

        self.client.logout()
        self.client.login(username='fabiano.polone', password='senha@123')
        response = self.client.post(reverse('chamados_delete', args=[ticket.id]))
        self.assertRedirects(response, reverse('chamados_list'))
        self.assertFalse(Ticket.objects.filter(id=ticket.id).exists())

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
        self.assertEqual(ticket.status, Ticket.Status.ABERTO)
        self.assertIsNotNone(running.ended_at)
        self.assertEqual(running.end_action, TicketAttendance.EndAction.PAUSE)
        self.assertEqual(running.note, 'Rede estabilizada e usuario orientado.')

    def test_ti_queue_shows_only_free_or_own_tickets_and_hides_closed(self):
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
        closed_ticket = Ticket.objects.create(
            title='Chamado fechado',
            description='Nao deve aparecer na fila principal.',
            priority=Ticket.Priority.BAIXA,
            status=Ticket.Status.FECHADO,
            created_by=self.normal_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_list'))
        self.assertContains(response, free_ticket.title)
        self.assertContains(response, own_ticket.title)
        self.assertNotContains(response, locked_ticket.title)
        self.assertContains(response, f'Fechados (1)')
        self.assertNotContains(response, closed_ticket.title)

        response = self.client.get(reverse('chamados_detail', args=[locked_ticket.id]))
        self.assertRedirects(response, reverse('chamados_list'))

        closed_response = self.client.get(reverse('chamados_closed_data'))
        self.assertEqual(closed_response.status_code, 200)
        self.assertIn(closed_ticket.title, closed_response.json()['items'][0]['title'])

    def test_ti_queue_includes_ticket_with_own_finished_attendance(self):
        reopened_like_ticket = Ticket.objects.create(
            title='Problemas com Microsoft Word',
            description='Historico de atendimento proprio, sem atendimento ativo.',
            priority=Ticket.Priority.MEDIA,
            status=Ticket.Status.ABERTO,
            created_by=self.normal_user,
        )
        TicketAttendance.objects.create(
            ticket=reopened_like_ticket,
            attendant=self.ti_user,
            started_at=reopened_like_ticket.created_at,
            ended_at=reopened_like_ticket.created_at,
            end_action=TicketAttendance.EndAction.PAUSE,
            note='Ciclo anterior finalizado.',
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_list'))
        self.assertContains(response, reopened_like_ticket.title)

    def test_ti_queue_hides_ticket_with_only_other_finished_attendance(self):
        hidden_ticket = Ticket.objects.create(
            title='Chamado pausado por outro atendente',
            description='Nao deve aparecer para quem nunca atendeu.',
            priority=Ticket.Priority.MEDIA,
            status=Ticket.Status.ABERTO,
            created_by=self.normal_user,
        )
        TicketAttendance.objects.create(
            ticket=hidden_ticket,
            attendant=self.other_ti_user,
            started_at=hidden_ticket.created_at,
            ended_at=hidden_ticket.created_at,
            end_action=TicketAttendance.EndAction.PAUSE,
            note='Atendimento anterior de outro atendente.',
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_list'))
        self.assertNotContains(response, hidden_ticket.title)

    def test_ti_can_consult_tickets_by_selected_attendant(self):
        free_ticket = Ticket.objects.create(
            title='Chamado livre geral',
            description='Sem atendente.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
        )
        own_ticket = Ticket.objects.create(
            title='Chamado do proprio atendente',
            description='Atendido pelo usuario.ti.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
        )
        other_ticket = Ticket.objects.create(
            title='Chamado do outro atendente',
            description='Atendido por outro.ti.',
            priority=Ticket.Priority.BAIXA,
            created_by=self.normal_user,
        )
        TicketAttendance.objects.create(
            ticket=own_ticket,
            attendant=self.ti_user,
            started_at=own_ticket.created_at,
        )
        TicketAttendance.objects.create(
            ticket=other_ticket,
            attendant=self.other_ti_user,
            started_at=other_ticket.created_at,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_list') + '?atendente=outro.ti')
        self.assertContains(response, 'Modo consulta ativo')
        self.assertContains(response, other_ticket.title)
        self.assertNotContains(response, free_ticket.title)
        self.assertNotContains(response, own_ticket.title)

    def test_ti_can_open_other_attendant_ticket_in_consult_mode_read_only(self):
        locked_ticket = Ticket.objects.create(
            title='Chamado consulta',
            description='Somente leitura para outros atendentes.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
        )
        TicketAttendance.objects.create(
            ticket=locked_ticket,
            attendant=self.other_ti_user,
            started_at=locked_ticket.created_at,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_detail', args=[locked_ticket.id]) + '?consult=1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Modo consulta')
        self.assertNotContains(response, 'Atendimento TI')

    def test_ticket_detail_hides_legacy_metadata_from_description_and_history(self):
        ticket = Ticket.objects.create(
            title='Chamado legado',
            description='Descricao util\n\nTipo legado: requisicao | Falha legado: -\n[ERP-TI-ID:343]',
            priority=Ticket.Priority.MEDIA,
            status=Ticket.Status.EM_ATENDIMENTO,
            created_by=self.normal_user,
        )
        TicketUpdate.objects.create(
            ticket=ticket,
            author=self.ti_user,
            message='Evento legado (assigned): novo -> em_atendimento\nChamado assumido por usuario.ti.\n[ERP-TI-EVENT:1552]',
            status_to=Ticket.Status.EM_ATENDIMENTO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_detail', args=[ticket.id]))
        self.assertContains(response, 'Descricao util')
        self.assertContains(response, 'Chamado assumido por usuario.ti.')
        self.assertNotContains(response, 'Tipo legado:')
        self.assertNotContains(response, 'Falha legado:')
        self.assertNotContains(response, 'ERP-TI-ID')
        self.assertNotContains(response, 'Evento legado')
        self.assertNotContains(response, 'ERP-TI-EVENT')

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

    def test_requisicoes_page_shows_image_thumbnail_for_budget_attachment(self):
        requisition = Requisition.objects.create(
            title='Compra de webcam',
            kind=Requisition.Kind.FISICA,
            request_text='Item para sala de reunioes.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            title='Orcamento principal',
            amount='450.00',
            notes='Fornecedor D',
        )
        budget.evidence_file.save('print_orcamento.png', ContentFile(b'fake-image-bytes'), save=True)

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertContains(response, '/media/requisitions/budgets/')
        self.assertContains(response, 'budget-thumb')

    def test_only_ti_can_access_insumos_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_insumos'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_insumos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Insumos TI')

    def test_ti_can_create_and_update_insumo_record(self):
        self.client.login(username='usuario.ti', password='senha@123')
        create_response = self.client.post(
            reverse('chamados_insumos'),
            data={
                'mode': 'create',
                'item': 'Mouse',
                'date': '2026-04-10',
                'quantity': '2,00',
                'name': 'Fabiano',
                'department': 'TI',
            },
        )
        self.assertRedirects(create_response, reverse('chamados_insumos'))
        insumo = Insumo.objects.get()
        self.assertEqual(insumo.item, 'Mouse')
        self.assertEqual(str(insumo.quantity), '2.00')

        update_response = self.client.post(
            reverse('chamados_insumos'),
            data={
                'mode': 'update',
                'insumo_id': insumo.id,
                'item': 'Mouse sem fio',
                'date': '2026-04-11',
                'quantity': '3,00',
                'name': 'Fabiano',
                'department': 'TI',
            },
        )
        self.assertRedirects(update_response, reverse('chamados_insumos'))
        insumo.refresh_from_db()
        self.assertEqual(insumo.item, 'Mouse sem fio')
        self.assertEqual(str(insumo.quantity), '3.00')

    def test_ti_can_register_stock_and_output(self):
        self.client.login(username='usuario.ti', password='senha@123')
        self.client.post(
            reverse('chamados_insumos'),
            data={
                'mode': 'stock_create',
                'stock_item': 'Bateria',
                'stock_quantity': '5,00',
            },
        )
        self.client.post(
            reverse('chamados_insumos'),
            data={
                'mode': 'stock_adjust',
                'stock_item': 'Bateria',
                'stock_direction': 'dec',
                'stock_quantity': '2,00',
                'stock_target': 'Setor PCP',
                'stock_reason': 'Reposicao',
            },
        )
        response = self.client.get(reverse('chamados_insumos'))
        self.assertContains(response, 'Bateria')
        self.assertContains(response, '>3<', html=False)

    def test_only_ti_can_access_starlinks_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_starlinks'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_starlinks'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Starlinks')

    def test_ti_can_create_starlink(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_starlinks'),
            data={
                'name': 'Starlink Matriz',
                'location': 'Recepcao',
                'email': 'starlink@sidertec.com.br',
                'plain_password': 'Senha@12345',
                'is_active': 'on',
                'payment_method': 'cartao',
                'card_final': '1234',
            },
        )
        self.assertRedirects(response, reverse('chamados_starlinks'))
        starlink = Starlink.objects.get()
        self.assertEqual(starlink.name, 'Starlink Matriz')
        self.assertEqual(starlink.location, 'Recepcao')
        self.assertEqual(starlink.email, 'starlink@sidertec.com.br')
        self.assertTrue(starlink.is_active)
        self.assertEqual(starlink.payment_method, Starlink.PaymentMethod.CARTAO)
        self.assertEqual(starlink.card_final, '1234')
        self.assertEqual(starlink.created_by, self.ti_user)
        self.assertEqual(starlink.get_secret_password(), 'Senha@12345')

    def test_ti_can_create_starlink_with_pix_without_card_final(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_starlinks'),
            data={
                'name': 'Starlink Filial',
                'location': 'Filial',
                'email': 'pix@sidertec.com.br',
                'plain_password': 'Senha@12345',
                'is_active': 'on',
                'payment_method': 'pix',
                'card_final': '',
            },
        )
        self.assertRedirects(response, reverse('chamados_starlinks'))
        starlink = Starlink.objects.get(name='Starlink Filial')
        self.assertEqual(starlink.payment_method, Starlink.PaymentMethod.PIX)
        self.assertEqual(starlink.card_final, '')

    def test_ti_can_view_starlink_detail_with_secret(self):
        starlink = Starlink.objects.create(
            name='Starlink Detalhe',
            location='PCP',
            email='detalhe@sidertec.com.br',
            is_active=True,
            payment_method=Starlink.PaymentMethod.CARTAO,
            card_final='9876',
            created_by=self.ti_user,
            password_encrypted='',
        )
        starlink.set_secret_password('SenhaDetalhe@123')
        starlink.save(update_fields=['password_encrypted'])

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_starlinks_detail', args=[starlink.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SenhaDetalhe@123')
        self.assertContains(response, 'Editar dados')
        self.assertContains(response, 'Apagar')

    def test_ti_can_update_starlink(self):
        starlink = Starlink.objects.create(
            name='Starlink Antiga',
            location='Almox',
            email='antiga@sidertec.com.br',
            is_active=True,
            payment_method=Starlink.PaymentMethod.CARTAO,
            card_final='1111',
            created_by=self.ti_user,
            password_encrypted='',
        )
        starlink.set_secret_password('SenhaAntiga@123')
        starlink.save(update_fields=['password_encrypted'])

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_starlinks_update', args=[starlink.id]),
            data={
                'name': 'Starlink Nova',
                'location': 'Expedicao',
                'email': 'nova@sidertec.com.br',
                'plain_password': 'SenhaNova@123',
                'payment_method': 'pix',
                'card_final': '',
                'is_active': '',
            },
        )

        self.assertRedirects(response, reverse('chamados_starlinks_detail', args=[starlink.id]))
        starlink.refresh_from_db()
        self.assertEqual(starlink.name, 'Starlink Nova')
        self.assertEqual(starlink.location, 'Expedicao')
        self.assertEqual(starlink.email, 'nova@sidertec.com.br')
        self.assertFalse(starlink.is_active)
        self.assertEqual(starlink.payment_method, Starlink.PaymentMethod.PIX)
        self.assertEqual(starlink.card_final, '')
        self.assertEqual(starlink.get_secret_password(), 'SenhaNova@123')

    def test_ti_can_delete_starlink(self):
        starlink = Starlink.objects.create(
            name='Starlink Apagar',
            location='Recepcao',
            email='apagar@sidertec.com.br',
            is_active=True,
            payment_method=Starlink.PaymentMethod.CARTAO,
            card_final='2222',
            created_by=self.ti_user,
            password_encrypted='',
        )
        starlink.set_secret_password('SenhaApagar@123')
        starlink.save(update_fields=['password_encrypted'])

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_starlinks_delete', args=[starlink.id]))

        self.assertRedirects(response, reverse('chamados_starlinks'))
        self.assertFalse(Starlink.objects.filter(id=starlink.id).exists())
