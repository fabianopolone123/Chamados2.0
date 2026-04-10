from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import DetailView, FormView, TemplateView

from users.access import is_ti_user

from .forms import TicketCreateForm, TicketTriageForm
from .models import Ticket, TicketUpdate


def _can_view_ticket(user, ticket: Ticket) -> bool:
    if is_ti_user(user):
        return True
    return ticket.created_by_id == getattr(user, 'id', None)


class TicketListView(LoginRequiredMixin, TemplateView):
    template_name = 'chamados/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ti_user = is_ti_user(self.request.user)
        if ti_user:
            tickets = Ticket.objects.select_related('created_by', 'assigned_to').all()
            context['tickets'] = tickets
            context['counts'] = {
                'abertos': tickets.filter(status=Ticket.Status.ABERTO).count(),
                'em_atendimento': tickets.filter(status=Ticket.Status.EM_ATENDIMENTO).count(),
                'aguardando_usuario': tickets.filter(status=Ticket.Status.AGUARDANDO_USUARIO).count(),
                'resolvidos': tickets.filter(status=Ticket.Status.RESOLVIDO).count(),
            }
        else:
            context['tickets'] = Ticket.objects.select_related('created_by', 'assigned_to').filter(
                created_by=self.request.user
            )
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


class TicketDetailView(LoginRequiredMixin, DetailView):
    template_name = 'chamados/detail.html'
    model = Ticket
    pk_url_kwarg = 'ticket_id'
    context_object_name = 'ticket'

    def get_queryset(self):
        return Ticket.objects.select_related('created_by', 'assigned_to').prefetch_related(
            'updates__author'
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
            context['triage_form'] = TicketTriageForm(instance=self.object)
        return context


class TicketTriageView(LoginRequiredMixin, FormView):
    form_class = TicketTriageForm
    template_name = 'chamados/detail.html'

    def dispatch(self, request, *args, **kwargs):
        if not is_ti_user(request.user):
            messages.error(request, 'Somente usuarios TI podem atender chamados.')
            return redirect('chamados_list')
        return super().dispatch(request, *args, **kwargs)

    def get_ticket(self):
        return get_object_or_404(
            Ticket.objects.select_related('created_by', 'assigned_to').prefetch_related('updates__author'),
            pk=self.kwargs['ticket_id'],
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.get_ticket()
        return kwargs

    def form_valid(self, form):
        ticket = self.get_ticket()
        previous_status = ticket.status
        ticket = form.save(commit=False)
        if ticket.status in {Ticket.Status.RESOLVIDO, Ticket.Status.FECHADO}:
            if not ticket.closed_at:
                ticket.closed_at = timezone.now()
        else:
            ticket.closed_at = None
        ticket.save()

        response_message = (form.cleaned_data.get('response_message') or '').strip()
        if response_message:
            TicketUpdate.objects.create(
                ticket=ticket,
                author=self.request.user,
                message=response_message,
                status_to=ticket.status,
            )
        elif previous_status != ticket.status:
            TicketUpdate.objects.create(
                ticket=ticket,
                author=self.request.user,
                message=f'Status alterado para "{ticket.get_status_display()}".',
                status_to=ticket.status,
            )

        messages.success(self.request, f'Chamado #{ticket.id} atualizado.')
        return redirect(reverse('chamados_detail', kwargs={'ticket_id': ticket.id}))
