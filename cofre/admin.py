from django.contrib import admin

from .models import VaultAuditLog, VaultCredential, VaultSettings


@admin.register(VaultSettings)
class VaultSettingsAdmin(admin.ModelAdmin):
    filter_horizontal = ('authorized_users',)
    readonly_fields = ('failed_unlock_attempts', 'lockout_until', 'updated_at')

    def has_add_permission(self, request):
        # Mantem configuracao unica do cofre.
        return not VaultSettings.objects.exists()


@admin.register(VaultCredential)
class VaultCredentialAdmin(admin.ModelAdmin):
    list_display = ('label', 'account_username', 'created_by', 'updated_at')
    search_fields = ('label', 'account_username')
    readonly_fields = ('password_encrypted', 'created_at', 'updated_at')


@admin.register(VaultAuditLog)
class VaultAuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'actor', 'credential', 'ip_address')
    search_fields = ('actor__username', 'credential__label', 'details', 'ip_address')
    list_filter = ('action', 'created_at')
    readonly_fields = (
        'created_at',
        'action',
        'actor',
        'credential',
        'ip_address',
        'user_agent',
        'details',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
