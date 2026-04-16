from django import forms

from .models import Requisition, Starlink, Ticket, TicketPending


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
            'content': forms.Textarea(
                attrs={
                    'rows': 4,
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
    plain_password = forms.CharField(
        label='Senha',
        strip=False,
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha da Starlink'}),
    )

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
