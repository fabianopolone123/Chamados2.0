from datetime import date, datetime
import csv
import io
import re
import logging
import unicodedata
import uuid
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.utils.html import escape
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView
from decimal import Decimal, InvalidOperation
import json

from users.access import is_ti_user

from . import whatsapp
from .excel_export import export_attendant_logs_to_uploaded_workbook
from .forms import (
    ContractAttachmentForm,
    CompletedServiceEntryForm,
    ContractEntryForm,
    DocumentEntryForm,
    EquipmentLoanAttendantSignatureForm,
    EquipmentLoanForm,
    EquipmentLoanItemForm,
    EquipmentLoanPhotoForm,
    EquipmentLoanSignedDocumentForm,
    EquipmentLoanStoredSignatureForm,
    EquipmentLoanUpdateForm,
    FuturaDigitalEntryForm,
    GoogleWorkspaceEmailImportForm,
    ManualClosedTicketForm,
    NetworkDeviceForm,
    PhoneExtensionForm,
    RequisitionForm,
    RequisitionStatusForm,
    resolve_failure_type_value,
    StarlinkEditForm,
    StarlinkForm,
    TicketCreateForm,
    TicketPendingForm,
    ticket_failure_type_choices,
    TipEntryForm,
)
from .models import (
    ContractEntry,
    ContractAttachment,
    CompletedServiceAttachment,
    CompletedServiceEntry,
    DocumentEntry,
    EquipmentLoan,
    EquipmentLoanAttendantSignature,
    EquipmentLoanItem,
    EquipmentLoanPhoto,
    FuturaDigitalEntry,
    GoogleWorkspaceEmail,
    HiddenTicketFailureType,
    Insumo,
    NetworkDevice,
    PhoneExtension,
    Requisition,
    RequisitionBudget,
    RequisitionBudgetAttachment,
    RequisitionBudgetHistory,
    RequisitionUpdate,
    Starlink,
    TicketAutoPauseReview,
    Ticket,
    TicketAttendance,
    TicketFailureType,
    TicketPending,
    TicketUpdate,
    TipEntry,
)
from .pdf_terms import (
    build_equipment_loan_pdf,
    build_equipment_return_pdf,
    equipment_loan_term_filename,
)


def _safe_next_url(request):
    candidate = (request.POST.get('next') or '').strip()
    if candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return reverse('chamados_list')


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def _attendance_rows(ticket: Ticket):
    prefetched = getattr(ticket, '_prefetched_objects_cache', {})
    if 'attendances' in prefetched:
        return list(prefetched['attendances'])
    return list(ticket.attendances.all())


_LEGACY_LINE_PATTERNS = (
    re.compile(r'^\[ERP-TI-ID:\d+\]\s*$', re.IGNORECASE),
    re.compile(r'^\[ERP-TI-EVENT:\d+\]\s*$', re.IGNORECASE),
    re.compile(r'^Tipo legado:.*$', re.IGNORECASE),
    re.compile(r'^Falha legado:.*$', re.IGNORECASE),
    re.compile(r'^Evento legado .*$', re.IGNORECASE),
)


def _clean_legacy_text(raw_value: str) -> str:
    lines = []
    for line in str(raw_value or '').splitlines():
        stripped = line.strip()
        if any(pattern.match(stripped) for pattern in _LEGACY_LINE_PATTERNS):
            continue
        lines.append(line.rstrip())

    cleaned = '\n'.join(lines)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def _can_ti_handle_ticket(user, ticket: Ticket) -> bool:
    attendance_rows = _attendance_rows(ticket)
    has_any_attendance = bool(attendance_rows)
    if not has_any_attendance:
        return True
    return any(row.attendant_id == user.id for row in attendance_rows)


def _can_view_ticket(user, ticket: Ticket, consult_mode: bool = False) -> bool:
    if is_ti_user(user):
        if ticket.status == Ticket.Status.FECHADO:
            return True
        if consult_mode:
            return True
        return _can_ti_handle_ticket(user, ticket)
    return ticket.created_by_id == getattr(user, 'id', None)


def _can_delete_ticket(user, ticket: Ticket) -> bool:
    _ = ticket
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and getattr(user, 'username', '') == 'fabiano.polone'
    )


def _can_delete_tip(user, tip: TipEntry) -> bool:
    _ = tip
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and getattr(user, 'username', '') == 'fabiano.polone'
    )


def _normalize_failure_type_key(value: str) -> str:
    return unicodedata.normalize('NFKD', (value or '').strip().lower()).encode('ascii', 'ignore').decode('ascii')


def _failure_type_management_rows():
    hidden_keys = set(HiddenTicketFailureType.objects.values_list('normalized_name', flat=True))
    rows = []
    seen_keys = set()
    for value, label in Ticket.FailureType.choices:
        normalized_values = {_normalize_failure_type_key(value), _normalize_failure_type_key(label)}
        if hidden_keys.intersection(normalized_values):
            continue
        seen_keys.update(normalized_values)
        rows.append(
            {
                'id': '',
                'value': value,
                'name': label,
                'usage_count': Ticket.objects.filter(failure_type=value).count(),
                'is_builtin': True,
            }
        )

    for item in TicketFailureType.objects.order_by('name'):
        normalized = _normalize_failure_type_key(item.name)
        if normalized in hidden_keys or normalized in seen_keys:
            continue
        rows.append(
            {
                'id': item.id,
                'value': item.name,
                'name': item.name,
                'usage_count': Ticket.objects.filter(failure_type=item.name).count(),
                'is_builtin': False,
            }
        )
    return rows


def _get_visible_tickets_for_ti(user):
    attendance_qs = TicketAttendance.objects.select_related('attendant').order_by('-started_at', '-id')
    any_attendance_qs = TicketAttendance.objects.filter(
        ticket_id=OuterRef('pk'),
    )
    my_attendance_qs = TicketAttendance.objects.filter(
        ticket_id=OuterRef('pk'),
        attendant=user,
    )
    return (
        Ticket.objects.select_related('created_by')
        .prefetch_related(Prefetch('attendances', queryset=attendance_qs))
        .annotate(
            has_any_attendance=Exists(any_attendance_qs),
            has_my_attendance=Exists(my_attendance_qs),
        )
        .filter(Q(has_any_attendance=False) | Q(has_my_attendance=True))
        .exclude(status=Ticket.Status.FECHADO)
        .distinct()
    )


def _get_ti_attendants():
    User = get_user_model()
    group_name = (getattr(settings, 'TI_GROUP_NAME', 'TI') or 'TI').strip()
    return (
        User.objects.filter(is_active=True, is_superuser=False)
        .filter(groups__name__iexact=group_name)
        .distinct()
        .order_by('first_name', 'username')
    )


def _mark_ticket_creator_ti(tickets):
    ticket_list = list(tickets)
    creator_ids = {ticket.created_by_id for ticket in ticket_list if ticket.created_by_id}
    group_name = (getattr(settings, 'TI_GROUP_NAME', 'TI') or 'TI').strip()
    User = get_user_model()
    ti_creator_ids = set(
        User.objects.filter(id__in=creator_ids, groups__name__iexact=group_name)
        .distinct()
        .values_list('id', flat=True)
    )
    for ticket in ticket_list:
        ticket.created_by_is_ti = ticket.created_by_id in ti_creator_ids
        has_any_attendance = getattr(ticket, 'has_any_attendance', None)
        if has_any_attendance is None:
            has_any_attendance = bool(_attendance_rows(ticket))
        ticket.needs_first_ti_attention = not ticket.created_by_is_ti and not has_any_attendance
    return ticket_list


def _build_timer_meta(ticket: Ticket, user):
    now = timezone.now()
    my_attendances = [row for row in _attendance_rows(ticket) if row.attendant_id == user.id]
    running = next((row for row in my_attendances if row.ended_at is None), None)
    total_seconds = 0
    for row in my_attendances:
        end_time = row.ended_at or now
        total_seconds += max(int((end_time - row.started_at).total_seconds()), 0)
    return {
        'has_history': bool(my_attendances),
        'running': running is not None,
        'running_started_at': running.started_at if running else None,
        'total_seconds': total_seconds,
        'total_label': _format_duration(total_seconds),
    }


def _current_attendant(ticket: Ticket):
    running = next((row for row in _attendance_rows(ticket) if row.ended_at is None), None)
    return running.attendant if running else None


def _last_attendant(ticket: Ticket):
    rows = _attendance_rows(ticket)
    return rows[0].attendant if rows else None


def _claim_ticket_for_attendant(ticket: Ticket, attendant, now):
    running_rows = [row for row in _attendance_rows(ticket) if row.ended_at is None]
    running_mine = next((row for row in running_rows if row.attendant_id == attendant.id), None)
    if running_mine:
        return False, 'Este chamado ja esta em atendimento com voce.'

    previous_attendants = []
    for row in running_rows:
        previous_attendants.append(row.attendant.username)
        row.ended_at = now
        row.end_action = TicketAttendance.EndAction.PAUSE
        row.note = f'Transferido para {attendant.username}.'
        row.save(update_fields=['ended_at', 'end_action', 'note'])

    TicketAttendance.objects.create(
        ticket=ticket,
        attendant=attendant,
        started_at=now,
    )
    ticket.status = Ticket.Status.EM_ATENDIMENTO
    ticket.closed_at = None
    ticket.save(update_fields=['status', 'closed_at', 'updated_at'])

    if previous_attendants:
        source_label = ', '.join(dict.fromkeys(previous_attendants))
        message = f'Chamado puxado de {source_label} para {attendant.username}.'
    else:
        message = f'Chamado puxado para {attendant.username}.'
    TicketUpdate.objects.create(
        ticket=ticket,
        author=attendant,
        message=message,
        status_to=ticket.status,
    )
    return True, message


def _auto_pause_reviews_qs(user):
    return (
        TicketAutoPauseReview.objects.select_related(
            'attendance',
            'attendance__ticket',
            'attendance__ticket__created_by',
            'attendance__attendant',
        )
        .filter(attendance__attendant=user, completed_at__isnull=True)
        .order_by('-created_at', '-id')
    )


class TiRequiredMixin(LoginRequiredMixin):
    ti_error_message = 'Somente usuarios TI podem acessar este recurso.'
    ti_redirect_name = 'chamados_list'

    def dispatch(self, request, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, self.ti_error_message)
            return redirect(self.ti_redirect_name)
        return super().dispatch(request, *args, **kwargs)


def _sync_requisition_timeline_dates(requisition: Requisition):
    today = timezone.localdate()
    update_fields = []

    if requisition.requested_at is None:
        requisition.requested_at = today
        update_fields.append('requested_at')

    if requisition.status == Requisition.Status.PENDENTE_APROVACAO:
        if requisition.approved_at is not None:
            requisition.approved_at = None
            update_fields.append('approved_at')
        if requisition.partially_received_at is not None:
            requisition.partially_received_at = None
            update_fields.append('partially_received_at')
        if requisition.received_at is not None:
            requisition.received_at = None
            update_fields.append('received_at')
    elif requisition.status == Requisition.Status.APROVADA:
        if requisition.approved_at is None:
            requisition.approved_at = today
            update_fields.append('approved_at')
        if requisition.partially_received_at is not None:
            requisition.partially_received_at = None
            update_fields.append('partially_received_at')
        if requisition.received_at is not None:
            requisition.received_at = None
            update_fields.append('received_at')
    elif requisition.status == Requisition.Status.NAO_APROVADA:
        if requisition.approved_at is not None:
            requisition.approved_at = None
            update_fields.append('approved_at')
        if requisition.partially_received_at is not None:
            requisition.partially_received_at = None
            update_fields.append('partially_received_at')
        if requisition.received_at is not None:
            requisition.received_at = None
            update_fields.append('received_at')
    elif requisition.status == Requisition.Status.PARCIALMENTE_ENTREGUE:
        if requisition.approved_at is None:
            requisition.approved_at = today
            update_fields.append('approved_at')
        if requisition.partially_received_at is None:
            requisition.partially_received_at = today
            update_fields.append('partially_received_at')
        if requisition.received_at is not None:
            requisition.received_at = None
            update_fields.append('received_at')
    elif requisition.status == Requisition.Status.ENTREGUE:
        if requisition.approved_at is None:
            requisition.approved_at = today
            update_fields.append('approved_at')
        if requisition.received_at is None:
            requisition.received_at = today
            update_fields.append('received_at')

    if update_fields:
        requisition.save(update_fields=update_fields + ['updated_at'])


def _format_date_br(value):
    if not value:
        return ''
    return value.strftime('%d/%m/%Y')


def _sync_requisition_status_from_budgets(requisition: Requisition, author=None):
    budgets = list(requisition.budgets.all())
    if not budgets:
        return False

    approved_exists = any(
        budget.approval_status == RequisitionBudget.ApprovalStatus.APROVADO
        for budget in budgets
    )
    if not approved_exists:
        return False

    if requisition.status in {
        Requisition.Status.APROVADA,
        Requisition.Status.PARCIALMENTE_ENTREGUE,
        Requisition.Status.ENTREGUE,
    }:
        return False

    requisition.status = Requisition.Status.APROVADA
    requisition.save(update_fields=['status', 'updated_at'])
    _sync_requisition_timeline_dates(requisition)
    if author is not None:
        RequisitionUpdate.objects.create(
            requisition=requisition,
            author=author,
            message='Requisicao aprovada automaticamente porque ao menos um orcamento foi marcado como aprovado.',
            status_to=requisition.status,
        )
    return True


def _budget_store_key(budget: RequisitionBudget) -> str:
    return (budget.store_name or '').strip().casefold()


def _budget_children_map_and_roots(budgets):
    children_map = {}
    root_budgets = []
    for budget in budgets:
        if budget.parent_budget_id:
            children_map.setdefault(budget.parent_budget_id, []).append(budget)
        else:
            root_budgets.append(budget)
    return children_map, root_budgets


def _collect_budget_approval_group(budget: RequisitionBudget, budgets=None):
    all_budgets = list(budgets) if budgets is not None else list(budget.requisition.budgets.all())
    children_map, root_budgets = _budget_children_map_and_roots(all_budgets)
    by_id = {item.id: item for item in all_budgets}
    selected = by_id.get(budget.id, budget)
    group_ids = {selected.id}

    def add_descendants(parent_id):
        for child in children_map.get(parent_id, []):
            if child.id in group_ids:
                continue
            group_ids.add(child.id)
            add_descendants(child.id)

    add_descendants(selected.id)

    store_key = _budget_store_key(selected)
    if selected.parent_budget_id is None and store_key:
        same_store_roots = [
            item for item in root_budgets
            if _budget_store_key(item) == store_key
        ]
        if len(same_store_roots) > 1:
            primary = max(
                same_store_roots,
                key=lambda item: (item.final_total, -(item.id or 0)),
            )
            if primary.id == selected.id:
                for sibling in same_store_roots:
                    if sibling.id in group_ids:
                        continue
                    group_ids.add(sibling.id)
                    add_descendants(sibling.id)

    return [
        item for item in all_budgets
        if item.id in group_ids
    ]


def _approve_budget_group(budget: RequisitionBudget, author=None, reason='Orcamento aprovado diretamente pela visualizacao.'):
    group = _collect_budget_approval_group(budget)
    changed = []
    for item in group:
        if item.approval_status == RequisitionBudget.ApprovalStatus.APROVADO:
            continue
        item.approval_status = RequisitionBudget.ApprovalStatus.APROVADO
        item.save(update_fields=['approval_status', 'updated_at'])
        changed.append(item)
        if author is not None:
            _create_budget_history_entry(
                item,
                author,
                f'{reason} {_format_budget_value_summary(item.amount, item.quantity, item.freight_amount, item.discount_amount, item.final_total, item.currency)}',
            )
    return changed


def _disapprove_budget_group(budget: RequisitionBudget, author=None, reason='Orcamento desaprovado diretamente pela visualizacao.'):
    group = _collect_budget_approval_group(budget)
    changed = []
    for item in group:
        if item.approval_status == RequisitionBudget.ApprovalStatus.NAO_APROVADO:
            continue
        item.approval_status = RequisitionBudget.ApprovalStatus.NAO_APROVADO
        item.save(update_fields=['approval_status', 'updated_at'])
        changed.append(item)
        if author is not None:
            _create_budget_history_entry(
                item,
                author,
                f'{reason} {_format_budget_value_summary(item.amount, item.quantity, item.freight_amount, item.discount_amount, item.final_total, item.currency)}',
            )
    return changed


def _sync_approved_budget_groups(requisition: Requisition):
    budgets = list(requisition.budgets.all())
    changed = False
    for budget in budgets:
        if (
            budget.parent_budget_id is None
            and budget.approval_status == RequisitionBudget.ApprovalStatus.APROVADO
        ):
            for item in _collect_budget_approval_group(budget, budgets=budgets):
                if item.approval_status == RequisitionBudget.ApprovalStatus.APROVADO:
                    continue
                item.approval_status = RequisitionBudget.ApprovalStatus.APROVADO
                item.save(update_fields=['approval_status', 'updated_at'])
                changed = True
    return changed


def _sync_requisition_status_after_budget_unapproval(requisition: Requisition, author=None):
    approved_exists = requisition.budgets.filter(
        approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
    ).exists()
    if approved_exists or requisition.status != Requisition.Status.APROVADA:
        return False

    previous_status = requisition.status
    requisition.status = Requisition.Status.PENDENTE_APROVACAO
    requisition.save(update_fields=['status', 'updated_at'])
    _sync_requisition_timeline_dates(requisition)
    if author is not None:
        RequisitionUpdate.objects.create(
            requisition=requisition,
            author=author,
            message=(
                'Requisicao voltou para pendente de aprovacao porque '
                'nao existe mais orcamento aprovado.'
            ),
            status_to=requisition.status,
        )
    return previous_status != requisition.status


