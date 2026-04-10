from django.conf import settings
from django.db import models


class Ticket(models.Model):
    class Priority(models.TextChoices):
        BAIXA = 'baixa', 'Baixa'
        MEDIA = 'media', 'Media'
        ALTA = 'alta', 'Alta'
        CRITICA = 'critica', 'Critica'

    class Status(models.TextChoices):
        ABERTO = 'aberto', 'Aberto'
        EM_ATENDIMENTO = 'em_atendimento', 'Em atendimento'
        AGUARDANDO_USUARIO = 'aguardando_usuario', 'Aguardando usuario'
        RESOLVIDO = 'resolvido', 'Resolvido'
        FECHADO = 'fechado', 'Fechado'

    title = models.CharField(max_length=180)
    description = models.TextField()
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIA,
    )
    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.ABERTO,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_tickets',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'Chamado'
        verbose_name_plural = 'Chamados'

    def __str__(self):
        return f'#{self.id} - {self.title}'


class TicketUpdate(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='updates',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ticket_updates',
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    status_to = models.CharField(max_length=25, choices=Ticket.Status.choices, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        verbose_name = 'Atualizacao de chamado'
        verbose_name_plural = 'Atualizacoes de chamados'

    def __str__(self):
        return f'Atualizacao #{self.id} - Ticket #{self.ticket_id}'


class TicketAttendance(models.Model):
    class EndAction(models.TextChoices):
        PAUSE = 'pause', 'Pause'
        STOP = 'stop', 'Stop'

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='attendances',
    )
    attendant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ticket_attendances',
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    end_action = models.CharField(max_length=10, choices=EndAction.choices, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-started_at', '-id']
        verbose_name = 'Ciclo de atendimento'
        verbose_name_plural = 'Ciclos de atendimento'

    def __str__(self):
        return f'Ticket #{self.ticket_id} - {self.attendant}'
