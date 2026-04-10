from django.urls import path

from .views import (
    VaultAccessManageView,
    VaultCopyPasswordView,
    VaultCredentialCreateView,
    VaultHomeView,
    VaultLockView,
    VaultMasterPasswordChangeView,
    VaultUnlockView,
)

urlpatterns = [
    path('', VaultHomeView.as_view(), name='cofre_home'),
    path('desbloquear/', VaultUnlockView.as_view(), name='cofre_unlock'),
    path('bloquear/', VaultLockView.as_view(), name='cofre_lock'),
    path('credenciais/nova/', VaultCredentialCreateView.as_view(), name='cofre_credential_create'),
    path('senha/', VaultMasterPasswordChangeView.as_view(), name='cofre_change_password'),
    path('acessos/', VaultAccessManageView.as_view(), name='cofre_manage_access'),
    path('copiar/<int:credential_id>/', VaultCopyPasswordView.as_view(), name='cofre_copy_password'),
]
