from django.contrib import admin

from .models import VaultCredential, VaultSettings


@admin.register(VaultSettings)
class VaultSettingsAdmin(admin.ModelAdmin):
    filter_horizontal = ('authorized_users',)

    def has_add_permission(self, request):
        # Mantem configuracao unica do cofre.
        return not VaultSettings.objects.exists()


@admin.register(VaultCredential)
class VaultCredentialAdmin(admin.ModelAdmin):
    list_display = ('label', 'account_username', 'created_by', 'updated_at')
    search_fields = ('label', 'account_username')
    readonly_fields = ('password_encrypted', 'created_at', 'updated_at')
