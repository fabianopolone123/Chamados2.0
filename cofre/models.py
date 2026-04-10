from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from datetime import timedelta

from .crypto import decrypt_text, encrypt_text


class VaultSettings(models.Model):
    password_hash = models.CharField(max_length=255, blank=True)
    failed_unlock_attempts = models.PositiveSmallIntegerField(default=0)
    lockout_until = models.DateTimeField(null=True, blank=True)
    authorized_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='vault_authorizations',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuracao do Cofre'
        verbose_name_plural = 'Configuracoes do Cofre'

    def __str__(self) -> str:
        return 'Configuracao do Cofre'

    @classmethod
    def load(cls) -> 'VaultSettings':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def ensure_default_password(self):
        if self.password_hash:
            return
        default_password = (getattr(settings, 'VAULT_DEFAULT_PASSWORD', '') or '').strip()
        if not default_password:
            return
        self.set_master_password(default_password)
        self.save(update_fields=['password_hash', 'updated_at'])

    def set_master_password(self, raw_password: str):
        self.password_hash = make_password(raw_password)
        self.failed_unlock_attempts = 0
        self.lockout_until = None

    def check_master_password(self, raw_password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password(raw_password, self.password_hash)

    def is_unlock_locked(self) -> bool:
        if not self.lockout_until:
            return False
        return self.lockout_until > timezone.now()

    def get_lockout_remaining_seconds(self) -> int:
        if not self.lockout_until:
            return 0
        remaining = int((self.lockout_until - timezone.now()).total_seconds())
        return max(remaining, 0)

    def register_failed_unlock_attempt(self):
        max_attempts = int(getattr(settings, 'VAULT_MAX_FAILED_ATTEMPTS', 5) or 5)
        lockout_seconds = int(getattr(settings, 'VAULT_LOCKOUT_SECONDS', 300) or 300)
        self.failed_unlock_attempts += 1
        if self.failed_unlock_attempts >= max_attempts:
            self.lockout_until = timezone.now() + timedelta(seconds=lockout_seconds)
            self.failed_unlock_attempts = 0
        self.save(update_fields=['failed_unlock_attempts', 'lockout_until', 'updated_at'])

    def reset_unlock_failures(self):
        self.failed_unlock_attempts = 0
        self.lockout_until = None
        self.save(update_fields=['failed_unlock_attempts', 'lockout_until', 'updated_at'])

    def user_has_access(self, user) -> bool:
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        if user.is_superuser:
            return True
        return self.authorized_users.filter(pk=user.pk).exists()


class VaultCredential(models.Model):
    label = models.CharField(max_length=120)
    account_username = models.CharField(max_length=150, blank=True)
    password_encrypted = models.TextField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.PROTECT,
        related_name='created_vault_credentials',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Credencial do Cofre'
        verbose_name_plural = 'Credenciais do Cofre'
        ordering = ['label']

    def __str__(self) -> str:
        return self.label

    def set_secret_password(self, raw_password: str):
        self.password_encrypted = encrypt_text(raw_password)

    def get_secret_password(self) -> str:
        return decrypt_text(self.password_encrypted)


class VaultAuditLog(models.Model):
    ACTION_UNLOCK_SUCCESS = 'unlock_success'
    ACTION_UNLOCK_FAILURE = 'unlock_failure'
    ACTION_UNLOCK_LOCKOUT = 'unlock_lockout'
    ACTION_CREDENTIAL_CREATED = 'credential_created'
    ACTION_CREDENTIAL_COPIED = 'credential_copied'
    ACTION_PASSWORD_CHANGED = 'password_changed'
    ACTION_ACCESS_LIST_CHANGED = 'access_list_changed'
    ACTION_LOCKED_MANUALLY = 'locked_manually'

    ACTION_CHOICES = [
        (ACTION_UNLOCK_SUCCESS, 'Unlock sucesso'),
        (ACTION_UNLOCK_FAILURE, 'Unlock falha'),
        (ACTION_UNLOCK_LOCKOUT, 'Unlock bloqueado temporariamente'),
        (ACTION_CREDENTIAL_CREATED, 'Credencial criada'),
        (ACTION_CREDENTIAL_COPIED, 'Credencial copiada'),
        (ACTION_PASSWORD_CHANGED, 'Senha do cofre alterada'),
        (ACTION_ACCESS_LIST_CHANGED, 'Lista de acesso alterada'),
        (ACTION_LOCKED_MANUALLY, 'Cofre bloqueado manualmente'),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=64, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='vault_audit_logs',
    )
    credential = models.ForeignKey(
        VaultCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_logs',
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Auditoria do Cofre'
        verbose_name_plural = 'Auditoria do Cofre'
        ordering = ['-created_at']

    def __str__(self) -> str:
        actor = self.actor.username if self.actor else 'anonimo'
        return f'{self.created_at:%d/%m/%Y %H:%M:%S} - {self.action} - {actor}'
