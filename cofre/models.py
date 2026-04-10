from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import models

from .crypto import decrypt_text, encrypt_text


class VaultSettings(models.Model):
    password_hash = models.CharField(max_length=255, blank=True)
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

    def check_master_password(self, raw_password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password(raw_password, self.password_hash)

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
