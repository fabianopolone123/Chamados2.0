from django.conf import settings
from django.db import models
from django.db.models import Sum


class Ticket(models.Model):
    class Priority(models.TextChoices):
        BAIXA = 'baixa', 'Baixa'
        MEDIA = 'media', 'Media'
        ALTA = 'alta', 'Alta'
        CRITICA = 'critica', 'Critica'
        PROGRAMADA = 'programada', 'Programada'

    class Status(models.TextChoices):
        ABERTO = 'aberto', 'Aberto'
        EM_ATENDIMENTO = 'em_atendimento', 'Em atendimento'
        AGUARDANDO_USUARIO = 'aguardando_usuario', 'Aguardando usuario'
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


class TicketAutoPauseReview(models.Model):
    attendance = models.OneToOneField(
        TicketAttendance,
        on_delete=models.CASCADE,
        related_name='auto_pause_review',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Revisao de pausa automatica'
        verbose_name_plural = 'Revisoes de pausas automaticas'

    def __str__(self):
        return f'Revisao auto-pause #{self.id} - Ticket #{self.attendance.ticket_id}'


class TicketPending(models.Model):
    attendant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ticket_pendings',
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'Pendencia de atendimento'
        verbose_name_plural = 'Pendencias de atendimento'

    def __str__(self):
        return f'Pendencia #{self.id} - {self.attendant}'


class Requisition(models.Model):
    class Kind(models.TextChoices):
        FISICA = 'fisica', 'Fisica'
        DIGITAL = 'digital', 'Digital'

    class Status(models.TextChoices):
        PENDENTE_APROVACAO = 'pendente_aprovacao', 'Pendente de aprovacao'
        APROVADA = 'aprovada', 'Aprovada'
        NAO_APROVADA = 'nao_aprovada', 'Nao aprovada'
        PARCIALMENTE_ENTREGUE = 'parcialmente_entregue', 'Parcialmente entregue'
        ENTREGUE = 'entregue', 'Entregue'

    code = models.CharField(max_length=24, unique=True, null=True, blank=True)
    title = models.CharField(max_length=180)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.FISICA)
    request_text = models.TextField()
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDENTE_APROVACAO,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='requisitions',
    )
    requested_at = models.DateField(null=True, blank=True)
    approved_at = models.DateField(null=True, blank=True)
    partially_received_at = models.DateField(null=True, blank=True)
    received_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'Requisicao'
        verbose_name_plural = 'Requisicoes'

    def __str__(self):
        return f'{self.code or "REQ"} - {self.title}'

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.code:
            generated_code = f'REQ-{self.pk:05d}'
            type(self).objects.filter(pk=self.pk).update(code=generated_code)
            self.code = generated_code

    @property
    def budget_total(self):
        return self.budgets.filter(parent_budget__isnull=True).aggregate(total=Sum('amount')).get('total') or 0


class RequisitionBudget(models.Model):
    requisition = models.ForeignKey(
        Requisition,
        on_delete=models.CASCADE,
        related_name='budgets',
    )
    parent_budget = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='sub_budgets',
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=160)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)
    evidence_file = models.FileField(upload_to='requisitions/budgets/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['parent_budget_id', 'id']
        verbose_name = 'Orcamento de requisicao'
        verbose_name_plural = 'Orcamentos de requisicao'

    def __str__(self):
        prefix = 'Suborcamento' if self.parent_budget_id else 'Orcamento'
        return f'{prefix} #{self.id} - {self.requisition.code or self.requisition_id}'


class RequisitionUpdate(models.Model):
    requisition = models.ForeignKey(
        Requisition,
        on_delete=models.CASCADE,
        related_name='updates',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='requisition_updates',
    )
    message = models.TextField()
    status_to = models.CharField(max_length=30, choices=Requisition.Status.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        verbose_name = 'Atualizacao de requisicao'
        verbose_name_plural = 'Atualizacoes de requisicao'

    def __str__(self):
        return f'Atualizacao #{self.id} - {self.requisition_id}'


class Insumo(models.Model):
    item = models.CharField(max_length=120)
    date = models.DateField()
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    name = models.CharField(max_length=200)
    department = models.CharField(max_length=120, blank=True, default='')
    legacy_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name = 'Insumo'
        verbose_name_plural = 'Insumos'

    def __str__(self):
        return f'{self.item} - {self.name} ({self.date:%d/%m/%Y})'


class Starlink(models.Model):
    class PaymentMethod(models.TextChoices):
        PIX = 'pix', 'Pix'
        CARTAO = 'cartao', 'Cartao'

    name = models.CharField(max_length=160)
    location = models.CharField(max_length=180)
    email = models.EmailField(max_length=254)
    password_encrypted = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    payment_method = models.CharField(
        max_length=12,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CARTAO,
    )
    card_final = models.CharField(max_length=4, blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_starlinks',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Starlink'
        verbose_name_plural = 'Starlinks'

    def __str__(self):
        return self.name


class Documentation(models.Model):
    name = models.CharField(max_length=180)
    notes = models.TextField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_documentations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Documentacao'
        verbose_name_plural = 'Documentacoes'

    def __str__(self):
        return self.name
