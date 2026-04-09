from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from ldap3 import Connection, Server, SUBTREE
from ldap3.utils.conv import escape_filter_chars


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

        if not server_uri or not base_dn or not user_filter:
            return None

        escaped_username = escape_filter_chars(username)
        search_filter = user_filter.replace('%(user)s', escaped_username)
        attributes = sorted(set(user_attr_map.values())) or ['sAMAccountName']

        try:
            server = Server(server_uri, get_info=None)
            bind_conn = Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
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
            return None

        try:
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
        except Exception:
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
