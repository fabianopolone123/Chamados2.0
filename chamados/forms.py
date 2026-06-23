from django import forms
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib.auth import get_user_model
from django.forms import formset_factory
from django.utils import timezone

from .models import (
    CompletedServiceEntry,
    ContractCustomField,
    ContractEntry,
    DocumentEntry,
    EquipmentLoan,
    EquipmentLoanAttendantSignature,
    EquipmentLoanItem,
    FuturaDigitalEntry,
    HiddenTicketFailureType,
    NetworkDevice,
    PhoneExtension,
    Requisition,
    SoftwareAsset,
    SoftwareLicense,
    Starlink,
    Ticket,
    TicketFailureType,
    TicketPending,
    TiResponsibility,
    TipEntry,
    WhatsAppConfig,
)


NEW_FAILURE_TYPE_VALUE = '__new__'


def _normalize_label(value: str) -> str:
    return unicodedata.normalize('NFKD', (value or '').strip().lower()).encode('ascii', 'ignore').decode('ascii')


def hidden_failure_type_keys() -> set[str]:
    return set(HiddenTicketFailureType.objects.values_list('normalized_name', flat=True))


def is_failure_type_hidden(*values: str) -> bool:
    hidden_keys = hidden_failure_type_keys()
    return any(_normalize_label(value) in hidden_keys for value in values if value)


def _builtin_failure_type_value(value: str) -> str:
    normalized = _normalize_label(value)
    for choice_value, choice_label in Ticket.FailureType.choices:
        if normalized in {_normalize_label(choice_value), _normalize_label(choice_label)}:
            return choice_value
    return ''


def ticket_failure_type_choices(*, include_blank=True, include_new=True, include_hidden=False):
    choices = []
    if include_blank:
        choices.append(('', 'Selecione...'))
    hidden_keys = hidden_failure_type_keys() if not include_hidden else set()
    choices.extend(
        (value, label)
        for value, label in Ticket.FailureType.choices
        if include_hidden or (_normalize_label(value) not in hidden_keys and _normalize_label(label) not in hidden_keys)
    )
    builtin_labels = {_normalize_label(label) for _, label in Ticket.FailureType.choices}
    builtin_values = {_normalize_label(value) for value, _ in Ticket.FailureType.choices}
    for item in TicketFailureType.objects.order_by('name'):
        normalized = _normalize_label(item.name)
        if normalized in builtin_labels or normalized in builtin_values:
            continue
        if not include_hidden and normalized in hidden_keys:
            continue
        choices.append((item.name, item.name))
    if include_new:
        choices.append((NEW_FAILURE_TYPE_VALUE, 'Nova categoria'))
    return choices


def resolve_failure_type_value(selected_value: str, new_name: str = ''):
    selected_value = (selected_value or '').strip()
    new_name = (new_name or '').strip()
    valid_values = {value for value, _ in ticket_failure_type_choices(include_blank=False, include_new=False)}

    if selected_value == NEW_FAILURE_TYPE_VALUE:
        if not new_name:
            return '', 'Informe o nome da nova categoria.'
        if is_failure_type_hidden(new_name):
            return '', 'Esta categoria foi excluida das opcoes. Use outro nome.'
        builtin_value = _builtin_failure_type_value(new_name)
        if builtin_value:
            return builtin_value, ''
        existing = TicketFailureType.objects.filter(name__iexact=new_name).first()
        if existing:
            return existing.name, ''
        created, _ = TicketFailureType.objects.get_or_create(name=new_name)
        return created.name, ''

    if selected_value in valid_values:
        return selected_value, ''
    return '', 'Escolha uma categoria valida.'


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleFileField, self).clean(file, initial) for file in files]


