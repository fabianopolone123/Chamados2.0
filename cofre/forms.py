from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import VaultCredential


class VaultUnlockForm(forms.Form):
    password = forms.CharField(
        label='Senha do cofre',
        strip=False,
        widget=forms.PasswordInput(attrs={'placeholder': 'Digite a senha do cofre'}),
    )


class VaultMasterPasswordChangeForm(forms.Form):
    old_password = forms.CharField(
        label='Senha atual do cofre',
        strip=False,
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha atual'}),
    )
    new_password = forms.CharField(
        label='Nova senha do cofre',
        strip=False,
        min_length=8,
        widget=forms.PasswordInput(attrs={'placeholder': 'Nova senha'}),
    )
    confirm_new_password = forms.CharField(
        label='Confirmar nova senha',
        strip=False,
        min_length=8,
        widget=forms.PasswordInput(attrs={'placeholder': 'Confirme a nova senha'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('confirm_new_password')
        if new_password and confirmation and new_password != confirmation:
            raise forms.ValidationError('A confirmacao da nova senha nao confere.')
        if new_password:
            validate_password(new_password)
        return cleaned_data


class VaultCredentialForm(forms.ModelForm):
    plain_password = forms.CharField(
        label='Senha',
        strip=False,
        widget=forms.PasswordInput(attrs={'placeholder': 'Senha da credencial'}),
    )

    class Meta:
        model = VaultCredential
        fields = ['label', 'account_username', 'notes']
        labels = {
            'label': 'Identificacao',
            'account_username': 'Usuario/Conta',
            'notes': 'Observacoes',
        }
        widgets = {
            'label': forms.TextInput(attrs={'placeholder': 'Ex.: Firewall principal'}),
            'account_username': forms.TextInput(attrs={'placeholder': 'Ex.: admin.firewall'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Informacoes adicionais'}),
        }


class VaultAccessControlForm(forms.Form):
    users = forms.ModelMultipleChoiceField(
        label='Usuarios com acesso ao botao Cofre',
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'size': 12}),
    )

    def __init__(self, *args, **kwargs):
        initial_users = kwargs.pop('initial_users', None)
        super().__init__(*args, **kwargs)
        self.fields['users'].queryset = get_user_model().objects.order_by('username')
        if initial_users is not None:
            self.initial['users'] = initial_users