def _reject_all_requisition_budgets(requisition: Requisition, author=None):
    budgets = list(requisition.budgets.all())
    changed_count = 0

    for budget in budgets:
        if budget.approval_status == RequisitionBudget.ApprovalStatus.NAO_APROVADO:
            continue
        budget.approval_status = RequisitionBudget.ApprovalStatus.NAO_APROVADO
        budget.save(update_fields=['approval_status', 'updated_at'])
        changed_count += 1
        if author is not None:
            _create_budget_history_entry(
                budget,
                author,
                'Orcamento marcado como nao aprovado junto com a rejeicao da requisicao.',
            )

    previous_status = requisition.status
    requisition.status = Requisition.Status.NAO_APROVADA
    requisition.save(update_fields=['status', 'updated_at'])
    _sync_requisition_timeline_dates(requisition)

    if author is not None:
        if changed_count:
            message = f'Requisicao marcada como nao aprovada. {changed_count} orcamento(s) marcado(s) como nao aprovado(s).'
        else:
            message = 'Requisicao marcada como nao aprovada. Nenhum orcamento pendente para atualizar.'
        RequisitionUpdate.objects.create(
            requisition=requisition,
            author=author,
            message=message,
            status_to=requisition.status,
        )

    return changed_count, previous_status != requisition.status


def _reconcile_requisition_statuses_from_budgets(requisitions):
    reconciled = []
    for requisition in requisitions:
        _sync_approved_budget_groups(requisition)
        _sync_requisition_status_from_budgets(requisition)
        reconciled.append(requisition)
    return reconciled


def _load_requisition_budgets_payload(request):
    raw_payload = (request.POST.get('budgets_payload') or '').strip()
    if not raw_payload:
        return []
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _parse_amount(raw_value):
    normalized = str(raw_value or '').strip().replace('R$', '').replace('US$', '').replace(' ', '')
    if ',' in normalized:
        normalized = normalized.replace('.', '').replace(',', '.')
    if not normalized:
        raise InvalidOperation
    value = Decimal(normalized)
    if value < 0:
        raise InvalidOperation
    return value.quantize(Decimal('0.01'))


def _parse_quantity(raw_value):
    normalized = str(raw_value or '').strip()
    if not normalized:
        return 1
    quantity = int(normalized)
    if quantity < 1:
        raise ValueError
    return quantity


def _parse_optional_amount(raw_value):
    normalized = str(raw_value or '').strip()
    if not normalized:
        return Decimal('0.00')
    return _parse_amount(normalized)


def _parse_received_quantity(raw_value, total_quantity: int):
    normalized = str(raw_value or '').strip()
    if not normalized:
        return 0
    quantity = int(normalized)
    if quantity < 0 or quantity > total_quantity:
        raise ValueError
    return quantity


def _parse_choice(raw_value, choices, default_value):
    normalized = str(raw_value or '').strip() or default_value
    valid_values = {choice[0] for choice in choices}
    if normalized not in valid_values:
        raise ValueError
    return normalized


def _normalize_receipt_progress(receipt_status: str, quantity: int, received_quantity: int):
    if receipt_status == RequisitionBudget.ReceiptStatus.RECEBIDO:
        return receipt_status, quantity
    if receipt_status == RequisitionBudget.ReceiptStatus.PENDENTE:
        return receipt_status, 0
    if quantity <= 1 or received_quantity <= 0 or received_quantity >= quantity:
        raise ValueError
    return receipt_status, received_quantity


def _format_decimal_br(value) -> str:
    normalized = f'{Decimal(value or 0):.2f}'
    integer_part, decimal_part = normalized.split('.')
    integer_part = f'{int(integer_part):,}'.replace(',', '.')
    return f'{integer_part},{decimal_part}'


def _budget_currency_symbol(currency: str) -> str:
    return 'US$' if currency == RequisitionBudget.Currency.USD else 'R$'


def _format_budget_money(value, currency: str) -> str:
    return f'{_budget_currency_symbol(currency)} {_format_decimal_br(value)}'


def _parse_budget_currency(raw_value) -> str:
    normalized = str(raw_value or RequisitionBudget.Currency.BRL).strip().upper()
    valid_currencies = {choice[0] for choice in RequisitionBudget.Currency.choices}
    if normalized not in valid_currencies:
        raise ValueError
    return normalized


def _pt_br_label(value) -> str:
    text = str(value or '')
    replacements = {
        'Requisicao': 'Requisição',
        'Titulo': 'Título',
        'Orcamentos': 'Orçamentos',
        'Aprovacao': 'Aprovação',
        'Descricao': 'Descrição',
        'Observacoes': 'Observações',
        'Fisica': 'Física',
        'Pendente de aprovacao': 'Pendente de aprovação',
        'Parcialmente entregue': 'Parcialmente entregue',
        'Nao aprovado': 'Não aprovado',
        'Nao aprovada': 'Não aprovada',
        'Nao': 'Não',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _format_budget_value_summary(amount, quantity, freight_amount, discount_amount, final_total, currency=RequisitionBudget.Currency.BRL):
    summary = [
        f'Qtd: {quantity}',
        f'Unit.: {_format_budget_money(amount, currency)}',
        f'Total bruto: {_format_budget_money(Decimal(amount or 0) * Decimal(quantity or 0), currency)}',
    ]
    if Decimal(freight_amount or 0):
        summary.append(f'Frete: {_format_budget_money(freight_amount, currency)}')
    if Decimal(discount_amount or 0):
        summary.append(f'Desconto: {_format_budget_money(discount_amount, currency)}')
    summary.append(f'Total final: {_format_budget_money(final_total, currency)}')
    return ' | '.join(summary)


def _create_budget_history_entry(budget: RequisitionBudget, author, message: str):
    RequisitionBudgetHistory.objects.create(
        budget=budget,
        author=author,
        message=message,
        store_name=budget.store_name,
        currency=budget.currency,
        amount=budget.amount,
        quantity=budget.quantity,
        line_total=budget.line_total,
        freight_amount=budget.freight_amount,
        discount_amount=budget.discount_amount,
        final_total=budget.final_total,
        approval_status=budget.approval_status,
        receipt_status=budget.receipt_status,
        received_quantity=budget.received_quantity,
        remaining_quantity=budget.remaining_quantity,
    )


def _is_image_file_name(file_name: str) -> bool:
    lowered = (file_name or '').strip().lower()
    return lowered.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))