class TicketCreateForm(forms.ModelForm):
    failure_type = forms.ChoiceField(
        label='Categoria',
        required=False,
        choices=(),
    )
    new_failure_type_name = forms.CharField(
        label='Nova categoria',
        required=False,
        max_length=80,
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Rede, Sistema ERP, Impressora'}),
    )
    attachments = MultipleFileField(
        required=False,
        label='Anexos',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['failure_type'].choices = ticket_failure_type_choices()

    def clean(self):
        cleaned_data = super().clean()
        selected_value = (cleaned_data.get('failure_type') or '').strip()
        new_name = (cleaned_data.get('new_failure_type_name') or '').strip()
        resolved, error = resolve_failure_type_value(selected_value or Ticket.FailureType.NA, new_name)
        if error:
            field_name = 'new_failure_type_name' if selected_value == NEW_FAILURE_TYPE_VALUE else 'failure_type'
            self.add_error(field_name, error)
        else:
            cleaned_data['failure_type'] = resolved
        return cleaned_data

    class Meta:
        model = Ticket
        fields = ['failure_type', 'title', 'description', 'priority']
        labels = {
            'title': 'Titulo',
            'description': 'Descricao do problema',
            'priority': 'Prioridade',
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Ex.: Impressora do setor nao imprime'}),
            'description': forms.Textarea(attrs={'rows': 5, 'placeholder': 'Descreva o problema com detalhes'}),
        }


class TicketMessageForm(forms.Form):
    message = forms.CharField(
        label='Mensagem',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Digite uma mensagem para o chamado'}),
    )
    attachments = MultipleFileField(
        required=False,
        label='Anexos',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        message = (cleaned_data.get('message') or '').strip()
        attachments = cleaned_data.get('attachments') or []
        if not message and not attachments:
            raise forms.ValidationError('Digite uma mensagem ou anexe pelo menos um arquivo.')
        cleaned_data['message'] = message
        return cleaned_data


class ManualClosedTicketForm(forms.Form):
    title = forms.CharField(
        label='Titulo',
        max_length=180,
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Ajuste realizado no computador do usuario'}),
    )
    description = forms.CharField(
        label='Descricao do chamado',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Descreva o problema ou solicitacao atendida'}),
    )
    resolution_note = forms.CharField(
        label='O que foi feito',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Descreva a acao/correcao realizada no atendimento'}),
    )
    service_date = forms.DateField(
        label='Dia',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    start_time = forms.TimeField(
        label='Horario inicial',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )
    end_time = forms.TimeField(
        label='Horario final',
        widget=forms.TimeInput(attrs={'type': 'time'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['service_date'].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        service_date = cleaned_data.get('service_date')
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')

        if service_date and start_time and end_time:
            current_tz = timezone.get_current_timezone()
            started_at = timezone.make_aware(datetime.combine(service_date, start_time), current_tz)
            ended_at = timezone.make_aware(datetime.combine(service_date, end_time), current_tz)
            if ended_at <= started_at:
                self.add_error('end_time', 'O horario final precisa ser maior que o horario inicial.')
            else:
                cleaned_data['started_at'] = started_at
                cleaned_data['ended_at'] = ended_at
        return cleaned_data


class TicketPendingForm(forms.ModelForm):
    class Meta:
        model = TicketPending
        fields = ['content']
        labels = {
            'content': 'Texto da pendencia',
        }
        widgets = {
            'content': forms.TextInput(
                attrs={
                    'placeholder': 'Ex.: Revisar acesso da pasta financeira e validar permissao do usuario',
                }
            ),
        }


class RequisitionForm(forms.ModelForm):
    class Meta:
        model = Requisition
        fields = ['title', 'kind', 'request_text']
        labels = {
            'title': 'Titulo',
            'kind': 'Tipo',
            'request_text': 'Texto da requisicao',
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Ex.: Compra de monitores para TI'}),
            'request_text': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Descreva o que deve ser requisitado e a justificativa.',
                }
            ),
        }


class RequisitionStatusForm(forms.Form):
    status = forms.ChoiceField(
        label='Status',
        choices=Requisition.Status.choices,
    )
    note = forms.CharField(
        label='Observacao',
        required=False,
        widget=forms.Textarea(
            attrs={
                'rows': 3,
                'placeholder': 'Opcional: detalhe da mudanca de status.',
            }
        ),
    )


class StarlinkForm(forms.ModelForm):
    class Meta:
        model = Starlink
        fields = [
            'name',
            'location',
            'starlink_identifier',
            'software_version',
            'serial_number',
            'kit_number',
            'email',
            'is_active',
            'payment_method',
            'card_final',
        ]
        labels = {
            'name': 'Nome',
            'location': 'Local',
            'starlink_identifier': 'ID da Starlink',
            'software_version': 'Versao do software',
            'serial_number': 'Numero de serie',
            'kit_number': 'Numero do kit',
            'email': 'Email',
            'is_active': 'Ativa',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Numero final do cartao',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Starlink Matriz'}),
            'location': forms.TextInput(attrs={'placeholder': 'Ex.: Recepcao / Fabrica'}),
            'starlink_identifier': forms.TextInput(attrs={'placeholder': 'Ex.: UT01000000-000000-000'}),
            'software_version': forms.TextInput(attrs={'placeholder': 'Ex.: 2026.05.1'}),
            'serial_number': forms.TextInput(attrs={'placeholder': 'Ex.: SN123456789'}),
            'kit_number': forms.TextInput(attrs={'placeholder': 'Ex.: KIT123456'}),
            'email': forms.EmailInput(attrs={'placeholder': 'conta@empresa.com'}),
            'payment_method': forms.Select(),
            'card_final': forms.TextInput(attrs={'placeholder': 'Ex.: 1234', 'maxlength': 4}),
        }

    def clean_card_final(self):
        payment_method = self.cleaned_data.get('payment_method')
        value = ''.join(char for char in str(self.cleaned_data.get('card_final') or '') if char.isdigit())
        if payment_method == Starlink.PaymentMethod.PIX:
            return ''
        if len(value) != 4:
            raise forms.ValidationError('Informe os 4 digitos finais do cartao.')
        return value


class StarlinkEditForm(forms.ModelForm):
    class Meta:
        model = Starlink
        fields = [
            'name',
            'location',
            'starlink_identifier',
            'software_version',
            'serial_number',
            'kit_number',
            'email',
            'is_active',
            'payment_method',
            'card_final',
        ]
        labels = {
            'name': 'Nome',
            'location': 'Local',
            'starlink_identifier': 'ID da Starlink',
            'software_version': 'Versao do software',
            'serial_number': 'Numero de serie',
            'kit_number': 'Numero do kit',
            'email': 'Email',
            'is_active': 'Ativa',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Numero final do cartao',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Starlink Matriz'}),
            'location': forms.TextInput(attrs={'placeholder': 'Ex.: Recepcao / Fabrica'}),
            'starlink_identifier': forms.TextInput(attrs={'placeholder': 'Ex.: UT01000000-000000-000'}),
            'software_version': forms.TextInput(attrs={'placeholder': 'Ex.: 2026.05.1'}),
            'serial_number': forms.TextInput(attrs={'placeholder': 'Ex.: SN123456789'}),
            'kit_number': forms.TextInput(attrs={'placeholder': 'Ex.: KIT123456'}),
            'email': forms.EmailInput(attrs={'placeholder': 'conta@empresa.com'}),
            'payment_method': forms.Select(),
            'card_final': forms.TextInput(attrs={'placeholder': 'Ex.: 1234', 'maxlength': 4}),
        }

    def clean_card_final(self):
        payment_method = self.cleaned_data.get('payment_method')
        value = ''.join(char for char in str(self.cleaned_data.get('card_final') or '') if char.isdigit())
        if payment_method == Starlink.PaymentMethod.PIX:
            return ''
        if len(value) != 4:
            raise forms.ValidationError('Informe os 4 digitos finais do cartao.')
        return value


