from decimal import Decimal
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Ticket(models.Model):
    class FailureType(models.TextChoices):
        SOFTWARE = 'software', 'Software'
        EQUIPAMENTO = 'equipamento', 'Equipamento'
        HARDWARE = 'hardware', 'Hardware'
        HUMANA = 'humana', 'Humana'
        NA = 'na', 'N/A'

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
        AGUARDANDO_AUTORIZACAO = 'aguardando_autorizacao', 'Aguardando autorizacao'
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
    failure_type = models.CharField(
        max_length=80,
        default=FailureType.NA,
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

    def get_failure_type_display(self):
        return dict(self.FailureType.choices).get(self.failure_type, self.failure_type or self.FailureType.NA.label)


class TicketFailureType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Categoria'
        verbose_name_plural = 'Categorias'

    def __str__(self):
        return self.name


class HiddenTicketFailureType(models.Model):
    normalized_name = models.CharField(max_length=120, unique=True)
    display_name = models.CharField(max_length=120, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_name', 'normalized_name']
        verbose_name = 'Categoria oculta'
        verbose_name_plural = 'Categorias ocultas'

    def __str__(self):
        return self.display_name or self.normalized_name


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


class TicketUpdateAttachment(models.Model):
    update = models.ForeignKey(
        TicketUpdate,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='ticket_updates/attachments/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Anexo de atualizacao de chamado'
        verbose_name_plural = 'Anexos de atualizacoes de chamados'

    def __str__(self):
        return self.file.name

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1]

    @property
    def is_image(self):
        return self.filename.lower().endswith((
            '.apng',
            '.avif',
            '.bmp',
            '.gif',
            '.jpeg',
            '.jpg',
            '.png',
            '.svg',
            '.webp',
        ))


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
    exported_at = models.DateTimeField(null=True, blank=True)
    exported_path = models.CharField(max_length=255, blank=True, default='')
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
    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
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
        legacy_freight_amount = self.freight_amount
        if not isinstance(legacy_freight_amount, Decimal):
            legacy_freight_amount = Decimal(str(legacy_freight_amount or '0.00'))
        return sum((item.final_total for item in self.budgets.all()), Decimal('0.00')) + legacy_freight_amount


class RequisitionBudget(models.Model):
    class Currency(models.TextChoices):
        BRL = 'BRL', 'Real'
        USD = 'USD', 'Dolar'

    class ApprovalStatus(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        APROVADO = 'aprovado', 'Aprovado'
        NAO_APROVADO = 'nao_aprovado', 'Nao aprovado'

    class ReceiptStatus(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        PARCIAL = 'parcial', 'Recebido parcial'
        RECEBIDO = 'recebido', 'Recebido'

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
    store_name = models.CharField(max_length=160, blank=True, default='')
    title = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.BRL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDENTE,
    )
    receipt_status = models.CharField(
        max_length=20,
        choices=ReceiptStatus.choices,
        default=ReceiptStatus.PENDENTE,
    )
    received_quantity = models.PositiveIntegerField(default=0)
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

    @property
    def line_total(self):
        return (self.amount or Decimal('0.00')) * Decimal(self.quantity or 0)

    @property
    def final_total(self):
        total = self.line_total + (self.freight_amount or Decimal('0.00')) - (self.discount_amount or Decimal('0.00'))
        return total if total >= Decimal('0.00') else Decimal('0.00')

    @property
    def remaining_quantity(self):
        return max((self.quantity or 0) - (self.received_quantity or 0), 0)


class RequisitionBudgetHistory(models.Model):
    budget = models.ForeignKey(
        RequisitionBudget,
        on_delete=models.CASCADE,
        related_name='history_entries',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='requisition_budget_history_entries',
    )
    message = models.TextField()
    store_name = models.CharField(max_length=160, blank=True, default='')
    currency = models.CharField(max_length=3, choices=RequisitionBudget.Currency.choices, default=RequisitionBudget.Currency.BRL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    final_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    approval_status = models.CharField(
        max_length=20,
        choices=RequisitionBudget.ApprovalStatus.choices,
        default=RequisitionBudget.ApprovalStatus.PENDENTE,
    )
    receipt_status = models.CharField(
        max_length=20,
        choices=RequisitionBudget.ReceiptStatus.choices,
        default=RequisitionBudget.ReceiptStatus.PENDENTE,
    )
    received_quantity = models.PositiveIntegerField(default=0)
    remaining_quantity = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Historico de orcamento de requisicao'
        verbose_name_plural = 'Historicos de orcamento de requisicao'

    def __str__(self):
        return f'Historico #{self.id} - Orcamento {self.budget_id}'


class RequisitionBudgetAttachment(models.Model):
    budget = models.ForeignKey(
        RequisitionBudget,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='requisitions/budget_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at', 'id']
        verbose_name = 'Anexo de orcamento de requisicao'
        verbose_name_plural = 'Anexos de orcamento de requisicao'

    def __str__(self):
        return f'Anexo #{self.id} - Orcamento {self.budget_id}'


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
    starlink_identifier = models.CharField(max_length=80, blank=True, default='')
    software_version = models.CharField(max_length=80, blank=True, default='')
    serial_number = models.CharField(max_length=120, blank=True, default='')
    kit_number = models.CharField(max_length=120, blank=True, default='')
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


class DocumentEntry(models.Model):
    name = models.CharField(max_length=180)
    notes = models.TextField()
    attachment = models.FileField(upload_to='documents/', null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'

    def __str__(self):
        return self.name


class PhoneExtension(models.Model):
    name = models.CharField(max_length=180)
    department = models.CharField(max_length=120, blank=True, default='')
    phone = models.CharField(max_length=40, blank=True, default='')
    extension = models.CharField(max_length=30)
    email = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_phone_extensions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'name', 'extension']
        verbose_name = 'Ramal'
        verbose_name_plural = 'Ramais'

    def __str__(self):
        return f'{self.name} - {self.extension}'


class TiResponsibility(models.Model):
    title = models.CharField(max_length=180)
    description = models.TextField(blank=True, default='')
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='ti_responsibilities',
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_ti_responsibilities',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title', 'id']
        verbose_name = 'Responsabilidade TI'
        verbose_name_plural = 'Responsabilidades TI'

    def __str__(self):
        return self.title


class SoftwareAsset(models.Model):
    name = models.CharField(max_length=180)
    license_quantity = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_software_assets',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Software'
        verbose_name_plural = 'Softwares'

    def __str__(self):
        return self.name

    @property
    def registered_licenses_count(self):
        return self.licenses.count()


class SoftwareLicense(models.Model):
    class ExpirationType(models.TextChoices):
        INDETERMINADO = 'indeterminado', 'Indeterminado'
        EXPIRA_EM = 'expira_em', 'Prazo para expirar'

    software = models.ForeignKey(
        SoftwareAsset,
        on_delete=models.CASCADE,
        related_name='licenses',
    )
    serial = models.CharField(max_length=240, blank=True, default='')
    linked_email = models.EmailField(max_length=254, blank=True, default='')
    expiration_type = models.CharField(
        max_length=20,
        choices=ExpirationType.choices,
        default=ExpirationType.INDETERMINADO,
    )
    expires_at = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=80, blank=True, default='')
    card_final = models.CharField(max_length=4, blank=True, default='')
    assigned_user = models.CharField(max_length=180, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_software_licenses',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['software__name', 'assigned_user', 'linked_email', 'id']
        verbose_name = 'Licenca de software'
        verbose_name_plural = 'Licencas de software'

    def __str__(self):
        owner = self.assigned_user or self.linked_email or self.serial or 'Sem identificacao'
        return f'{self.software.name} - {owner}'

    @property
    def expiration_label(self):
        if self.expiration_type == self.ExpirationType.INDETERMINADO:
            return 'Indeterminado'
        if self.expires_at:
            return self.expires_at.strftime('%d/%m/%Y')
        return 'Prazo nao informado'


class NetworkDevice(models.Model):
    class Category(models.TextChoices):
        SERVERS = 'servers', 'Servidores'
        SWITCHES = 'switches', 'Switchs'
        IDFACE_TURNSTILES = 'idface_turnstiles', 'IdFace + Catracas'
        PRINTERS = 'printers', 'Impressoras'
        WIFI = 'wifi', 'Wi-Fi'
        MONITORING = 'monitoring', 'Zabbix & Grafana'

    category = models.CharField(max_length=30, choices=Category.choices)
    ip_address = models.CharField(max_length=45, unique=True)
    name = models.CharField(max_length=180, blank=True, default='')
    manufacturer = models.CharField(max_length=180, blank=True, default='')
    mac_address = models.CharField(max_length=80, blank=True, default='')
    access = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_network_devices',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'ip_address']
        verbose_name = 'IP'
        verbose_name_plural = 'IPs'

    def __str__(self):
        label = self.name or self.manufacturer or self.ip_address
        return f'{self.ip_address} - {label}'


class GoogleWorkspaceEmail(models.Model):
    first_name = models.CharField(max_length=120, blank=True, default='')
    last_name = models.CharField(max_length=120, blank=True, default='')
    email = models.EmailField(max_length=254, unique=True)
    status = models.CharField(max_length=40, blank=True, default='')
    last_sign_in = models.CharField(max_length=40, blank=True, default='')
    email_usage = models.CharField(max_length=40, blank=True, default='')
    drive_usage = models.CharField(max_length=40, blank=True, default='')
    storage_used = models.CharField(max_length=40, blank=True, default='')
    license_code = models.CharField(max_length=120, blank=True, default='')
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='imported_google_workspace_emails',
    )
    last_imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['email']
        verbose_name = 'Email Google Workspace'
        verbose_name_plural = 'Emails Google Workspace'

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip() or '-'

    @property
    def is_active_account(self):
        return self.status.lower() == 'active'


class CompletedServiceEntry(models.Model):
    service_name = models.CharField(max_length=180)
    company = models.CharField(max_length=180)
    description = models.TextField()
    service_date = models.DateField(default=timezone.localdate)
    attachment = models.FileField(upload_to='completed_services/', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_completed_services',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-id']
        verbose_name = 'Servico feito'
        verbose_name_plural = 'Servicos feitos'

    def __str__(self):
        return f'{self.service_name} - {self.company}'

    @property
    def amount_display(self):
        normalized = f'{self.amount:.2f}'
        integer_part, decimal_part = normalized.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        return f'{integer_part},{decimal_part}'


class CompletedServiceAttachment(models.Model):
    service = models.ForeignKey(
        CompletedServiceEntry,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='completed_services/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Anexo de servico feito'
        verbose_name_plural = 'Anexos de servicos feitos'

    def __str__(self):
        return self.file.name


class ContractEntry(models.Model):
    class PaymentSchedule(models.TextChoices):
        MENSAL = 'mensal', 'Mensal'
        ANUAL = 'anual', 'Anual'
        PAGAMENTO_UNICO = 'pagamento_unico', 'Pagamento único'

    name = models.CharField(max_length=180)
    notes = models.TextField(blank=True, default='')
    attachment = models.FileField(upload_to='contracts/', null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    contract_start = models.DateField(null=True, blank=True)
    contract_end = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=80, blank=True, default='')
    card_final = models.CharField(max_length=4, blank=True, default='')
    payment_schedule = models.CharField(
        max_length=20,
        choices=PaymentSchedule.choices,
        default=PaymentSchedule.MENSAL,
    )
    finished_at = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_contracts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Contrato'
        verbose_name_plural = 'Contratos'

    def __str__(self):
        return self.name

    @property
    def is_finished(self):
        return bool(self.finished_at)

    @property
    def amount_display(self):
        if self.amount in (None, ''):
            return '-'
        normalized = f'{self.amount:.2f}'
        integer_part, decimal_part = normalized.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        return f'{integer_part},{decimal_part}'

    @property
    def contract_duration_label(self):
        if not self.contract_start or not self.contract_end:
            return '-'
        if self.contract_end < self.contract_start:
            return '-'

        total_months = (self.contract_end.year - self.contract_start.year) * 12 + (
            self.contract_end.month - self.contract_start.month
        )
        if self.contract_end.day < self.contract_start.day:
            total_months -= 1

        if total_months < 1:
            return 'Menos de 1 mes'
        if total_months % 12 == 0:
            years = total_months // 12
            return f'{years} ano' if years == 1 else f'{years} anos'
        if total_months > 12:
            years = total_months // 12
            months = total_months % 12
            year_label = f'{years} ano' if years == 1 else f'{years} anos'
            month_label = f'{months} mes' if months == 1 else f'{months} meses'
            return f'{year_label} e {month_label}'
        return f'{total_months} mes' if total_months == 1 else f'{total_months} meses'


class ContractAttachment(models.Model):
    contract = models.ForeignKey(
        ContractEntry,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    file = models.FileField(upload_to='contracts/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Anexo de contrato'
        verbose_name_plural = 'Anexos de contratos'

    def __str__(self):
        return self.file.name


def _format_decimal_br(value) -> str:
    if value in (None, ''):
        return '-'
    normalized = f'{Decimal(value):.2f}'
    integer_part, decimal_part = normalized.split('.')
    integer_part = f'{int(integer_part):,}'.replace(',', '.')
    return f'{integer_part},{decimal_part}'


class ContractAmountHistory(models.Model):
    contract = models.ForeignKey(
        ContractEntry,
        on_delete=models.CASCADE,
        related_name='amount_history_entries',
    )
    previous_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    new_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='contract_amount_history_entries',
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['changed_at', 'id']
        verbose_name = 'Historico de valor de contrato'
        verbose_name_plural = 'Historico de valores de contratos'

    def __str__(self):
        return f'Contrato #{self.contract_id} - {self.changed_at:%d/%m/%Y %H:%M:%S}'

    @property
    def previous_amount_display(self):
        return _format_decimal_br(self.previous_amount)

    @property
    def new_amount_display(self):
        return _format_decimal_br(self.new_amount)

    @property
    def timeline_label(self):
        if self.previous_amount in (None, ''):
            return f'Valor inicial: R$ {self.new_amount_display}'
        return f'R$ {self.previous_amount_display} -> R$ {self.new_amount_display}'


def _format_contract_history_value(value) -> str:
    if value in (None, ''):
        return '-'
    if isinstance(value, bool):
        return 'Sim' if value else 'Nao'
    if isinstance(value, Decimal):
        return _format_decimal_br(value)
    return str(value)


class ContractFieldHistory(models.Model):
    contract = models.ForeignKey(
        ContractEntry,
        on_delete=models.CASCADE,
        related_name='field_history_entries',
    )
    custom_field = models.ForeignKey(
        'ContractCustomField',
        on_delete=models.SET_NULL,
        related_name='history_entries',
        null=True,
        blank=True,
    )
    field_name = models.CharField(max_length=80)
    field_label = models.CharField(max_length=120)
    previous_value = models.TextField(blank=True, default='')
    new_value = models.TextField(blank=True, default='')
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='contract_field_history_entries',
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['changed_at', 'id']
        verbose_name = 'Historico de campo de contrato'
        verbose_name_plural = 'Historico de campos de contratos'

    def __str__(self):
        target = self.field_label or self.field_name
        return f'{target} - {self.contract_id}'

    @property
    def previous_value_display(self):
        return _format_contract_history_value(self.previous_value)

    @property
    def new_value_display(self):
        return _format_contract_history_value(self.new_value)

    @property
    def timeline_label(self):
        if self.previous_value in (None, ''):
            return f'{self.field_label}: {self.new_value_display}'
        return f'{self.field_label}: {self.previous_value_display} -> {self.new_value_display}'


class ContractCustomField(models.Model):
    class FieldType(models.TextChoices):
        TEXT = 'texto', 'Texto'
        NUMBER = 'numero', 'Numero'
        BOOLEAN = 'sim_nao', 'Sim / Nao'

    contract = models.ForeignKey(
        ContractEntry,
        on_delete=models.CASCADE,
        related_name='custom_fields',
    )
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=20, choices=FieldType.choices)
    value = models.TextField(blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        verbose_name = 'Campo personalizado de contrato'
        verbose_name_plural = 'Campos personalizados de contratos'

    def __str__(self):
        return f'{self.label} - {self.contract_id}'

    @property
    def field_type_label(self):
        return dict(self.FieldType.choices).get(self.field_type, self.field_type)

    @property
    def display_value(self):
        if self.field_type == self.FieldType.NUMBER:
            try:
                return _format_decimal_br(Decimal(str(self.value)))
            except Exception:
                return self.value or '-'
        if self.field_type == self.FieldType.BOOLEAN:
            normalized = (self.value or '').strip().lower()
            if normalized in {'sim', 'true', '1', 'yes', 'on'}:
                return 'Sim'
            if normalized in {'nao', 'não', 'false', '0', 'no', 'off'}:
                return 'Nao'
        return self.value or '-'


class EquipmentLoan(models.Model):
    collaborator_name = models.CharField(max_length=180)
    collaborator_company = models.CharField(max_length=180)
    collaborator_document = models.CharField(max_length=80, blank=True, default='')
    collaborator_email = models.EmailField(max_length=254, blank=True, default='')
    collaborator_phone = models.CharField(max_length=40, blank=True, default='')
    equipment_type = models.CharField(max_length=120)
    equipment_brand = models.CharField(max_length=120, blank=True, default='')
    equipment_model = models.CharField(max_length=160, blank=True, default='')
    equipment_serial = models.CharField(max_length=120, blank=True, default='')
    patrimony_tag = models.CharField(max_length=80, blank=True, default='')
    accessories = models.TextField(blank=True, default='')
    loan_date = models.DateField(default=timezone.localdate)
    expected_return_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    attendant_signature = models.FileField(upload_to='equipment_loans/signatures/', null=True, blank=True)
    attendant_signature_profile = models.ForeignKey(
        'EquipmentLoanAttendantSignature',
        on_delete=models.SET_NULL,
        related_name='equipment_loans',
        null=True,
        blank=True,
    )
    attendant_signature_x_offset = models.IntegerField(
        default=0,
        help_text='Ajuste horizontal especifico desta assinatura no PDF.',
    )
    attendant_signature_y_offset = models.IntegerField(
        default=0,
        help_text='Ajuste vertical especifico desta assinatura no PDF.',
    )
    signed_document = models.FileField(upload_to='equipment_loans/signed/', null=True, blank=True)
    documentation_ok = models.BooleanField(default=False)
    documentation_ok_at = models.DateTimeField(null=True, blank=True)
    returned = models.BooleanField(default=False)
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='returned_equipment_loans',
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_equipment_loans',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-loan_date', '-id']
        verbose_name = 'Emprestimo de equipamento'
        verbose_name_plural = 'Emprestimos de equipamentos'

    def __str__(self):
        return f'{self.collaborator_name} - {self.equipment_type}'

    def save(self, *args, **kwargs):
        self.equipment_model = (self.equipment_model or '').strip().upper()
        self.equipment_serial = (self.equipment_serial or '').strip().upper()
        super().save(*args, **kwargs)

    @property
    def equipment_label(self):
        parts = [
            self.equipment_type,
            self.equipment_brand,
            self.equipment_model,
        ]
        label = ' '.join(part for part in parts if part).strip()
        return label or '-'

    @property
    def documentation_status_label(self):
        return 'Documentacao OK' if self.documentation_ok else 'Aguardando documento assinado'

    @property
    def return_status_label(self):
        return 'Devolvido' if self.returned else 'Em aberto'


class EquipmentLoanItem(models.Model):
    loan = models.ForeignKey(
        EquipmentLoan,
        on_delete=models.CASCADE,
        related_name='items',
    )
    equipment_type = models.CharField(max_length=120)
    equipment_brand = models.CharField(max_length=120, blank=True, default='')
    equipment_model = models.CharField(max_length=160, blank=True, default='')
    equipment_serial = models.CharField(max_length=120, blank=True, default='')
    patrimony_tag = models.CharField(max_length=80, blank=True, default='')
    accessories = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Equipamento do emprestimo'
        verbose_name_plural = 'Equipamentos do emprestimo'

    def __str__(self):
        return self.equipment_label

    def save(self, *args, **kwargs):
        self.equipment_model = (self.equipment_model or '').strip().upper()
        self.equipment_serial = (self.equipment_serial or '').strip().upper()
        super().save(*args, **kwargs)

    @property
    def equipment_label(self):
        parts = [
            self.equipment_type,
            self.equipment_brand,
            self.equipment_model,
        ]
        label = ' '.join(part for part in parts if part).strip()
        return label or '-'


class EquipmentLoanAttendantSignature(models.Model):
    name = models.CharField(max_length=120)
    image = models.FileField(upload_to='equipment_loans/signature_profiles/')
    authorization_password_hash = models.CharField(max_length=256)
    signature_x_offset = models.IntegerField(
        default=0,
        help_text='Ajuste horizontal da imagem no PDF (positivo = direita, negativo = esquerda).',
    )
    signature_y_offset = models.IntegerField(
        default=0,
        help_text='Ajuste vertical da imagem no PDF (positivo = cima, negativo = baixo).',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_equipment_loan_signatures',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        verbose_name = 'Assinatura autorizada para emprestimo'
        verbose_name_plural = 'Assinaturas autorizadas para emprestimos'

    def __str__(self):
        return self.name

    def set_authorization_password(self, raw_password):
        self.authorization_password_hash = make_password(raw_password)

    def check_authorization_password(self, raw_password):
        return bool(raw_password) and check_password(raw_password, self.authorization_password_hash)


class EquipmentLoanPhoto(models.Model):
    loan = models.ForeignKey(
        EquipmentLoan,
        on_delete=models.CASCADE,
        related_name='photos',
    )
    item = models.ForeignKey(
        EquipmentLoanItem,
        on_delete=models.SET_NULL,
        related_name='photos',
        null=True,
        blank=True,
    )
    image = models.FileField(upload_to='equipment_loans/photos/')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Foto de equipamento emprestado'
        verbose_name_plural = 'Fotos de equipamentos emprestados'

    def __str__(self):
        return self.image.name


class FuturaDigitalEntry(models.Model):
    name = models.CharField(max_length=180, blank=True, default='')
    invoice = models.CharField(max_length=80, blank=True, default='')
    reference_month = models.DateField()
    color_copies = models.PositiveIntegerField(default=0)
    franchise_copies = models.PositiveIntegerField(default=23000)
    franchise_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('1610.00'))
    excess_copies = models.PositiveIntegerField(default=0)
    copies_count = models.PositiveIntegerField()
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    document = models.FileField(upload_to='futura_digital/documents/', null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_futura_digital_entries',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-reference_month', '-id']
        verbose_name = 'Futura Digital'
        verbose_name_plural = 'Futura Digital'

    def __str__(self):
        name = self.name or 'Futura Digital'
        return f'{name} - {self.reference_month:%m/%Y}'

    @property
    def reference_label(self):
        return self.reference_month.strftime('%m/%Y')

    @property
    def color_copies_display(self):
        return f'{self.color_copies:,}'.replace(',', '.')

    @property
    def franchise_copies_display(self):
        return f'{self.franchise_copies:,}'.replace(',', '.')

    @property
    def franchise_amount_display(self):
        normalized = f'{self.franchise_amount:.2f}'
        integer_part, decimal_part = normalized.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        return f'{integer_part},{decimal_part}'

    @property
    def excess_copies_display(self):
        return f'{self.excess_copies:,}'.replace(',', '.')

    @property
    def copies_count_display(self):
        return f'{self.copies_count:,}'.replace(',', '.')

    @property
    def paid_amount_display(self):
        normalized = f'{self.paid_amount:.2f}'
        integer_part, decimal_part = normalized.split('.')
        integer_part = f'{int(integer_part):,}'.replace(',', '.')
        return f'{integer_part},{decimal_part}'


class TipEntry(models.Model):
    class Category(models.TextChoices):
        GERAL = 'geral', 'Geral'
        CONFIGURACAO = 'configuracao', 'Configuracao'
        RESOLUCAO = 'resolucao', 'Resolucao'

    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.GERAL,
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    attachment = models.FileField(upload_to='tips/', null=True, blank=True)
    legacy_attachment_path = models.CharField(max_length=255, blank=True, default='')
    legacy_id = models.PositiveIntegerField(null=True, blank=True, unique=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_tips',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'title', 'id']
        verbose_name = 'Dica'
        verbose_name_plural = 'Dicas'

    def __str__(self):
        return self.title


class WhatsAppConfig(models.Model):
    """Singleton — sempre pk=1. Configuracoes de WhatsApp editaveis pela UI."""

    class Provider(models.TextChoices):
        AUTO = '', 'Automatico'
        WAPI = 'wapi', 'W-API (w-api.app)'
        WEBHOOK = 'webhook', 'Webhook personalizado'

    notifications_enabled = models.BooleanField(default=False, verbose_name='Notificacoes ativadas')
    provider = models.CharField(max_length=10, choices=Provider.choices, blank=True, default='', verbose_name='Provider')
    group_jid = models.CharField(max_length=100, blank=True, default='', verbose_name='JID do grupo')
    send_group_on_new_ticket = models.BooleanField(default=True, verbose_name='Enviar ao grupo em novo chamado')
    template_new_ticket = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='Template mensagem novo chamado',
        help_text='Variaveis: {urgencia}, {solicitante}, {title}, {chamado}',
    )
    # W-API
    wapi_token = models.CharField(max_length=300, blank=True, default='', verbose_name='W-API Token')
    wapi_instance = models.CharField(max_length=200, blank=True, default='', verbose_name='W-API Instance ID')
    wapi_base_url = models.CharField(max_length=300, blank=True, default='', verbose_name='W-API Base URL')
    # Webhook
    webhook_url = models.CharField(max_length=500, blank=True, default='', verbose_name='Webhook URL')
    webhook_token = models.CharField(max_length=300, blank=True, default='', verbose_name='Webhook Token (Bearer)')

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao WhatsApp'
        verbose_name_plural = 'Configuracao WhatsApp'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> 'WhatsAppConfig':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Configuracao WhatsApp'
