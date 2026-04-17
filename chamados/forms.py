from django import forms

from .models import ContractEntry, DocumentEntry, Requisition, Starlink, Ticket, TicketPending


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

class ContractEntryForm(forms.ModelForm):
    class Meta:
        model = ContractEntry
        fields = [
            'name',
            'notes',
            'attachment',
            'amount',
            'validity_date',
            'payment_method',
            'duration_value',
            'duration_unit',
            'payment_schedule',
        ]
        labels = {
            'name': 'Nome',
            'notes': 'Observacao',
            'attachment': 'Documento anexo',
            'amount': 'Valor',
            'validity_date': 'Vigencia do contrato',
            'payment_method': 'Forma de pagamento',
            'duration_value': 'Tempo do contrato',
            'duration_unit': 'Periodo',
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
            'amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': 'Ex.: 2500.00'}),
            'validity_date': forms.DateInput(attrs={'type': 'date'}),
            'payment_method': forms.TextInput(attrs={'placeholder': 'Ex.: Boleto, Pix, Cartao, Transferencia'}),
            'duration_value': forms.NumberInput(attrs={'min': 1, 'placeholder': 'Ex.: 12'}),
            'duration_unit': forms.Select(),
            'payment_schedule': forms.Select(),
        }

    def clean(self):
        cleaned_data = super().clean()
        duration_value = cleaned_data.get('duration_value')
        duration_unit = cleaned_data.get('duration_unit')
        if duration_value and not duration_unit:
            self.add_error('duration_unit', 'Informe se o contrato esta em meses ou anos.')
        if duration_value is not None and duration_value <= 0:
            self.add_error('duration_value', 'Informe um tempo de contrato valido.')
        return cleaned_data
