from django import forms

from .models import Ticket, TicketPending


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
