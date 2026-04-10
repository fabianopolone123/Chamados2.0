from django import forms
from django.contrib.auth import get_user_model

from .models import Ticket


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


class TicketTriageForm(forms.ModelForm):
    response_message = forms.CharField(
        label='Atualizacao de atendimento',
        required=False,
        widget=forms.Textarea(
            attrs={
                'rows': 4,
                'placeholder': 'Mensagem para registrar andamento, orientacao ou resolucao',
            }
        ),
    )

    class Meta:
        model = Ticket
        fields = ['status', 'assigned_to']
        labels = {
            'status': 'Status',
            'assigned_to': 'Responsavel TI',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = get_user_model().objects.order_by('username')
        self.fields['assigned_to'].required = False
