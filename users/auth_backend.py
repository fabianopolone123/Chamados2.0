from __future__ import annotations

import logging
import ssl
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from ldap3 import Connection, Server, SUBTREE, Tls
from ldap3.utils.conv import escape_filter_chars

logger = logging.getLogger(__name__)


class ActiveDirectoryBackend:
    """Autentica usuarios no Active Directory via LDAP (ldap3)."""

    def authenticate(
        self,
        request,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ):
        if not username or not password:
            return None

        server_uri = getattr(settings, 'AD_LDAP_SERVER_URI', '')
        base_dn = getattr(settings, 'AD_LDAP_BASE_DN', '')
        bind_dn = getattr(settings, 'AD_LDAP_BIND_DN', '')
        bind_password = getattr(settings, 'AD_LDAP_BIND_PASSWORD', '')
        user_filter = getattr(settings, 'AD_LDAP_USER_FILTER', '')
        user_attr_map = getattr(settings, 'AD_LDAP_USER_ATTR_MAP', {})
        validate_cert = bool(getattr(settings, 'AD_LDAP_VALIDATE_CERT', True))
        ca_cert_file = (getattr(settings, 'AD_LDAP_CA_CERT_FILE', '') or '').strip()
        connect_timeout = int(getattr(settings, 'AD_LDAP_CONNECT_TIMEOUT', 5) or 5)

        if not server_uri or not base_dn or not user_filter:
            return None

        parsed = urlparse(server_uri)
        if parsed.scheme in {'ldap', 'ldaps'}:
            host = parsed.hostname or server_uri
            port = parsed.port or (636 if parsed.scheme == 'ldaps' else 389)
            use_ssl = parsed.scheme == 'ldaps'
        else:
            host = server_uri
            port = 636
            use_ssl = True

        escaped_username = escape_filter_chars(username)
        search_filter = user_filter.replace('%(user)s', escaped_username)
        attributes = sorted(set(user_attr_map.values())) or ['sAMAccountName']
        tls = Tls(
            validate=ssl.CERT_REQUIRED if validate_cert else ssl.CERT_NONE,
            ca_certs_file=ca_cert_file or None,
        )

        try:
            server = Server(
                host=host,
                port=port,
                use_ssl=use_ssl,
                tls=tls,
                get_info=None,
                connect_timeout=connect_timeout,
            )
            bind_conn = Connection(
                server,
                user=bind_dn,
                password=bind_password,
                auto_bind=True,
                raise_exceptions=True,
                read_only=True,
            )
            bind_conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=attributes,
            )
            if not bind_conn.entries:
                bind_conn.unbind()
                return None

            entry = bind_conn.entries[0]
            user_dn = entry.entry_dn
            bind_conn.unbind()
        except Exception:
            logger.exception('Falha ao consultar AD para login do usuario=%s', username)
            return None

        try:
            user_conn = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
                raise_exceptions=True,
                read_only=True,
            )
            user_conn.unbind()
        except Exception:
            logger.warning('Credencial AD invalida para usuario=%s', username)
            return None

        User = get_user_model()
        user, _created = User.objects.get_or_create(username=username)

        for field, ldap_attr in user_attr_map.items():
            if hasattr(user, field) and ldap_attr in entry:
                value = entry[ldap_attr].value
                if value is not None:
                    setattr(user, field, value)

        user.set_unusable_password()
        user.save()
        return user

    def get_user(self, user_id: int):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
