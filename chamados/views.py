from datetime import datetime
import re
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

from .forms import RequisitionForm, RequisitionStatusForm, TicketCreateForm, TicketPendingForm
from .models import (
    Insumo,
    Requisition,
    RequisitionBudget,
    RequisitionUpdate,
    Ticket,
    TicketAttendance,
    TicketPending,
    TicketUpdate,
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
        title = (item_data.get('title') or '').strip()
        amount_raw = item_data.get('amount')
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

        if row_id and row_id in existing:
            row = existing[row_id]
        else:
            row = RequisitionBudget(requisition=requisition)

        row.title = title
        row.amount = amount
        row.notes = notes
        row.parent_budget = parent_budget

        file_obj = request.FILES.get(file_key) if file_key else None
        if file_obj:
            row.evidence_file = file_obj
        elif clear_file and row.pk:
            row.evidence_file = None

        row.save()
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
        'title': item.title,
        'amount': str(item.amount),
        'notes': item.notes,
        'parent_id': item.parent_budget_id,
        'evidence_url': evidence_url,
        'evidence_is_image': _is_image_file_name(evidence_name),
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
        total = sum((item.amount for item in root_budgets), Decimal('0.00'))
        rows.append(
            {
                'requisition': requisition,
                'root_budgets': root_lines,
                'total': total,
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
                f'- {budget.get("title") or "-"} | R$ {budget.get("amount") or "0.00"}'
            )
            for sub in budget.get('sub_budgets') or []:
                lines.append(
                    f'  - Sub: {sub.get("title") or "-"} | R$ {sub.get("amount") or "0.00"}'
                )
    lines.extend(['', f'Total principal: R$ {payload_item.get("total") or "0.00"}'])
    return '\n'.join(lines)


class TicketListView(LoginRequiredMixin, TemplateView):
    template_name = 'chamados/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ti_user = is_ti_user(self.request.user)
        if ti_user:
            ti_attendants = _get_ti_attendants()
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
                resolvidos=Count('id', filter=Q(status=Ticket.Status.RESOLVIDO), distinct=True),
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
            context['ti_attendants'] = ti_attendants
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
            context['ti_attendants'] = []
            context['selected_attendant'] = None
            context['consultation_mode'] = False
            context['counts'] = None
        context['is_ti'] = ti_user
        return context


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
            title=f'Pendencia: {title_core}',
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
                queryset=RequisitionBudget.objects.order_by('parent_budget_id', 'id'),
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
            ticket.status = Ticket.Status.ABERTO
            ticket.closed_at = None
        else:
            ticket.status = Ticket.Status.RESOLVIDO
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