class DocumentEntryForm(forms.ModelForm):
    class Meta:
        model = DocumentEntry
        fields = ['name', 'notes', 'attachment']
        labels = {
            'name': 'Nome',
            'notes': 'Observação',
            'attachment': 'Documento anexo',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Contrato Microsoft / Manual impressora / Link do fornecedor'}),
            'notes': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Observacoes, links, instrucoes, local do arquivo, contato ou qualquer detalhe util.',
                }
            ),
        }


class PhoneExtensionForm(forms.ModelForm):
    class Meta:
        model = PhoneExtension
        fields = ['department', 'name', 'phone', 'extension', 'email']
        labels = {
            'department': 'Departamento',
            'name': 'Colaborador',
            'phone': 'Telefone',
            'extension': 'Ramal',
            'email': 'Email',
        }
        widgets = {
            'department': forms.TextInput(attrs={'placeholder': 'Ex.: Financeiro, PCP, Comercial'}),
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Marcelo Sorigotti'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Ex.: (16) 0000-0000'}),
            'extension': forms.TextInput(attrs={'placeholder': 'Ex.: 204'}),
            'email': forms.Textarea(
                attrs={
                    'rows': 2,
                    'placeholder': 'colaborador@sidertec.com.br',
                }
            ),
        }


class TiResponsibilityForm(forms.ModelForm):
    class Meta:
        model = TiResponsibility
        fields = ['title']
        labels = {
            'title': 'Responsabilidade',
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Ex.: Backups, impressoras, GLPI, telefonia'}),
        }


