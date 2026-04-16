from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView, View

from .forms import (
    VaultAccessControlForm,
    VaultCredentialForm,
    VaultCredentialPasswordChangeForm,
    VaultMasterPasswordChangeForm,
    VaultUnlockForm,
)
from .models import VaultAuditLog, VaultCredential
from .services import (
    get_unlock_remaining_seconds,
    get_vault_settings,
    is_vault_unlocked,
    lock_vault_session,
    log_vault_event,
    unlock_vault_session,
    user_can_access_vault,
)


class VaultAccessRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return user_can_access_vault(self.request.user)

    def handle_no_permission(self):
        messages.error(self.request, 'Voce nao possui permissao para acessar o cofre.')
        return redirect('chamados_list')


class VaultUnlockedRequiredMixin:
    def dispatch(self, request, *args, **kwargs):
        if not is_vault_unlocked(request):
            messages.warning(request, 'Cofre bloqueado. Informe a senha novamente.')
            return redirect('cofre_unlock')
        return super().dispatch(request, *args, **kwargs)


@method_decorator(never_cache, name='dispatch')
class VaultUnlockView(LoginRequiredMixin, VaultAccessRequiredMixin, FormView):
    template_name = 'cofre/unlock.html'
    form_class = VaultUnlockForm
    success_url = reverse_lazy('cofre_home')

    def dispatch(self, request, *args, **kwargs):
        if is_vault_unlocked(request):
            return redirect('cofre_home')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        settings_obj = get_vault_settings()
        if not settings_obj.password_hash:
            form.add_error(
                None,
                'Senha do cofre nao configurada. Defina VAULT_DEFAULT_PASSWORD no .env.',
            )
            return self.form_invalid(form)

        if settings_obj.is_unlock_locked():
            remaining = settings_obj.get_lockout_remaining_seconds()
            log_vault_event(
                self.request,
                VaultAuditLog.ACTION_UNLOCK_LOCKOUT,
                details=f'locked_for_seconds={remaining}',
            )
            form.add_error(
                'password',
                f'Muitas tentativas invalidas. Tente novamente em {remaining}s.',
            )
            return self.form_invalid(form)

        entered_password = form.cleaned_data['password']
        if settings_obj.check_master_password(entered_password):
            settings_obj.reset_unlock_failures()
            unlock_vault_session(self.request)
            log_vault_event(self.request, VaultAuditLog.ACTION_UNLOCK_SUCCESS)
            messages.success(self.request, 'Cofre desbloqueado por 1 minuto.')
            return super().form_valid(form)

        settings_obj.register_failed_unlock_attempt()
        log_vault_event(self.request, VaultAuditLog.ACTION_UNLOCK_FAILURE)
        if settings_obj.is_unlock_locked():
            remaining = settings_obj.get_lockout_remaining_seconds()
            log_vault_event(
                self.request,
                VaultAuditLog.ACTION_UNLOCK_LOCKOUT,
                details=f'locked_for_seconds={remaining}',
            )
            form.add_error(
                'password',
                f'Muitas tentativas invalidas. Tente novamente em {remaining}s.',
            )
            return self.form_invalid(form)

        form.add_error('password', 'Senha do cofre invalida.')
        return self.form_invalid(form)


@method_decorator(never_cache, name='dispatch')
class VaultHomeView(
    LoginRequiredMixin,
    VaultAccessRequiredMixin,
    VaultUnlockedRequiredMixin,
    TemplateView,
):
    template_name = 'cofre/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['credentials'] = VaultCredential.objects.all()
        context['unlock_remaining_seconds'] = get_unlock_remaining_seconds(self.request)
        context['clipboard_clear_seconds'] = int(
            getattr(settings, 'VAULT_CLIPBOARD_CLEAR_SECONDS', 15) or 15
        )
        return context


@method_decorator(never_cache, name='dispatch')
class VaultCredentialCreateView(
    LoginRequiredMixin,
    VaultAccessRequiredMixin,
    VaultUnlockedRequiredMixin,
    FormView,
):
    template_name = 'cofre/credential_form.html'
    form_class = VaultCredentialForm
    success_url = reverse_lazy('cofre_home')

    def form_valid(self, form):
        credential = form.save(commit=False)
        credential.created_by = self.request.user
        credential.set_secret_password(form.cleaned_data['plain_password'])
        credential.save()
        log_vault_event(
            self.request,
            VaultAuditLog.ACTION_CREDENTIAL_CREATED,
            credential=credential,
            details=f'label={credential.label}',
        )
        messages.success(self.request, 'Credencial salva com sucesso.')
        return super().form_valid(form)


