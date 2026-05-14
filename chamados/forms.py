from django import forms
import unicodedata
from decimal import Decimal, InvalidOperation
from django.utils import timezone

from .models import CompletedServiceEntry, ContractEntry, DocumentEntry, EquipmentLoan, EquipmentLoanAttendantSignature, FuturaDigitalEntry, PhoneExtension, Requisition, Starlink, Ticket, TicketFailureType, TicketPending, TipEntry


NEW_FAILURE_TYPE_VALUE = '__new__'


def _normalize_label(value: str) -> str:
    return unicodedata.normalize('NFKD', (value or '').strip().lower()).encode('ascii', 'ignore').decode('ascii')


def _builtin_failure_type_value(value: str) -> str:
    normalized = _normalize_label(value)
    for choice_value, choice_label in Ticket.FailureType.choices:
        if normalized in {_normalize_label(choice_value), _normalize_label(choice_label)}:
            return choice_value
    return ''


def ticket_failure_type_choices(*, include_blank=True, include_new=True):
    choices = []
    if include_blank:
        choices.append(('', 'Selecione...'))
    choices.extend(Ticket.FailureType.choices)
    builtin_labels = {_normalize_label(label) for _, label in Ticket.FailureType.choices}
    builtin_values = {_normalize_label(value) for value, _ in Ticket.FailureType.choices}
    for item in TicketFailureType.objects.order_by('name'):
        normalized = _normalize_label(item.name)
        if normalized in builtin_labels or normalized in builtin_values:
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
        fields = ['name', 'location', 'email', 'is_active', 'payment_method', 'card_final']
        labels = {
            'name': 'Nome',
            'location': 'Local',
            'email': 'Email',
            'is_active': 'Ativa',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Numero final do cartao',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Starlink Matriz'}),
            'location': forms.TextInput(attrs={'placeholder': 'Ex.: Recepcao / Fabrica'}),
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
        fields = ['name', 'location', 'email', 'is_active', 'payment_method', 'card_final']
        labels = {
            'name': 'Nome',
            'location': 'Local',
            'email': 'Email',
            'is_active': 'Ativa',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Numero final do cartao',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Starlink Matriz'}),
            'location': forms.TextInput(attrs={'placeholder': 'Ex.: Recepcao / Fabrica'}),
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
            'collaborator_email': forms.EmailInput(attrs={'placeholder': 'colaborador@empresa.com'}),
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
        fields = ['name', 'image', 'authorization_password']
        labels = {
            'name': 'Nome da assinatura',
            'image': 'Imagem da assinatura',
            'authorization_password': 'Senha de autorização',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Marcelo Sorigotti'}),
            'image': forms.ClearableFileInput(attrs={'accept': '.png,.jpg,.jpeg'}),
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
    reference_month = forms.DateField(
        label='Mes/Ano',
        input_formats=['%Y-%m'],
        widget=forms.DateInput(attrs={'type': 'month'}),
    )
    paid_amount = forms.CharField(
        label='Valor pago',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Ex.: 1.250,00',
                'inputmode': 'numeric',
                'autocomplete': 'off',
            }
        ),
    )

    class Meta:
        model = FuturaDigitalEntry
        fields = ['name', 'invoice', 'reference_month', 'copies_count', 'paid_amount']
        labels = {
            'name': 'Nome',
            'invoice': 'Fatura',
            'reference_month': 'Mes/Ano',
            'copies_count': 'Quantidade de copias',
            'paid_amount': 'Valor pago',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Impressora Recepcao'}),
            'invoice': forms.TextInput(attrs={'placeholder': 'Ex.: FAT-2026-0042'}),
            'copies_count': forms.NumberInput(attrs={'min': '0', 'step': '1', 'placeholder': 'Ex.: 1520'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        amount_value = self.initial.get('paid_amount')
        if amount_value not in (None, ''):
            normalized = f'{Decimal(amount_value):.2f}'
            integer_part, decimal_part = normalized.split('.')
            integer_part = f'{int(integer_part):,}'.replace(',', '.')
            self.initial['paid_amount'] = f'{integer_part},{decimal_part}'

    def clean_copies_count(self):
        value = self.cleaned_data.get('copies_count')
        if value is None or value < 0:
            raise forms.ValidationError('Informe uma quantidade de copias valida.')
        return value

    def clean_paid_amount(self):
        raw_value = str(self.cleaned_data.get('paid_amount') or '').strip()
        if not raw_value:
            raise forms.ValidationError('Informe o valor pago.')

        normalized = raw_value.replace('R$', '').replace(' ', '')
        if ',' in normalized:
            normalized = normalized.replace('.', '').replace(',', '.')

        try:
            value = Decimal(normalized)
        except InvalidOperation:
            raise forms.ValidationError('Informe um valor pago valido.')

        if value < 0:
            raise forms.ValidationError('O valor pago nao pode ser negativo.')
        return value.quantize(Decimal('0.01'))


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
