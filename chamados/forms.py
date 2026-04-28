from django import forms
import unicodedata
from decimal import Decimal, InvalidOperation
from django.utils import timezone

from .models import CompletedServiceEntry, ContractEntry, DocumentEntry, FuturaDigitalEntry, Requisition, Starlink, Ticket, TicketPending, TipEntry


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
    class Meta:
        model = Ticket
        fields = ['title', 'description', 'priority']
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
            'notes': 'Observacao',
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
            'amount',
            'contract_start',
            'contract_end',
            'payment_method',
            'card_final',
            'payment_schedule',
        ]
        labels = {
            'name': 'Nome',
            'notes': 'Observacao',
            'attachment': 'Documento anexo',
            'amount': 'Valor',
            'contract_start': 'Data inicial do contrato',
            'contract_end': 'Data final do contrato',
            'payment_method': 'Forma de pagamento',
            'card_final': 'Final do cartao',
            'payment_schedule': 'Tipo de cobranca',
        }
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex.: Contrato licenca Microsoft 365'}),
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
                self.add_error('card_final', 'Informe os 4 digitos finais do cartao.')
            else:
                cleaned_data['card_final'] = card_final
        else:
            cleaned_data['card_final'] = ''
        return cleaned_data


class ContractAttachmentForm(forms.ModelForm):
    class Meta:
        model = ContractEntry
        fields = ['attachment']
        labels = {
            'attachment': 'Documento anexo',
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if not attachment:
            raise forms.ValidationError('Selecione um arquivo para anexar ao contrato.')
        return attachment


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
