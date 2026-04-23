from datetime import datetime
import re
import logging
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView
from decimal import Decimal, InvalidOperation
import json

from users.access import is_ti_user

from . import whatsapp
from .excel_export import export_attendant_logs_to_excel, get_attendant_default_workbook_path
from .forms import (
    ContractAttachmentForm,
    ContractEntryForm,
    DocumentEntryForm,
    FuturaDigitalEntryForm,
    RequisitionForm,
    RequisitionStatusForm,
    StarlinkEditForm,
    StarlinkForm,
    TicketCreateForm,
    TicketPendingForm,
    TipEntryForm,
)
from .models import (
    ContractEntry,
    DocumentEntry,
    FuturaDigitalEntry,
    Insumo,
    Requisition,
    RequisitionBudget,
    RequisitionBudgetHistory,
    RequisitionUpdate,
    Starlink,
    TicketAutoPauseReview,
    Ticket,
    TicketAttendance,
    TicketPending,
    TicketUpdate,
    TipEntry,
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
    normalized = str(raw_value or '').strip().replace('R$', '').replace(' ', '').replace(',', '.')
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


def _format_budget_value_summary(amount, quantity, discount_amount, final_total):
    summary = [
        f'Qtd: {quantity}',
        f'Unit.: R$ {_format_decimal_br(amount)}',
        f'Total bruto: R$ {_format_decimal_br(Decimal(amount or 0) * Decimal(quantity or 0))}',
    ]
    if Decimal(discount_amount or 0):
        summary.append(f'Desconto: R$ {_format_decimal_br(discount_amount)}')
    summary.append(f'Total final: R$ {_format_decimal_br(final_total)}')
    return ' | '.join(summary)


def _create_budget_history_entry(budget: RequisitionBudget, author, message: str):
    RequisitionBudgetHistory.objects.create(
        budget=budget,
        author=author,
        message=message,
        store_name=budget.store_name,
        amount=budget.amount,
        quantity=budget.quantity,
        line_total=budget.line_total,
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
        amount_raw = item_data.get('amount')
        quantity_raw = item_data.get('quantity')
        discount_raw = item_data.get('discount_amount')
        approval_status_raw = item_data.get('approval_status')
        receipt_status_raw = item_data.get('receipt_status')
        received_quantity_raw = item_data.get('received_quantity')
        notes = (item_data.get('notes') or '').strip()
        clear_file = str(item_data.get('clear_file') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        file_key = (item_data.get('file_key') or '').strip()
        temp_key = (item_data.get('temp_key') or '').strip()

        if not title and not amount_raw:
            return None
        if not title:
            raise ValueError('Informe o titulo de todos os orcamentos.')

        try:
            amount = _parse_amount(amount_raw)
        except InvalidOperation:
            raise ValueError(f'Valor invalido no orcamento "{title}".')
        try:
            quantity = _parse_quantity(quantity_raw)
        except ValueError:
            raise ValueError(f'Quantidade invalida no orcamento "{title}".')
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
                'amount': row.amount,
                'quantity': row.quantity,
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
        row.amount = amount
        row.quantity = quantity
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
        if previous_snapshot is None:
            _create_budget_history_entry(
                row,
                request.user,
                f'Orcamento cadastrado. {_format_budget_value_summary(row.amount, row.quantity, row.discount_amount, row.final_total)}',
            )
        else:
            changed_labels = []
            if previous_snapshot['store_name'] != row.store_name or previous_snapshot['title'] != row.title or previous_snapshot['notes'] != row.notes or previous_snapshot['parent_budget_id'] != row.parent_budget_id:
                changed_labels.append('dados gerais')
            if previous_snapshot['amount'] != row.amount or previous_snapshot['quantity'] != row.quantity or previous_snapshot['discount_amount'] != row.discount_amount:
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
                    f'Orcamento atualizado ({", ".join(changed_labels)}). {_format_budget_value_summary(row.amount, row.quantity, row.discount_amount, row.final_total)}',
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
    return {
        'id': item.id,
        'store_name': item.store_name,
        'title': item.title,
        'amount': str(item.amount),
        'quantity': item.quantity,
        'line_total': str(item.line_total),
        'discount_amount': str(item.discount_amount),
        'final_total': str(item.final_total),
        'approval_status': item.approval_status,
        'approval_status_display': item.get_approval_status_display(),
        'receipt_status': item.receipt_status,
        'receipt_status_display': item.get_receipt_status_display(),
        'received_quantity': item.received_quantity,
        'remaining_quantity': item.remaining_quantity,
        'line_total_display': _format_decimal_br(item.line_total),
        'discount_amount_display': _format_decimal_br(item.discount_amount),
        'final_total_display': _format_decimal_br(item.final_total),
        'notes': item.notes,
        'parent_id': item.parent_budget_id,
        'evidence_url': evidence_url,
        'evidence_is_image': _is_image_file_name(evidence_name),
        'history_entries': [
            {
                'message': entry.message,
                'created_at': timezone.localtime(entry.created_at).strftime('%d/%m/%Y %H:%M'),
                'author': entry.author.username,
                'store_name': entry.store_name,
                'amount_display': _format_decimal_br(entry.amount),
                'quantity': entry.quantity,
                'line_total_display': _format_decimal_br(entry.line_total),
                'discount_amount_display': _format_decimal_br(entry.discount_amount),
                'final_total_display': _format_decimal_br(entry.final_total),
                'approval_status_display': entry.get_approval_status_display(),
                'receipt_status_display': entry.get_receipt_status_display(),
                'received_quantity': entry.received_quantity,
                'remaining_quantity': entry.remaining_quantity,
            }
            for entry in history_entries
        ],
        'sub_budgets': [_serialize_budget_line(child, children_map) for child in children],
    }


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
        total = sum((item.final_total for item in budgets), Decimal('0.00'))
        budget_summaries = [
            {
                'title': item.title,
                'store_name': item.store_name,
                'value_display': _format_decimal_br(item.final_total),
                'approval_status_display': item.get_approval_status_display(),
                'receipt_status_display': item.get_receipt_status_display(),
            }
            for item in budgets
        ]
        rows.append(
            {
                'requisition': requisition,
                'root_budgets': root_lines,
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
                'status': requisition.status,
                'status_display': requisition.get_status_display(),
                'requested_by': requisition.requested_by.username,
                'budgets': root_lines,
                'total': str(total),
                'total_display': _format_decimal_br(total),
            }
        )
    return rows, requisitions_payload


def _build_requisition_share_text(payload_item):
    code = payload_item.get('code') or 'REQ'
    lines = [
        f'Requisicao {code}',
        f'Titulo: {payload_item.get("title") or "-"}',
        f'Tipo: {payload_item.get("kind_display") or "-"}',
        f'Status: {payload_item.get("status_display") or "-"}',
        f'Solicitante: {payload_item.get("requested_by") or "-"}',
        '',
        'Requisicao:',
        payload_item.get('request_text') or '-',
    ]

    budgets = payload_item.get('budgets') or []
    if budgets:
        lines.extend(['', 'Orcamentos:'])
        for budget in budgets:
            lines.append(
                f'- Loja: {budget.get("store_name") or "-"} | {budget.get("title") or "-"} | Qtd: {budget.get("quantity") or 1} | Unit.: R$ {_format_decimal_br(budget.get("amount") or "0.00")} | Desconto: R$ {_format_decimal_br(budget.get("discount_amount") or "0.00")} | Total final: R$ {_format_decimal_br(budget.get("final_total") or "0.00")} | Aprovacao: {budget.get("approval_status_display") or "-"} | Recebimento: {budget.get("receipt_status_display") or "-"}'
            )
            for sub in budget.get('sub_budgets') or []:
                lines.append(
                    f'  - Sub: Loja: {sub.get("store_name") or "-"} | {sub.get("title") or "-"} | Qtd: {sub.get("quantity") or 1} | Unit.: R$ {_format_decimal_br(sub.get("amount") or "0.00")} | Desconto: R$ {_format_decimal_br(sub.get("discount_amount") or "0.00")} | Total final: R$ {_format_decimal_br(sub.get("final_total") or "0.00")} | Aprovacao: {sub.get("approval_status_display") or "-"} | Recebimento: {sub.get("receipt_status_display") or "-"}'
                )
    lines.extend(['', f'Total geral: R$ {payload_item.get("total_display") or _format_decimal_br(payload_item.get("total") or "0.00")}'])
    return '\n'.join(lines)


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
                    .exclude(status=Ticket.Status.FECHADO)
                    .distinct()
                )

            counts = tickets.aggregate(
                abertos=Count('id', filter=Q(status=Ticket.Status.ABERTO), distinct=True),
                em_atendimento=Count('id', filter=Q(status=Ticket.Status.EM_ATENDIMENTO), distinct=True),
                aguardando_usuario=Count('id', filter=Q(status=Ticket.Status.AGUARDANDO_USUARIO), distinct=True),
                fechados=Count('id', filter=Q(status=Ticket.Status.FECHADO), distinct=True),
            )
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
            context['chamados_xlsx_default_path'] = (
                getattr(settings, 'CHAMADOS_XLSX_PATH', '') or getattr(settings, 'CHAMADOS_XLSX_SERVER_PATH', '')
            )
            context['attendant_default_workbook_paths'] = {
                attendant.username: get_attendant_default_workbook_path(attendant)
                for attendant in spreadsheet_attendants
            }
            context['selected_attendant'] = selected_attendant
            context['consultation_mode'] = consultation_mode
            context['counts'] = counts
        else:
            tickets = Ticket.objects.select_related('created_by').filter(
                created_by=self.request.user
            )
            context['tickets'] = tickets
            context['ticket_rows'] = [(ticket, None) for ticket in tickets]
            context['closed_tickets'] = []
            context['closed_tickets_count'] = 0
            context['auto_pause_reviews_count'] = 0
            context['ti_attendants'] = []
            context['spreadsheet_attendants'] = []
            context['chamados_xlsx_default_path'] = ''
            context['attendant_default_workbook_paths'] = {}
            context['selected_attendant'] = None
            context['consultation_mode'] = False
            context['counts'] = None
        context['is_ti'] = ti_user
        return context


class TicketSpreadsheetExportView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem preencher a planilha.'

    def post(self, request, *args, **kwargs):
        attendant_id = (request.POST.get('attendant_id') or '').strip()
        workbook_path = (request.POST.get('workbook_path') or '').strip()
        next_url = _safe_next_url(request)

        attendant = _get_ti_attendants().filter(id=attendant_id).first()
        if attendant is None:
            messages.error(request, 'Escolha um atendente TI valido para preencher a planilha.')
            return redirect(next_url)

        ok, exported_count, detail = export_attendant_logs_to_excel(
            attendant=attendant,
            workbook_path=workbook_path,
        )
        if ok:
            if exported_count > 0:
                messages.success(request, detail)
            else:
                messages.info(request, detail)
        else:
            messages.error(request, detail)
        return redirect(next_url)


class ClosedTicketsDataView(TiRequiredMixin, View):
    ti_error_message = 'Somente atendentes TI podem acessar chamados fechados.'

    def get(self, request, *args, **kwargs):
        closed_tickets = (
            Ticket.objects.select_related('created_by')
            .filter(status=Ticket.Status.FECHADO)
            .order_by('-updated_at', '-id')
        )
        payload = [
            {
                'id': ticket.id,
                'title': ticket.title,
                'created_by': ticket.created_by.username if ticket.created_by_id else '-',
                'updated_at': timezone.localtime(ticket.updated_at).strftime('%d/%m/%Y %H:%M'),
                'detail_url': reverse('chamados_detail', args=[ticket.id]),
            }
            for ticket in closed_tickets
        ]
        return JsonResponse({'items': payload})


class TicketCreateView(LoginRequiredMixin, FormView):
    template_name = 'chamados/new.html'
    form_class = TicketCreateForm
    success_url = reverse_lazy('chamados_list')

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
            requisitions = requisitions.filter(status=status_filter)
        else:
            status_filter = ''
        requisitions = requisitions.distinct()

        requisition_rows, requisitions_payload = _build_requisition_rows(requisitions)
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
        context['counts'] = {
            'pendente_aprovacao': requisitions.filter(status=Requisition.Status.PENDENTE_APROVACAO).count(),
            'aprovada': requisitions.filter(status=Requisition.Status.APROVADA).count(),
            'nao_aprovada': requisitions.filter(status=Requisition.Status.NAO_APROVADA).count(),
            'parcialmente_entregue': requisitions.filter(status=Requisition.Status.PARCIALMENTE_ENTREGUE).count(),
            'entregue': requisitions.filter(status=Requisition.Status.ENTREGUE).count(),
        }
        return context


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

                if creating:
                    RequisitionUpdate.objects.create(
                        requisition=saved,
                        author=request.user,
                        message='Requisicao cadastrada.',
                        status_to=saved.status,
                    )
                    messages.success(request, f'Requisicao {saved.code} cadastrada com sucesso.')
                else:
                    RequisitionUpdate.objects.create(
                        requisition=saved,
                        author=request.user,
                        message='Requisicao atualizada.',
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
        if not _can_ti_handle_ticket(request.user, ticket):
            messages.error(request, 'Este chamado ja esta sob atendimento de outro atendente TI.')
            return redirect(_safe_next_url(request))

        action = (request.POST.get('action') or '').strip().lower()
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
            }
            if pause_status not in valid_pause_statuses:
                messages.error(request, 'Escolha se o chamado volta para aberto ou aguardando usuario.')
                my_running.ended_at = None
                my_running.end_action = ''
                my_running.note = ''
                my_running.save(update_fields=['ended_at', 'end_action', 'note'])
                return redirect(_safe_next_url(request))
            ticket.status = pause_status
            ticket.closed_at = None
        else:
            ticket.status = Ticket.Status.FECHADO
            ticket.closed_at = now
        ticket.save(update_fields=['status', 'closed_at', 'updated_at'])

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


class ContractListView(TiRequiredMixin, TemplateView):
    template_name = 'chamados/contratos.html'
    ti_error_message = 'Somente usuarios TI podem acessar Contratos.'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contratos = ContractEntry.objects.select_related('created_by').all()
        context['contratos'] = contratos
        context['form'] = kwargs.get('form') or ContractEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = contratos.count()
        context['with_attachment_count'] = contratos.filter(attachment__isnull=False).exclude(attachment='').count()
        context['monthly_count'] = contratos.filter(
            payment_schedule=ContractEntry.PaymentSchedule.MENSAL
        ).count()
        context['annual_count'] = contratos.filter(
            payment_schedule=ContractEntry.PaymentSchedule.ANUAL
        ).count()
        context['attachment_form'] = kwargs.get('attachment_form') or ContractAttachmentForm()
        context['contract_attachment_edit'] = kwargs.get('contract_attachment_edit')
        return context

    def post(self, request, *args, **kwargs):
        form = ContractEntryForm(request.POST, request.FILES)
        if form.is_valid():
            contrato = form.save(commit=False)
            contrato.created_by = request.user
            contrato.save()
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
            form.save()
            messages.success(request, f'Anexo do contrato "{contract.name}" atualizado com sucesso.')
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entries = FuturaDigitalEntry.objects.select_related('created_by').all()
        context['entries'] = entries
        context['form'] = kwargs.get('form') or FuturaDigitalEntryForm()
        context['open_create_modal'] = kwargs.get('open_create_modal', False)
        context['total_count'] = entries.count()
        context['total_copies'] = sum(item.copies_count for item in entries)
        total_paid = sum(item.paid_amount for item in entries)
        normalized_total = f'{total_paid:.2f}'
        integer_part, decimal_part = normalized_total.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        context['total_paid_display'] = f'{integer_part},{decimal_part}'
        context['latest_reference'] = entries[0].reference_label if entries else '-'
        return context

    def post(self, request, *args, **kwargs):
        form = FuturaDigitalEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            messages.success(request, 'Registro da Futura Digital cadastrado com sucesso.')
            return redirect('chamados_futura_digital')

        context = self.get_context_data(form=form, open_create_modal=True)
        return self.render_to_response(context)


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