@method_decorator(never_cache, name='dispatch')
class VaultCredentialPasswordChangeView(
    LoginRequiredMixin,
    VaultAccessRequiredMixin,
    VaultUnlockedRequiredMixin,
    FormView,
):
    template_name = 'cofre/credential_password_change.html'
    form_class = VaultCredentialPasswordChangeForm
    success_url = reverse_lazy('cofre_home')

    def dispatch(self, request, *args, **kwargs):
        self.credential = get_object_or_404(VaultCredential, pk=kwargs['credential_id'])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['credential'] = self.credential
        return context

    def form_valid(self, form):
        self.credential.set_secret_password(form.cleaned_data['new_password'])
        self.credential.save(update_fields=['password_encrypted', 'updated_at'])
        log_vault_event(
            self.request,
            VaultAuditLog.ACTION_CREDENTIAL_UPDATED,
            credential=self.credential,
            details=f'label={self.credential.label}',
        )
        messages.success(self.request, 'Senha da credencial atualizada com sucesso.')
        return super().form_valid(form)


@method_decorator(never_cache, name='dispatch')
class VaultMasterPasswordChangeView(
    LoginRequiredMixin,
    VaultAccessRequiredMixin,
    VaultUnlockedRequiredMixin,
    FormView,
):
    template_name = 'cofre/change_password.html'
    form_class = VaultMasterPasswordChangeForm
    success_url = reverse_lazy('cofre_home')

    def form_valid(self, form):
        settings_obj = get_vault_settings()
        old_password = form.cleaned_data['old_password']
        if not settings_obj.check_master_password(old_password):
            form.add_error('old_password', 'Senha atual do cofre incorreta.')
            return self.form_invalid(form)

        settings_obj.set_master_password(form.cleaned_data['new_password'])
        settings_obj.save(
            update_fields=['password_hash', 'failed_unlock_attempts', 'lockout_until', 'updated_at']
        )
        lock_vault_session(self.request)
        log_vault_event(self.request, VaultAuditLog.ACTION_PASSWORD_CHANGED)
        messages.success(self.request, 'Senha do cofre atualizada. Desbloqueie novamente para continuar.')
        return redirect('cofre_unlock')


@method_decorator(never_cache, name='dispatch')
class VaultAccessManageView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = 'cofre/manage_access.html'
    form_class = VaultAccessControlForm
    success_url = reverse_lazy('cofre_manage_access')

    def test_func(self):
        return bool(getattr(self.request.user, 'is_superuser', False))

    def handle_no_permission(self):
        messages.error(self.request, 'Somente administradores podem gerir acessos do cofre.')
        return redirect('chamados_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        settings_obj = get_vault_settings()
        kwargs['initial_users'] = settings_obj.authorized_users.all()
        return kwargs

    def form_valid(self, form):
        settings_obj = get_vault_settings()
        selected_users = list(form.cleaned_data['users'])
        settings_obj.authorized_users.set(selected_users)
        log_vault_event(
            self.request,
            VaultAuditLog.ACTION_ACCESS_LIST_CHANGED,
            details=f'authorized_users_count={len(selected_users)}',
        )
        messages.success(self.request, 'Lista de usuarios com acesso ao cofre atualizada.')
        return super().form_valid(form)


@method_decorator(never_cache, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class VaultLockView(LoginRequiredMixin, VaultAccessRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        lock_vault_session(request)
        log_vault_event(request, VaultAuditLog.ACTION_LOCKED_MANUALLY)
        messages.info(request, 'Cofre bloqueado manualmente.')
        return redirect('cofre_unlock')


@method_decorator(never_cache, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class VaultCopyPasswordView(LoginRequiredMixin, VaultAccessRequiredMixin, View):
    def post(self, request, credential_id: int, *args, **kwargs):
        if not is_vault_unlocked(request):
            return JsonResponse(
                {'detail': 'Cofre bloqueado. Informe a senha novamente.'},
                status=423,
            )

        credential = get_object_or_404(VaultCredential, pk=credential_id)
        response = JsonResponse(
            {
                'password': credential.get_secret_password(),
                'clear_seconds': int(getattr(settings, 'VAULT_CLIPBOARD_CLEAR_SECONDS', 15) or 15),
            }
        )
        log_vault_event(
            request,
            VaultAuditLog.ACTION_CREDENTIAL_COPIED,
            credential=credential,
            details=f'label={credential.label}',
        )
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response
