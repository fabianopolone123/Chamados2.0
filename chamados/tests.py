import json
import sqlite3
from decimal import Decimal
from datetime import datetime
from datetime import date
from datetime import timedelta
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

from .models import CompletedServiceAttachment, CompletedServiceEntry, ContractEntry, DocumentEntry, FuturaDigitalEntry, Insumo, Requisition, RequisitionBudget, RequisitionBudgetHistory, RequisitionUpdate, Starlink, Ticket, TicketAttendance, TicketAutoPauseReview, TicketPending, TicketUpdate, TipEntry
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
                    'freight_amount': '150.00',
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
                    'freight_amount': '30.00',
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
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertTrue(requisition.code.startswith('REQ-'))
        self.assertEqual(RequisitionBudget.objects.filter(requisition=requisition).count(), 2)
        root_budget = RequisitionBudget.objects.get(requisition=requisition, parent_budget__isnull=True)
        sub_budget = RequisitionBudget.objects.get(requisition=requisition, parent_budget__isnull=False)
        self.assertEqual(sub_budget.parent_budget_id, root_budget.id)
        self.assertEqual(root_budget.store_name, 'Kabum')
        self.assertEqual(sub_budget.store_name, 'Instaladora XPTO')
        self.assertEqual(root_budget.quantity, 2)
        self.assertEqual(sub_budget.quantity, 3)
        self.assertEqual(str(root_budget.freight_amount), '150.00')
        self.assertEqual(str(sub_budget.freight_amount), '30.00')
        self.assertEqual(str(root_budget.discount_amount), '100.00')
        self.assertEqual(root_budget.approval_status, RequisitionBudget.ApprovalStatus.APROVADO)
        self.assertEqual(root_budget.receipt_status, RequisitionBudget.ReceiptStatus.PARCIAL)
        self.assertEqual(root_budget.received_quantity, 1)
        self.assertEqual(requisition.budget_total, Decimal('3980.00'))
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
                    'freight_amount': '89.90',
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
        self.assertEqual(RequisitionUpdate.objects.filter(requisition=requisition).count(), 3)
        self.assertEqual(RequisitionBudget.objects.filter(requisition=requisition).count(), 1)
        root_budget.refresh_from_db()
        self.assertEqual(root_budget.store_name, 'Pichau')
        self.assertEqual(root_budget.title, 'Orcamento principal atualizado')
        self.assertEqual(str(root_budget.amount), '2000.00')
        self.assertEqual(root_budget.quantity, 4)
        self.assertEqual(str(root_budget.freight_amount), '89.90')
        self.assertEqual(str(root_budget.discount_amount), '200.00')
        self.assertEqual(root_budget.receipt_status, RequisitionBudget.ReceiptStatus.RECEBIDO)
        self.assertEqual(root_budget.received_quantity, 4)
        self.assertEqual(requisition.budget_total, Decimal('7889.90'))
        self.assertEqual(RequisitionBudgetHistory.objects.filter(budget=root_budget).count(), 2)

    def test_requisition_save_auto_approves_when_any_budget_is_approved(self):
        self.client.login(username='usuario.ti', password='senha@123')
        payload = json.dumps(
            [
                {
                    'id': '',
                    'temp_key': 'tmp_root_1',
                    'parent_ref': '',
                    'store_name': 'Loja Exemplo',
                    'title': 'Notebook',
                    'amount': '2500.00',
                    'quantity': '1',
                    'discount_amount': '0',
                    'approval_status': RequisitionBudget.ApprovalStatus.APROVADO,
                    'receipt_status': RequisitionBudget.ReceiptStatus.PENDENTE,
                    'received_quantity': '0',
                    'notes': '',
                    'file_key': 'budget_file_tmp_root_1',
                    'clear_file': False,
                }
            ]
        )
        response = self.client.post(
            reverse('chamados_requisicoes_save'),
            data={
                'title': 'Compra emergencial',
                'kind': Requisition.Kind.FISICA,
                'request_text': 'Reposicao.',
                'budgets_payload': payload,
            },
        )

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        requisition = Requisition.objects.get(title='Compra emergencial')
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)

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

    def test_ti_can_reject_pending_requisition_and_all_budgets(self):
        requisition = Requisition.objects.create(
            title='Compra nao aprovada',
            kind=Requisition.Kind.FISICA,
            request_text='Nenhum orcamento aprovado.',
            requested_by=self.ti_user,
            status=Requisition.Status.PENDENTE_APROVACAO,
        )
        root_budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor principal',
            title='Desktop',
            amount='2500.00',
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
        )
        sub_budget = RequisitionBudget.objects.create(
            requisition=requisition,
            parent_budget=root_budget,
            store_name='Fornecedor sub',
            title='Memoria adicional',
            amount='300.00',
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_requisicoes_reject_all_budgets', args=[requisition.id]))

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        requisition.refresh_from_db()
        root_budget.refresh_from_db()
        sub_budget.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.NAO_APROVADA)
        self.assertIsNone(requisition.approved_at)
        self.assertEqual(root_budget.approval_status, RequisitionBudget.ApprovalStatus.NAO_APROVADO)
        self.assertEqual(sub_budget.approval_status, RequisitionBudget.ApprovalStatus.NAO_APROVADO)
        self.assertTrue(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                status_to=Requisition.Status.NAO_APROVADA,
                message__icontains='2 orcamento(s) marcado(s) como nao aprovado(s)',
            ).exists()
        )
        self.assertEqual(
            RequisitionBudgetHistory.objects.filter(
                budget__in=[root_budget, sub_budget],
                message__icontains='rejeicao da requisicao',
            ).count(),
            2,
        )

    def test_ti_can_reapply_reject_all_on_already_rejected_requisition(self):
        requisition = Requisition.objects.create(
            title='Compra antiga nao aprovada',
            kind=Requisition.Kind.FISICA,
            request_text='Corrigir orcamentos antigos.',
            requested_by=self.ti_user,
            status=Requisition.Status.NAO_APROVADA,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor antigo',
            title='Item antigo',
            amount='180.00',
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_requisicoes_reject_all_budgets', args=[requisition.id]))

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        budget.refresh_from_db()
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.NAO_APROVADA)
        self.assertEqual(budget.approval_status, RequisitionBudget.ApprovalStatus.NAO_APROVADO)

    def test_requisition_payload_includes_reject_all_url(self):
        requisition = Requisition.objects.create(
            title='Compra aguardando decisao',
            kind=Requisition.Kind.FISICA,
            request_text='Validar botao nao aprovado.',
            requested_by=self.ti_user,
            status=Requisition.Status.PENDENTE_APROVACAO,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor pendente',
            title='Switch',
            amount='700.00',
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        payload = response.context['requisitions_payload'][0]
        self.assertEqual(
            payload['reject_all_url'],
            reverse('chamados_requisicoes_reject_all_budgets', args=[requisition.id]),
        )
        self.assertContains(response, 'requisitionRejectAllForm')

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

    def test_ti_can_disapprove_specific_requisition_budget(self):
        requisition = Requisition.objects.create(
            title='Compra de monitor aprovado',
            kind=Requisition.Kind.FISICA,
            request_text='Compra aprovada por engano.',
            status=Requisition.Status.APROVADA,
            requested_by=self.ti_user,
            approved_at=date(2026, 4, 1),
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor A',
            title='Monitor 24',
            amount='900.00',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(reverse('chamados_requisicoes_budget_disapprove', args=[budget.id]))

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        budget.refresh_from_db()
        requisition.refresh_from_db()
        self.assertEqual(budget.approval_status, RequisitionBudget.ApprovalStatus.NAO_APROVADO)
        self.assertEqual(requisition.status, Requisition.Status.PENDENTE_APROVACAO)
        self.assertIsNone(requisition.approved_at)
        self.assertTrue(
            RequisitionBudgetHistory.objects.filter(
                budget=budget,
                message__icontains='Orcamento desaprovado diretamente pela visualizacao',
            ).exists()
        )

    def test_requisition_payload_marks_approved_budget_as_disapprovable(self):
        requisition = Requisition.objects.create(
            title='Compra com botao desaprovar',
            kind=Requisition.Kind.FISICA,
            request_text='Validar botao.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor B',
            title='Notebook',
            amount='3500.00',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        payload = response.context['requisitions_payload'][0]['budgets'][0]
        self.assertFalse(payload['can_approve'])
        self.assertTrue(payload['can_disapprove'])
        self.assertEqual(payload['disapprove_url'], reverse('chamados_requisicoes_budget_disapprove', args=[budget.id]))

    def test_disapprove_button_does_not_skip_budget_attachment_rendering(self):
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'chamados' / 'requisicoes.html'
        content = template_path.read_text(encoding='utf-8')
        disapprove_index = content.index('Desaprovar orçamento')
        evidence_index = content.index('if (budget.evidence_url && budget.evidence_is_image)', disapprove_index)
        self.assertNotIn('return item;', content[disapprove_index:evidence_index])

    def test_rejected_budget_summary_strikes_budget_title(self):
        requisition = Requisition.objects.create(
            title='Compra rejeitada',
            kind=Requisition.Kind.FISICA,
            request_text='Validar destaque visual.',
            requested_by=self.ti_user,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor rejeitado',
            title='Item recusado',
            amount='120.00',
            approval_status=RequisitionBudget.ApprovalStatus.NAO_APROVADO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        self.assertContains(response, 'requisition-budget-chip rejected')
        self.assertContains(response, '<span class="requisition-budget-chip-title">Fornecedor rejeitado</span>', html=True)
        css_path = Path(__file__).resolve().parents[1] / 'static' / 'css' / 'login.css'
        css_content = css_path.read_text(encoding='utf-8')
        self.assertIn('.requisition-budget-chip.rejected .requisition-budget-chip-title', css_content)
        self.assertIn('text-decoration: line-through;', css_content)

    def test_requisicoes_page_orders_newest_requisitions_first(self):
        older_requisition = Requisition.objects.create(
            title='Requisicao antiga',
            kind=Requisition.Kind.FISICA,
            request_text='Criada antes.',
            requested_by=self.ti_user,
        )
        newer_requisition = Requisition.objects.create(
            title='Requisicao nova',
            kind=Requisition.Kind.FISICA,
            request_text='Criada depois.',
            requested_by=self.ti_user,
        )
        now = timezone.now()
        Requisition.objects.filter(pk=older_requisition.pk).update(created_at=now - timedelta(days=2))
        Requisition.objects.filter(pk=newer_requisition.pk).update(created_at=now - timedelta(days=1))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        titles = [row['requisition'].title for row in response.context['requisition_rows']]
        self.assertEqual(titles, ['Requisicao nova', 'Requisicao antiga'])

    def test_requisicoes_page_shows_approval_date_in_list(self):
        Requisition.objects.create(
            title='Compra aprovada com data',
            kind=Requisition.Kind.FISICA,
            request_text='Mostrar data na listagem.',
            requested_by=self.ti_user,
            status=Requisition.Status.APROVADA,
            approved_at=date(2026, 4, 27),
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        self.assertContains(response, 'Aprovada em 27/04/2026')

    def test_requisicoes_page_reconciles_old_pending_status_when_budget_is_approved(self):
        requisition = Requisition.objects.create(
            title='Orcamento legado aprovado',
            kind=Requisition.Kind.FISICA,
            request_text='Registro antigo antes da sincronizacao automatica.',
            requested_by=self.ti_user,
            status=Requisition.Status.PENDENTE_APROVACAO,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor legado',
            title='Bateria de nobreak',
            amount='564.50',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_requisicoes'))

        self.assertEqual(response.status_code, 200)
        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertContains(response, 'Aprovada')

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
        self.assertContains(response, 'Copiar relatório do mês')
        self.assertContains(response, '<span class="requisition-budget-chip-title">Fornecedor C</span>', html=True)
        self.assertContains(response, 'R$ 1.930,00')
        self.assertContains(response, 'Pendente')
        self.assertNotContains(response, 'https://wa.me/')
        share_text = response.context['requisition_share_map'][str(requisition.id)]
        self.assertIn('------------------------------', share_text)
        self.assertIn('Orçamento 1', share_text)
        self.assertIn('Valor final: R$ 1.930,00', share_text)
        self.assertNotIn('Total geral', share_text)
        self.assertNotIn('Aprovação:', share_text)

    def test_monthly_requisition_copy_uses_only_approved_budgets(self):
        april_requisition = Requisition.objects.create(
            title='Compra de bateria',
            kind=Requisition.Kind.FISICA,
            request_text='Baterias para nobreak.',
            requested_by=self.ti_user,
            requested_at=date(2026, 4, 12),
        )
        RequisitionBudget.objects.create(
            requisition=april_requisition,
            store_name='Pinha',
            title='Bateria 12V',
            amount='500.00',
            quantity=2,
            freight_amount='25.00',
            discount_amount='15.00',
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
        )
        RequisitionBudget.objects.create(
            requisition=april_requisition,
            store_name='Gaspar',
            title='Bateria pendente',
            amount='800.00',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
        )
        may_requisition = Requisition.objects.create(
            title='Compra de memoria',
            kind=Requisition.Kind.FISICA,
            request_text='Memoria para desktop.',
            requested_by=self.ti_user,
            requested_at=date(2026, 5, 5),
        )
        RequisitionBudget.objects.create(
            requisition=may_requisition,
            store_name='Loja Maio',
            title='Memoria',
            amount='300.00',
            quantity=1,
            approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(
            reverse('chamados_requisicoes_monthly_copy'),
            data={'month': '2026-04'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['total_display'], '1.010,00')
        self.assertEqual(payload['requisition_count'], 1)
        self.assertEqual(payload['approved_budget_count'], 1)
        self.assertEqual(payload['completed_service_count'], 0)
        self.assertEqual(payload['contract_count'], 0)
        self.assertIn('Requisições aprovadas - 04/2026', payload['text'])
        self.assertIn('REQ-', payload['text'])
        self.assertIn('Pinha', payload['text'])
        self.assertIn('Valor final: R$ 1.010,00', payload['text'])
        self.assertIn('Total geral do mês: R$ 1.010,00', payload['text'])
        self.assertNotIn('Descrição:', payload['text'])
        self.assertNotIn('Baterias para nobreak.', payload['text'])
        self.assertIn('Resumo mensal TI - 04/2026', payload['html'])
        self.assertIn('Orçamentos aprovados', payload['html'])
        self.assertIn('Total geral', payload['html'])
        self.assertIn('R$ 1.010,00', payload['html'])
        self.assertNotIn('Gaspar', payload['text'])
        self.assertNotIn('Loja Maio', payload['text'])

    def test_monthly_requisition_copy_includes_services_and_contracts(self):
        CompletedServiceEntry.objects.create(
            service_name='Manutencao nobreak',
            company='Energia Segura',
            description='Servico executado.',
            service_date=date(2026, 4, 15),
            amount='250.00',
            created_by=self.ti_user,
        )
        CompletedServiceEntry.objects.create(
            service_name='Servico fora do mes',
            company='Outra empresa',
            description='Nao deve entrar.',
            service_date=date(2026, 5, 1),
            amount='999.00',
            created_by=self.ti_user,
        )
        ContractEntry.objects.create(
            name='Contrato mensal ativo',
            notes='',
            amount='100.00',
            contract_start=date(2026, 3, 1),
            contract_end=date(2026, 5, 31),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL,
            created_by=self.ti_user,
        )
        ContractEntry.objects.create(
            name='Contrato pagamento unico',
            notes='',
            amount='300.00',
            contract_start=date(2026, 4, 10),
            contract_end=date(2026, 4, 10),
            payment_method='Pix',
            payment_schedule=ContractEntry.PaymentSchedule.PAGAMENTO_UNICO,
            created_by=self.ti_user,
        )
        ContractEntry.objects.create(
            name='Contrato anual',
            notes='',
            amount='1200.00',
            contract_start=date(2025, 4, 20),
            contract_end=date(2027, 4, 20),
            payment_method='Cartao',
            card_final='1234',
            payment_schedule=ContractEntry.PaymentSchedule.ANUAL,
            created_by=self.ti_user,
        )
        ContractEntry.objects.create(
            name='Contrato mensal fora',
            notes='',
            amount='777.00',
            contract_start=date(2026, 5, 1),
            contract_end=date(2026, 6, 1),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL,
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(
            reverse('chamados_requisicoes_monthly_copy'),
            data={'month': '2026-04'},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['completed_service_count'], 1)
        self.assertEqual(payload['contract_count'], 1)
        self.assertEqual(payload['total_display'], '550,00')
        self.assertIn('Serviços feitos no mês', payload['text'])
        self.assertIn('Manutencao nobreak', payload['text'])
        self.assertIn('Contratos do mês', payload['text'])
        self.assertIn('Contrato pagamento unico', payload['text'])
        self.assertIn('Pagamento único', payload['text'])
        self.assertNotIn('Contrato mensal ativo', payload['text'])
        self.assertNotIn('Contrato anual', payload['text'])
        self.assertNotIn('Contrato mensal fora', payload['text'])
        self.assertNotIn('Servico fora do mes', payload['text'])
        self.assertIn('Serviços feitos', payload['html'])
        self.assertIn('Contratos do mês', payload['html'])

    def test_monthly_requisition_copy_requires_valid_month(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(
            reverse('chamados_requisicoes_monthly_copy'),
            data={'month': '04/2026'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

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

    def test_requisition_total_includes_budget_freight_amounts(self):
        requisition = Requisition.objects.create(
            title='Compra com frete',
            kind=Requisition.Kind.FISICA,
            request_text='Entrega para filial.',
            requested_by=self.ti_user,
        )
        RequisitionBudget.objects.create(
            requisition=requisition,
            store_name='Fornecedor frete',
            title='Item principal',
            amount='1000.00',
            quantity=2,
            freight_amount='150.50',
        )

        self.assertEqual(requisition.budget_total, Decimal('2150.50'))

    def test_requisition_save_accepts_brazilian_budget_freight_amount(self):
        self.client.login(username='usuario.ti', password='senha@123')
        payload = json.dumps(
            [
                {
                    'id': '',
                    'temp_key': 'tmp_root_freight',
                    'parent_ref': '',
                    'store_name': 'Fornecedor Y',
                    'title': 'Switch',
                    'amount': '1200.00',
                    'quantity': '1',
                    'freight_amount': '1.250,40',
                    'discount_amount': '0',
                    'approval_status': RequisitionBudget.ApprovalStatus.PENDENTE,
                    'receipt_status': RequisitionBudget.ReceiptStatus.PENDENTE,
                    'received_quantity': '0',
                    'notes': '',
                    'file_key': 'budget_file_tmp_root_freight',
                    'clear_file': False,
                }
            ]
        )

        response = self.client.post(
            reverse('chamados_requisicoes_save'),
            data={
                'title': 'Compra com frete brasileiro',
                'kind': Requisition.Kind.FISICA,
                'request_text': 'Teste de frete.',
                'budgets_payload': payload,
            },
        )

        self.assertRedirects(response, reverse('chamados_requisicoes'))
        requisition = Requisition.objects.get(title='Compra com frete brasileiro')
        budget = requisition.budgets.get()
        self.assertEqual(str(budget.freight_amount), '1250.40')
        self.assertEqual(requisition.budget_total, Decimal('2450.40'))

    def test_sync_legacy_requisition_statuses_promotes_imported_requisition(self):
        requisition = Requisition.objects.create(
            code='LEG-REQ-00007',
            title='Requisicao importada',
            kind=Requisition.Kind.FISICA,
            request_text='[ERP-TI-REQ-ID:7]',
            status=Requisition.Status.PENDENTE_APROVACAO,
            requested_by=self.ti_user,
        )

        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / 'legacy.sqlite3'
            connection = sqlite3.connect(legacy_path)
            connection.execute(
                """
                CREATE TABLE core_requisition (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    approved_at TEXT,
                    partially_received_at TEXT,
                    received_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO core_requisition (id, status, approved_at, partially_received_at, received_at)
                VALUES (7, 'approved', '2026-04-01', NULL, NULL)
                """
            )
            connection.commit()
            connection.close()

            call_command('sync_legacy_requisition_statuses', source=str(legacy_path))

        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertEqual(str(requisition.approved_at), '2026-04-01')
        self.assertTrue(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                status_to=Requisition.Status.APROVADA,
                message__icontains='Status sincronizado do legado ERP-TI',
            ).exists()
        )

    def test_sync_legacy_requisition_statuses_does_not_downgrade_imported_requisition(self):
        requisition = Requisition.objects.create(
            code='LEG-REQ-00009',
            title='Requisicao importada entregue',
            kind=Requisition.Kind.FISICA,
            request_text='[ERP-TI-REQ-ID:9]',
            status=Requisition.Status.ENTREGUE,
            requested_by=self.ti_user,
            approved_at=date(2026, 4, 1),
            received_at=date(2026, 4, 5),
        )

        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / 'legacy.sqlite3'
            connection = sqlite3.connect(legacy_path)
            connection.execute(
                """
                CREATE TABLE core_requisition (
                    id INTEGER PRIMARY KEY,
                    status TEXT,
                    approved_at TEXT,
                    partially_received_at TEXT,
                    received_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO core_requisition (id, status, approved_at, partially_received_at, received_at)
                VALUES (9, 'approved', '2026-04-01', NULL, NULL)
                """
            )
            connection.commit()
            connection.close()

            call_command('sync_legacy_requisition_statuses', source=str(legacy_path))

        requisition.refresh_from_db()
        self.assertEqual(requisition.status, Requisition.Status.ENTREGUE)
        self.assertEqual(str(requisition.received_at), '2026-04-05')
        self.assertFalse(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                message__icontains='Status sincronizado do legado ERP-TI',
            ).exists()
        )

    def test_import_erp_ti_data_imports_requisition_quote_quantity(self):
        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / 'legacy.sqlite3'
            connection = sqlite3.connect(legacy_path)
            connection.execute(
                """
                CREATE TABLE core_requisition (
                    id INTEGER PRIMARY KEY,
                    request TEXT,
                    quantity INTEGER,
                    unit_value DECIMAL,
                    total_value DECIMAL,
                    requested_at TEXT,
                    approved_at TEXT,
                    received_at TEXT,
                    invoice TEXT,
                    approved_by_2 TEXT,
                    req_type TEXT,
                    location TEXT,
                    link TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    status TEXT,
                    title TEXT,
                    kind TEXT,
                    delivered_quantity INTEGER,
                    partially_received_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE core_requisitionquote (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    value DECIMAL,
                    photo TEXT,
                    link TEXT,
                    created_at TEXT,
                    requisition_id INTEGER,
                    freight DECIMAL,
                    is_selected BOOL,
                    quantity INTEGER,
                    payment_installments INTEGER,
                    payment_method TEXT,
                    parent_id INTEGER
                )
                """
            )
            connection.execute(
                """
                INSERT INTO core_requisition (
                    id, request, quantity, unit_value, total_value, requested_at, approved_at,
                    received_at, invoice, approved_by_2, req_type, location, link, created_at,
                    updated_at, status, title, kind, delivered_quantity, partially_received_at
                )
                VALUES (
                    77, 'Compra de mouse', 4, 35, 140, '2026-04-01', NULL,
                    NULL, '', '', 'TI', 'Matriz', '', '2026-04-01 08:00:00',
                    '2026-04-01 08:00:00', 'pending_approval', 'Mouses USB', 'physical', 0, NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO core_requisitionquote (
                    id, name, value, photo, link, created_at, requisition_id, freight,
                    is_selected, quantity, payment_installments, payment_method, parent_id
                )
                VALUES (
                    501, 'Mouse Logitech', 33.31, '', '', '2026-04-01 08:00:00', 77, 10.36,
                    1, 4, 1, 'Pix', NULL
                )
                """
            )
            connection.commit()
            connection.close()

            call_command(
                'import_erp_ti_data',
                source=str(legacy_path),
                owner_username='usuario.ti',
            )

        budget = RequisitionBudget.objects.get(notes__contains='[ERP-TI-QUOTE-ID:501]')
        self.assertEqual(budget.quantity, 4)
        self.assertEqual(budget.approval_status, RequisitionBudget.ApprovalStatus.APROVADO)
        self.assertEqual(str(budget.amount), '33.31')
        self.assertEqual(str(budget.freight_amount), '10.36')

    def test_sync_legacy_requisition_quantities_updates_imported_budget(self):
        requisition = Requisition.objects.create(
            code='LEG-REQ-00015',
            title='Legado quantidade',
            kind=Requisition.Kind.FISICA,
            request_text='Quantidade veio incorreta.',
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            title='Windows Server CAL',
            amount='325.00',
            quantity=1,
            notes='Quantidade: 90\n[ERP-TI-QUOTE-ID:900]',
        )

        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / 'legacy.sqlite3'
            connection = sqlite3.connect(legacy_path)
            connection.execute(
                """
                CREATE TABLE core_requisitionquote (
                    id INTEGER PRIMARY KEY,
                    quantity INTEGER
                )
                """
            )
            connection.execute('INSERT INTO core_requisitionquote (id, quantity) VALUES (900, 90)')
            connection.commit()
            connection.close()

            call_command('sync_legacy_requisition_quantities', source=str(legacy_path))

        budget.refresh_from_db()
        self.assertEqual(budget.quantity, 90)

    def test_sync_legacy_requisition_budget_approvals_marks_selected_quote(self):
        requisition = Requisition.objects.create(
            code='LEG-REQ-00016',
            title='Legado aprovado',
            kind=Requisition.Kind.FISICA,
            request_text='Orcamento selecionado no legado.',
            status=Requisition.Status.PENDENTE_APROVACAO,
            requested_by=self.ti_user,
        )
        budget = RequisitionBudget.objects.create(
            requisition=requisition,
            title='Tablet Xiaomi',
            amount='1429.00',
            quantity=9,
            approval_status=RequisitionBudget.ApprovalStatus.PENDENTE,
            notes='Selecionado legado: True\n[ERP-TI-QUOTE-ID:777]',
        )

        with TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / 'legacy.sqlite3'
            connection = sqlite3.connect(legacy_path)
            connection.execute(
                """
                CREATE TABLE core_requisitionquote (
                    id INTEGER PRIMARY KEY,
                    is_selected BOOL
                )
                """
            )
            connection.execute('INSERT INTO core_requisitionquote (id, is_selected) VALUES (777, 1)')
            connection.commit()
            connection.close()

            call_command('sync_legacy_requisition_budget_approvals', source=str(legacy_path))

        budget.refresh_from_db()
        requisition.refresh_from_db()
        self.assertEqual(budget.approval_status, RequisitionBudget.ApprovalStatus.APROVADO)
        self.assertEqual(requisition.status, Requisition.Status.APROVADA)
        self.assertTrue(
            RequisitionUpdate.objects.filter(
                requisition=requisition,
                message__icontains='Orcamento aprovado sincronizado do legado ERP-TI',
            ).exists()
        )

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

    def test_only_ti_can_access_servicos_feitos_page(self):
        self.client.login(username='usuario.comum', password='senha@123')
        response = self.client.get(reverse('chamados_servicos_feitos'))
        self.assertRedirects(response, reverse('chamados_list'))

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_servicos_feitos'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Servicos feitos')

    def test_ti_can_create_servico_feito_with_attachment_and_brazilian_amount(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_servicos_feitos'),
            data={
                'service_name': 'Manutencao nobreak',
                'company': 'Energia Segura Ltda',
                'description': 'Troca de baterias e teste de autonomia.',
                'service_date': '2026-04-15',
                'attachments': ContentFile(b'ordem-servico', name='os_nobreak.pdf'),
                'amount': '1.250,40',
            },
        )

        self.assertRedirects(response, reverse('chamados_servicos_feitos'))
        entry = CompletedServiceEntry.objects.get()
        self.assertEqual(entry.service_name, 'Manutencao nobreak')
        self.assertEqual(entry.company, 'Energia Segura Ltda')
        self.assertEqual(entry.description, 'Troca de baterias e teste de autonomia.')
        self.assertEqual(entry.service_date, date(2026, 4, 15))
        self.assertEqual(str(entry.amount), '1250.40')
        attachment = entry.attachments.get()
        self.assertTrue(attachment.file.name.endswith('.pdf'))
        self.assertEqual(entry.created_by, self.ti_user)

    def test_ti_can_create_servico_feito_with_multiple_attachments(self):
        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_servicos_feitos'),
            data={
                'service_name': 'Instalacao cameras',
                'company': 'Seguranca Total',
                'description': 'Instalacao e validacao.',
                'service_date': '2026-04-16',
                'attachments': [
                    ContentFile(b'nota-fiscal', name='nota.pdf'),
                    ContentFile(b'fotos-servico', name='fotos.zip'),
                ],
                'amount': '850,00',
            },
        )

        self.assertRedirects(response, reverse('chamados_servicos_feitos'))
        entry = CompletedServiceEntry.objects.get(service_name='Instalacao cameras')
        attachments = list(entry.attachments.order_by('id'))
        self.assertEqual(len(attachments), 2)
        self.assertTrue(attachments[0].file.name.endswith('.pdf'))
        self.assertTrue(attachments[1].file.name.endswith('.zip'))

    def test_ti_can_update_servico_feito_service_date(self):
        entry = CompletedServiceEntry.objects.create(
            service_name='Troca de bateria',
            company='Energia Segura',
            description='Troca concluida.',
            service_date=date(2026, 4, 10),
            amount='300.00',
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_servicos_feitos'),
            data={
                'mode': 'update_service_date',
                'entry_id': entry.id,
                'service_date': '2026-04-20',
            },
        )

        self.assertRedirects(response, reverse('chamados_servicos_feitos'))
        entry.refresh_from_db()
        self.assertEqual(entry.service_date, date(2026, 4, 20))

    def test_servicos_feitos_page_displays_amount_in_brazilian_format(self):
        CompletedServiceEntry.objects.create(
            service_name='Cabeamento rack',
            company='Infra Redes',
            description='Organizacao e identificacao.',
            service_date=date(2026, 4, 17),
            amount='2499.90',
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_servicos_feitos'))

        self.assertContains(response, 'R$ 2.499,90')
        self.assertContains(response, '2026-04-17')

    def test_servicos_feitos_page_lists_multiple_attachments(self):
        entry = CompletedServiceEntry.objects.create(
            service_name='Backup servidor',
            company='Infra Redes',
            description='Backup completo.',
            service_date=date(2026, 4, 18),
            amount='500.00',
            created_by=self.ti_user,
        )
        CompletedServiceAttachment.objects.create(
            service=entry,
            file=ContentFile(b'relatorio', name='relatorio.pdf'),
        )
        CompletedServiceAttachment.objects.create(
            service=entry,
            file=ContentFile(b'evidencia', name='evidencia.png'),
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.get(reverse('chamados_servicos_feitos'))

        self.assertContains(response, 'Abrir 1')
        self.assertContains(response, 'Abrir 2')

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

    def test_ti_can_edit_existing_contract_data(self):
        contrato = ContractEntry.objects.create(
            name='Contrato antigo',
            notes='Dados antigos.',
            amount='350.00',
            contract_start=date(2026, 1, 1),
            contract_end=date(2026, 12, 31),
            payment_method='Boleto',
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL,
            created_by=self.ti_user,
        )

        self.client.login(username='usuario.ti', password='senha@123')
        response = self.client.post(
            reverse('chamados_contratos'),
            data={
                'mode': 'update_contract',
                'contract_id': contrato.id,
                'name': 'Contrato atualizado',
                'notes': 'Dados novos.',
                'amount': '1.200,50',
                'contract_start': '2026-04-01',
                'contract_end': '2027-03-31',
                'payment_method': 'Pix',
                'card_final': '',
                'payment_schedule': ContractEntry.PaymentSchedule.ANUAL,
            },
        )

        self.assertRedirects(response, reverse('chamados_contratos'))
        contrato.refresh_from_db()
        self.assertEqual(contrato.name, 'Contrato atualizado')
        self.assertEqual(contrato.notes, 'Dados novos.')
        self.assertEqual(str(contrato.amount), '1200.50')
        self.assertEqual(contrato.contract_start, date(2026, 4, 1))
        self.assertEqual(contrato.contract_end, date(2027, 3, 31))
        self.assertEqual(contrato.payment_method, 'Pix')
        self.assertEqual(contrato.payment_schedule, ContractEntry.PaymentSchedule.ANUAL)

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
        self.assertContains(response, 'tip-title-highlight')

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
