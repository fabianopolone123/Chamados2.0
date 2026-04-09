from django import forms
from django.contrib.auth.forms import AuthenticationForm


class SidertecAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label='Username',
        max_length=150,
        widget=forms.TextInput(
            attrs={
                'autofocus': True,
                'placeholder': 'Digite seu usuario AD',
                'autocomplete': 'username',
            }
        ),
    )
    password = forms.CharField(
        label='Senha',
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                'placeholder': 'Digite sua senha',
                'autocomplete': 'current-password',
            }
        ),
    )