def _sync_requisition_budgets(request, requisition: Requisition):
    payload = _load_requisition_budgets_payload(request)
    if payload is None:
        return False, 'Nao foi possivel ler os orcamentos informados.'

    existing = {str(item.id): item for item in requisition.budgets.all()}
    keep_ids = set()
    created_by_temp = {}
    pending_children = []

    def upsert_row(item_data, parent_budget):
        row_id = str(item_data.get('id') or '').strip()
        store_name = (item_data.get('store_name') or '').strip()
        title = (item_data.get('title') or '').strip()
        currency_raw = item_data.get('currency')
        amount_raw = item_data.get('amount')
        quantity_raw = item_data.get('quantity')
        freight_raw = item_data.get('freight_amount')
        discount_raw = item_data.get('discount_amount')
        approval_status_raw = item_data.get('approval_status')
        receipt_status_raw = item_data.get('receipt_status')
        received_quantity_raw = item_data.get('received_quantity')
        notes = (item_data.get('notes') or '').strip()
        clear_file = str(item_data.get('clear_file') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        file_key = (item_data.get('file_key') or '').strip()
        attachment_key = (item_data.get('attachment_key') or '').strip()
        temp_key = (item_data.get('temp_key') or '').strip()

        if not title and not amount_raw:
            return None
        if not title:
            raise ValueError('Informe o titulo de todos os orcamentos.')

        try:
            currency = _parse_budget_currency(currency_raw)
        except ValueError:
            raise ValueError(f'Moeda invalida no orcamento "{title}".')
        try:
            amount = _parse_amount(amount_raw)
        except InvalidOperation:
            raise ValueError(f'Valor invalido no orcamento "{title}".')
        try:
            quantity = _parse_quantity(quantity_raw)
        except ValueError:
            raise ValueError(f'Quantidade invalida no orcamento "{title}".')
        try:
            freight_amount = _parse_optional_amount(freight_raw)
        except InvalidOperation:
            raise ValueError(f'Frete invalido no orcamento "{title}".')
        try:
            discount_amount = _parse_optional_amount(discount_raw)
        except InvalidOperation:
            raise ValueError(f'Desconto invalido no orcamento "{title}".')
        try:
            approval_status = _parse_choice(
                approval_status_raw,
                RequisitionBudget.ApprovalStatus.choices,
                RequisitionBudget.ApprovalStatus.PENDENTE,
            )
        except ValueError:
            raise ValueError(f'Status de aprovacao invalido no orcamento "{title}".')
        try:
            receipt_status = _parse_choice(
                receipt_status_raw,
                RequisitionBudget.ReceiptStatus.choices,
                RequisitionBudget.ReceiptStatus.PENDENTE,
            )
            received_quantity = _parse_received_quantity(received_quantity_raw, quantity)
            receipt_status, received_quantity = _normalize_receipt_progress(
                receipt_status,
                quantity,
                received_quantity,
            )
        except ValueError:
            raise ValueError(f'Recebimento invalido no orcamento "{title}".')

        if row_id and row_id in existing:
            row = existing[row_id]
            previous_snapshot = {
                'store_name': row.store_name,
                'title': row.title,
                'currency': row.currency,
                'amount': row.amount,
                'quantity': row.quantity,
                'freight_amount': row.freight_amount,
                'discount_amount': row.discount_amount,
                'approval_status': row.approval_status,
                'receipt_status': row.receipt_status,
                'received_quantity': row.received_quantity,
                'notes': row.notes,
                'parent_budget_id': row.parent_budget_id,
                'evidence_name': row.evidence_file.name if row.evidence_file else '',
            }
        else:
            row = RequisitionBudget(requisition=requisition)
            previous_snapshot = None

        row.store_name = store_name
        row.title = title
        row.currency = currency
        row.amount = amount
        row.quantity = quantity
        row.freight_amount = freight_amount
        row.discount_amount = discount_amount
        row.approval_status = approval_status
        row.receipt_status = receipt_status
        row.received_quantity = received_quantity
        row.notes = notes
        row.parent_budget = parent_budget

        file_obj = request.FILES.get(file_key) if file_key else None
        attachment_changed = False
        if file_obj:
            row.evidence_file = file_obj
            attachment_changed = True
        elif clear_file and row.pk:
            row.evidence_file = None
            attachment_changed = True

        row.save()
        extra_attachments = request.FILES.getlist(attachment_key) if attachment_key else []
        for attachment in extra_attachments:
            RequisitionBudgetAttachment.objects.create(budget=row, file=attachment)
        if extra_attachments:
            attachment_changed = True
        if previous_snapshot is None:
            _create_budget_history_entry(
                row,
                request.user,
                f'Orcamento cadastrado. {_format_budget_value_summary(row.amount, row.quantity, row.freight_amount, row.discount_amount, row.final_total, row.currency)}',
            )
        else:
            changed_labels = []
            if previous_snapshot['store_name'] != row.store_name or previous_snapshot['title'] != row.title or previous_snapshot['notes'] != row.notes or previous_snapshot['parent_budget_id'] != row.parent_budget_id:
                changed_labels.append('dados gerais')
            if previous_snapshot['currency'] != row.currency or previous_snapshot['amount'] != row.amount or previous_snapshot['quantity'] != row.quantity or previous_snapshot['freight_amount'] != row.freight_amount or previous_snapshot['discount_amount'] != row.discount_amount:
                changed_labels.append('valores')
            if previous_snapshot['approval_status'] != row.approval_status:
                changed_labels.append('aprovacao')
            if previous_snapshot['receipt_status'] != row.receipt_status or previous_snapshot['received_quantity'] != row.received_quantity:
                changed_labels.append('recebimento')
            if attachment_changed or previous_snapshot['evidence_name'] != (row.evidence_file.name if row.evidence_file else ''):
                changed_labels.append('anexo')
            if changed_labels:
                _create_budget_history_entry(
                    row,
                    request.user,
                    f'Orcamento atualizado ({", ".join(changed_labels)}). {_format_budget_value_summary(row.amount, row.quantity, row.freight_amount, row.discount_amount, row.final_total, row.currency)}',
                )
        keep_ids.add(str(row.id))
        if temp_key:
            created_by_temp[temp_key] = row
        return row

    try:
        for item in payload:
            if not isinstance(item, dict):
                continue
            parent_ref = str(item.get('parent_ref') or '').strip()
            if parent_ref:
                pending_children.append(item)
                continue
            upsert_row(item, parent_budget=None)

        for item in pending_children:
            parent_ref = str(item.get('parent_ref') or '').strip()
            parent_budget = None
            if parent_ref.startswith('id:'):
                parent_id = parent_ref[3:]
                parent_budget = existing.get(parent_id)
                if parent_budget is None:
                    parent_budget = RequisitionBudget.objects.filter(
                        requisition=requisition,
                        id=parent_id,
                    ).first()
            elif parent_ref.startswith('tmp:'):
                parent_budget = created_by_temp.get(parent_ref[4:])

            if parent_budget is None:
                return False, 'Nao foi possivel identificar o orcamento pai para um suborcamento.'
            upsert_row(item, parent_budget=parent_budget)
    except ValueError as exc:
        return False, str(exc)

    to_delete_ids = [
        budget_id
        for budget_id in existing.keys()
        if budget_id not in keep_ids
    ]
    if to_delete_ids:
        RequisitionBudget.objects.filter(requisition=requisition, id__in=to_delete_ids).delete()

    return True, ''


def _serialize_budget_line(item: RequisitionBudget, children_map):
    children = children_map.get(item.id, [])
    child_lines = [_serialize_budget_line(child, children_map) for child in children]
    group_total = item.final_total + sum(
        (Decimal(child.get('group_total') or '0.00') for child in child_lines),
        Decimal('0.00'),
    )
    history_entries = list(getattr(item, 'prefetched_history_entries', []))
    if not history_entries:
        prefetched = getattr(item, '_prefetched_objects_cache', {})
        history_entries = list(prefetched.get('history_entries', []))
    evidence_name = item.evidence_file.name if item.evidence_file else ''
    evidence_url = ''
    if item.evidence_file:
        try:
            if item.evidence_file.storage.exists(item.evidence_file.name):
                evidence_url = item.evidence_file.url
        except Exception:
            evidence_url = ''
    attachments = []
    for attachment in item.attachments.all():
        file_name = attachment.file.name if attachment.file else ''
        file_url = ''
        if attachment.file:
            try:
                if attachment.file.storage.exists(attachment.file.name):
                    file_url = attachment.file.url
            except Exception:
                file_url = ''
        if file_url:
            attachments.append(
                {
                    'id': attachment.id,
                    'name': file_name.rsplit('/', 1)[-1] or 'Documento',
                    'url': file_url,
                    'is_image': _is_image_file_name(file_name),
                }
            )
    return {
        'id': item.id,
        'store_name': item.store_name,
        'title': item.title,
        'currency': item.currency,
        'currency_symbol': _budget_currency_symbol(item.currency),
        'amount': str(item.amount),
        'quantity': item.quantity,
        'line_total': str(item.line_total),
        'freight_amount': str(item.freight_amount),
        'discount_amount': str(item.discount_amount),
        'final_total': str(item.final_total),
        'approval_status': item.approval_status,
        'approval_status_display': item.get_approval_status_display(),
        'approve_url': reverse('chamados_requisicoes_budget_approve', args=[item.id]),
        'disapprove_url': reverse('chamados_requisicoes_budget_disapprove', args=[item.id]),
        'can_approve': item.approval_status != RequisitionBudget.ApprovalStatus.APROVADO,
        'can_disapprove': item.approval_status == RequisitionBudget.ApprovalStatus.APROVADO,
        'receipt_status': item.receipt_status,
        'receipt_status_display': item.get_receipt_status_display(),
        'received_quantity': item.received_quantity,
        'remaining_quantity': item.remaining_quantity,
        'line_total_display': _format_decimal_br(item.line_total),
        'freight_amount_display': _format_decimal_br(item.freight_amount),
        'discount_amount_display': _format_decimal_br(item.discount_amount),
        'final_total_display': _format_decimal_br(item.final_total),
        'notes': item.notes,
        'parent_id': item.parent_budget_id,
        'evidence_url': evidence_url,
        'evidence_is_image': _is_image_file_name(evidence_name),
        'attachments': attachments,
        'group_total': str(group_total),
        'group_total_display': _format_decimal_br(group_total),
        'history_entries': [
            {
                'message': entry.message,
                'created_at': timezone.localtime(entry.created_at).strftime('%d/%m/%Y %H:%M'),
                'author': entry.author.username,
                'store_name': entry.store_name,
                'currency': entry.currency,
                'currency_symbol': _budget_currency_symbol(entry.currency),
                'amount_display': _format_decimal_br(entry.amount),
                'quantity': entry.quantity,
                'line_total_display': _format_decimal_br(entry.line_total),
                'freight_amount_display': _format_decimal_br(entry.freight_amount),
                'discount_amount_display': _format_decimal_br(entry.discount_amount),
                'final_total_display': _format_decimal_br(entry.final_total),
                'approval_status_display': entry.get_approval_status_display(),
                'receipt_status_display': entry.get_receipt_status_display(),
                'received_quantity': entry.received_quantity,
                'remaining_quantity': entry.remaining_quantity,
            }
            for entry in history_entries
        ],
        'sub_budgets': child_lines,
    }


def _serialize_budget_summary(item: RequisitionBudget, children_map):
    children = children_map.get(item.id, [])
    return {
        'title': item.title,
        'store_name': item.store_name,
        'quantity': item.quantity,
        'currency': item.currency,
        'currency_symbol': _budget_currency_symbol(item.currency),
        'unit_value_display': _format_decimal_br(item.amount),
        'value_display': _format_decimal_br(item.final_total),
        'approval_status': item.approval_status,
        'approval_status_display': item.get_approval_status_display(),
        'receipt_status_display': item.get_receipt_status_display(),
        'sub_summaries': [_serialize_budget_summary(child, children_map) for child in children],
    }


def _build_budget_summaries_for_list(root_budgets, children_map):
    summary_children = {
        parent_id: list(children)
        for parent_id, children in children_map.items()
    }
    roots_by_store = {}
    for budget in root_budgets:
        store_key = (budget.store_name or '').strip().casefold()
        if store_key:
            roots_by_store.setdefault(store_key, []).append(budget)

    inferred_child_ids = set()
    for same_store_budgets in roots_by_store.values():
        if len(same_store_budgets) < 2:
            continue
        primary = max(
            same_store_budgets,
            key=lambda item: (item.final_total, -(item.id or 0)),
        )
        inferred_children = [
            item for item in same_store_budgets
            if item.id != primary.id
        ]
        if not inferred_children:
            continue
        summary_children.setdefault(primary.id, []).extend(inferred_children)
        inferred_child_ids.update(item.id for item in inferred_children)

    visible_roots = [
        item for item in root_budgets
        if item.id not in inferred_child_ids
    ]
    return [_serialize_budget_summary(item, summary_children) for item in visible_roots]


def _build_budget_lines_for_copy(root_budgets, children_map):
    copy_children = {
        parent_id: list(children)
        for parent_id, children in children_map.items()
    }
    roots_by_store = {}
    for budget in root_budgets:
        store_key = (budget.store_name or '').strip().casefold()
        if store_key:
            roots_by_store.setdefault(store_key, []).append(budget)

    inferred_child_ids = set()
    for same_store_budgets in roots_by_store.values():
        if len(same_store_budgets) < 2:
            continue
        primary = max(
            same_store_budgets,
            key=lambda item: (item.final_total, -(item.id or 0)),
        )
        inferred_children = [
            item for item in same_store_budgets
            if item.id != primary.id
        ]
        if not inferred_children:
            continue
        copy_children.setdefault(primary.id, []).extend(inferred_children)
        inferred_child_ids.update(item.id for item in inferred_children)

    visible_roots = [
        item for item in root_budgets
        if item.id not in inferred_child_ids
    ]
    return [_serialize_budget_line(item, copy_children) for item in visible_roots]


def _build_requisition_rows(requisitions):
    rows = []
    requisitions_payload = []
    for requisition in requisitions:
        budgets = list(requisition.budgets.all())
        children_map = {}
        root_budgets = []
        for budget in budgets:
            if budget.parent_budget_id:
                children_map.setdefault(budget.parent_budget_id, []).append(budget)
            else:
                root_budgets.append(budget)

        root_lines = [_serialize_budget_line(item, children_map) for item in root_budgets]
        copy_budget_lines = _build_budget_lines_for_copy(root_budgets, children_map)
        budgets_total = sum((item.final_total for item in budgets), Decimal('0.00'))
        total = requisition.budget_total
        budget_summaries = _build_budget_summaries_for_list(root_budgets, children_map)
        rows.append(
            {
                'requisition': requisition,
                'root_budgets': root_lines,
                'budgets_total': budgets_total,
                'budgets_total_display': _format_decimal_br(budgets_total),
                'total': total,
                'total_display': _format_decimal_br(total),
                'budget_summaries': budget_summaries,
            }
        )
        requisitions_payload.append(
            {
                'id': requisition.id,
                'code': requisition.code,
                'title': requisition.title,
                'kind': requisition.kind,
                'kind_display': requisition.get_kind_display(),
                'request_text': requisition.request_text,
                'budgets_total': str(budgets_total),
                'budgets_total_display': _format_decimal_br(budgets_total),
                'status': requisition.status,
                'status_display': requisition.get_status_display(),
                'reject_all_url': reverse('chamados_requisicoes_reject_all_budgets', args=[requisition.id]),
                'deliver_url': reverse('chamados_requisicoes_deliver', args=[requisition.id]),
                'can_mark_delivered': requisition.status in {
                    Requisition.Status.APROVADA,
                    Requisition.Status.PARCIALMENTE_ENTREGUE,
                },
                'requested_at_display': _format_date_br(requisition.requested_at),
                'approved_at_display': _format_date_br(requisition.approved_at),
                'partially_received_at_display': _format_date_br(requisition.partially_received_at),
                'received_at_display': _format_date_br(requisition.received_at),
                'requested_by': requisition.requested_by.username,
                'updates': [
                    {
                        'message': update.message,
                        'status_display': update.get_status_to_display() if update.status_to else '',
                        'created_at': timezone.localtime(update.created_at).strftime('%d/%m/%Y %H:%M'),
                        'author': update.author.username,
                    }
                    for update in requisition.updates.all()
                ],
                'budgets': root_lines,
                'copy_budgets': copy_budget_lines,
                'total': str(total),
                'total_display': _format_decimal_br(total),
            }
        )
    return rows, requisitions_payload


def _budget_payload_decimal(value):
    try:
        return Decimal(str(value or '0.00'))
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _budget_payload_final_total(budget):
    return _budget_payload_decimal(
        budget.get('final_total')
        or budget.get('line_total')
        or budget.get('amount')
        or '0.00'
    )


def _budget_payload_group_total(budget):
    return _budget_payload_final_total(budget) + sum(
        (_budget_payload_group_total(sub) for sub in budget.get('sub_budgets') or []),
        Decimal('0.00'),
    )


def _build_requisition_share_text(payload_item):
    code = payload_item.get('code') or 'REQ'
    lines = [
        f'Requisição {code}',
        f'Título: {payload_item.get("title") or "-"}',
        f'Tipo: {payload_item.get("kind_display") or "-"}',
        f'Status: {_pt_br_label(payload_item.get("status_display") or "-")}',
        f'Solicitante: {payload_item.get("requested_by") or "-"}',
        '',
        'Requisição:',
        payload_item.get('request_text') or '-',
    ]

    budgets = payload_item.get('copy_budgets') or payload_item.get('budgets') or []
    if budgets:
        lines.extend(['', 'Orçamentos:'])
        for index, budget in enumerate(budgets, start=1):
            sub_budgets = budget.get('sub_budgets') or []
            main_total = _budget_payload_final_total(budget)
            sub_total = sum((_budget_payload_group_total(sub) for sub in sub_budgets), Decimal('0.00'))
            group_total = main_total + sub_total
            lines.extend(
                [
                    '',
                    '------------------------------',
                    f'Orçamento {index}',
                    '------------------------------',
                    f'Loja: {budget.get("store_name") or "-"}',
                    f'Título: {budget.get("title") or "-"}',
                    f'Quantidade: {budget.get("quantity") or 1}',
                    f'Valor unitário: {_format_budget_money(budget.get("amount") or "0.00", budget.get("currency"))}',
                    f'Frete: {_format_budget_money(budget.get("freight_amount") or "0.00", budget.get("currency"))}',
                    f'Desconto: {_format_budget_money(budget.get("discount_amount") or "0.00", budget.get("currency"))}',
                    f'Valor final: {_format_budget_money(budget.get("final_total") or "0.00", budget.get("currency"))}',
                ]
            )
            if sub_budgets:
                lines.extend(
                    [
                        f'Total orçamento principal: {_format_budget_money(main_total, budget.get("currency"))}',
                        f'Total suborçamentos: {_format_budget_money(sub_total, budget.get("currency"))}',
                        f'Total orçamento + suborçamentos: {_format_budget_money(group_total, budget.get("currency"))}',
                    ]
                )
            attachments = budget.get('attachments') or []
            if attachments:
                lines.append('Documentos adicionais:')
                for attachment_index, attachment in enumerate(attachments, start=1):
                    lines.append(
                        f'Documento {attachment_index}: {attachment.get("url") or attachment.get("name") or "-"}'
                    )
            for sub_index, sub in enumerate(sub_budgets, start=1):
                lines.extend(
                    [
                        '',
                        f'  Suborçamento {index}.{sub_index}',
                        '  ----------------------------',
                        f'  Loja: {sub.get("store_name") or "-"}',
                        f'  Título: {sub.get("title") or "-"}',
                        f'  Quantidade: {sub.get("quantity") or 1}',
                        f'  Valor unitário: {_format_budget_money(sub.get("amount") or "0.00", sub.get("currency"))}',
                        f'  Frete: {_format_budget_money(sub.get("freight_amount") or "0.00", sub.get("currency"))}',
                        f'  Desconto: {_format_budget_money(sub.get("discount_amount") or "0.00", sub.get("currency"))}',
                        f'  Valor final: {_format_budget_money(sub.get("final_total") or "0.00", sub.get("currency"))}',
                    ]
                )
                sub_attachments = sub.get('attachments') or []
                if sub_attachments:
                    lines.append('  Documentos adicionais:')
                    for attachment_index, attachment in enumerate(sub_attachments, start=1):
                        lines.append(
                            f'  Documento {attachment_index}: {attachment.get("url") or attachment.get("name") or "-"}'
                        )
    return '\n'.join(lines)


def _requisition_month_reference(requisition):
    if requisition.requested_at:
        return requisition.requested_at
    return timezone.localtime(requisition.created_at).date()


def _contract_monthly_report_reference(contract, year, month):
    if not contract.amount:
        return None

    contract_start = contract.contract_start
    if contract.payment_schedule != ContractEntry.PaymentSchedule.PAGAMENTO_UNICO:
        return None

    if contract_start and contract_start.year == year and contract_start.month == month:
        return contract_start, 'Pagamento único'
    return None


def _build_monthly_approved_requisitions_payload(year, month):
    requisitions = Requisition.objects.select_related('requested_by').prefetch_related(
        Prefetch(
            'budgets',
            queryset=RequisitionBudget.objects.filter(
                approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
            ).order_by('parent_budget_id', 'id'),
        )
    ).filter(
        Q(requested_at__year=year, requested_at__month=month)
        | Q(requested_at__isnull=True, created_at__year=year, created_at__month=month)
    ).distinct().order_by('requested_at', 'created_at', 'id')

    month_label = f'{month:02d}/{year}'
    lines = [
        f'Requisições aprovadas - {month_label}',
        'Somente orçamentos aprovados',
    ]
    cards_html = []
    grand_total = Decimal('0.00')
    requisition_count = 0
    approved_budget_count = 0

    for requisition in requisitions:
        approved_budgets = list(requisition.budgets.all())
        if not approved_budgets:
            continue

        requisition_count += 1
        reference_date = _requisition_month_reference(requisition)
        requisition_code = escape(requisition.code or 'REQ')
        requisition_title = escape(requisition.title or '-')
        requested_by = escape(requisition.requested_by.username or '-')
        lines.extend(
            [
                '',
                '==================================================',
                f'{requisition.code or "REQ"} - {requisition.title}',
                f'Data: {reference_date:%d/%m/%Y} | Solicitante: {requisition.requested_by.username}',
                '',
                'Orçamentos aprovados:',
            ]
        )
        budget_items_html = []

        for index, budget in enumerate(approved_budgets, start=1):
            approved_budget_count += 1
            grand_total += budget.final_total
            budget_label = 'Suborçamento' if budget.parent_budget_id else 'Orçamento'
            safe_budget_label = escape(budget_label)
            safe_budget_title = escape(budget.title or '-')
            safe_store_name = escape(budget.store_name or '-')
            lines.extend(
                [
                    '',
                    '------------------------------',
                    f'{budget_label} aprovado {index}',
                    '------------------------------',
                    f'Loja: {budget.store_name or "-"}',
                    f'Título: {budget.title or "-"}',
                    f'Quantidade: {budget.quantity or 1}',
                    f'Valor unitário: {_format_budget_money(budget.amount, budget.currency)}',
                    f'Frete: {_format_budget_money(budget.freight_amount, budget.currency)}',
                    f'Desconto: {_format_budget_money(budget.discount_amount, budget.currency)}',
                    f'Valor final: {_format_budget_money(budget.final_total, budget.currency)}',
                ]
            )
            budget_items_html.append(
                f'''
                <div style="margin-top:12px; padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">
                    <p style="margin:0 0 6px; font-size:12px; font-weight:800; color:#2563eb; text-transform:uppercase; letter-spacing:0.04em;">{safe_budget_label} aprovado {index}</p>
                    <h4 style="margin:0 0 8px; font-size:16px; color:#0f172a;">{safe_budget_title}</h4>
                    <p style="margin:0 0 6px; font-size:14px; color:#334155;"><strong>Loja:</strong> {safe_store_name}</p>
                    <p style="margin:0; font-size:14px; line-height:1.65; color:#334155;">
                        <strong>Qtd:</strong> {budget.quantity or 1}
                        &nbsp;|&nbsp; <strong>Unit.:</strong> {_format_budget_money(budget.amount, budget.currency)}
                        &nbsp;|&nbsp; <strong>Frete:</strong> {_format_budget_money(budget.freight_amount, budget.currency)}
                        &nbsp;|&nbsp; <strong>Desconto:</strong> {_format_budget_money(budget.discount_amount, budget.currency)}
                        &nbsp;|&nbsp; <strong>Final:</strong> {_format_budget_money(budget.final_total, budget.currency)}
                    </p>
                </div>
                '''
            )

        cards_html.append(
            f'''
            <div style="margin:0 0 16px; padding:16px 18px; border-radius:18px; background:#ffffff; border:1px solid #dbe4ef; box-shadow:0 8px 24px rgba(15,23,42,0.06);">
                <p style="margin:0 0 6px; font-size:12px; font-weight:800; color:#64748b; text-transform:uppercase; letter-spacing:0.05em;">{reference_date:%d/%m/%Y} | {requested_by}</p>
                <h3 style="margin:0 0 4px; font-size:18px; color:#0f172a;">{requisition_code} - {requisition_title}</h3>
                {''.join(budget_items_html)}
            </div>
            '''
        )

    requisition_total = grand_total

    completed_services = list(
        CompletedServiceEntry.objects.select_related('created_by')
        .filter(service_date__year=year, service_date__month=month)
        .order_by('service_date', 'id')
    )
    service_total = sum((item.amount for item in completed_services), Decimal('0.00'))
    service_cards_html = []
    lines.extend(['', '==================================================', 'Serviços feitos no mês'])
    if completed_services:
        for service in completed_services:
            lines.extend(
                [
                    '',
                    f'{service.service_date:%d/%m/%Y} - {service.service_name}',
                    f'Empresa: {service.company}',
                    f'Valor: R$ {_format_decimal_br(service.amount)}',
                ]
            )
            service_cards_html.append(
                f'''
                <div style="margin-top:10px; padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">
                    <p style="margin:0 0 6px; font-size:12px; font-weight:800; color:#0f766e; text-transform:uppercase; letter-spacing:0.04em;">{service.service_date:%d/%m/%Y}</p>
                    <h4 style="margin:0 0 6px; font-size:16px; color:#0f172a;">{escape(service.service_name)}</h4>
                    <p style="margin:0; font-size:14px; color:#334155;"><strong>Empresa:</strong> {escape(service.company)} &nbsp;|&nbsp; <strong>Valor:</strong> R$ {_format_decimal_br(service.amount)}</p>
                </div>
                '''
            )
    else:
        lines.append('Nenhum serviço feito encontrado neste mês.')

    contract_items = []
    for contract in ContractEntry.objects.select_related('created_by').filter(amount__isnull=False).order_by('name', 'id'):
        reference = _contract_monthly_report_reference(contract, year, month)
        if reference is None:
            continue
        reference_date, charge_label = reference
        contract_items.append((contract, reference_date, charge_label))

    contract_total = sum((contract.amount or Decimal('0.00') for contract, _, _ in contract_items), Decimal('0.00'))
    contract_cards_html = []
    lines.extend(['', '==================================================', 'Contratos do mês'])
    if contract_items:
        for contract, reference_date, charge_label in contract_items:
            lines.extend(
                [
                    '',
                    f'{reference_date:%d/%m/%Y} - {contract.name}',
                    f'Tipo: {charge_label}',
                    f'Pagamento: {contract.payment_method or "-"}',
                    f'Valor: R$ {_format_decimal_br(contract.amount)}',
                ]
            )
            contract_cards_html.append(
                f'''
                <div style="margin-top:10px; padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">
                    <p style="margin:0 0 6px; font-size:12px; font-weight:800; color:#b45309; text-transform:uppercase; letter-spacing:0.04em;">{charge_label} | {reference_date:%d/%m/%Y}</p>
                    <h4 style="margin:0 0 6px; font-size:16px; color:#0f172a;">{escape(contract.name)}</h4>
                    <p style="margin:0; font-size:14px; color:#334155;"><strong>Pagamento:</strong> {escape(contract.payment_method or '-')} &nbsp;|&nbsp; <strong>Valor:</strong> R$ {_format_decimal_br(contract.amount)}</p>
                </div>
                '''
            )
    else:
        lines.append('Nenhum contrato encontrado para este mês.')

    report_total = requisition_total + service_total + contract_total
    if approved_budget_count == 0:
        lines.extend(['', 'Nenhum orçamento aprovado encontrado neste mês.'])

    lines.extend(
        [
            '',
            '==================================================',
            f'Total de requisições com orçamento aprovado: {requisition_count}',
            f'Total de orçamentos aprovados: {approved_budget_count}',
            f'Total de serviços feitos: {len(completed_services)}',
            f'Total de contratos do mês: {len(contract_items)}',
            f'Total de requisições: R$ {_format_decimal_br(requisition_total)}',
            f'Total de serviços: R$ {_format_decimal_br(service_total)}',
            f'Total de contratos: R$ {_format_decimal_br(contract_total)}',
            f'Total geral do mês: R$ {_format_decimal_br(report_total)}',
        ]
    )

    payload_html = f'''
        <div style="font-family:Segoe UI, Arial, sans-serif; color:#0f172a; max-width:860px;">
            <div style="margin:0 0 18px; padding:20px 22px; border-radius:20px; background:#eff6ff; border:1px solid #bfdbfe;">
                <p style="margin:0 0 6px; font-size:12px; font-weight:800; color:#2563eb; text-transform:uppercase; letter-spacing:0.08em;">Relatório mensal</p>
                <h2 style="margin:0; font-size:24px; color:#0f172a;">Resumo mensal TI - {month_label}</h2>
                <p style="margin:8px 0 0; font-size:14px; color:#334155;">Orçamentos aprovados, serviços feitos e contratos do mês.</p>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:10px; margin:0 0 18px;">
                <div style="padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;"><strong>{requisition_count}</strong><br><span style="font-size:12px; color:#64748b;">Requisições</span></div>
                <div style="padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;"><strong>{approved_budget_count}</strong><br><span style="font-size:12px; color:#64748b;">Orçamentos aprovados</span></div>
                <div style="padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;"><strong>{len(completed_services)}</strong><br><span style="font-size:12px; color:#64748b;">Serviços feitos</span></div>
                <div style="padding:12px 14px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;"><strong>{len(contract_items)}</strong><br><span style="font-size:12px; color:#64748b;">Contratos</span></div>
                <div style="padding:12px 14px; border-radius:14px; background:#dcfce7; border:1px solid #bbf7d0;"><strong>R$ {_format_decimal_br(report_total)}</strong><br><span style="font-size:12px; color:#166534;">Total geral</span></div>
            </div>
            <h3 style="margin:0 0 10px; font-size:18px; color:#0f172a;">Orçamentos aprovados</h3>
            {''.join(cards_html) if cards_html else '<p style="margin:0; padding:16px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">Nenhum orçamento aprovado encontrado neste mês.</p>'}
            <h3 style="margin:18px 0 10px; font-size:18px; color:#0f172a;">Serviços feitos</h3>
            {''.join(service_cards_html) if service_cards_html else '<p style="margin:0; padding:16px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">Nenhum serviço feito encontrado neste mês.</p>'}
            <h3 style="margin:18px 0 10px; font-size:18px; color:#0f172a;">Contratos do mês</h3>
            {''.join(contract_cards_html) if contract_cards_html else '<p style="margin:0; padding:16px; border-radius:14px; background:#f8fafc; border:1px solid #e2e8f0;">Nenhum contrato encontrado para este mês.</p>'}
        </div>
    '''.strip()

    return (
        '\n'.join(lines),
        payload_html,
        report_total,
        requisition_count,
        approved_budget_count,
        len(completed_services),
        len(contract_items),
    )


class TicketListView(LoginRequiredMixin, TemplateView):
    template_name = 'chamados/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ti_user = is_ti_user(self.request.user)
        if ti_user:
            ti_attendants = _get_ti_attendants().exclude(id=self.request.user.id)
            spreadsheet_attendants = _get_ti_attendants()
            selected_attendant_username = (self.request.GET.get('atendente') or '').strip()
            selected_attendant = ti_attendants.filter(username=selected_attendant_username).first()
            consultation_mode = selected_attendant is not None

            tickets = _get_visible_tickets_for_ti(self.request.user)
            if consultation_mode:
                attendance_qs = TicketAttendance.objects.select_related('attendant').order_by('-started_at', '-id')
                tickets = (
                    Ticket.objects.select_related('created_by')
                    .prefetch_related(Prefetch('attendances', queryset=attendance_qs))
                    .filter(attendances__attendant=selected_attendant)
                    .distinct()
                )

            counts = tickets.aggregate(
                abertos=Count('id', filter=Q(status=Ticket.Status.ABERTO), distinct=True),
                em_atendimento=Count('id', filter=Q(status=Ticket.Status.EM_ATENDIMENTO), distinct=True),
                aguardando_usuario=Count('id', filter=Q(status=Ticket.Status.AGUARDANDO_USUARIO), distinct=True),
                aguardando_autorizacao=Count('id', filter=Q(status=Ticket.Status.AGUARDANDO_AUTORIZACAO), distinct=True),
                fechados=Count('id', filter=Q(status=Ticket.Status.FECHADO), distinct=True),
            )
            tickets = _mark_ticket_creator_ti(tickets)
            context['tickets'] = tickets
            if consultation_mode:
                context['ticket_rows'] = [(ticket, None) for ticket in tickets]
            else:
                context['ticket_rows'] = [
                    (ticket, _build_timer_meta(ticket, self.request.user)) for ticket in tickets
                ]
            context['closed_tickets'] = []
            context['closed_tickets_count'] = Ticket.objects.filter(status=Ticket.Status.FECHADO).count()
            context['auto_pause_reviews_count'] = _auto_pause_reviews_qs(self.request.user).count()
            context['ti_attendants'] = ti_attendants
            context['spreadsheet_attendants'] = spreadsheet_attendants
            context['failure_type_choices'] = ticket_failure_type_choices()
            context['manual_closed_ticket_form'] = kwargs.get('manual_closed_ticket_form') or ManualClosedTicketForm()
            context['open_manual_closed_ticket_modal'] = kwargs.get('open_manual_closed_ticket_modal', False)
            context['failure_type_management_rows'] = _failure_type_management_rows()
            context['selected_attendant'] = selected_attendant
            context['consultation_mode'] = consultation_mode
            context['counts'] = counts
        else:
            tickets = Ticket.objects.select_related('created_by').filter(
                created_by=self.request.user
            )
            tickets = _mark_ticket_creator_ti(tickets)
            context['tickets'] = tickets
            context['ticket_rows'] = [(ticket, None) for ticket in tickets]
            context['closed_tickets'] = []
            context['closed_tickets_count'] = 0
            context['auto_pause_reviews_count'] = 0
            context['ti_attendants'] = []
            context['spreadsheet_attendants'] = []
            context['failure_type_choices'] = []
            context['manual_closed_ticket_form'] = None
            context['open_manual_closed_ticket_modal'] = False
            context['failure_type_management_rows'] = []
            context['selected_attendant'] = None
            context['consultation_mode'] = False
            context['counts'] = None
        context['is_ti'] = ti_user
        context['priority_choices'] = Ticket.Priority.choices
        return context


class TicketSpreadsheetExportView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem preencher a planilha.'

    def post(self, request, *args, **kwargs):
        attendant_id = (request.POST.get('attendant_id') or '').strip()
        export_month_raw = (request.POST.get('export_month') or '').strip()
        workbook_file = request.FILES.get('workbook_file')
        next_url = _safe_next_url(request)

        attendant = _get_ti_attendants().filter(id=attendant_id).first()
        if attendant is None:
            messages.error(request, 'Escolha um atendente TI valido para preencher a planilha.')
            return redirect(next_url)

        if not workbook_file:
            messages.error(request, 'Selecione a planilha .xlsx que sera exportada.')
            return redirect(next_url)

        export_month = parse_date(f'{export_month_raw}-01') if export_month_raw else None
        if export_month is None:
            messages.error(request, 'Selecione o mes que deseja preencher na planilha.')
            return redirect(next_url)

        ok, exported_count, detail, workbook_bytes, download_name = export_attendant_logs_to_uploaded_workbook(
            attendant=attendant,
            uploaded_file=workbook_file,
            export_month=export_month,
        )
        if ok and exported_count > 0 and workbook_bytes:
            response = HttpResponse(
                workbook_bytes,
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            )
            response['Content-Disposition'] = f'attachment; filename="{download_name}"'
            return response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse(
                {
                    'ok': ok,
                    'exported_count': exported_count,
                    'detail': detail,
                },
                status=200 if ok else 400,
            )
        if ok:
            messages.info(request, detail)
        else:
            messages.error(request, detail)
        return redirect(next_url)


class ClosedTicketsDataView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem acessar chamados fechados.'

    def get(self, request, *args, **kwargs):
        attendant_filter = (request.GET.get('attendant') or '').strip()
        date_from = parse_date((request.GET.get('date_from') or '').strip())
        date_to = parse_date((request.GET.get('date_to') or '').strip())
        closed_tickets = (
            Ticket.objects.select_related('created_by')
            .prefetch_related(Prefetch('attendances', queryset=TicketAttendance.objects.select_related('attendant').order_by('-started_at', '-id')))
            .filter(status=Ticket.Status.FECHADO)
            .order_by('-updated_at', '-id')
        )
        if attendant_filter:
            closed_tickets = closed_tickets.filter(attendances__attendant__username=attendant_filter).distinct()
        if date_from:
            closed_tickets = closed_tickets.filter(
                Q(closed_at__date__gte=date_from)
                | Q(closed_at__isnull=True, updated_at__date__gte=date_from)
            )
        if date_to:
            closed_tickets = closed_tickets.filter(
                Q(closed_at__date__lte=date_to)
                | Q(closed_at__isnull=True, updated_at__date__lte=date_to)
            )
        payload = []
        for ticket in closed_tickets:
            attendant = _last_attendant(ticket)
            payload.append(
                {
                    'id': ticket.id,
                    'title': ticket.title,
                    'created_by': ticket.created_by.username if ticket.created_by_id else '-',
                    'attendant': attendant.username if attendant else '-',
                    'closed_at': timezone.localtime(ticket.closed_at or ticket.updated_at).strftime('%d/%m/%Y %H:%M'),
                    'updated_at': timezone.localtime(ticket.updated_at).strftime('%d/%m/%Y %H:%M'),
                    'detail_url': reverse('chamados_detail', args=[ticket.id]),
                }
            )
        return JsonResponse({'items': payload})


class TicketCreateView(LoginRequiredMixin, FormView):
    template_name = 'chamados/new.html'
    form_class = TicketCreateForm
    success_url = reverse_lazy('chamados_list')
    token_session_key = 'ticket_create_tokens'

    def _issue_create_token(self):
        token = uuid.uuid4().hex
        tokens = list(self.request.session.get(self.token_session_key, []))
        tokens.append(token)
        self.request.session[self.token_session_key] = tokens[-10:]
        self.request.session.modified = True
        return token

    def _consume_create_token(self, token):
        tokens = list(self.request.session.get(self.token_session_key, []))
        if token not in tokens:
            return False
        tokens.remove(token)
        self.request.session[self.token_session_key] = tokens
        self.request.session.modified = True
        self.request.session.save()
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ticket_create_token'] = self._issue_create_token()
        return context

    def post(self, request, *args, **kwargs):
        token = (request.POST.get('ticket_create_token') or '').strip()
        if not self._consume_create_token(token):
            messages.warning(request, 'Este chamado ja foi enviado. Confira a lista antes de criar outro igual.')
            return redirect(self.success_url)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        ticket = form.save(commit=False)
        ticket.created_by = self.request.user
        ticket.save()
        TicketUpdate.objects.create(
            ticket=ticket,
            author=self.request.user,
            message='Chamado aberto pelo usuario.',
            status_to=ticket.status,
        )
        try:
            whatsapp.notify_group_new_ticket(ticket)
        except Exception:
            logger.exception('Falha inesperada ao notificar WhatsApp do chamado #%s', ticket.id)
        messages.success(self.request, f'Chamado #{ticket.id} criado com sucesso.')
        return super().form_valid(form)


class TicketManualClosedCreateView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem registrar chamados finalizados.'

    def post(self, request, *args, **kwargs):
        form = ManualClosedTicketForm(request.POST)
        if form.is_valid():
            description = form.cleaned_data['description'].strip()
            resolution_note = form.cleaned_data['resolution_note'].strip()
            started_at = form.cleaned_data['started_at']
            ended_at = form.cleaned_data['ended_at']
            ticket = Ticket.objects.create(
                title=form.cleaned_data['title'].strip(),
                description=description,
                priority=Ticket.Priority.MEDIA,
                status=Ticket.Status.FECHADO,
                failure_type=Ticket.FailureType.NA,
                created_by=request.user,
                closed_at=ended_at,
            )
            TicketAttendance.objects.create(
                ticket=ticket,
                attendant=request.user,
                started_at=started_at,
                ended_at=ended_at,
                end_action=TicketAttendance.EndAction.STOP,
                note=resolution_note,
            )
            TicketUpdate.objects.create(
                ticket=ticket,
                author=request.user,
                message=f'Chamado registrado manualmente pelo atendente TI e finalizado.\n\nAcao/Correcao: {resolution_note}',
                status_to=ticket.status,
            )
            messages.success(request, f'Chamado #{ticket.id} registrado e finalizado com sucesso.')
            return redirect('chamados_list')

        list_view = TicketListView()
        list_view.setup(request)
        context = list_view.get_context_data(
            manual_closed_ticket_form=form,
            open_manual_closed_ticket_modal=True,
        )
        messages.error(request, 'Nao foi possivel registrar o chamado finalizado. Verifique os campos.')
        return list_view.render_to_response(context)


class TicketPendingListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/pending_list.html'
    ti_error_message = 'Somente atendentes TI podem acessar pendencias.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = TicketPendingForm()
        context['pendings'] = TicketPending.objects.filter(attendant=self.request.user)
        return context

    def post(self, request, *args, **kwargs):
        form = TicketPendingForm(request.POST)
        if form.is_valid():
            pending = form.save(commit=False)
            pending.attendant = request.user
            pending.save()
            messages.success(request, 'Pendencia adicionada com sucesso.')
            return redirect('chamados_pending_list')
        context = self.get_context_data()
        context['form'] = form
        return self.render_to_response(context)


class TicketPendingDeleteView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem excluir pendencias.'

    def post(self, request, pending_id: int, *args, **kwargs):
        pending = get_object_or_404(TicketPending, pk=pending_id, attendant=request.user)
        pending.delete()
        messages.success(request, 'Pendencia removida.')
        return redirect('chamados_pending_list')


class TicketPendingCreateTicketView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem criar chamados por pendencia.'

    def post(self, request, pending_id: int, *args, **kwargs):
        pending = get_object_or_404(TicketPending, pk=pending_id, attendant=request.user)
        now = timezone.now()
        raw_text = (pending.content or '').strip()
        title_core = raw_text[:120] if raw_text else f'Pendencia #{pending.id}'

        ticket = Ticket.objects.create(
            title=title_core,
            description=raw_text or f'Pendencia convertida automaticamente: #{pending.id}.',
            priority=Ticket.Priority.PROGRAMADA,
            status=Ticket.Status.EM_ATENDIMENTO,
            failure_type=Ticket.FailureType.NA,
            created_by=request.user,
            closed_at=None,
        )
        TicketAttendance.objects.create(
            ticket=ticket,
            attendant=request.user,
            started_at=now,
        )
        TicketUpdate.objects.create(
            ticket=ticket,
            author=request.user,
            message=f'Chamado criado a partir da pendencia #{pending.id} com atendimento iniciado (play).',
            status_to=ticket.status,
        )

        pending.delete()
        messages.success(request, f'Chamado #{ticket.id} criado da pendencia com play ativo.')
        return redirect('chamados_list')


class TicketAutoPauseReviewListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/auto_pause_reviews.html'
    ti_error_message = 'Somente atendentes TI podem acessar pausas automaticas.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        reviews = list(_auto_pause_reviews_qs(self.request.user))
        context['review_rows'] = [
            {
                'review': review,
                'ticket': review.attendance.ticket,
                'attendance': review.attendance,
                'duration_label': _format_duration(
                    max(
                        int((review.attendance.ended_at - review.attendance.started_at).total_seconds()),
                        0,
                    ) if review.attendance.ended_at else 0
                ),
            }
            for review in reviews
        ]
        context['review_count'] = len(reviews)
        context['status_choices'] = (
            (Ticket.Status.ABERTO, Ticket.Status.ABERTO.label),
            (Ticket.Status.AGUARDANDO_USUARIO, Ticket.Status.AGUARDANDO_USUARIO.label),
            (Ticket.Status.FECHADO, Ticket.Status.FECHADO.label),
        )
        return context

    def post(self, request, *args, **kwargs):
        review_id = (request.POST.get('review_id') or '').strip()
        note = (request.POST.get('note') or '').strip()
        status = (request.POST.get('status') or '').strip()
        valid_statuses = {
            Ticket.Status.ABERTO,
            Ticket.Status.AGUARDANDO_USUARIO,
            Ticket.Status.FECHADO,
        }

        review = get_object_or_404(
            _auto_pause_reviews_qs(request.user),
            pk=review_id,
        )

        if not note:
            messages.error(request, 'Informe o que foi feito neste chamado pausado automaticamente.')
            return redirect('chamados_auto_pause_reviews')
        if status not in valid_statuses:
            messages.error(request, 'Escolha um status valido para concluir a pausa automatica.')
            return redirect('chamados_auto_pause_reviews')

        attendance = review.attendance
        ticket = attendance.ticket
        now = timezone.now()

        attendance.note = note
        attendance.save(update_fields=['note'])

        ticket.status = status
        ticket.closed_at = now if status == Ticket.Status.FECHADO else None
        ticket.save(update_fields=['status', 'closed_at', 'updated_at'])

        TicketUpdate.objects.create(
            ticket=ticket,
            author=request.user,
            message=f'Complemento da pausa automatica: {note}',
            status_to=ticket.status,
        )

        review.completed_at = now
        review.save(update_fields=['completed_at'])
        messages.success(request, f'Chamado #{ticket.id} atualizado apos pausa automatica.')
        return redirect('chamados_auto_pause_reviews')


class InsumosView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/insumos.html'
    ti_error_message = 'Somente usuarios TI podem acessar insumos.'
    STOCK_CREATE_DEPARTMENT = 'Cadastro de estoque'
    STOCK_IN_PREFIX = 'Entrada:'
    STOCK_OUT_PREFIX = 'Saida:'

    @staticmethod
    def _normalize_item_name(raw_value: str) -> str:
        return ' '.join((raw_value or '').strip().split())

    @classmethod
    def _stock_movement_q(cls):
        return (
            Q(department=cls.STOCK_CREATE_DEPARTMENT)
            | Q(department__startswith=cls.STOCK_IN_PREFIX)
            | Q(department__startswith=cls.STOCK_OUT_PREFIX)
        )

    @classmethod
    def _stock_movements_queryset(cls):
        return Insumo.objects.filter(cls._stock_movement_q())

    @classmethod
    def _stock_snapshot(cls) -> dict[str, dict[str, Decimal | str]]:
        snapshot: dict[str, dict[str, Decimal | str]] = {}
        for row in cls._stock_movements_queryset().only('item', 'quantity').order_by('item', 'id'):
            item_name = cls._normalize_item_name(row.item)
            if not item_name:
                continue
            key = item_name.casefold()
            if key not in snapshot:
                snapshot[key] = {'item': item_name, 'quantity': Decimal('0.00')}
            snapshot[key]['quantity'] = Decimal(snapshot[key]['quantity']) + Decimal(row.quantity or 0)
        return snapshot

    @classmethod
    def _stock_rows(cls) -> list[dict[str, Decimal | str]]:
        rows = list(cls._stock_snapshot().values())
        rows.sort(key=lambda row: str(row['item']).casefold())
        return rows

    @staticmethod
    def _parse_decimal_br(raw_value: str, *, allow_negative: bool = False) -> Decimal:
        normalized_value = (raw_value or '').strip().replace(' ', '')
        if ',' in normalized_value and '.' in normalized_value:
            if normalized_value.rfind(',') > normalized_value.rfind('.'):
                normalized_value = normalized_value.replace('.', '').replace(',', '.')
            else:
                normalized_value = normalized_value.replace(',', '')
        elif ',' in normalized_value:
            normalized_value = normalized_value.replace('.', '').replace(',', '.')
        elif normalized_value.count('.') > 1:
            normalized_value = normalized_value.replace('.', '')
        value = Decimal(normalized_value or '0')
        if value == 0:
            raise InvalidOperation
        if value < 0 and not allow_negative:
            raise InvalidOperation
        return value.quantize(Decimal('0.01'))

    def _redirect_self(self):
        return redirect('chamados_insumos')

    def post(self, request, *args, **kwargs):
        mode = (request.POST.get('mode') or 'create').strip().lower()

        if mode == 'stock_create':
            stock_item = self._normalize_item_name(request.POST.get('stock_item') or request.POST.get('item'))
            stock_quantity_raw = (request.POST.get('stock_quantity') or request.POST.get('quantity') or '').strip()
            if not stock_item:
                messages.error(request, 'Informe o nome do insumo para cadastrar no estoque.')
                return self._redirect_self()
            try:
                stock_quantity = self._parse_decimal_br(stock_quantity_raw)
            except (InvalidOperation, ValueError):
                messages.error(request, 'Quantidade invalida. Ex.: 1,00')
                return self._redirect_self()
            Insumo.objects.create(
                item=stock_item,
                date=timezone.localdate(),
                quantity=stock_quantity,
                name='Estoque',
                department=self.STOCK_CREATE_DEPARTMENT,
            )
            messages.success(request, f'Estoque de "{stock_item}" cadastrado com sucesso.')
            return self._redirect_self()

        if mode == 'stock_delete':
            stock_item = self._normalize_item_name(request.POST.get('stock_item') or request.POST.get('item'))
            if not stock_item:
                messages.error(request, 'Informe o insumo para apagar do estoque.')
                return self._redirect_self()
            normalized_key = stock_item.casefold()
            ids_to_delete = []
            for row in self._stock_movements_queryset().only('id', 'item'):
                if self._normalize_item_name(row.item).casefold() == normalized_key:
                    ids_to_delete.append(row.id)
            if not ids_to_delete:
                messages.error(request, f'Item "{stock_item}" nao encontrado no estoque.')
                return self._redirect_self()
            deleted_count, _ = Insumo.objects.filter(id__in=ids_to_delete).delete()
            if deleted_count <= 0:
                messages.error(request, f'Nao foi possivel apagar "{stock_item}" do estoque.')
                return self._redirect_self()
            messages.success(request, f'Estoque de "{stock_item}" apagado com sucesso.')
            return self._redirect_self()

        if mode == 'stock_adjust':
            stock_item = self._normalize_item_name(request.POST.get('stock_item') or request.POST.get('item'))
            stock_direction = (request.POST.get('stock_direction') or '').strip().lower()
            stock_quantity_raw = (request.POST.get('stock_quantity') or request.POST.get('quantity') or '').strip()
            stock_target = (request.POST.get('stock_target') or request.POST.get('name') or '').strip()
            stock_reason = (request.POST.get('stock_reason') or '').strip()

            if not stock_item:
                messages.error(request, 'Informe o insumo.')
                return self._redirect_self()
            if stock_direction not in {'inc', 'dec'}:
                messages.error(request, 'Movimentacao invalida.')
                return self._redirect_self()
            if stock_direction == 'dec' and not stock_target:
                messages.error(request, 'Informe para quem foi o insumo.')
                return self._redirect_self()
            if not stock_reason:
                messages.error(request, 'Informe o motivo da movimentacao.')
                return self._redirect_self()
            if stock_direction == 'inc':
                stock_target = 'Estoque'

            try:
                stock_quantity = self._parse_decimal_br(stock_quantity_raw)
            except (InvalidOperation, ValueError):
                messages.error(request, 'Quantidade invalida. Ex.: 1,00')
                return self._redirect_self()

            movement_quantity = stock_quantity
            if stock_direction == 'dec':
                current_qty = Decimal(self._stock_snapshot().get(stock_item.casefold(), {}).get('quantity') or 0)
                if current_qty < stock_quantity:
                    current_text = f'{current_qty:.2f}'.replace('.', ',')
                    messages.error(request, f'Estoque insuficiente de "{stock_item}". Atual: {current_text}')
                    return self._redirect_self()
                movement_quantity = -stock_quantity

            direction_label = 'Entrada' if stock_direction == 'inc' else 'Saida'
            department_value = f'{direction_label}: {stock_reason}'
            Insumo.objects.create(
                item=stock_item,
                date=timezone.localdate(),
                quantity=movement_quantity,
                name=stock_target[:200],
                department=department_value[:120],
            )

            if stock_direction == 'dec':
                Insumo.objects.create(
                    item=stock_item,
                    date=timezone.localdate(),
                    quantity=stock_quantity,
                    name=stock_target[:200],
                    department=stock_reason[:120],
                )
            messages.success(request, 'Movimentacao de estoque registrada com sucesso.')
            return self._redirect_self()

        insumo_id = (request.POST.get('insumo_id') or '').strip()
        item = self._normalize_item_name(request.POST.get('item') or '')
        date_raw = (request.POST.get('date') or '').strip()
        quantity_raw = (request.POST.get('quantity') or '').strip()
        name = (request.POST.get('name') or '').strip()
        department = (request.POST.get('department') or '').strip()

        if not item:
            messages.error(request, 'Informe o insumo.')
            return self._redirect_self()
        if not date_raw:
            messages.error(request, 'Informe a data.')
            return self._redirect_self()
        if not name:
            messages.error(request, 'Informe o nome.')
            return self._redirect_self()

        try:
            entry_date = datetime.strptime(date_raw, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Data invalida.')
            return self._redirect_self()

        try:
            quantity = self._parse_decimal_br(quantity_raw, allow_negative=(mode == 'update'))
        except (InvalidOperation, ValueError):
            messages.error(request, 'Quantidade invalida. Ex.: 1,00')
            return self._redirect_self()

        if mode == 'update':
            insumo = Insumo.objects.exclude(self._stock_movement_q()).filter(id=insumo_id).first()
            if not insumo:
                messages.error(request, 'Registro de insumo nao encontrado para edicao.')
                return self._redirect_self()
            insumo.item = item
            insumo.date = entry_date
            insumo.quantity = quantity
            insumo.name = name
            insumo.department = department
            insumo.save(update_fields=['item', 'date', 'quantity', 'name', 'department'])
            messages.success(request, 'Insumo atualizado com sucesso.')
            return self._redirect_self()

        Insumo.objects.create(
            item=item,
            date=entry_date,
            quantity=quantity,
            name=name,
            department=department,
        )
        messages.success(request, 'Insumo cadastrado com sucesso.')
        return self._redirect_self()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_text = (self.request.GET.get('q') or '').strip()
        edit_id_raw = (self.request.GET.get('edit') or '').strip()

        records = Insumo.objects.exclude(self._stock_movement_q()).order_by('-date', '-id')
        if query_text:
            records = records.filter(
                Q(item__icontains=query_text)
                | Q(name__icontains=query_text)
                | Q(department__icontains=query_text)
            )

        edit_insumo = None
        if edit_id_raw.isdigit():
            edit_insumo = Insumo.objects.exclude(self._stock_movement_q()).filter(id=int(edit_id_raw)).first()

        stock_rows = self._stock_rows()
        stock_total_quantity = sum((Decimal(row['quantity']) for row in stock_rows), Decimal('0.00'))
        context['insumos'] = records
        context['insumo_edit'] = edit_insumo
        context['insumo_default_date'] = timezone.localdate().isoformat()
        context['estoque_atual'] = stock_rows
        context['stock_item_choices'] = [row['item'] for row in stock_rows]
        context['query_text'] = query_text
        context['insumos_total_count'] = records.count()
        context['stock_total_items'] = len(stock_rows)
        context['stock_total_quantity'] = stock_total_quantity
        return context


class RequisitionHubView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/requisicoes.html'
    ti_error_message = 'Somente usuarios TI podem acessar requisicoes.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_text = (self.request.GET.get('q') or '').strip()
        status_filter = (self.request.GET.get('status') or '').strip()
        valid_statuses = {choice[0] for choice in Requisition.Status.choices}

        requisitions = Requisition.objects.select_related('requested_by').prefetch_related(
            Prefetch(
                'budgets',
                queryset=RequisitionBudget.objects.order_by('parent_budget_id', 'id').prefetch_related(
                    'attachments',
                    Prefetch(
                        'history_entries',
                        queryset=RequisitionBudgetHistory.objects.select_related('author').order_by('-created_at', '-id'),
                        to_attr='prefetched_history_entries',
                    )
                ),
            ),
            Prefetch(
                'updates',
                queryset=RequisitionUpdate.objects.select_related('author').order_by('-created_at', '-id'),
            )
        )
        if query_text:
            requisitions = requisitions.filter(
                Q(code__icontains=query_text)
                | Q(title__icontains=query_text)
                | Q(request_text__icontains=query_text)
                | Q(requested_by__username__icontains=query_text)
                | Q(budgets__title__icontains=query_text)
                | Q(budgets__notes__icontains=query_text)
            )
        if status_filter in valid_statuses:
            selected_status = status_filter
        else:
            selected_status = ''
            status_filter = ''
        requisitions = _reconcile_requisition_statuses_from_budgets(list(requisitions.distinct()))
        requisitions = sorted(
            requisitions,
            key=lambda requisition: (requisition.created_at, requisition.id or 0),
            reverse=True,
        )
        if selected_status:
            filtered_requisitions = [
                requisition for requisition in requisitions
                if requisition.status == selected_status
            ]
        else:
            filtered_requisitions = requisitions

        requisition_rows, requisitions_payload = _build_requisition_rows(filtered_requisitions)
        share_map = {
            str(item['id']): _build_requisition_share_text(item)
            for item in requisitions_payload
        }

        context['requisition_rows'] = requisition_rows
        context['requisitions_payload'] = requisitions_payload
        context['requisition_share_map'] = share_map
        context['requisition_form'] = RequisitionForm()
        context['requisition_status_form'] = RequisitionStatusForm()
        context['status_choices'] = Requisition.Status.choices
        context['kind_choices'] = Requisition.Kind.choices
        context['query_text'] = query_text
        context['status_filter'] = status_filter
        context['monthly_copy_default_month'] = timezone.localdate().strftime('%Y-%m')
        context['delivery_default_date'] = timezone.localdate().isoformat()
        context['counts'] = {
            'pendente_aprovacao': sum(
                1 for requisition in requisitions
                if requisition.status == Requisition.Status.PENDENTE_APROVACAO
            ),
            'aprovada': sum(
                1 for requisition in requisitions
                if requisition.status == Requisition.Status.APROVADA
            ),
            'nao_aprovada': sum(
                1 for requisition in requisitions
                if requisition.status == Requisition.Status.NAO_APROVADA
            ),
            'parcialmente_entregue': sum(
                1 for requisition in requisitions
                if requisition.status == Requisition.Status.PARCIALMENTE_ENTREGUE
            ),
            'entregue': sum(
                1 for requisition in requisitions
                if requisition.status == Requisition.Status.ENTREGUE
            ),
        }
        return context


class RequisitionMonthlyApprovedCopyView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem copiar relatorios de requisicoes.'

    def get(self, request, *args, **kwargs):
        raw_month = (request.GET.get('month') or '').strip()
        try:
            parsed_month = datetime.strptime(raw_month, '%Y-%m')
        except ValueError:
            return JsonResponse(
                {'ok': False, 'error': 'Informe o mes no formato AAAA-MM.'},
                status=400,
            )

        (
            text,
            html,
            total,
            requisition_count,
            approved_budget_count,
            completed_service_count,
            contract_count,
        ) = _build_monthly_approved_requisitions_payload(
            parsed_month.year,
            parsed_month.month,
        )
        return JsonResponse(
            {
                'ok': True,
                'text': text,
                'html': html,
                'total_display': _format_decimal_br(total),
                'requisition_count': requisition_count,
                'approved_budget_count': approved_budget_count,
                'completed_service_count': completed_service_count,
                'contract_count': contract_count,
            }
        )


class RequisitionSaveView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem cadastrar ou editar requisicoes.'

    def post(self, request, *args, **kwargs):
        requisition_id = (request.POST.get('requisition_id') or '').strip()
        requisition = None
        if requisition_id:
            requisition = Requisition.objects.filter(id=requisition_id).first()
            if requisition is None:
                messages.error(request, 'Requisicao nao encontrada para edicao.')
                return redirect('chamados_requisicoes')

        form = RequisitionForm(request.POST, instance=requisition)
        if not form.is_valid():
            messages.error(request, 'Nao foi possivel salvar a requisicao. Verifique os campos.')
            return redirect('chamados_requisicoes')

        creating = requisition is None
        try:
            with transaction.atomic():
                saved = form.save(commit=False)
                if creating:
                    saved.requested_by = request.user
                saved.save()
                _sync_requisition_timeline_dates(saved)

                ok, error_message = _sync_requisition_budgets(request, saved)
                if not ok:
                    raise ValueError(error_message)
                auto_status_changed = _sync_requisition_status_from_budgets(saved, author=request.user)

                if creating:
                    RequisitionUpdate.objects.create(
                        requisition=saved,
                        author=request.user,
                        message='Requisicao cadastrada.' if not auto_status_changed else 'Requisicao cadastrada e aprovada com base nos orcamentos.',
                        status_to=saved.status,
                    )
                    messages.success(request, f'Requisicao {saved.code} cadastrada com sucesso.')
                else:
                    RequisitionUpdate.objects.create(
                        requisition=saved,
                        author=request.user,
                        message='Requisicao atualizada.' if not auto_status_changed else 'Requisicao atualizada e aprovada com base nos orcamentos.',
                        status_to=saved.status,
                    )
                    messages.success(request, f'Requisicao {saved.code} atualizada com sucesso.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('chamados_requisicoes')


class RequisitionStatusUpdateView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem alterar status de requisicoes.'

    def post(self, request, requisition_id: int, *args, **kwargs):
        requisition = get_object_or_404(Requisition, pk=requisition_id)
        form = RequisitionStatusForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Status invalido para requisicao.')
            return redirect('chamados_requisicoes')

        previous_status = requisition.status
        requisition.status = form.cleaned_data['status']
        requisition.save(update_fields=['status', 'updated_at'])
        _sync_requisition_timeline_dates(requisition)

        note = (form.cleaned_data.get('note') or '').strip()
        if note:
            message = f'Status alterado: {note}'
        elif requisition.status != previous_status:
            message = f'Status alterado para "{requisition.get_status_display()}".'
        else:
            message = 'Status confirmado sem alteracoes.'

        RequisitionUpdate.objects.create(
            requisition=requisition,
            author=request.user,
            message=message,
            status_to=requisition.status,
        )
        messages.success(request, f'Status da requisicao {requisition.code} atualizado.')
        return redirect('chamados_requisicoes')


class RequisitionDeliverView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem marcar requisicoes como entregues.'

    def post(self, request, requisition_id: int, *args, **kwargs):
        requisition = get_object_or_404(
            Requisition.objects.prefetch_related('budgets'),
            pk=requisition_id,
        )
        allowed_statuses = {
            Requisition.Status.APROVADA,
            Requisition.Status.PARCIALMENTE_ENTREGUE,
        }
        if requisition.status not in allowed_statuses:
            messages.error(
                request,
                f'A requisicao {requisition.code} precisa estar aprovada para ser marcada como entregue.',
            )
            return redirect('chamados_requisicoes')

        delivered_at = parse_date(request.POST.get('delivered_at') or '')
        if delivered_at is None:
            messages.error(request, 'Informe uma data valida para a entrega.')
            return redirect('chamados_requisicoes')

        note = (request.POST.get('note') or '').strip()
        delivery_label = _format_date_br(delivered_at)
        changed_budget_count = 0

        with transaction.atomic():
            requisition.status = Requisition.Status.ENTREGUE
            requisition.received_at = delivered_at
            if requisition.approved_at is None:
                requisition.approved_at = delivered_at
            requisition.save(update_fields=['status', 'received_at', 'approved_at', 'updated_at'])

            approved_budgets = requisition.budgets.filter(
                approval_status=RequisitionBudget.ApprovalStatus.APROVADO,
            )
            for budget in approved_budgets:
                if (
                    budget.receipt_status == RequisitionBudget.ReceiptStatus.RECEBIDO
                    and budget.received_quantity == budget.quantity
                ):
                    continue

                budget.receipt_status = RequisitionBudget.ReceiptStatus.RECEBIDO
                budget.received_quantity = budget.quantity
                budget.save(update_fields=['receipt_status', 'received_quantity', 'updated_at'])
                changed_budget_count += 1
                _create_budget_history_entry(
                    budget,
                    request.user,
                    f'Compra entregue em {delivery_label}.',
                )

            message = f'Compra entregue em {delivery_label}.'
            if note:
                message = f'{message} {note}'
            if changed_budget_count:
                message = f'{message} {changed_budget_count} orcamento(s) aprovado(s) marcado(s) como recebido(s).'

            RequisitionUpdate.objects.create(
                requisition=requisition,
                author=request.user,
                message=message,
                status_to=requisition.status,
            )

        messages.success(request, f'Requisicao {requisition.code} marcada como entregue.')
        return redirect('chamados_requisicoes')


class RequisitionRejectAllBudgetsView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem reprovar requisicoes.'

    def post(self, request, requisition_id: int, *args, **kwargs):
        requisition = get_object_or_404(
            Requisition.objects.prefetch_related('budgets'),
            pk=requisition_id,
        )
        allowed_statuses = {
            Requisition.Status.PENDENTE_APROVACAO,
            Requisition.Status.NAO_APROVADA,
        }
        if requisition.status not in allowed_statuses:
            messages.info(
                request,
                f'A requisicao {requisition.code} nao pode ser marcada como nao aprovada neste status.',
            )
            return redirect('chamados_requisicoes')

        changed_count, _ = _reject_all_requisition_budgets(requisition, author=request.user)
        messages.success(
            request,
            f'Requisicao {requisition.code} marcada como nao aprovada. {changed_count} orcamento(s) atualizado(s).',
        )
        return redirect('chamados_requisicoes')


class RequisitionBudgetApproveView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem aprovar orcamentos de requisicoes.'

    def post(self, request, budget_id: int, *args, **kwargs):
        budget = get_object_or_404(RequisitionBudget.objects.select_related('requisition'), pk=budget_id)
        changed_budgets = _approve_budget_group(budget, author=request.user)
        if not changed_budgets:
            messages.info(request, f'O orcamento "{budget.title}" e seus relacionados ja estavam aprovados.')
            return redirect('chamados_requisicoes')

        requisition = budget.requisition
        status_changed = _sync_requisition_status_from_budgets(requisition, author=request.user)
        if status_changed:
            latest_update = requisition.updates.order_by('-created_at', '-id').first()
            if latest_update:
                latest_update.message = f'Requisicao aprovada a partir do orcamento "{budget.title}".'
                latest_update.save(update_fields=['message'])

        if len(changed_budgets) == 1:
            messages.success(request, f'Orcamento "{budget.title}" aprovado com sucesso.')
        else:
            messages.success(
                request,
                f'Orcamento "{budget.title}" aprovado com sucesso. {len(changed_budgets) - 1} suborcamento(s) relacionado(s) aprovado(s) automaticamente.',
            )
        return redirect('chamados_requisicoes')


class RequisitionBudgetDisapproveView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem desaprovar orcamentos de requisicoes.'

    def post(self, request, budget_id: int, *args, **kwargs):
        budget = get_object_or_404(RequisitionBudget.objects.select_related('requisition'), pk=budget_id)
        changed_budgets = _disapprove_budget_group(budget, author=request.user)
        if not changed_budgets:
            messages.info(request, f'O orcamento "{budget.title}" e seus relacionados nao estavam aprovados.')
            return redirect('chamados_requisicoes')

        _sync_requisition_status_after_budget_unapproval(budget.requisition, author=request.user)
        if len(changed_budgets) == 1:
            messages.success(request, f'Orcamento "{budget.title}" marcado como nao aprovado.')
        else:
            messages.success(
                request,
                f'Orcamento "{budget.title}" marcado como nao aprovado. {len(changed_budgets) - 1} suborcamento(s) relacionado(s) atualizado(s).',
            )
        return redirect('chamados_requisicoes')


class TicketDetailView(LoginRequiredMixin, DetailView):
    template_name = 'chamados/detail.html'
    model = Ticket
    pk_url_kwarg = 'ticket_id'
    context_object_name = 'ticket'

    def get_queryset(self):
        attendance_qs = TicketAttendance.objects.select_related('attendant').order_by('-started_at', '-id')
        updates_qs = TicketUpdate.objects.select_related('author').order_by('created_at', 'id')
        return Ticket.objects.select_related('created_by').prefetch_related(
            Prefetch('updates', queryset=updates_qs),
            Prefetch('attendances', queryset=attendance_qs),
        )

    def get_object(self, queryset=None):
        if hasattr(self, '_cached_object'):
            return self._cached_object
        self._cached_object = super().get_object(queryset=queryset)
        return self._cached_object

    def dispatch(self, request, *args, **kwargs):
        ticket = self.get_object()
        consult_mode = (request.GET.get('consult') or '').strip() == '1'
        if not _can_view_ticket(request.user, ticket, consult_mode=consult_mode):
            messages.error(request, 'Voce nao possui permissao para visualizar este chamado.')
            return redirect('chamados_list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        consult_mode = (self.request.GET.get('consult') or '').strip() == '1'
        current_attendant = _current_attendant(self.object)
        last_attendant = _last_attendant(self.object)
        context['consult_mode'] = consult_mode
        context['is_ti'] = is_ti_user(self.request.user)
        context['can_delete_ticket'] = _can_delete_ticket(self.request.user, self.object)
        context['priority_choices'] = Ticket.Priority.choices
        context['failure_type_choices'] = ticket_failure_type_choices()
        context['can_claim_ticket'] = context['is_ti'] and consult_mode and self.object.status != Ticket.Status.FECHADO
        context['can_handle_ticket'] = context['is_ti'] and _can_ti_handle_ticket(
            self.request.user,
            self.object,
        ) and not consult_mode
        context['display_description'] = _clean_legacy_text(self.object.description)
        context['current_attendant'] = current_attendant
        context['last_attendant'] = last_attendant
        context['display_updates'] = [
            {
                'author_username': update.author.username if update.author_id else 'Sistema',
                'created_at': update.created_at,
                'status_to': update.status_to,
                'status_display': update.get_status_to_display() if update.status_to else '',
                'message': _clean_legacy_text(update.message),
            }
            for update in self.object.updates.all()
            if _clean_legacy_text(update.message)
        ]
        if context['can_handle_ticket']:
            context['timer_meta'] = _build_timer_meta(self.object, self.request.user)
        else:
            context['timer_meta'] = None
        return context


class TicketTimerActionView(LoginRequiredMixin, View):
    def post(self, request, ticket_id: int, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente usuarios TI podem atender chamados.')
            return redirect(_safe_next_url(request))

        attendance_qs = TicketAttendance.objects.select_related('attendant').order_by('-started_at', '-id')
        ticket = get_object_or_404(
            Ticket.objects.prefetch_related(Prefetch('attendances', queryset=attendance_qs)).select_related('created_by'),
            pk=ticket_id,
        )

        action = (request.POST.get('action') or '').strip().lower()
        if action == 'claim':
            if ticket.status == Ticket.Status.FECHADO:
                messages.error(request, 'Chamados fechados nao podem ser puxados para atendimento.')
                return redirect(_safe_next_url(request))
            changed, detail = _claim_ticket_for_attendant(ticket, request.user, timezone.now())
            if changed:
                messages.success(request, f'Chamado #{ticket.id} puxado para voce.')
            else:
                messages.info(request, detail)
            return redirect(_safe_next_url(request))

        if action == 'priority':
            priority = (request.POST.get('priority') or '').strip()
            valid_priorities = {choice[0] for choice in Ticket.Priority.choices}
            if priority not in valid_priorities:
                messages.error(request, 'Escolha uma prioridade valida.')
                return redirect(_safe_next_url(request))

            old_priority = ticket.get_priority_display()
            if ticket.priority == priority:
                messages.info(request, 'A prioridade do chamado ja estava selecionada.')
                return redirect(_safe_next_url(request))

            ticket.priority = priority
            ticket.save(update_fields=['priority', 'updated_at'])
            TicketUpdate.objects.create(
                ticket=ticket,
                author=request.user,
                message=f'Prioridade alterada de "{old_priority}" para "{ticket.get_priority_display()}".',
                status_to=ticket.status,
            )
            messages.success(request, f'Prioridade do chamado #{ticket.id} atualizada.')
            return redirect(_safe_next_url(request))

        if not _can_ti_handle_ticket(request.user, ticket):
            messages.error(request, 'Este chamado ja esta sob atendimento de outro atendente TI.')
            return redirect(_safe_next_url(request))

        note = (request.POST.get('note') or '').strip()
        now = timezone.now()

        attendance_rows = _attendance_rows(ticket)
        my_running = next(
            (
                row
                for row in attendance_rows
                if row.attendant_id == request.user.id and row.ended_at is None
            ),
            None,
        )
        running_by_other = any(
            row.ended_at is None and row.attendant_id != request.user.id
            for row in attendance_rows
        )
        my_attendance_exists = any(row.attendant_id == request.user.id for row in attendance_rows)

        if action == 'play':
            if running_by_other:
                messages.error(request, 'Outro atendente ja iniciou este chamado.')
                return redirect(_safe_next_url(request))
            if my_running:
                messages.info(request, 'Voce ja esta atendendo este chamado.')
                return redirect(_safe_next_url(request))

            TicketAttendance.objects.create(
                ticket=ticket,
                attendant=request.user,
                started_at=now,
            )
            ticket.status = Ticket.Status.EM_ATENDIMENTO
            ticket.closed_at = None
            ticket.save(update_fields=['status', 'closed_at', 'updated_at'])
            TicketUpdate.objects.create(
                ticket=ticket,
                author=request.user,
                message='Atendimento iniciado (play).',
                status_to=ticket.status,
            )
            messages.success(request, f'Atendimento iniciado no chamado #{ticket.id}.')
            return redirect(_safe_next_url(request))

        if action == 'close':
            if ticket.status == Ticket.Status.FECHADO:
                messages.info(request, 'Este chamado ja esta fechado.')
                return redirect(_safe_next_url(request))
            if my_running:
                messages.error(request, 'Use Stop para fechar um chamado que esta em atendimento agora.')
                return redirect(_safe_next_url(request))
            if running_by_other:
                messages.error(request, 'Outro atendente esta com este chamado em atendimento.')
                return redirect(_safe_next_url(request))
            if not my_attendance_exists:
                messages.error(request, 'Somente chamados que ja tiveram atendimento podem ser fechados sem novo registro de tempo.')
                return redirect(_safe_next_url(request))
            if not note:
                messages.error(request, 'Informe uma observacao para fechar o chamado.')
                return redirect(_safe_next_url(request))

            ticket.status = Ticket.Status.FECHADO
            ticket.closed_at = now
            ticket.save(update_fields=['status', 'closed_at', 'updated_at'])
            TicketUpdate.objects.create(
                ticket=ticket,
                author=request.user,
                message=f'Fechamento sem novo apontamento de tempo: {note}',
                status_to=ticket.status,
            )
            messages.success(request, f'Chamado #{ticket.id} fechado sem novo registro de tempo.')
            return redirect(_safe_next_url(request))

        if action not in {'pause', 'stop'}:
            messages.error(request, 'Acao de atendimento invalida.')
            return redirect(_safe_next_url(request))

        if not my_running:
            messages.error(request, 'Nao existe atendimento em andamento para pausar/parar.')
            return redirect(_safe_next_url(request))

        if not note:
            messages.error(request, 'Informe o que foi feito antes de pausar/parar.')
            return redirect(_safe_next_url(request))

        my_running.ended_at = now
        my_running.end_action = TicketAttendance.EndAction.PAUSE if action == 'pause' else TicketAttendance.EndAction.STOP
        my_running.note = note
        my_running.save(update_fields=['ended_at', 'end_action', 'note'])

        if action == 'pause':
            pause_status = (request.POST.get('pause_status') or '').strip()
            valid_pause_statuses = {
                Ticket.Status.ABERTO,
                Ticket.Status.AGUARDANDO_USUARIO,
                Ticket.Status.AGUARDANDO_AUTORIZACAO,
            }
            if pause_status not in valid_pause_statuses:
                messages.error(request, 'Escolha se o chamado volta para aberto, aguardando usuario ou aguardando autorizacao.')
                my_running.ended_at = None
                my_running.end_action = ''
                my_running.note = ''
                my_running.save(update_fields=['ended_at', 'end_action', 'note'])
                return redirect(_safe_next_url(request))
            ticket.status = pause_status
            ticket.closed_at = None
        else:
            failure_type = (request.POST.get('failure_type') or '').strip()
            new_failure_type_name = (request.POST.get('new_failure_type_name') or '').strip()
            resolved_failure_type, failure_error = resolve_failure_type_value(failure_type, new_failure_type_name)
            if failure_error:
                messages.error(request, failure_error if failure_type else 'Escolha a categoria antes de fechar o chamado.')
                my_running.ended_at = None
                my_running.end_action = ''
                my_running.note = ''
                my_running.save(update_fields=['ended_at', 'end_action', 'note'])
                return redirect(_safe_next_url(request))
            ticket.failure_type = resolved_failure_type
            ticket.status = Ticket.Status.FECHADO
            ticket.closed_at = now
        ticket.save(update_fields=['status', 'closed_at', 'failure_type', 'updated_at'])

        action_label = 'Pause' if action == 'pause' else 'Stop'
        TicketUpdate.objects.create(
            ticket=ticket,
            author=request.user,
            message=f'{action_label}: {note}',
            status_to=ticket.status,
        )
        messages.success(request, f'Chamado #{ticket.id} atualizado com {action_label.lower()}.')
        return redirect(_safe_next_url(request))


class TicketDeleteView(LoginRequiredMixin, View):
    def post(self, request, ticket_id: int, *args, **kwargs):
        ticket = get_object_or_404(Ticket, pk=ticket_id)
        if not _can_delete_ticket(request.user, ticket):
            messages.error(request, 'Voce nao possui permissao para excluir este chamado.')
            return redirect('chamados_detail', ticket_id=ticket.id)

        ticket_label = f'#{ticket.id} - {ticket.title}'
        ticket.delete()
        messages.success(request, f'Chamado {ticket_label} excluido com sucesso.')
        return redirect('chamados_list')


class TicketFailureTypeDeleteView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem excluir categorias de chamados.'

    def post(self, request, failure_type_id: int, *args, **kwargs):
        failure_type = get_object_or_404(TicketFailureType, pk=failure_type_id)
        name = failure_type.name
        usage_count = Ticket.objects.filter(failure_type=name).count()
        HiddenTicketFailureType.objects.get_or_create(
            normalized_name=_normalize_failure_type_key(name),
            defaults={'display_name': name},
        )
        failure_type.delete()

        if usage_count:
            messages.success(
                request,
                f'Categoria "{name}" excluida das opcoes futuras. {usage_count} chamado(s) antigo(s) continuam com essa categoria no historico.',
            )
        else:
            messages.success(request, f'Categoria "{name}" excluida com sucesso.')
        return redirect('chamados_list')


class TicketFailureTypeHideView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem excluir categorias de chamados.'

    def post(self, request, *args, **kwargs):
        name = (request.POST.get('failure_type_name') or '').strip()
        value = (request.POST.get('failure_type_value') or name).strip()
        normalized = _normalize_failure_type_key(value) or _normalize_failure_type_key(name)
        if not normalized:
            messages.error(request, 'Categoria invalida.')
            return redirect('chamados_list')

        HiddenTicketFailureType.objects.get_or_create(
            normalized_name=normalized,
            defaults={'display_name': name or value},
        )
        TicketFailureType.objects.filter(name__iexact=name or value).delete()
        messages.success(request, f'Categoria "{name or value}" excluida das opcoes futuras.')
        return redirect('chamados_list')


class StarlinkListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/starlinks.html'
    ti_error_message = 'Somente usuarios TI podem acessar Starlinks.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        starlinks = Starlink.objects.select_related('created_by').all()
        context['starlinks'] = starlinks
        context['form'] = StarlinkForm()
        context['active_count'] = starlinks.filter(is_active=True).count()
        context['inactive_count'] = starlinks.filter(is_active=False).count()
        return context

    def post(self, request, *args, **kwargs):
        form = StarlinkForm(request.POST)
        if form.is_valid():
            starlink = form.save(commit=False)
            starlink.created_by = request.user
            starlink.save()
            messages.success(request, 'Starlink cadastrada com sucesso.')
            return redirect('chamados_starlinks')

        context = self.get_context_data()
        context['form'] = form
        context['open_create_modal'] = True
        return self.render_to_response(context)


class StarlinkDetailView(TiRequiredMixin, DetailView):
    model = Starlink
    context_object_name = 'starlink'
    pk_url_kwarg = 'starlink_id'
    template_name = 'chamados/starlink_detail.html'
    ti_error_message = 'Somente usuarios TI podem acessar Starlinks.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['edit_form'] = kwargs.get('edit_form') or StarlinkEditForm(instance=self.object)
        context['open_edit_modal'] = kwargs.get('open_edit_modal', False)
        return context


class StarlinkUpdateView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Starlinks.'

    def post(self, request, starlink_id: int, *args, **kwargs):
        starlink = get_object_or_404(Starlink, pk=starlink_id)
        form = StarlinkEditForm(request.POST, instance=starlink)
        if form.is_valid():
            starlink = form.save(commit=False)
            starlink.save()
            messages.success(request, 'Dados da Starlink atualizados com sucesso.')
            return redirect('chamados_starlinks_detail', starlink_id=starlink.id)

        detail_view = StarlinkDetailView()
        detail_view.setup(request, starlink_id=starlink.id)
        detail_view.object = starlink
        context = detail_view.get_context_data(edit_form=form, open_edit_modal=True)
        return detail_view.render_to_response(context)


class StarlinkDeleteView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Starlinks.'

    def post(self, request, starlink_id: int, *args, **kwargs):
        starlink = get_object_or_404(Starlink, pk=starlink_id)
        label = starlink.name
        starlink.delete()
        messages.success(request, f'Starlink "{label}" apagada com sucesso.')
        return redirect('chamados_starlinks')


class DocumentListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/documentos.html'
    ti_error_message = 'Somente usuarios TI podem acessar Documentos.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        documentos = DocumentEntry.objects.select_related('created_by').all()
        context['documentos'] = documentos
        context['form'] = kwargs.get('form') or DocumentEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = documentos.count()
        context['with_attachment_count'] = documentos.filter(attachment__isnull=False).exclude(attachment='').count()
        return context

    def post(self, request, *args, **kwargs):
        form = DocumentEntryForm(request.POST, request.FILES)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.created_by = request.user
            documento.save()
            messages.success(request, 'Documento cadastrado com sucesso.')
            return redirect('chamados_documentos')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


def _save_equipment_loan_photos(loan: EquipmentLoan, photos, item=None):
    created_count = 0
    for photo in photos or []:
        EquipmentLoanPhoto.objects.create(loan=loan, item=item, image=photo)
        created_count += 1
    if created_count:
        loan.save(update_fields=['updated_at'])
    return created_count


def _single_equipment_item(loan: EquipmentLoan):
    items = list(loan.items.all()[:2])
    if len(items) == 1:
        return items[0]
    return None


def _attach_unassigned_photos_to_single_item(loan: EquipmentLoan):
    item = _single_equipment_item(loan)
    if not item:
        return 0
    return loan.photos.filter(item__isnull=True).update(item=item)


def _uppercase_equipment_identifiers(row):
    row['equipment_model'] = (row.get('equipment_model') or '').strip().upper()
    row['equipment_serial'] = (row.get('equipment_serial') or '').strip().upper()
    return row


def _sync_primary_equipment_item(loan: EquipmentLoan):
    item = loan.items.order_by('id').first()
    data = {
        'equipment_type': loan.equipment_type,
        'equipment_brand': loan.equipment_brand,
        'equipment_model': loan.equipment_model,
        'equipment_serial': loan.equipment_serial,
        'patrimony_tag': loan.patrimony_tag,
        'accessories': loan.accessories,
    }
    if item:
        for field, value in data.items():
            setattr(item, field, value)
        item.save(update_fields=[*data.keys()])
        return item
    return EquipmentLoanItem.objects.create(loan=loan, **data)


def _extra_equipment_rows_from_request(request):
    rows = []
    types = request.POST.getlist('extra_equipment_type')
    brands = request.POST.getlist('extra_equipment_brand')
    models = request.POST.getlist('extra_equipment_model')
    serials = request.POST.getlist('extra_equipment_serial')
    patrimonies = request.POST.getlist('extra_patrimony_tag')
    accessories = request.POST.getlist('extra_accessories')
    photo_keys = request.POST.getlist('extra_equipment_photo_key')
    for index, equipment_type in enumerate(types):
        row = {
            'equipment_type': (equipment_type or '').strip(),
            'equipment_brand': (brands[index] if index < len(brands) else '').strip(),
            'equipment_model': (models[index] if index < len(models) else '').strip(),
            'equipment_serial': (serials[index] if index < len(serials) else '').strip(),
            'patrimony_tag': (patrimonies[index] if index < len(patrimonies) else '').strip(),
            'accessories': (accessories[index] if index < len(accessories) else '').strip(),
            'photos_key': (photo_keys[index] if index < len(photo_keys) else '').strip(),
        }
        _uppercase_equipment_identifiers(row)
        if any(row.values()):
            rows.append(row)
    return rows


def _save_extra_equipment_items(loan: EquipmentLoan, rows, files=None):
    created_items = []
    files = files or {}
    for row in rows:
        if not row['equipment_type']:
            continue
        row_files_key = row.pop('photos_key', '')
        item = EquipmentLoanItem.objects.create(loan=loan, **row)
        row_files = files.getlist(row_files_key) if hasattr(files, 'getlist') else files.get(row_files_key, [])
        _save_equipment_loan_photos(loan, row_files, item=item)
        created_items.append(item)
    if created_items:
        loan.save(update_fields=['updated_at'])
    return created_items


class EquipmentLoanListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/emprestimos.html'
    ti_error_message = 'Somente usuarios TI podem acessar Emprestimos.'
    token_session_key = 'equipment_loan_create_tokens'

    def _issue_create_token(self):
        token = uuid.uuid4().hex
        tokens = list(self.request.session.get(self.token_session_key, []))
        tokens.append(token)
        self.request.session[self.token_session_key] = tokens[-10:]
        self.request.session.modified = True
        return token

    def _consume_create_token(self, token):
        tokens = list(self.request.session.get(self.token_session_key, []))
        if token not in tokens:
            return False
        tokens.remove(token)
        self.request.session[self.token_session_key] = tokens
        self.request.session.modified = True
        self.request.session.save()
        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for loan in EquipmentLoan.objects.prefetch_related('items').all():
            _attach_unassigned_photos_to_single_item(loan)
        loans = EquipmentLoan.objects.select_related('created_by', 'returned_by').prefetch_related(
            'photos',
            Prefetch('items', queryset=EquipmentLoanItem.objects.prefetch_related('photos')),
        ).all()
        context['loans'] = loans
        context['form'] = kwargs.get('form') or EquipmentLoanForm()
        context['signed_form'] = EquipmentLoanSignedDocumentForm()
        context['attendant_signature_form'] = EquipmentLoanAttendantSignatureForm()
        context['stored_signature_form'] = kwargs.get('stored_signature_form') or EquipmentLoanStoredSignatureForm()
        context['signature_profiles'] = EquipmentLoanAttendantSignature.objects.all()
        context['email_suggestions'] = GoogleWorkspaceEmail.objects.order_by('email')
        context['photo_form'] = EquipmentLoanPhotoForm()
        context['equipment_loan_create_token'] = self._issue_create_token()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['open_signature_modal'] = kwargs.get('open_signature_modal', False)
        context['total_count'] = loans.count()
        context['documentation_ok_count'] = loans.filter(documentation_ok=True).count()
        context['pending_documentation_count'] = loans.filter(documentation_ok=False).count()
        context['returned_count'] = loans.filter(returned=True).count()
        return context

    def post(self, request, *args, **kwargs):
        mode = (request.POST.get('mode') or 'create').strip()
        if mode == 'create_attendant_signature':
            form = EquipmentLoanStoredSignatureForm(request.POST, request.FILES)
            if form.is_valid():
                signature = form.save(commit=False)
                signature.created_by = request.user
                signature.save()
                messages.success(request, f'Assinatura "{signature.name}" cadastrada com sucesso.')
                return redirect('chamados_emprestimos')

            context = self.get_context_data(stored_signature_form=form, open_signature_modal=True)
            return self.render_to_response(context)

        if mode == 'update_loan_details':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            form = EquipmentLoanUpdateForm(request.POST, instance=loan)
            if form.is_valid():
                loan = form.save()
                _sync_primary_equipment_item(loan)
                messages.success(request, f'Dados do emprestimo de {loan.collaborator_name} atualizados com sucesso.')
                return redirect('chamados_emprestimos')

            messages.error(request, 'Nao foi possivel atualizar os dados do emprestimo. Confira os campos obrigatorios.')
            return redirect('chamados_emprestimos')

        if mode == 'delete_loan':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            collaborator_name = loan.collaborator_name
            loan.delete()
            messages.success(request, f'Emprestimo duplicado de {collaborator_name} apagado com sucesso.')
            return redirect('chamados_emprestimos')

        if mode == 'add_equipment_item':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            row = {
                'equipment_type': (request.POST.get('equipment_type') or '').strip(),
                'equipment_brand': (request.POST.get('equipment_brand') or '').strip(),
                'equipment_model': (request.POST.get('equipment_model') or '').strip(),
                'equipment_serial': (request.POST.get('equipment_serial') or '').strip(),
                'patrimony_tag': (request.POST.get('patrimony_tag') or '').strip(),
                'accessories': (request.POST.get('accessories') or '').strip(),
            }
            _uppercase_equipment_identifiers(row)
            if not row['equipment_type']:
                messages.error(request, 'Informe o tipo do equipamento para adicionar ao emprestimo.')
                return redirect('chamados_emprestimos')
            _save_extra_equipment_items(
                loan,
                [{**row, 'photos_key': 'equipment_photos'}],
                files=request.FILES,
            )
            messages.success(request, f'Equipamento adicionado ao termo de {loan.collaborator_name}.')
            return redirect('chamados_emprestimos')

        if mode == 'update_equipment_item':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            equipment_item = get_object_or_404(EquipmentLoanItem, pk=request.POST.get('equipment_item_id'), loan=loan)
            form = EquipmentLoanItemForm(request.POST, instance=equipment_item)
            if form.is_valid():
                form.save()
                if not loan.items.filter(id__lt=equipment_item.id).exists():
                    loan.equipment_type = equipment_item.equipment_type
                    loan.equipment_brand = equipment_item.equipment_brand
                    loan.equipment_model = equipment_item.equipment_model
                    loan.equipment_serial = equipment_item.equipment_serial
                    loan.patrimony_tag = equipment_item.patrimony_tag
                    loan.accessories = equipment_item.accessories
                    loan.save(update_fields=[
                        'equipment_type',
                        'equipment_brand',
                        'equipment_model',
                        'equipment_serial',
                        'patrimony_tag',
                        'accessories',
                        'updated_at',
                    ])
                else:
                    loan.save(update_fields=['updated_at'])
                messages.success(request, f'Equipamento "{equipment_item.equipment_label}" atualizado com sucesso.')
                return redirect('chamados_emprestimos')

            messages.error(request, 'Nao foi possivel atualizar o equipamento. Confira os campos obrigatorios.')
            return redirect('chamados_emprestimos')

        if mode == 'mark_returned':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            if not loan.returned:
                loan.returned = True
                loan.returned_at = timezone.now()
                loan.returned_by = request.user
                loan.save(update_fields=['returned', 'returned_at', 'returned_by', 'updated_at'])
                messages.success(request, f'Equipamento de {loan.collaborator_name} marcado como devolvido.')
            else:
                messages.info(request, f'Equipamento de {loan.collaborator_name} ja estava marcado como devolvido.')
            return redirect('chamados_emprestimos')

        if mode == 'upload_signed':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            form = EquipmentLoanSignedDocumentForm(request.POST, request.FILES, instance=loan)
            if form.is_valid():
                signed_file = form.cleaned_data.get('signed_document')
                if not signed_file and not loan.signed_document:
                    messages.error(request, 'Anexe o termo assinado antes de marcar a documentacao como OK.')
                    return redirect('chamados_emprestimos')
                loan = form.save(commit=False)
                loan.documentation_ok = True
                loan.documentation_ok_at = timezone.now()
                loan.save(update_fields=['signed_document', 'documentation_ok', 'documentation_ok_at', 'updated_at'])
                messages.success(request, f'Documentacao de {loan.collaborator_name} marcada como OK.')
                return redirect('chamados_emprestimos')

            messages.error(request, 'Nao foi possivel salvar o termo assinado. Verifique o arquivo enviado.')
            return redirect('chamados_emprestimos')

        if mode == 'apply_attendant_signature':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            form = EquipmentLoanAttendantSignatureForm(request.POST)
            if form.is_valid():
                profile = form.cleaned_data['attendant_signature_profile']
                loan.attendant_signature_profile = profile
                loan.attendant_signature = profile.image.name
                loan.save(update_fields=['attendant_signature_profile', 'attendant_signature', 'updated_at'])
                messages.success(request, f'Assinatura "{profile.name}" aplicada ao emprestimo de {loan.collaborator_name}.')
                return redirect('chamados_emprestimos')

            messages.error(request, 'Nao foi possivel aplicar a assinatura. Confira a assinatura selecionada e a senha.')
            return redirect('chamados_emprestimos')

        if mode == 'add_photos':
            loan = get_object_or_404(EquipmentLoan, pk=request.POST.get('loan_id'))
            equipment_item = None
            equipment_item_id = (request.POST.get('equipment_item_id') or '').strip()
            if equipment_item_id:
                equipment_item = get_object_or_404(EquipmentLoanItem, pk=equipment_item_id, loan=loan)
            else:
                equipment_item = _single_equipment_item(loan)
            form = EquipmentLoanPhotoForm(request.POST, request.FILES)
            if form.is_valid():
                created_count = _save_equipment_loan_photos(loan, form.cleaned_data.get('photos'), item=equipment_item)
                target_label = equipment_item.equipment_label if equipment_item else f'emprestimo de {loan.collaborator_name}'
                messages.success(request, f'{created_count} foto(s) adicionada(s) em {target_label}.')
                return redirect('chamados_emprestimos')

            messages.error(request, 'Selecione ao menos uma foto valida para anexar.')
            return redirect('chamados_emprestimos')

        token = (request.POST.get('equipment_loan_create_token') or '').strip()
        if not self._consume_create_token(token):
            messages.warning(request, 'Este emprestimo ja foi enviado. Confira a lista antes de criar outro igual.')
            return redirect('chamados_emprestimos')

        form = EquipmentLoanForm(request.POST, request.FILES)
        if form.is_valid():
            loan = form.save(commit=False)
            loan.created_by = request.user
            signature_profile = form.cleaned_data.get('attendant_signature_profile')
            if signature_profile:
                loan.attendant_signature_profile = signature_profile
                loan.attendant_signature = signature_profile.image.name
            loan.save()
            primary_item = _sync_primary_equipment_item(loan)
            extra_rows = _extra_equipment_rows_from_request(request)
            _save_extra_equipment_items(loan, extra_rows, files=request.FILES)
            _save_equipment_loan_photos(loan, form.cleaned_data.get('photos'), item=primary_item)
            messages.success(request, f'Emprestimo cadastrado. O termo de {loan.collaborator_name} ja pode ser baixado.')
            return redirect('chamados_emprestimos')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class EquipmentLoanTermDownloadView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem baixar termos de emprestimo.'

    def get(self, request, loan_id: int, *args, **kwargs):
        loan = get_object_or_404(EquipmentLoan.objects.prefetch_related('items'), pk=loan_id)
        response = HttpResponse(build_equipment_loan_pdf(loan, generated_by=request.user), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{equipment_loan_term_filename(loan, "emprestimo")}"'
        return response


class EquipmentLoanReturnTermDownloadView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem baixar termos de devolucao.'

    def get(self, request, loan_id: int, *args, **kwargs):
        loan = get_object_or_404(EquipmentLoan.objects.select_related('returned_by').prefetch_related('items'), pk=loan_id)
        response = HttpResponse(build_equipment_return_pdf(loan, generated_by=request.user), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{equipment_loan_term_filename(loan, "devolucao")}"'
        return response


GOOGLE_WORKSPACE_EMAIL_COLUMNS = {
    'first_name': 'First Name [Required]',
    'last_name': 'Last Name [Required]',
    'email': 'Email Address [Required]',
    'status': 'Status [READ ONLY]',
    'last_sign_in': 'Last Sign In [READ ONLY]',
    'email_usage': 'Email Usage [READ ONLY]',
    'drive_usage': 'Drive Usage [READ ONLY]',
    'storage_used': 'Storage Used [READ ONLY]',
    'license_code': 'Licenses [READ ONLY]',
}


def _decode_uploaded_csv(uploaded_file):
    raw_content = uploaded_file.read()
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            return raw_content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_content.decode('utf-8', errors='replace')


def _clean_workspace_csv_value(value):
    return str(value or '').strip()


def _import_google_workspace_emails(uploaded_file, user):
    csv_text = _decode_uploaded_csv(uploaded_file)
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames or []
    missing_columns = [
        column_label
        for column_label in GOOGLE_WORKSPACE_EMAIL_COLUMNS.values()
        if column_label not in fieldnames
    ]
    if missing_columns:
        return {
            'ok': False,
            'message': 'Colunas obrigatorias ausentes no CSV: ' + ', '.join(missing_columns),
        }

    created_count = 0
    updated_count = 0
    unchanged_count = 0
    skipped_count = 0
    deleted_count = 0
    imported_at = timezone.now()
    rows_by_email = {}

    for row in reader:
        data = {
            field_name: _clean_workspace_csv_value(row.get(column_label))
            for field_name, column_label in GOOGLE_WORKSPACE_EMAIL_COLUMNS.items()
        }
        data['email'] = data['email'].lower()
        if not data['email']:
            skipped_count += 1
            continue
        rows_by_email[data['email']] = data

    if not rows_by_email:
        return {
            'ok': False,
            'message': 'Nenhum email valido foi encontrado no CSV. A lista atual foi mantida sem alteracoes.',
        }

    with transaction.atomic():
        for email, data in rows_by_email.items():
            existing = GoogleWorkspaceEmail.objects.filter(email=email).first()
            if existing is None:
                GoogleWorkspaceEmail.objects.create(
                    **data,
                    imported_by=user,
                    last_imported_at=imported_at,
                )
                created_count += 1
                continue

            changed_fields = [
                field_name
                for field_name, value in data.items()
                if getattr(existing, field_name) != value
            ]
            if changed_fields:
                for field_name, value in data.items():
                    setattr(existing, field_name, value)
                existing.imported_by = user
                existing.last_imported_at = imported_at
                existing.save(update_fields=[*changed_fields, 'imported_by', 'last_imported_at', 'updated_at'])
                updated_count += 1
            else:
                existing.imported_by = user
                existing.last_imported_at = imported_at
                existing.save(update_fields=['imported_by', 'last_imported_at', 'updated_at'])
                unchanged_count += 1

        deleted_count, _ = GoogleWorkspaceEmail.objects.exclude(email__in=list(rows_by_email)).delete()

    return {
        'ok': True,
        'created': created_count,
        'updated': updated_count,
        'unchanged': unchanged_count,
        'skipped': skipped_count,
        'deleted': deleted_count,
    }


class GoogleWorkspaceEmailListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/emails.html'
    ti_error_message = 'Somente usuarios TI podem acessar Emails.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = (self.request.GET.get('q') or '').strip()
        emails = GoogleWorkspaceEmail.objects.select_related('imported_by').all()

        for term in query.split():
            emails = emails.filter(
                Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(email__icontains=term)
                | Q(status__icontains=term)
                | Q(last_sign_in__icontains=term)
                | Q(email_usage__icontains=term)
                | Q(drive_usage__icontains=term)
                | Q(storage_used__icontains=term)
                | Q(license_code__icontains=term)
            )

        all_emails = GoogleWorkspaceEmail.objects.all()
        context['emails'] = emails
        context['form'] = kwargs.get('form') or GoogleWorkspaceEmailImportForm()
        context['query'] = query
        context['total_count'] = all_emails.count()
        context['active_count'] = all_emails.filter(status__iexact='Active').count()
        context['suspended_count'] = all_emails.filter(status__iexact='Suspended').count()
        context['filtered_count'] = emails.count()
        context['latest_import'] = all_emails.order_by('-last_imported_at').first()
        return context

    def post(self, request, *args, **kwargs):
        form = GoogleWorkspaceEmailImportForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'Selecione um arquivo CSV valido para importar.')
            return self.render_to_response(self.get_context_data(form=form))

        result = _import_google_workspace_emails(form.cleaned_data['csv_file'], request.user)
        if not result['ok']:
            messages.error(request, result['message'])
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(
            request,
            (
                'Importacao concluida: '
                f'{result["created"]} criados, '
                f'{result["updated"]} atualizados, '
                f'{result["unchanged"]} sem alteracao'
                f', {result["deleted"]} removidos'
                f'{", " + str(result["skipped"]) + " ignorados" if result["skipped"] else ""}.'
            ),
        )
        return redirect('chamados_emails')


class PhoneExtensionListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/ramais.html'
    ti_error_message = 'Somente usuarios TI podem acessar Ramais.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        extensions = PhoneExtension.objects.select_related('created_by').all()
        context['extensions'] = extensions
        context['form'] = kwargs.get('form') or PhoneExtensionForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = extensions.count()
        context['department_count'] = (
            extensions.exclude(department='').values('department').distinct().count()
        )
        context['department_options'] = (
            extensions.exclude(department='')
            .order_by('department')
            .values_list('department', flat=True)
            .distinct()
        )
        context['extension_options'] = (
            extensions.exclude(extension='')
            .order_by('extension')
            .values_list('extension', flat=True)
            .distinct()
        )
        return context

    def post(self, request, *args, **kwargs):
        mode = request.POST.get('mode') or 'create'
        if mode == 'update':
            extension = get_object_or_404(PhoneExtension, pk=request.POST.get('extension_id'))
            form = PhoneExtensionForm(request.POST, instance=extension)
            if form.is_valid():
                form.save()
                messages.success(request, f'Ramal de {extension.name} atualizado com sucesso.')
                return redirect('chamados_ramais')

            messages.error(request, 'Nao foi possivel atualizar o ramal. Confira os campos obrigatorios.')
            return redirect('chamados_ramais')

        if mode == 'delete':
            extension = get_object_or_404(PhoneExtension, pk=request.POST.get('extension_id'))
            extension_name = extension.name or extension.extension
            extension.delete()
            messages.success(request, f'Ramal de {extension_name} apagado com sucesso.')
            return redirect('chamados_ramais')

        form = PhoneExtensionForm(request.POST)
        if form.is_valid():
            extension = form.save(commit=False)
            extension.created_by = request.user
            extension.save()
            messages.success(request, f'Ramal de {extension.name} cadastrado com sucesso.')
            return redirect(f'{reverse("chamados_ramais")}?novo={extension.id}')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class NetworkDeviceListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/ips.html'
    ti_error_message = 'Somente usuarios TI podem acessar IPs.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        devices = NetworkDevice.objects.select_related('created_by').all()
        category_counts = {
            row['category']: row['total']
            for row in devices.values('category').annotate(total=Count('id'))
        }
        context['devices'] = devices
        context['form'] = kwargs.get('form') or NetworkDeviceForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = devices.count()
        context['category_options'] = [
            {
                'value': value,
                'label': label,
                'count': category_counts.get(value, 0),
            }
            for value, label in NetworkDevice.Category.choices
        ]
        context['filled_access_count'] = devices.exclude(access='').count()
        return context

    def post(self, request, *args, **kwargs):
        mode = request.POST.get('mode') or 'create'
        if mode == 'update':
            device = get_object_or_404(NetworkDevice, pk=request.POST.get('device_id'))
            form = NetworkDeviceForm(request.POST, instance=device)
            if form.is_valid():
                form.save()
                messages.success(request, f'IP {device.ip_address} atualizado com sucesso.')
                return redirect('chamados_ips')

            messages.error(request, 'Nao foi possivel atualizar o IP. Confira os campos obrigatorios.')
            return redirect('chamados_ips')

        if mode == 'delete':
            device = get_object_or_404(NetworkDevice, pk=request.POST.get('device_id'))
            device_label = device.ip_address
            device.delete()
            messages.success(request, f'IP {device_label} apagado com sucesso.')
            return redirect('chamados_ips')

        form = NetworkDeviceForm(request.POST)
        if form.is_valid():
            device = form.save(commit=False)
            device.created_by = request.user
            device.save()
            messages.success(request, f'IP {device.ip_address} cadastrado com sucesso.')
            return redirect(f'{reverse("chamados_ips")}?novo={device.id}')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class CompletedServiceListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/servicos_feitos.html'
    ti_error_message = 'Somente usuarios TI podem acessar Servicos feitos.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = list(
            CompletedServiceEntry.objects.select_related('created_by').prefetch_related('attachments').all()
        )
        total_amount = sum((item.amount for item in entries), Decimal('0.00'))
        context['entries'] = entries
        context['form'] = kwargs.get('form') or CompletedServiceEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = len(entries)
        context['with_attachment_count'] = sum(
            1
            for item in entries
            if item.attachment or list(item.attachments.all())
        )
        context['total_amount_display'] = _format_decimal_br(total_amount)
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('mode') == 'update_service_date':
            entry = get_object_or_404(CompletedServiceEntry, pk=request.POST.get('entry_id'))
            service_date = parse_date(request.POST.get('service_date') or '')
            if service_date is None:
                messages.error(request, 'Informe uma data valida para o servico.')
                return redirect('chamados_servicos_feitos')

            entry.service_date = service_date
            entry.save(update_fields=['service_date', 'updated_at'])
            messages.success(request, 'Data do servico atualizada com sucesso.')
            return redirect('chamados_servicos_feitos')

        if request.POST.get('mode') == 'add_attachments':
            entry = get_object_or_404(CompletedServiceEntry, pk=request.POST.get('entry_id'))
            attachments = request.FILES.getlist('attachments')
            if not attachments:
                messages.error(request, 'Selecione ao menos um anexo para adicionar.')
                return redirect('chamados_servicos_feitos')

            for attachment in attachments:
                CompletedServiceAttachment.objects.create(service=entry, file=attachment)
            entry.save(update_fields=['updated_at'])
            messages.success(request, f'Anexo(s) adicionados ao servico "{entry.service_name}" com sucesso.')
            return redirect('chamados_servicos_feitos')

        form = CompletedServiceEntryForm(request.POST, request.FILES)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            attachments = form.cleaned_data.get('attachments') or []
            for attachment in attachments:
                CompletedServiceAttachment.objects.create(service=entry, file=attachment)
            messages.success(request, 'Servico feito cadastrado com sucesso.')
            return redirect('chamados_servicos_feitos')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class ContractListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/contratos.html'
    ti_error_message = 'Somente usuarios TI podem acessar Contratos.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contratos = ContractEntry.objects.select_related('created_by').prefetch_related('attachments').all()
        context['contratos'] = contratos
        context['form'] = kwargs.get('form') or ContractEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = contratos.count()
        context['with_attachment_count'] = sum(
            1
            for contrato in contratos
            if contrato.attachment or list(contrato.attachments.all())
        )
        context['monthly_count'] = contratos.filter(
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL
        ).count()
        context['annual_count'] = contratos.filter(
            payment_schedule=ContractEntry.PaymentSchedule.ANUAL
        ).count()
        context['finished_count'] = contratos.filter(finished_at__isnull=False).count()
        context['today'] = timezone.localdate()
        context['payment_schedule_choices'] = ContractEntry.PaymentSchedule.choices
        context['attachment_form'] = kwargs.get('attachment_form') or ContractAttachmentForm()
        context['contract_attachment_edit'] = kwargs.get('contract_attachment_edit')
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get('mode') == 'finish_contract':
            contract = get_object_or_404(ContractEntry, pk=request.POST.get('contract_id'))
            finished_at_raw = request.POST.get('finished_at') or ''
            finished_at = parse_date(finished_at_raw) if finished_at_raw else timezone.localdate()
            if finished_at is None:
                messages.error(request, 'Informe uma data valida para dar baixa no contrato.')
                return redirect('chamados_contratos')

            contract.finished_at = finished_at
            contract.save(update_fields=['finished_at', 'updated_at'])
            messages.success(request, f'Contrato "{contract.name}" finalizado em {finished_at.strftime("%d/%m/%Y")}.')
            return redirect('chamados_contratos')

        if request.POST.get('mode') == 'reopen_contract':
            contract = get_object_or_404(ContractEntry, pk=request.POST.get('contract_id'))
            contract.finished_at = None
            contract.save(update_fields=['finished_at', 'updated_at'])
            messages.success(request, f'Contrato "{contract.name}" reaberto com sucesso.')
            return redirect('chamados_contratos')

        if request.POST.get('mode') == 'update_contract':
            contract = get_object_or_404(ContractEntry, pk=request.POST.get('contract_id'))
            form = ContractEntryForm(request.POST, request.FILES, instance=contract)
            if form.is_valid():
                contract = form.save()
                attachments = form.cleaned_data.get('attachments') or []
                for attachment in attachments:
                    ContractAttachment.objects.create(contract=contract, file=attachment)
                messages.success(request, f'Contrato "{contract.name}" atualizado com sucesso.')
                return redirect('chamados_contratos')

            messages.error(request, 'Nao foi possivel atualizar o contrato. Verifique os campos informados.')
            context = self.get_context_data()
            return self.render_to_response(context)

        form = ContractEntryForm(request.POST, request.FILES)
        if form.is_valid():
            contrato = form.save(commit=False)
            contrato.created_by = request.user
            contrato.save()
            attachments = form.cleaned_data.get('attachments') or []
            for attachment in attachments:
                ContractAttachment.objects.create(contract=contrato, file=attachment)
            messages.success(request, 'Contrato cadastrado com sucesso.')
            return redirect('chamados_contratos')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class ContractAttachmentUpdateView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Contratos.'

    def post(self, request, contract_id: int, *args, **kwargs):
        contract = get_object_or_404(ContractEntry, pk=contract_id)
        form = ContractAttachmentForm(request.POST, request.FILES, instance=contract)
        if form.is_valid():
            attachments = form.cleaned_data.get('attachments') or []
            for attachment in attachments:
                ContractAttachment.objects.create(contract=contract, file=attachment)
            messages.success(request, f'{len(attachments)} anexo(s) adicionado(s) ao contrato "{contract.name}".')
            return redirect('chamados_contratos')

        list_view = ContractListView()
        list_view.setup(request)
        context = list_view.get_context_data(
            attachment_form=form,
            contract_attachment_edit=contract,
        )
        messages.error(request, 'Nao foi possivel atualizar o anexo do contrato.')
        return list_view.render_to_response(context)


class FuturaDigitalListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/futura_digital.html'
    ti_error_message = 'Somente usuarios TI podem acessar Futura Digital.'
    DEFAULT_FRANCHISE_COPIES = 23000
    DEFAULT_FRANCHISE_AMOUNT = Decimal('1610.00')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = FuturaDigitalEntry.objects.select_related('created_by').order_by('-reference_month', '-id')
        futura_edit = kwargs.get('futura_edit')
        context['entries'] = entries
        context['form'] = kwargs.get('form') or FuturaDigitalEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['futura_edit'] = futura_edit
        context['edit_form'] = kwargs.get('edit_form') or FuturaDigitalEntryForm(
            instance=futura_edit,
            prefix='edit_futura',
        )
        context['open_edit_modal'] = kwargs.get('open_edit_modal', False)
        context['total_count'] = entries.count()
        total_copies = sum(item.copies_count for item in entries)
        context['total_copies'] = total_copies
        context['total_copies_display'] = f'{total_copies:,}'.replace(',', '.')
        total_paid = sum(item.paid_amount for item in entries)
        normalized_total = f'{total_paid:.2f}'
        integer_part, decimal_part = normalized_total.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        context['total_paid_display'] = f'{integer_part},{decimal_part}'
        context['latest_reference'] = entries[0].reference_label if entries else '-'
        if entries:
            latest_entry = entries[0]
            default_franchise_copies = latest_entry.franchise_copies or self.DEFAULT_FRANCHISE_COPIES
            default_franchise_amount = latest_entry.franchise_amount or self.DEFAULT_FRANCHISE_AMOUNT
        else:
            default_franchise_copies = self.DEFAULT_FRANCHISE_COPIES
            default_franchise_amount = self.DEFAULT_FRANCHISE_AMOUNT
        context['default_franchise_copies'] = f'{default_franchise_copies:,}'.replace(',', '.')
        amount_normalized = f'{default_franchise_amount:.2f}'
        amount_integer, amount_decimal = amount_normalized.split('.')
        amount_integer = f'{int(amount_integer):,}'.replace(',', '.')
        context['default_franchise_amount'] = f'{amount_integer},{amount_decimal}'

        monthly_totals = {}
        for item in entries:
            month_key = item.reference_month.replace(day=1)
            if month_key not in monthly_totals:
                monthly_totals[month_key] = {
                    'pb_total_copies': 0,
                    'color_total_copies': 0,
                    'paid': Decimal('0.00'),
                }
            monthly_totals[month_key]['pb_total_copies'] += (item.franchise_copies + item.excess_copies)
            monthly_totals[month_key]['color_total_copies'] += item.color_copies
            monthly_totals[month_key]['paid'] += item.paid_amount

        pb_values = [data['pb_total_copies'] for data in monthly_totals.values()]
        color_values = [data['color_total_copies'] for data in monthly_totals.values()]
        min_pb = min(pb_values, default=0)
        max_pb = max(pb_values, default=0)
        min_color = min(color_values, default=0)
        max_color = max(color_values, default=0)

        def scaled_height(value: int, min_value: int, max_value: int) -> int:
            if value <= 0:
                return 0
            if max_value == min_value:
                return 65
            # Scale between 20% and 100% to enhance visible month-over-month differences.
            return 20 + int(((value - min_value) / (max_value - min_value)) * 80)

        monthly_chart = []
        for month_key, data in monthly_totals.items():
            paid = data['paid']
            normalized_paid = f'{paid:.2f}'
            paid_integer, paid_decimal = normalized_paid.split('.')
            paid_integer = f'{int(paid_integer):,}'.replace(',', '.')
            pb_total_copies = data['pb_total_copies']
            color_total_copies = data['color_total_copies']

            monthly_chart.append(
                {
                    'label': month_key.strftime('%m/%Y'),
                    'paid_display': f'{paid_integer},{paid_decimal}',
                    'pb_total_copies_display': f'{pb_total_copies:,}'.replace(',', '.'),
                    'color_total_copies_display': f'{color_total_copies:,}'.replace(',', '.'),
                    'pb_bar_height': scaled_height(pb_total_copies, min_pb, max_pb),
                    'color_bar_height': scaled_height(color_total_copies, min_color, max_color),
                }
            )
        context['monthly_chart'] = monthly_chart
        return context

    def post(self, request, *args, **kwargs):
        form = FuturaDigitalEntryForm(request.POST, request.FILES)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            messages.success(request, 'Registro da Futura Digital cadastrado com sucesso.')
            return redirect('chamados_futura_digital')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class FuturaDigitalUpdateView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Futura Digital.'

    def post(self, request, entry_id: int, *args, **kwargs):
        entry = get_object_or_404(FuturaDigitalEntry, pk=entry_id)
        form = FuturaDigitalEntryForm(
            request.POST,
            request.FILES,
            instance=entry,
            prefix='edit_futura',
        )
        if form.is_valid():
            form.save()
            messages.success(request, 'Registro da Futura Digital atualizado com sucesso.')
            return redirect('chamados_futura_digital')

        list_view = FuturaDigitalListView()
        list_view.setup(request)
        context = list_view.get_context_data(
            edit_form=form,
            open_edit_modal=True,
            futura_edit=entry,
        )
        messages.error(request, 'Nao foi possivel atualizar o registro da Futura Digital.')
        return list_view.render_to_response(context)


class TipListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/dicas.html'
    ti_error_message = 'Somente usuarios TI podem acessar Dicas.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dica_filter = (self.request.GET.get('categoria') or '').strip()
        dicas = TipEntry.objects.select_related('created_by').all()
        if dica_filter in {choice[0] for choice in TipEntry.Category.choices}:
            dicas = dicas.filter(category=dica_filter)
        else:
            dica_filter = ''

        tip_edit = kwargs.get('tip_edit')
        context['dicas'] = dicas
        context['form'] = kwargs.get('form') or TipEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['tip_edit'] = tip_edit
        context['edit_form'] = kwargs.get('edit_form') or TipEntryForm(instance=tip_edit, prefix='edit_tip')
        context['open_edit_modal'] = kwargs.get('open_edit_modal', False)
        context['category_filter'] = dica_filter
        context['category_choices'] = TipEntry.Category.choices
        context['total_count'] = TipEntry.objects.count()
        context['geral_count'] = TipEntry.objects.filter(category=TipEntry.Category.GERAL).count()
        context['configuracao_count'] = TipEntry.objects.filter(category=TipEntry.Category.CONFIGURACAO).count()
        context['resolucao_count'] = TipEntry.objects.filter(category=TipEntry.Category.RESOLUCAO).count()
        return context

    def post(self, request, *args, **kwargs):
        form = TipEntryForm(request.POST, request.FILES)
        if form.is_valid():
            dica = form.save(commit=False)
            dica.created_by = request.user
            dica.save()
            messages.success(request, 'Dica cadastrada com sucesso.')
            return redirect('chamados_dicas')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


class TipUpdateView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Dicas.'

    def post(self, request, tip_id: int, *args, **kwargs):
        tip = get_object_or_404(TipEntry, pk=tip_id)
        form = TipEntryForm(request.POST, request.FILES, instance=tip, prefix='edit_tip')
        if form.is_valid():
            form.save()
            messages.success(request, 'Dica atualizada com sucesso.')
            return redirect('chamados_dicas')

        list_view = TipListView()
        list_view.setup(request)
        context = list_view.get_context_data(
            edit_form=form,
            open_edit_modal=True,
            tip_edit=tip,
        )
        return list_view.render_to_response(context)


class TipDeleteView(TiRequiredMixin, View):
    ti_error_message = 'Somente usuarios TI podem acessar Dicas.'

    def post(self, request, tip_id: int, *args, **kwargs):
        tip = get_object_or_404(TipEntry, pk=tip_id)
        if not _can_delete_tip(request.user, tip):
            messages.error(request, 'Somente fabiano.polone pode apagar dicas.')
            return redirect('chamados_dicas')

        label = tip.title
        tip.delete()
        messages.success(request, f'Dica "{label}" apagada com sucesso.')
        return redirect('chamados_dicas')
logger = logging.getLogger(__name__)