class TiResponsibilityAssignmentForm(forms.Form):
    assignees = forms.ModelMultipleChoiceField(
        label='Atendentes de TI',
        queryset=get_user_model().objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 6}),
    )
    responsibilities = forms.ModelMultipleChoiceField(
        label='Responsabilidades',
        queryset=TiResponsibility.objects.none(),
        widget=forms.SelectMultiple(attrs={'size': 8}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        group_name = (getattr(settings, 'TI_GROUP_NAME', 'TI') or 'TI').strip()
        self.fields['assignees'].queryset = (
            get_user_model().objects.filter(groups__name__iexact=group_name).distinct().order_by('username')
        )
        self.fields['responsibilities'].queryset = (
            TiResponsibility.objects.filter(assignees__isnull=True).distinct().order_by('title')
        )


class SoftwareAssetForm(forms.ModelForm):
    class Meta:
        model = SoftwareAsset
        fields = ['name', 'license_quantity', 'notes']
        labels = {
            'name': 'Software',
            'license_quantity': 'Quantidade de licencas',
            'notes': 'Observacoes',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Microsoft 365, AutoCAD, AnyDesk'}),
            'license_quantity': forms.NumberInput(attrs={'min': 1}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Observacoes gerais do software.'}),
        }

    def clean_license_quantity(self):
        value = self.cleaned_data.get('license_quantity') or 0
        if value < 1:
            raise forms.ValidationError('Informe ao menos 1 licenca.')
        return value


class SoftwareLicenseForm(forms.ModelForm):
    class Meta:
        model = SoftwareLicense
        fields = [
            'software',
            'serial',
            'linked_email',
            'expiration_type',
            'expires_at',
            'payment_method',
            'card_final',
            'assigned_user',
            'notes',
        ]
        labels = {
            'software': 'Software',
            'serial': 'Serial',
            'linked_email': 'Email vinculado (opcional)',
            'expiration_type': 'Prazo',
            'expires_at': 'Data de expiracao',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Final do cartao',
            'assigned_user': 'Usuario usando',
            'notes': 'Observacoes',
        }
        widgets = {
            'software': forms.Select(),
            'serial': forms.TextInput(attrs={'placeholder': 'Serial/chave da licenca'}),
            'linked_email': forms.EmailInput(attrs={'placeholder': 'Opcional'}),
            'expiration_type': forms.Select(),
            'expires_at': forms.DateInput(attrs={'type': 'date'}),
            'payment_method': forms.TextInput(attrs={'placeholder': 'Ex.: Cartao, boleto, pix'}),
            'card_final': forms.TextInput(attrs={'placeholder': '1234', 'maxlength': 4, 'inputmode': 'numeric'}),
            'assigned_user': forms.TextInput(attrs={'placeholder': 'Nome do usuario usando'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Detalhes importantes da licenca.'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['software'].queryset = SoftwareAsset.objects.order_by('name')

    def clean_card_final(self):
        return ''.join(char for char in str(self.cleaned_data.get('card_final') or '') if char.isdigit())

    def clean(self):
        cleaned_data = super().clean()
        expiration_type = cleaned_data.get('expiration_type')
        expires_at = cleaned_data.get('expires_at')
        payment_method = unicodedata.normalize(
            'NFKD',
            str(cleaned_data.get('payment_method') or '').strip().lower(),
        ).encode('ascii', 'ignore').decode('ascii')
        card_final = cleaned_data.get('card_final') or ''

        if expiration_type == SoftwareLicense.ExpirationType.EXPIRA_EM and not expires_at:
            self.add_error('expires_at', 'Informe a data de expiracao.')

        if 'cartao' in payment_method and len(card_final) != 4:
            self.add_error('card_final', 'Informe os 4 ultimos digitos do cartao.')

        if not 'cartao' in payment_method:
            cleaned_data['card_final'] = ''

        return cleaned_data


class NetworkDeviceForm(forms.ModelForm):
    class Meta:
        model = NetworkDevice
        fields = ['category', 'ip_address', 'name', 'manufacturer', 'mac_address', 'notes']
        labels = {
            'category': 'Categoria',
            'ip_address': 'IP',
            'name': 'Nome',
            'manufacturer': 'Fabricante',
            'mac_address': 'Endereco MAC',
            'notes': 'Observacoes',
        }
        widgets = {
            'category': forms.Select(),
            'ip_address': forms.TextInput(attrs={'placeholder': 'Ex.: 192.168.22.17'}),
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: SRV-CHAMADOS'}),
            'manufacturer': forms.TextInput(attrs={'placeholder': 'Ex.: Microsoft Corporation, Ubiquiti, Ricoh'}),
            'mac_address': forms.TextInput(attrs={'placeholder': 'Ex.: 00:15:5D:16:52:0E'}),
            'notes': forms.Textarea(
                attrs={
                    'rows': 2,
                    'placeholder': 'Observacoes internas sobre o equipamento ou uso do IP.',
                }
            ),
        }

    def clean_ip_address(self):
        return (self.cleaned_data.get('ip_address') or '').strip()

    def clean_mac_address(self):
        return (self.cleaned_data.get('mac_address') or '').strip().upper()


class GoogleWorkspaceEmailImportForm(forms.Form):
    csv_file = forms.FileField(
        label='Arquivo CSV do Google Workspace',
        widget=forms.ClearableFileInput(
            attrs={
                'accept': '.csv,text/csv',
            }
        ),
    )


class CompletedServiceEntryForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        label='Documentos anexos',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
                'accept': '.pdf,.png,.jpg,.jpeg,.gif,.webp,.bmp,.txt,.log,.csv,.xlsx,.xls,.doc,.docx,.ppt,.pptx,.zip,.rar,.7z',
            }
        ),
    )
    amount = forms.CharField(
        label='Valor',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 850,00',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )

    class Meta:
        model = CompletedServiceEntry
        fields = ['service_name', 'company', 'description', 'service_date', 'attachments', 'amount']
        labels = {
            'service_name': 'Nome do servico',
            'company': 'Empresa',
            'description': 'Descricao',
            'service_date': 'Data do servico',
            'amount': 'Valor',
        }
        widgets = {
            'service_name': forms.TextInput(attrs={'placeholder': 'Ex.: Manutencao nobreak sala TI'}),
            'company': forms.TextInput(attrs={'placeholder': 'Ex.: Empresa prestadora'}),
            'description': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Descreva o que foi feito, detalhes do atendimento, garantia ou observacoes importantes.',
                }
            ),
            'service_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields['service_date'].initial = timezone.localdate()

    def clean_amount(self):
        raw_value = str(self.cleaned_data.get('amount') or '').strip()
        if not raw_value:
            raise forms.ValidationError('Informe o valor do servico.')

        normalized = raw_value.replace('R$', '').replace(' ', '')
        if ',' in normalized:
            normalized = normalized.replace('.', '').replace(',', '.')

        try:
            value = Decimal(normalized)
        except InvalidOperation:
            raise forms.ValidationError('Informe um valor valido.')

        if value < 0:
            raise forms.ValidationError('O valor nao pode ser negativo.')
        return value.quantize(Decimal('0.01'))


class ContractEntryForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        label='Documentos anexos',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
                'accept': '.pdf,.png,.jpg,.jpeg,.gif,.webp,.bmp,.txt,.log,.csv,.xlsx,.xls,.doc,.docx,.ppt,.pptx,.zip,.rar,.7z',
            }
        ),
    )
    amount = forms.CharField(
        required=False,
        label='Valor',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 2.500,00',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )

    class Meta:
        model = ContractEntry
        fields = [
            'name',
            'notes',
            'attachment',
            'attachments',
            'amount',
            'contract_start',
            'contract_end',
            'payment_method',
            'card_final',
            'payment_schedule',
        ]
        labels = {
            'name': 'Nome',
            'notes': 'Observação',
            'attachment': 'Anexo principal antigo',
            'amount': 'Valor',
            'contract_start': 'Data inicial do contrato',
            'contract_end': 'Data final do contrato',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Final do cartão',
            'payment_schedule': 'Tipo de cobrança',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Contrato licença Microsoft 365'}),
            'notes': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Observacoes, renovacao, contato, clausulas importantes, centro de custo etc.',
                }
            ),
            'contract_start': forms.DateInput(attrs={'type': 'date'}),
            'contract_end': forms.DateInput(attrs={'type': 'date'}),
            'payment_method': forms.TextInput(attrs={'placeholder': 'Ex.: Boleto, Pix, Cartao, Transferencia'}),
            'card_final': forms.TextInput(attrs={'placeholder': 'Ex.: 1234', 'maxlength': 4}),
            'payment_schedule': forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        amount_value = self.initial.get('amount')
        if amount_value not in (None, ''):
            normalized = f'{Decimal(amount_value):.2f}'
            integer_part, decimal_part = normalized.split('.')
            integer_part = f'{int(integer_part):,}'.replace(',', '.')
            self.initial['amount'] = f'{integer_part},{decimal_part}'

    def clean_amount(self):
        raw_value = str(self.cleaned_data.get('amount') or '').strip()
        if not raw_value:
            return None

        normalized = raw_value.replace('R$', '').replace(' ', '')
        if ',' in normalized:
            normalized = normalized.replace('.', '').replace(',', '.')

        try:
            value = Decimal(normalized)
        except InvalidOperation:
            raise forms.ValidationError('Informe um valor valido.')

        if value < 0:
            raise forms.ValidationError('O valor nao pode ser negativo.')
        return value.quantize(Decimal('0.01'))

    def clean(self):
        cleaned_data = super().clean()
        contract_start = cleaned_data.get('contract_start')
        contract_end = cleaned_data.get('contract_end')
        payment_method = unicodedata.normalize(
            'NFKD',
            str(cleaned_data.get('payment_method') or '').strip().lower(),
        ).encode('ascii', 'ignore').decode('ascii')
        card_final = ''.join(char for char in str(cleaned_data.get('card_final') or '') if char.isdigit())

        if contract_start and contract_end and contract_end < contract_start:
            self.add_error('contract_end', 'A data final nao pode ser anterior a data inicial.')

        if 'cartao' in payment_method:
            if len(card_final) != 4:
                self.add_error('card_final', 'Informe os 4 digitos finais do cartão.')
            else:
                cleaned_data['card_final'] = card_final
        else:
            cleaned_data['card_final'] = ''
        return cleaned_data


class ContractAttachmentForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=True,
        label='Documentos anexos',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
                'accept': '.pdf,.png,.jpg,.jpeg,.gif,.webp,.bmp,.txt,.log,.csv,.xlsx,.xls,.doc,.docx,.ppt,.pptx,.zip,.rar,.7z',
            }
        ),
    )

    class Meta:
        model = ContractEntry
        fields = []


class ContractCustomFieldForm(forms.Form):
    field_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    label = forms.CharField(
        label='Nome do campo',
        max_length=120,
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: Centro de custo'}),
    )
    field_type = forms.ChoiceField(
        label='Tipo',
        choices=ContractCustomField.FieldType.choices,
    )
    value_text = forms.CharField(
        label='Valor do texto',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Digite o texto'}),
    )
    value_number = forms.CharField(
        label='Valor numerico',
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ex.: 1234,56', 'inputmode': 'decimal'}),
    )
    value_bool = forms.ChoiceField(
        label='Valor sim / nao',
        required=False,
        choices=[
            ('', 'Selecione...'),
            ('sim', 'Sim'),
            ('nao', 'Nao'),
        ],
        widget=forms.Select(),
    )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('DELETE'):
            return cleaned_data

        label = (cleaned_data.get('label') or '').strip()
        field_type = (cleaned_data.get('field_type') or '').strip()
        value_text = (cleaned_data.get('value_text') or '').strip()
        value_number = (cleaned_data.get('value_number') or '').strip()
        value_bool = (cleaned_data.get('value_bool') or '').strip().lower()

        if not label and not field_type and not value_text and not value_number and not value_bool:
            return cleaned_data

        if not label:
            self.add_error('label', 'Informe o nome do campo.')
        if field_type not in dict(ContractCustomField.FieldType.choices):
            self.add_error('field_type', 'Escolha um tipo valido.')
            return cleaned_data

        if field_type == ContractCustomField.FieldType.TEXT:
            if not value_text:
                self.add_error('value_text', 'Informe o valor do texto.')
            cleaned_data['resolved_value'] = value_text
        elif field_type == ContractCustomField.FieldType.NUMBER:
            normalized = value_number.replace('R$', '').replace(' ', '')
            if ',' in normalized:
                normalized = normalized.replace('.', '').replace(',', '.')
            try:
                decimal_value = Decimal(normalized)
            except InvalidOperation:
                self.add_error('value_number', 'Informe um numero valido.')
            else:
                cleaned_data['resolved_value'] = format(decimal_value, 'f').rstrip('0').rstrip('.') if '.' in format(decimal_value, 'f') else format(decimal_value, 'f')
        elif field_type == ContractCustomField.FieldType.BOOLEAN:
            if value_bool not in {'sim', 'nao'}:
                self.add_error('value_bool', 'Escolha Sim ou Nao.')
            else:
                cleaned_data['resolved_value'] = value_bool

        cleaned_data['label'] = label
        cleaned_data['field_type'] = field_type
        return cleaned_data


ContractCustomFieldFormSet = formset_factory(
    ContractCustomFieldForm,
    extra=1,
    can_delete=True,
    max_num=20,
)


