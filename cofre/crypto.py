import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _build_fernet() -> Fernet:
    configured_key = (getattr(settings, 'VAULT_ENCRYPTION_KEY', '') or '').strip()
    if configured_key:
        try:
            return Fernet(configured_key.encode('utf-8'))
        except Exception as exc:
            raise ImproperlyConfigured(
                'VAULT_ENCRYPTION_KEY invalida. Gere uma chave Fernet valida.'
            ) from exc

    # Fallback para desenvolvimento: deriva uma chave estavel a partir da SECRET_KEY.
    digest = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_text(value: str) -> str:
    if value is None:
        raise ValueError('value nao pode ser None')
    fernet = _build_fernet()
    token = fernet.encrypt(value.encode('utf-8'))
    return token.decode('utf-8')


def decrypt_text(token: str) -> str:
    if not token:
        return ''
    fernet = _build_fernet()
    try:
        value = fernet.decrypt(token.encode('utf-8'))
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            'Nao foi possivel descriptografar a credencial. Verifique VAULT_ENCRYPTION_KEY.'
        ) from exc
    return value.decode('utf-8')
