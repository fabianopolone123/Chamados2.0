from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import VaultAuditLog, VaultSettings

VAULT_UNLOCK_SESSION_KEY = 'vault_unlocked_until_iso'


def get_vault_settings() -> VaultSettings:
    settings_obj = VaultSettings.load()
    settings_obj.ensure_default_password()
    return settings_obj


def user_can_access_vault(user) -> bool:
    try:
        settings_obj = get_vault_settings()
    except Exception:
        return False
    return settings_obj.user_has_access(user)


def unlock_vault_session(request):
    unlock_seconds = int(getattr(settings, 'VAULT_UNLOCK_SECONDS', 60) or 60)
    expires_at = timezone.now() + timedelta(seconds=unlock_seconds)
    request.session[VAULT_UNLOCK_SESSION_KEY] = expires_at.isoformat()
    request.session.modified = True


def lock_vault_session(request):
    request.session.pop(VAULT_UNLOCK_SESSION_KEY, None)
    request.session.modified = True


def get_vault_unlock_expires_at(request):
    raw = request.session.get(VAULT_UNLOCK_SESSION_KEY)
    if not raw:
        return None
    try:
        parsed = timezone.datetime.fromisoformat(raw)
    except Exception:
        lock_vault_session(request)
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def is_vault_unlocked(request) -> bool:
    expires_at = get_vault_unlock_expires_at(request)
    if not expires_at:
        return False
    if expires_at <= timezone.now():
        lock_vault_session(request)
        return False
    return True


def get_unlock_remaining_seconds(request) -> int:
    expires_at = get_vault_unlock_expires_at(request)
    if not expires_at:
        return 0
    remaining = int((expires_at - timezone.now()).total_seconds())
    return max(remaining, 0)


def _extract_client_ip(request) -> str:
    xff = (request.META.get('HTTP_X_FORWARDED_FOR') or '').strip()
    if xff:
        return xff.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def _extract_user_agent(request) -> str:
    return (request.META.get('HTTP_USER_AGENT') or '').strip()[:255]


def log_vault_event(request, action: str, credential=None, details: str = ''):
    actor = request.user if getattr(request.user, 'is_authenticated', False) else None
    VaultAuditLog.objects.create(
        action=action,
        actor=actor,
        credential=credential,
        ip_address=_extract_client_ip(request) or None,
        user_agent=_extract_user_agent(request),
        details=(details or '').strip(),
    )