class EquipmentLoanForm(forms.ModelForm):
    attendant_signature_profile = forms.ModelChoiceField(
        queryset=EquipmentLoanAttendantSignature.objects.all(),
        required=False,
        label='Assinatura cadastrada',
        empty_label='Sem assinatura do atendente',
    )
    attendant_signature_password = forms.CharField(
        required=False,
        label='Senha de autorização',
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha cadastrada junto com a assinatura'}),
    )

    photos = MultipleFileField(
        required=False,
        label='Fotos do equipamento',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
                'accept': 'image/*',
            }
        ),
    )

    class Meta:
        model = EquipmentLoan
        fields = [
            'collaborator_name',
            'collaborator_company',
            'collaborator_document',
            'collaborator_email',
            'collaborator_phone',
            'equipment_type',
            'equipment_brand',
            'equipment_model',
            'equipment_serial',
            'patrimony_tag',
            'accessories',
            'loan_date',
            'expected_return_date',
            'notes',
            'attendant_signature_profile',
            'attendant_signature_password',
            'photos',
        ]
        labels = {
            'collaborator_name': 'Nome do colaborador externo',
            'collaborator_company': 'Empresa / terceirizada',
            'collaborator_document': 'Documento / CPF',
            'collaborator_email': 'Email',
            'collaborator_phone': 'Telefone',
            'equipment_type': 'Tipo de equipamento',
            'equipment_brand': 'Marca',
            'equipment_model': 'Modelo',
            'equipment_serial': 'Numero de serie',
            'patrimony_tag': 'Patrimonio / etiqueta',
            'accessories': 'Acessorios entregues',
            'loan_date': 'Data do emprestimo',
            'expected_return_date': 'Previsao de devolucao',
            'notes': 'Observacoes internas',
            'attendant_signature_profile': 'Assinatura cadastrada',
            'attendant_signature_password': 'Senha de autorização',
            'photos': 'Fotos do equipamento',
        }
        widgets = {
            'collaborator_name': forms.TextInput(attrs={'placeholder': 'Ex.: Alexandre Graciano'}),
            'collaborator_company': forms.TextInput(attrs={'placeholder': 'Ex.: Empresa parceira / prestador externo'}),
            'collaborator_document': forms.TextInput(attrs={'placeholder': 'Ex.: CPF ou RG'}),
            'collaborator_email': forms.EmailInput(
                attrs={
                    'placeholder': 'colaborador@empresa.com',
                    'list': 'equipmentLoanEmailOptions',
                    'autocomplete': 'off',
                }
            ),
            'collaborator_phone': forms.TextInput(attrs={'placeholder': 'Ex.: (00) 00000-0000'}),
            'equipment_type': forms.TextInput(attrs={'placeholder': 'Ex.: Notebook, tablet, celular, monitor'}),
            'equipment_brand': forms.TextInput(attrs={'placeholder': 'Ex.: Dell, Lenovo, Samsung'}),
            'equipment_model': forms.TextInput(attrs={'placeholder': 'Ex.: Latitude 5420'}),
            'equipment_serial': forms.TextInput(attrs={'placeholder': 'Ex.: SN123456'}),
            'patrimony_tag': forms.TextInput(attrs={'placeholder': 'Ex.: TI-000123'}),
            'accessories': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Ex.: Fonte, mouse, mochila, cabo HDMI'}),
            'loan_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_return_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Observacoes internas sobre o comodato.'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields['loan_date'].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        loan_date = cleaned_data.get('loan_date')
        expected_return_date = cleaned_data.get('expected_return_date')
        attendant_signature_profile = cleaned_data.get('attendant_signature_profile')
        attendant_signature_password = cleaned_data.get('attendant_signature_password')
        if loan_date and expected_return_date and expected_return_date < loan_date:
            self.add_error('expected_return_date', 'A previsao de devolucao nao pode ser anterior ao emprestimo.')
        if attendant_signature_password and not attendant_signature_profile:
            self.add_error('attendant_signature_profile', 'Selecione a assinatura cadastrada para usar esta senha.')
        if attendant_signature_profile and not attendant_signature_profile.check_authorization_password(attendant_signature_password):
            self.add_error('attendant_signature_password', 'Senha de autorizacao invalida para esta assinatura.')
        return cleaned_data


class EquipmentLoanSignedDocumentForm(forms.ModelForm):
    class Meta:
        model = EquipmentLoan
        fields = ['signed_document']
        labels = {
            'signed_document': 'Termo assinado',
        }
        widgets = {
            'signed_document': forms.ClearableFileInput(
                attrs={
                    'accept': '.pdf,.png,.jpg,.jpeg,.doc,.docx',
                }
            ),
        }


class EquipmentLoanUpdateForm(forms.ModelForm):
    class Meta:
        model = EquipmentLoan
        fields = [
            'collaborator_name',
            'collaborator_company',
            'collaborator_document',
            'collaborator_email',
            'collaborator_phone',
            'loan_date',
            'expected_return_date',
            'attendant_signature_x_offset',
            'attendant_signature_y_offset',
            'notes',
        ]
        labels = {
            'attendant_signature_x_offset': 'Ajuste horizontal da assinatura (px)',
            'attendant_signature_y_offset': 'Ajuste vertical da assinatura (px)',
        }
        widgets = {
            'loan_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'expected_return_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'attendant_signature_x_offset': forms.NumberInput(attrs={'placeholder': '0', 'min': '-200', 'max': '200'}),
            'attendant_signature_y_offset': forms.NumberInput(attrs={'placeholder': '0', 'min': '-200', 'max': '200'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'attendant_signature_x_offset': 'Positivo move para a direita, negativo para a esquerda.',
            'attendant_signature_y_offset': 'Positivo move para cima, negativo para baixo.',
        }

    def clean(self):
        cleaned_data = super().clean()
        loan_date = cleaned_data.get('loan_date')
        expected_return_date = cleaned_data.get('expected_return_date')
        if loan_date and expected_return_date and expected_return_date < loan_date:
            self.add_error('expected_return_date', 'A previsao de devolucao nao pode ser anterior ao emprestimo.')
        return cleaned_data


class EquipmentLoanItemForm(forms.ModelForm):
    class Meta:
        model = EquipmentLoanItem
        fields = ['equipment_type', 'equipment_brand', 'equipment_model', 'equipment_serial', 'patrimony_tag', 'accessories']


class EquipmentLoanAttendantSignatureForm(forms.Form):
    attendant_signature_profile = forms.ModelChoiceField(
        queryset=EquipmentLoanAttendantSignature.objects.all(),
        required=True,
        label='Assinatura cadastrada',
        empty_label='Selecione a assinatura',
    )
    attendant_signature_password = forms.CharField(
        required=True,
        label='Senha de autorização',
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha cadastrada junto com a assinatura'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        profile = cleaned_data.get('attendant_signature_profile')
        password = cleaned_data.get('attendant_signature_password')
        if profile and not profile.check_authorization_password(password):
            self.add_error('attendant_signature_password', 'Senha de autorizacao invalida para esta assinatura.')
        return cleaned_data


class EquipmentLoanStoredSignatureForm(forms.ModelForm):
    authorization_password = forms.CharField(
        required=True,
        label='Senha de autorização',
        widget=forms.PasswordInput(attrs={'placeholder': 'Crie uma senha para autorizar o uso'}),
    )

    class Meta:
        model = EquipmentLoanAttendantSignature
        fields = ['name', 'image', 'authorization_password', 'signature_x_offset', 'signature_y_offset']
        labels = {
            'name': 'Nome da assinatura',
            'image': 'Imagem da assinatura',
            'authorization_password': 'Senha de autorização',
            'signature_x_offset': 'Ajuste horizontal (px)',
            'signature_y_offset': 'Ajuste vertical (px)',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Marcelo Sorigotti'}),
            'image': forms.ClearableFileInput(attrs={'accept': '.png,.jpg,.jpeg'}),
            'signature_x_offset': forms.NumberInput(attrs={'placeholder': '0', 'min': '-200', 'max': '200'}),
            'signature_y_offset': forms.NumberInput(attrs={'placeholder': '0', 'min': '-200', 'max': '200'}),
        }
        help_texts = {
            'signature_x_offset': 'Positivo move para a direita, negativo para a esquerda.',
            'signature_y_offset': 'Positivo move para cima, negativo para baixo.',
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_authorization_password(self.cleaned_data['authorization_password'])
        if commit:
            instance.save()
        return instance


class EquipmentLoanPhotoForm(forms.Form):
    photos = MultipleFileField(
        required=True,
        label='Fotos do equipamento',
        widget=MultipleFileInput(
            attrs={
                'multiple': True,
                'accept': 'image/*',
            }
        ),
    )


class FuturaDigitalEntryForm(forms.ModelForm):
    FRANCHISE_INCLUDED_PAGES = 23000
    FRANCHISE_MONTHLY_PRICE = Decimal('1610.00')
    EXCESS_PAGE_PRICE = Decimal('0.07')
    COLOR_PAGE_PRICE = Decimal('0.75')

    reference_month = forms.DateField(
        label='Mes/Ano',
        input_formats=['%Y-%m'],
        widget=forms.DateInput(attrs={'type': 'month'}),
    )
    paid_amount = forms.CharField(
        label='Valor pago',
        required=False,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Calculado automaticamente',
                'inputmode': 'numeric',
                'autocomplete': 'off',
                'readonly': 'readonly',
            }
        ),
    )
    color_copies = forms.CharField(
        label='Impressoes coloridas',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 1.520',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )
    franchise_copies = forms.CharField(
        label='Franquia',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 23.000',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )
    franchise_amount = forms.CharField(
        label='Valor da franquia',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 1.610,00',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )
    excess_copies = forms.CharField(
        label='Impressoes excedentes',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 250',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )
    copies_count = forms.CharField(
        label='Total copias',
        required=False,
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Calculado automaticamente',
                'inputmode': 'numeric',
                'autocomplete': 'off',
                'readonly': 'readonly',
            }
        ),
    )

    class Meta:
        model = FuturaDigitalEntry
        fields = [
            'reference_month',
            'color_copies',
            'franchise_copies',
            'franchise_amount',
            'excess_copies',
            'copies_count',
            'paid_amount',
            'document',
        ]
        labels = {
            'reference_month': 'Mes/Ano',
            'color_copies': 'Impressoes coloridas',
            'franchise_copies': 'Franquia',
            'franchise_amount': 'Valor da franquia',
            'excess_copies': 'Impressoes excedentes',
            'copies_count': 'Total copias',
            'paid_amount': 'Valor pago',
            'document': 'Documento',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        amount_value = self.initial.get('paid_amount')
        if amount_value not in (None, ''):
            normalized = f'{Decimal(amount_value):.2f}'
            integer_part, decimal_part = normalized.split('.')
            integer_part = f'{int(integer_part):,}'.replace(',', '.')
            self.initial['paid_amount'] = f'{integer_part},{decimal_part}'
        franchise_amount_value = self.initial.get('franchise_amount')
        if franchise_amount_value not in (None, ''):
            normalized = f'{Decimal(franchise_amount_value):.2f}'
            integer_part, decimal_part = normalized.split('.')
            integer_part = f'{int(integer_part):,}'.replace(',', '.')
            self.initial['franchise_amount'] = f'{integer_part},{decimal_part}'
        if not self.is_bound:
            self.initial.setdefault('franchise_copies', self.FRANCHISE_INCLUDED_PAGES)
            self.initial.setdefault('franchise_amount', self.FRANCHISE_MONTHLY_PRICE)
        for field_name in ('color_copies', 'franchise_copies', 'excess_copies', 'copies_count'):
            raw_value = self.initial.get(field_name)
            if raw_value not in (None, ''):
                self.initial[field_name] = f'{int(raw_value):,}'.replace(',', '.')

    def _parse_copies_value(self, raw_value: str, error_message: str) -> int:
        digits = ''.join(ch for ch in raw_value if ch.isdigit())
        if not digits:
            raise forms.ValidationError(error_message)
        value = int(digits)
        if value < 0:
            raise forms.ValidationError(error_message)
        return value

    def clean_color_copies(self):
        raw_value = str(self.cleaned_data.get('color_copies') or '').strip()
        return self._parse_copies_value(raw_value, 'Informe uma quantidade valida para impressoes coloridas.')

    def clean_franchise_copies(self):
        raw_value = str(self.cleaned_data.get('franchise_copies') or '').strip()
        return self._parse_copies_value(raw_value, 'Informe uma quantidade valida para franquia.')

    def clean_franchise_amount(self):
        raw_value = str(self.cleaned_data.get('franchise_amount') or '').strip()
        if not raw_value:
            raise forms.ValidationError('Informe o valor da franquia.')

        normalized = raw_value.replace('R$', '').replace(' ', '')
        if ',' in normalized:
            normalized = normalized.replace('.', '').replace(',', '.')

        try:
            value = Decimal(normalized)
        except InvalidOperation:
            raise forms.ValidationError('Informe um valor valido para franquia.')

        if value < 0:
            raise forms.ValidationError('O valor da franquia nao pode ser negativo.')
        return value.quantize(Decimal('0.01'))

    def clean_excess_copies(self):
        raw_value = str(self.cleaned_data.get('excess_copies') or '').strip()
        return self._parse_copies_value(raw_value, 'Informe uma quantidade valida para impressoes excedentes.')

    def clean(self):
        cleaned_data = super().clean()
        color_copies = cleaned_data.get('color_copies')
        franchise_copies = cleaned_data.get('franchise_copies')
        franchise_amount = cleaned_data.get('franchise_amount')
        excess_copies = cleaned_data.get('excess_copies')
        if (
            color_copies is None
            or franchise_copies is None
            or franchise_amount is None
            or excess_copies is None
        ):
            return cleaned_data

        cleaned_data['copies_count'] = franchise_copies + excess_copies + color_copies
        paid_amount = (
            franchise_amount
            + (Decimal(excess_copies) * self.EXCESS_PAGE_PRICE)
            + (Decimal(color_copies) * self.COLOR_PAGE_PRICE)
        ).quantize(Decimal('0.01'))
        cleaned_data['paid_amount'] = paid_amount
        return cleaned_data


class TipEntryForm(forms.ModelForm):
    class Meta:
        model = TipEntry
        fields = ['category', 'title', 'content', 'attachment']
        labels = {
            'category': 'Categoria',
            'title': 'Titulo',
            'content': 'Conteudo',
            'attachment': 'Documento anexo',
        }
        widgets = {
            'category': forms.Select(),
            'title': forms.TextInput(attrs={'placeholder': 'Ex.: Power Fab nao conecta'}),
            'content': forms.Textarea(
                attrs={
                    'rows': 5,
                    'placeholder': 'Descreva a dica, passo a passo, link ou procedimento.',
                }
            ),
        }


class WhatsAppConfigForm(forms.ModelForm):
    class Meta:
        model = WhatsAppConfig
        fields = [
            'notifications_enabled',
            'provider',
            'group_jid',
            'send_group_on_new_ticket',
            'template_new_ticket',
            'wapi_token',
            'wapi_instance',
            'wapi_base_url',
            'webhook_url',
            'webhook_token',
        ]
        widgets = {
            'group_jid': forms.TextInput(attrs={'placeholder': 'Ex.: 120363421981424263@g.us'}),
            'template_new_ticket': forms.TextInput(attrs={'placeholder': '🚨 {urgencia} - {solicitante}\n📄 {title}'}),
            'wapi_token': forms.TextInput(attrs={'placeholder': 'Token da W-API'}),
            'wapi_instance': forms.TextInput(attrs={'placeholder': 'Instance ID da W-API'}),
            'wapi_base_url': forms.TextInput(attrs={'placeholder': 'https://api.w-api.app/v1'}),
            'webhook_url': forms.TextInput(attrs={'placeholder': 'https://seu-servidor.com/webhook'}),
            'webhook_token': forms.TextInput(attrs={'placeholder': 'Bearer token (opcional)'}),
        }

