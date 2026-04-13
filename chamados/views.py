from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, TemplateView

from users.access import is_ti_user

from .forms import TicketCreateForm, TicketPendingForm
from .models import Ticket, TicketAttendance, TicketPending, TicketUpdate


def _safe_next_url(request):
    candidate = (request.POST.get('next') or '').strip()
    if candidate.startswith('/') and not candidate.startswith('//'):
        return candidate
    return reverse('chamados_list')


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def _can_ti_handle_ticket(user, ticket: Ticket) -> bool:
    has_any_attendance = ticket.attendances.exists()
    if not has_any_attendance:
        return True
    return ticket.attendances.filter(attendant=user).exists()


def _can_view_ticket(user, ticket: Ticket) -> bool:
    if is_ti_user(user):
        return _can_ti_handle_ticket(user, ticket)
    return ticket.created_by_id == getattr(user, 'id', None)


def _get_visible_tickets_for_ti(user):
    return (
        Ticket.objects.select_related('created_by')
        .prefetch_related('attendances', 'updates__author')
        .filter(Q(attendances__isnull=True) | Q(attendances__attendant=user))
        .distinct()
    )


def _build_timer_meta(ticket: Ticket, user):
    now = timezone.now()
    my_attendances = [row for row in ticket.attendances.all() if row.attendant_id == user.id]
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


class TicketListView(LoginRequiredMixin, TemplateView):
    template_name = 'chamados/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ti_user = is_ti_user(self.request.user)
        if ti_user:
            tickets = _get_visible_tickets_for_ti(self.request.user)
            context['tickets'] = tickets
            context['ticket_rows'] = [
                (ticket, _build_timer_meta(ticket, self.request.user)) for ticket in tickets
            ]
            context['counts'] = {
                'abertos': tickets.filter(status=Ticket.Status.ABERTO).count(),
                'em_atendimento': tickets.filter(status=Ticket.Status.EM_ATENDIMENTO).count(),
                'aguardando_usuario': tickets.filter(status=Ticket.Status.AGUARDANDO_USUARIO).count(),
                'resolvidos': tickets.filter(status=Ticket.Status.RESOLVIDO).count(),
            }
        else:
            tickets = Ticket.objects.select_related('created_by').filter(
                created_by=self.request.user
            )
            context['tickets'] = tickets
            context['ticket_rows'] = [(ticket, None) for ticket in tickets]
            context['counts'] = None
        context['is_ti'] = ti_user
        return context


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


class TicketPendingListView(LoginRequiredMixin, TemplateView):
    template_name = 'chamados/pending_list.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente atendentes TI podem acessar pendencias.')
            return redirect('chamados_list')
        return super().dispatch(request, *args, **kwargs)

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


class TicketPendingDeleteView(LoginRequiredMixin, View):
    def post(self, request, pending_id: int, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente atendentes TI podem excluir pendencias.')
            return redirect('chamados_list')
        pending = get_object_or_404(TicketPending, pk=pending_id, attendant=request.user)
        pending.delete()
        messages.success(request, 'Pendencia removida.')
        return redirect('chamados_pending_list')


class TicketPendingCreateTicketView(LoginRequiredMixin, View):
    def post(self, request, pending_id: int, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente atendentes TI podem criar chamados por pendencia.')
            return redirect('chamados_list')

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


class TicketDetailView(LoginRequiredMixin, DetailView):
    template_name = 'chamados/detail.html'
    model = Ticket
    pk_url_kwarg = 'ticket_id'
    context_object_name = 'ticket'

    def get_queryset(self):
        return Ticket.objects.select_related('created_by').prefetch_related(
            'updates__author',
            'attendances',
        )

    def dispatch(self, request, *args, **kwargs):
        ticket = self.get_object()
        if not _can_view_ticket(request.user, ticket):
            messages.error(request, 'Voce nao possui permissao para visualizar este chamado.')
            return redirect('chamados_list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_ti'] = is_ti_user(self.request.user)
        if context['is_ti']:
            context['timer_meta'] = _build_timer_meta(self.object, self.request.user)
        return context


class TicketTimerActionView(LoginRequiredMixin, View):
    def post(self, request, ticket_id: int, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente usuarios TI podem atender chamados.')
            return redirect(_safe_next_url(request))

        ticket = get_object_or_404(
            Ticket.objects.prefetch_related('attendances').select_related('created_by'),
            pk=ticket_id,
        )
        if not _can_ti_handle_ticket(request.user, ticket):
            messages.error(request, 'Este chamado ja esta sob atendimento de outro atendente TI.')
            return redirect(_safe_next_url(request))

        action = (request.POST.get('action') or '').strip().lower()
        note = (request.POST.get('note') or '').strip()
        now = timezone.now()

        my_running = ticket.attendances.filter(
            attendant=request.user,
            ended_at__isnull=True,
        ).order_by('-started_at').first()
        running_by_other = ticket.attendances.filter(ended_at__isnull=True).exclude(
            attendant=request.user
        ).exists()

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
            ticket.status = Ticket.Status.AGUARDANDO_USUARIO
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
