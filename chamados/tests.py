import json
from decimal import Decimal
from datetime import datetime
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from .models import ContractEntry, DocumentEntry, FuturaDigitalEntry, Insumo, Requisition, RequisitionBudget, RequisitionBudgetHistory, RequisitionUpdate, Starlink, Ticket, TicketAttendance, TicketAutoPauseReview, TicketPending, TicketUpdate, TipEntry
from .excel_export import _looks_like_windows_unc_path, _translate_windows_unc_path


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
        self.fabiano_user.groups.add(ti_group)

    def test_normal_user_creates_ticket_and_sees_own_only(self):
        self.client.login(username='usuario.comum', password='senha@123')
        with patch('chamados.views.whatsapp.notify_group_new_ticket') as mock_notify:
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
        mock_notify.assert_called_once_with(ticket)

        Ticket.objects.create(
            title='Teste externo',
            description='Outro chamado',
            priority=Ticket.Priority.BAIXA,
            created_by=self.other_user,
        )

        response = self.client.get(reverse('chamados_list'))
        self.assertContains(response, 'Notebook sem rede')
        self.assertNotContains(response, 'Teste externo')

    def test_ticket_creation_still_succeeds_if_whatsapp_notification_fails(self):
        self.client.login(username='usuario.comum', password='senha@123')
        with patch('chamados.views.whatsapp.notify_group_new_ticket', side_effect=RuntimeError('falha wapi')):
            response = self.client.post(
                reverse('chamados_new'),
                data={
                    'title': 'Notebook sem rede',
                    'description': 'Nao conecta na rede corporativa.',
                    'priority': Ticket.Priority.ALTA,
                },
            )

        self.assertRedirects(response, reverse('chamados_list'))
        self.assertTrue(Ticket.objects.filter(title='Notebook sem rede').exists())

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

    def test_ti_can_export_attendances_to_spreadsheet(self):
        ticket = Ticket.objects.create(
            title='Planilha de teste',
            description='Falha ao acessar a impressora do financeiro.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=timezone.make_aware(datetime(2026, 4, 17, 8, 0)),
            ended_at=timezone.make_aware(datetime(2026, 4, 17, 9, 30)),
            end_action=TicketAttendance.EndAction.STOP,
            note='Reinstalado driver e validado teste de impressao.',
        )

        with TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / 'chamados.xlsx'
            wb = Workbook()
            ws = wb.active
            ws.title = 'Abril 2026'
            ws.append(['TI', 'Data', 'Contato', 'Setor', 'Notificacao', 'Prioridade', 'Falha', 'Acao / Correcao', 'Fechado', 'Tempo', 'Acao eficaz'])
            wb.save(workbook_path)

            self.client.login(username='usuario.ti', password='senha@123')
            response = self.client.post(
                reverse('chamados_preencher_planilha'),
                data={
                    'attendant_id': self.ti_user.id,
                    'workbook_path': str(workbook_path),
                    'next': reverse('chamados_list'),
                },
            )

            self.assertRedirects(response, reverse('chamados_list'))
            attendance.refresh_from_db()
            self.assertIsNotNone(attendance.exported_at)
            self.assertEqual(attendance.exported_path, str(workbook_path))

            saved = load_workbook(workbook_path)
            sheet = saved['Abril 2026']
            self.assertEqual(sheet.cell(row=2, column=1).value, ticket.id)
            self.assertEqual(sheet.cell(row=2, column=3).value, 'usuario.comum')
            self.assertEqual(sheet.cell(row=2, column=5).value, 'Planilha de teste')
            self.assertEqual(sheet.cell(row=2, column=6).value, 'Alta')
            self.assertEqual(sheet.cell(row=2, column=7).value, 'Falha ao acessar a impressora do financeiro.')
            self.assertEqual(sheet.cell(row=2, column=8).value, 'Reinstalado driver e validado teste de impressao.')
            self.assertEqual(sheet.cell(row=2, column=10).value, '01:30')

    def test_spreadsheet_export_is_blocked_when_auto_pause_review_is_pending(self):
        ticket = Ticket.objects.create(
            title='Chamado com pausa automatica',
            description='Descricao qualquer.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=timezone.make_aware(datetime(2026, 4, 17, 8, 0)),
            ended_at=timezone.make_aware(datetime(2026, 4, 17, 9, 0)),
            end_action=TicketAttendance.EndAction.PAUSE,
            note='Atendimento encerrado automaticamente.',
        )
        TicketAutoPauseReview.objects.create(attendance=attendance)

        with TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / 'chamados.xlsx'
            wb = Workbook()
            wb.save(workbook_path)

            self.client.login(username='usuario.ti', password='senha@123')
            response = self.client.post(
                reverse('chamados_preencher_planilha'),
                data={
                    'attendant_id': self.ti_user.id,
                    'workbook_path': str(workbook_path),
                    'next': reverse('chamados_list'),
                },
                follow=True,
            )

            self.assertContains(response, 'Existem pausas automaticas pendentes para este atendente.')
            attendance.refresh_from_db()
            self.assertIsNone(attendance.exported_at)

    @override_settings(CHAMADOS_WINDOWS_DRIVE_MOUNT_ROOT='/mnt')
    def test_unc_path_without_leading_backslashes_is_supported(self):
        raw_path = r'192.168.22.5\Sidertec\TI\Documentos\Chamados\Chamados 2026 - Fabiano.xlsx'

        self.assertTrue(_looks_like_windows_unc_path(raw_path))
        self.assertEqual(
            _translate_windows_unc_path(raw_path),
            str(Path('/mnt') / 'sidertec' / 'TI' / 'Documentos' / 'Chamados' / 'Chamados 2026 - Fabiano.xlsx'),
        )

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
                'pause_status': Ticket.Status.ABERTO,
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

    def test_ti_can_pause_ticket_as_aguardando_usuario(self):
        ticket = Ticket.objects.create(
            title='Liberacao pendente do usuario',
            description='Aguardando retorno do usuario.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
            status=Ticket.Status.EM_ATENDIMENTO,
        )
        TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=ticket.created_at,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_action', args=[ticket.id]),
            data={
                'action': 'pause',
                'note': 'Solicitado retorno do usuario para teste final.',
                'pause_status': Ticket.Status.AGUARDANDO_USUARIO,
                'next': reverse('chamados_list'),
            },
        )

        self.assertRedirects(response, reverse('chamados_list'))
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.AGUARDANDO_USUARIO)

    def test_ti_can_stop_ticket_and_it_becomes_closed(self):
        ticket = Ticket.objects.create(
            title='Chamado para fechar',
            description='Fluxo de encerramento.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
            status=Ticket.Status.EM_ATENDIMENTO,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=ticket.created_at,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_action', args=[ticket.id]),
            data={
                'action': 'stop',
                'note': 'Equipamento ajustado e validado.',
                'next': reverse('chamados_list'),
            },
        )

        self.assertRedirects(response, reverse('chamados_list'))
        ticket.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.FECHADO)
        self.assertIsNotNone(ticket.closed_at)
        self.assertEqual(attendance.end_action, TicketAttendance.EndAction.STOP)

    def test_management_command_auto_pauses_running_tickets(self):
        ticket = Ticket.objects.create(
            title='Chamado auto pause',
            description='Deve sair do play no fim do expediente.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
            status=Ticket.Status.EM_ATENDIMENTO,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=ticket.created_at,
        )

        fake_now = timezone.make_aware(datetime(2026, 4, 17, 21, 0, 0))
        with patch('chamados.management.commands.autopause_open_tickets.timezone.now', return_value=fake_now):
            call_command('autopause_open_tickets')

        ticket.refresh_from_db()
        attendance.refresh_from_db()
        review = TicketAutoPauseReview.objects.get(attendance=attendance)
        self.assertEqual(ticket.status, Ticket.Status.ABERTO)
        self.assertIsNotNone(attendance.ended_at)
        self.assertEqual(attendance.end_action, TicketAttendance.EndAction.PAUSE)
        self.assertIsNone(review.completed_at)
        self.assertTrue(
            TicketUpdate.objects.filter(
                ticket=ticket,
                message__icontains='Pause automatico no fim do expediente',
            ).exists()
        )

    def test_management_command_skips_before_end_of_day_without_force(self):
        ticket = Ticket.objects.create(
            title='Chamado ainda em expediente',
            description='Nao deve pausar antes das 17:45.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
            status=Ticket.Status.EM_ATENDIMENTO,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=ticket.created_at,
        )

        fake_now = timezone.make_aware(datetime(2026, 4, 17, 17, 44, 0))
        with patch('chamados.management.commands.autopause_open_tickets.timezone.now', return_value=fake_now):
            call_command('autopause_open_tickets')

        ticket.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.EM_ATENDIMENTO)
        self.assertIsNone(attendance.ended_at)
        self.assertFalse(TicketAutoPauseReview.objects.filter(attendance=attendance).exists())

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
        self.assertNotContains(response, '>usuario.ti<', html=False)

    def test_ti_can_review_auto_paused_tickets(self):
        ticket = Ticket.objects.create(
            title='Chamado revisao auto pause',
            description='Registro do dia seguinte.',
            priority=Ticket.Priority.MEDIA,
            created_by=self.normal_user,
            status=Ticket.Status.EM_ATENDIMENTO,
        )
        attendance = TicketAttendance.objects.create(
            ticket=ticket,
            attendant=self.ti_user,
            started_at=ticket.created_at,
            ended_at=ticket.created_at,
            end_action=TicketAttendance.EndAction.PAUSE,
            note='',
        )
        review = TicketAutoPauseReview.objects.create(attendance=attendance)

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_auto_pause_reviews'))
        self.assertContains(response, 'Pausas automaticas')
        self.assertContains(response, ticket.title)

        save_response = self.client.post(
            reverse('chamados_auto_pause_reviews'),
            data={
                'review_id': review.id,
                'note': 'Troca concluida e validada antes de encerrar o expediente.',
                'status': Ticket.Status.FECHADO,
            },
        )
        self.assertRedirects(save_response, reverse('chamados_auto_pause_reviews'))

        ticket.refresh_from_db()
        attendance.refresh_from_db()
        review.refresh_from_db()
        self.assertEqual(ticket.status, Ticket.Status.FECHADO)
        self.assertEqual(attendance.note, 'Troca concluida e validada antes de encerrar o expediente.')
        self.assertIsNotNone(review.completed_at)

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
        self.assertEqual(ticket.title, 'Atualizar permissoes de acesso da pasta financeira.')
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
                    'store_name': 'Kabum',
                    'title': 'Orcamento principal',
                    'amount': '1500.00',
                    'quantity': '2',
                    'discount_amount': '100.00',
                    'approval_status': RequisitionBudget.ApprovalStatus.APROVADO,
                    'receipt_status': RequisitionBudget.ReceiptStatus.PARCIAL,
                    'received_quantity': '1',
                    'notes': 'Fornecedor A',
                    'file_key': 'budget_file_tmp_root_1',
                    'clear_file': False,
                },
                {
                    'id': '',
                    'temp_key': 'tmp_sub_1',
                    'parent_ref': 'tmp:tmp_root_1',
                    'store_name': 'Instaladora XPTO',
                    'title': 'Suborcamento de instalacao',
                    'amount': '300.00',
                    'quantity': '3',
                    'discount_amount': '0',
                    'approval_status': RequisitionBudget.ApprovalStatus.PENDENTE,
                    'receipt_status': RequisitionBudget.ReceiptStatus.PENDENTE,
                    'received_quantity': '0',
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
        self.assertEqual(root_budget.store_name, 'Kabum')
        self.assertEqual(sub_budget.store_name, 'Instaladora XPTO')
        self.assertEqual(root_budget.quantity, 2)
        self.assertEqual(sub_budget.quantity, 3)
        self.assertEqual(str(root_budget.discount_amount), '100.00')
        self.assertEqual(root_budget.approval_status, RequisitionBudget.ApprovalStatus.APROVADO)
        self.assertEqual(root_budget.receipt_status, RequisitionBudget.ReceiptStatus.PARCIAL)
        self.assertEqual(root_budget.received_quantity, 1)
        self.assertEqual(requisition.budget_total, Decimal('3800.00'))
        self.assertEqual(RequisitionBudgetHistory.objects.filter(budget=root_budget).count(), 1)

        payload_edit = json.dumps(
            [
                {
                    'id': str(root_budget.id),
                    'temp_key': 'tmp_root_1',
                    'parent_ref': '',
                    'store_name': 'Pichau',
                    'title': 'Orcamento principal atualizado',
                    'amount': '2000.00',
                    'quantity': '4',
                    'discount_amount': '200.00',
                    'approval_status': RequisitionBudget.ApprovalStatus.APROVADO,
                    'receipt_status': RequisitionBudget.ReceiptStatus.RECEBIDO,
                    'received_quantity': '4',
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
        self.assertEqual(root_budget.store_name, 'Pichau')
        self.assertEqual(root_budget.title, 'Orcamento principal atualizado')
        self.assertEqual(str(root_budget.amount), '2000.00')
        self.assertEqual(root_budget.quantity, 4)
        self.assertEqual(str(root_budget.discount_amount), '200.00')
        self.assertEqual(root_budget.receipt_status, RequisitionBudget.ReceiptStatus.RECEBIDO)
        self.assertEqual(root_budget.received_quantity, 4)
        self.assertEqual(requisition.budget_total, Decimal('7800.00'))
        self.assertEqual(RequisitionBudgetHistory.objects.filter(budget=root_budget).count(), 2)

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

    def test_ti_can_approve_specific_requisition_budget(self):
        requisition = Requisition.objects.create(
            title='Compra de nobreak',
            kind=Requisition.Kind.FISICA,
            request_text='Reposicao do CPD.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor Z',
            title='Nobreak 1500VA',
            amount='1800.00',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_requisicoes_budget_approve', args=[budget.id]))

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        budget.refresh_from_db()
        requisition.refresh_from_db()
        self.assertEqual(budget.approval_status, RequisitionBudget.ApprovalStatus.APROVADO)
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertTrue(
            RequisitionBudgetHistory.objects.filter(
                budget=budget,
                message__icontains='Orcamento aprovado diretamente pela visualizacao',
            ).exists()
        )
        self.assertTrue(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                status_to=Requisition.Status.APROVADA,
                message__icontains='Requisicao aprovada a partir do orcamento',
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
            store_name='Fornecedor C',
            title='Orcamento principal',
            amount='980.00',
            quantity=2,
            discount_amount='30.00',
            notes='Fornecedor C',
        )
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertContains(response, 'Copiar para Email')
        self.assertContains(response, 'Copiar para WhatsApp')
        self.assertContains(response, 'Fornecedor C: R$ 1.930,00')
        self.assertContains(response, 'Pendente')

    def test_requisition_total_uses_unit_amount_times_quantity(self):
        requisition = Requisition.objects.create(
            title='Compra de cadeiras',
            kind=Requisition.Kind.FISICA,
            request_text='Reposicao do administrativo.',
            requested_by=self.ti_user,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor principal',
            title='Cadeira presidente',
            amount='850.00',
            quantity=2,
            notes='Fornecedor principal',
        )
        root_budget = RequisitionBudget.objects.get(requisition=requisition, parent_budget__isnull=True)
        RequisitionBudget.objects.create(
            requisition=requisition,
            parent_budget=root_budget,
            title='Montagem',
            amount='120.00',
            quantity=3,
            notes='Servico adicional',
        )

        self.assertEqual(requisition.budget_total, Decimal('2060.00'))

    def test_requisition_budget_history_is_visible_in_payload(self):
        requisition = Requisition.objects.create(
            title='Compra de monitor',
            kind=Requisition.Kind.FISICA,
            request_text='Expansao de equipe.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor E',
            title='Monitor 27',
            amount='1200.00',
            quantity=2,
            discount_amount='150.00',
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
            receipt_status=RequisitionBudget.ReceiptStatus.PARCIAL,
            received_quantity=1,
            notes='Fornecedor E',
        )
        RequisitionBudgetHistory.objects.create(
            budget=budget,
            author=self.ti_user,
            message='Orcamento atualizado (valores).',
            store_name='Fornecedor E',
            amount='1200.00',
            quantity=2,
            line_total='2400.00',
            discount_amount='150.00',
            final_total='2250.00',
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
            receipt_status=RequisitionBudget.ReceiptStatus.PARCIAL,
            received_quantity=1,
            remaining_quantity=1,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))
        self.assertContains(response, 'Historico')
        self.assertContains(response, 'Recebido parcial')

    def test_requisicoes_page_shows_image_thumbnail_for_budget_attachment(self):
        requisition = Requisition.objects.create(
            title='Compra de webcam',
            kind=Requisition.Kind.FISICA,
            request_text='Item para sala de reunioes.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor D',
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
        self.assertEqual(starlink.password_encrypted, '')

    def test_ti_can_create_starlink_with_pix_without_card_final(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_starlinks'),
            data={
                'name': 'Starlink Filial',
                'location': 'Filial',
                'email': 'pix@sidertec.com.br',
                'is_active': 'on',
                'payment_method': 'pix',
                'card_final': '',
            },
        )
        self.assertRedirects(response, reverse('chamados_starlinks'))
        starlink = Starlink.objects.get(name='Starlink Filial')
        self.assertEqual(starlink.payment_method, Starlink.PaymentMethod.PIX)
        self.assertEqual(starlink.card_final, '')

    def test_ti_can_view_starlink_detail_without_password(self):
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

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_starlinks_detail', args=[starlink.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Editar dados')
        self.assertContains(response, 'Apagar')
        self.assertNotContains(response, 'Senha')

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

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_starlinks_update', args=[starlink.id]),
            data={
                'name': 'Starlink Nova',
                'location': 'Expedicao',
                'email': 'nova@sidertec.com.br',
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
        self.assertEqual(starlink.password_encrypted, '')

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

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_starlinks_delete', args=[starlink.id]))

        self.assertRedirects(response, reverse('chamados_starlinks'))
        self.assertFalse(Starlink.objects.filter(id=starlink.id).exists())

    def test_only_ti_can_access_documentos_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_documentos'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_documentos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Documentos')

    def test_ti_can_create_documento(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_documentos'),
            data={
                'name': 'Manual da impressora fiscal',
                'notes': 'Arquivo e observacoes para reinstalacao rapida.',
            },
        )

        self.assertRedirects(response, reverse('chamados_documentos'))
        documento = DocumentEntry.objects.get()
        self.assertEqual(documento.name, 'Manual da impressora fiscal')
        self.assertEqual(documento.notes, 'Arquivo e observacoes para reinstalacao rapida.')
        self.assertEqual(documento.created_by, self.ti_user)

    def test_ti_can_create_documento_with_attachment(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_documentos'),
            data={
                'name': 'Procedimento VPN',
                'notes': 'Passo a passo de configuracao.',
                'attachment': ContentFile(b'pdf-teste', name='procedimento_vpn.pdf'),
            },
        )

        self.assertRedirects(response, reverse('chamados_documentos'))
        documento = DocumentEntry.objects.get(name='Procedimento VPN')
        self.assertIn('procedimento_vpn', documento.attachment.name)
        self.assertTrue(documento.attachment.name.endswith('.pdf'))

    def test_only_ti_can_access_contratos_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_contratos'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_contratos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contratos')

    def test_ti_can_create_contrato(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_contratos'),
            data={
                'name': 'Contrato Microsoft 365',
                'notes': 'Renovacao anual do licenciamento corporativo.',
                'amount': '2499.90',
                'contract_start': '2026-01-01',
                'contract_end': '2026-12-31',
                'payment_method': 'Cartao',
                'card_final': '1234',
                'payment_schedule': 'mensal',
            },
        )

        self.assertRedirects(response, reverse('chamados_contratos'))
        contrato = ContractEntry.objects.get()
        self.assertEqual(contrato.name, 'Contrato Microsoft 365')
        self.assertEqual(contrato.notes, 'Renovacao anual do licenciamento corporativo.')
        self.assertEqual(str(contrato.amount), '2499.90')
        self.assertEqual(str(contrato.contract_start), '2026-01-01')
        self.assertEqual(str(contrato.contract_end), '2026-12-31')
        self.assertEqual(contrato.payment_method, 'Cartao')
        self.assertEqual(contrato.card_final, '1234')
        self.assertEqual(contrato.payment_schedule, ContractEntry.PaymentSchedule.MENSAL)
        self.assertEqual(contrato.created_by, self.ti_user)

    def test_ti_can_create_contrato_anual(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_contratos'),
            data={
                'name': 'Contrato antivirus',
                'notes': 'Renovacao anual.',
                'amount': '1200.00',
                'contract_start': '2026-01-01',
                'contract_end': '2026-12-31',
                'payment_method': 'Boleto',
                'card_final': '',
                'payment_schedule': 'anual',
            },
        )

        self.assertRedirects(response, reverse('chamados_contratos'))
        contrato = ContractEntry.objects.get(name='Contrato antivirus')
        self.assertEqual(contrato.payment_schedule, ContractEntry.PaymentSchedule.ANUAL)

    def test_ti_can_create_contrato_with_brazilian_amount_mask(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_contratos'),
            data={
                'name': 'Contrato com mascara',
                'notes': 'Valor digitado no formato brasileiro.',
                'amount': '2.499,90',
                'contract_start': '2026-01-01',
                'contract_end': '2026-12-31',
                'payment_method': 'Boleto',
                'card_final': '',
                'payment_schedule': 'mensal',
            },
        )

        self.assertRedirects(response, reverse('chamados_contratos'))
        contrato = ContractEntry.objects.get(name='Contrato com mascara')
        self.assertEqual(str(contrato.amount), '2499.90')

    def test_contratos_page_displays_amount_in_brazilian_format(self):
        ContractEntry.objects.create(
            name='Contrato exibicao',
            notes='',
            amount='2499.90',
            contract_start=date(2026, 1, 1),
            contract_end=date(2026, 12, 31),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL,
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_contratos'))

        self.assertContains(response, 'R$ 2.499,90')

    def test_ti_can_attach_file_to_existing_contract(self):
        contrato = ContractEntry.objects.create(
            name='Contrato sem anexo',
            notes='',
            amount='350.00',
            contract_start=date(2026, 1, 1),
            contract_end=date(2026, 12, 31),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL,
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_contratos_attachment', args=[contrato.id]),
            data={
                'attachment': ContentFile(b'contrato-anexo', name='contrato.pdf'),
            },
        )

        self.assertRedirects(response, reverse('chamados_contratos'))
        contrato.refresh_from_db()
        self.assertTrue(contrato.attachment.name.endswith('.pdf'))

    def test_contract_duration_label_is_derived_from_dates(self):
        contrato = ContractEntry.objects.create(
            name='Contrato teste',
            notes='',
            amount='100.00',
            contract_start=date(2026, 1, 1),
            contract_end=date(2027, 1, 1),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.PAGAMENTO_UNICO,
            created_by=self.ti_user,
        )

        self.assertEqual(contrato.contract_duration_label, '1 ano')

    def test_only_ti_can_access_futura_digital_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_futura_digital'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_futura_digital'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Futura Digital')

    def test_ti_can_create_futura_digital_entry(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_futura_digital'),
            data={
                'name': 'Impressora RH',
                'invoice': 'FAT-2048',
                'reference_month': '2026-04',
                'copies_count': '1875',
                'paid_amount': '1.250,40',
            },
        )

        self.assertRedirects(response, reverse('chamados_futura_digital'))
        entry = FuturaDigitalEntry.objects.get()
        self.assertEqual(entry.name, 'Impressora RH')
        self.assertEqual(entry.invoice, 'FAT-2048')
        self.assertEqual(str(entry.reference_month), '2026-04-01')
        self.assertEqual(entry.copies_count, 1875)
        self.assertEqual(str(entry.paid_amount), '1250.40')
        self.assertEqual(entry.created_by, self.ti_user)

    def test_only_ti_can_access_dicas_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_dicas'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_dicas'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dicas')
        self.assertContains(response, 'Power Fab nao conecta')

    def test_ti_can_create_tip_with_attachment(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_dicas'),
            data={
                'category': TipEntry.Category.GERAL,
                'title': 'Nova dica de teste',
                'content': 'Conteudo da dica.',
                'attachment': ContentFile(b'anexo-dica', name='dica_teste.txt'),
            },
        )

        self.assertRedirects(response, reverse('chamados_dicas'))
        dica = TipEntry.objects.get(title='Nova dica de teste')
        self.assertEqual(dica.created_by, self.ti_user)
        self.assertIn('dica_teste', dica.attachment.name)

    def test_whatsapp_message_uses_legacy_template_defaults(self):
        self.normal_user.first_name = 'Cassia'
        self.normal_user.last_name = 'Estevo'
        self.normal_user.save(update_fields=['first_name', 'last_name'])
        ticket = Ticket.objects.create(
            title='Chamado WhatsApp',
            description='Teste de notificacao.',
            priority=Ticket.Priority.CRITICA,
            created_by=self.normal_user,
        )

        with self.settings(
            WHATSAPP_TEMPLATE_NEW_TICKET='🚨 {urgencia} - {solicitante}\n📄 {title}'
        ):
            from chamados.whatsapp import render_new_ticket_message

            message = render_new_ticket_message(ticket)

        self.assertIn('🚨 Critica - Cassia Estevo', message)
        self.assertIn('📄 Chamado WhatsApp', message)

    def test_whatsapp_message_humanizes_username_and_newline_template(self):
        ticket = Ticket.objects.create(
            title='Teste WhatsApp',
            description='Teste de notificacao.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
        )

        with self.settings(
            WHATSAPP_TEMPLATE_NEW_TICKET='🚨 {urgencia} - {solicitante}\\n📄 {title}'
        ):
            from chamados.whatsapp import render_new_ticket_message

            message = render_new_ticket_message(ticket)

        self.assertEqual(message, '🚨 Alta - Usuario Comum\n📄 Teste WhatsApp')

    def test_whatsapp_notifications_detect_wapi_provider(self):
        with self.settings(
            WHATSAPP_NOTIFICATIONS_ENABLED=True,
            WHATSAPP_GROUP_JID='120363421981424263@g.us',
            WAPI_TOKEN='token-wapi',
            WAPI_INSTANCE='instance-01',
            WHATSAPP_WEBHOOK_URL='',
            WHATSAPP_PROVIDER='',
        ):
            from chamados.whatsapp import active_provider, notifications_enabled

            self.assertEqual(active_provider(), 'wapi')
            self.assertTrue(notifications_enabled())

    def test_whatsapp_notifications_send_via_wapi(self):
        ticket = Ticket.objects.create(
            title='Chamado WAPI',
            description='Teste de envio para W-API.',
            priority=Ticket.Priority.ALTA,
            created_by=self.normal_user,
        )

        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({'status': 'success', 'messageId': 'abc123'}).encode('utf-8')
        mocked_urlopen = MagicMock()
        mocked_urlopen.return_value.__enter__.return_value = response

        with self.settings(
            WHATSAPP_NOTIFICATIONS_ENABLED=True,
            WHATSAPP_GROUP_JID='120363421981424263@g.us',
            WHATSAPP_SEND_GROUP_ON_NEW_TICKET=True,
            WAPI_TOKEN='token-wapi',
            WAPI_INSTANCE='instance-01',
            WAPI_BASE_URL='https://api.w-api.app/v1',
            WHATSAPP_PROVIDER='wapi',
            WHATSAPP_WEBHOOK_URL='',
        ), patch('chamados.whatsapp.request.urlopen', mocked_urlopen):
            from chamados.whatsapp import notify_group_new_ticket

            sent = notify_group_new_ticket(ticket)

        self.assertTrue(sent)
        req = mocked_urlopen.call_args.args[0]
        self.assertIn('message/send-text?instanceId=instance-01', req.full_url)
        self.assertEqual(req.headers['Authorization'], 'Bearer token-wapi')
        payload = json.loads(req.data.decode('utf-8'))
        self.assertEqual(payload['token'], 'token-wapi')
        self.assertEqual(payload['phone'], '120363421981424263@g.us')
        self.assertIn('Chamado WAPI', payload['message'])

    def test_whatsapp_timeout_tuple_is_normalized_for_urllib(self):
        from chamados.whatsapp import _normalize_timeout

        self.assertEqual(_normalize_timeout((6.0, 20.0)), 20.0)
        self.assertEqual(_normalize_timeout(10), 10)

    def test_ti_can_update_tip(self):
        dica = TipEntry.objects.create(
            category=TipEntry.Category.GERAL,
            title='Dica antiga',
            content='Conteudo antigo.',
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_dicas_update', args=[dica.id]),
            data={
                'edit_tip-category': TipEntry.Category.RESOLUCAO,
                'edit_tip-title': 'Dica atualizada',
                'edit_tip-content': 'Conteudo atualizado.',
            },
        )

        self.assertRedirects(response, reverse('chamados_dicas'))
        dica.refresh_from_db()
        self.assertEqual(dica.category, TipEntry.Category.RESOLUCAO)
        self.assertEqual(dica.title, 'Dica atualizada')
        self.assertEqual(dica.content, 'Conteudo atualizado.')

    def test_only_fabiano_can_delete_tip(self):
        dica = TipEntry.objects.create(
            category=TipEntry.Category.GERAL,
            title='Dica para apagar',
            content='Conteudo removivel.',
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_dicas_delete', args=[dica.id]), follow=True)
        self.assertContains(response, 'Somente fabiano.polone pode apagar dicas.')
        self.assertTrue(TipEntry.objects.filter(id=dica.id).exists())

        self.client.logout()
        self.client.login(username='fabiano.polone', password='senha@123')
        response = self.client.post(reverse('chamados_dicas_delete', args=[dica.id]))
        self.assertRedirects(response, reverse('chamados_dicas'))
        self.assertFalse(TipEntry.objects.filter(id=dica.id).exists())
