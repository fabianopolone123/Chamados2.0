from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import VaultSettings

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
