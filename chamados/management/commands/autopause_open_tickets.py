from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from chamados.models import Ticket, TicketAttendance, TicketAutoPauseReview, TicketUpdate


class Command(BaseCommand):
    help = 'Pausa automaticamente os chamados em atendimento no fim do expediente.'

    def handle(self, *args, **options):
        now = timezone.now()
        running_attendances = list(
            TicketAttendance.objects.select_related('ticket', 'attendant')
            .filter(ended_at__isnull=True)
            .order_by('started_at', 'id')
        )

        paused_count = 0
        for attendance in running_attendances:
            with transaction.atomic():
                attendance.ended_at = now
                attendance.end_action = TicketAttendance.EndAction.PAUSE
                attendance.note = ''
                attendance.save(update_fields=['ended_at', 'end_action', 'note'])

                ticket = attendance.ticket
                ticket.status = Ticket.Status.ABERTO
                ticket.closed_at = None
                ticket.save(update_fields=['status', 'closed_at', 'updated_at'])

                TicketUpdate.objects.create(
                    ticket=ticket,
                    author=attendance.attendant,
                    message='Pause automatico no fim do expediente. Pendente de complemento no proximo acesso.',
                    status_to=ticket.status,
                )

                TicketAutoPauseReview.objects.get_or_create(attendance=attendance)
                paused_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Pausas automaticas executadas: {paused_count}'
            )
        )
