from __future__ import annotations

import json
import logging
from urllib import error, request

from django.conf import settings

from .models import Ticket


logger = logging.getLogger(__name__)

WAPI_SUCCESS_STATUSES = {'success', 'sent', 'ok', 'queued'}


def _clean(value: str) -> str:
    return (value or '').strip()


def _wapi_configured() -> bool:
    return bool(_clean(getattr(settings, 'WAPI_TOKEN', '')) and _clean(getattr(settings, 'WAPI_INSTANCE', '')))


def _webhook_configured() -> bool:
    return bool(_clean(getattr(settings, 'WHATSAPP_WEBHOOK_URL', '')))


def active_provider() -> str:
    configured = _clean(getattr(settings, 'WHATSAPP_PROVIDER', '')).lower()
    if configured in {'wapi', 'webhook'}:
        return configured
    if _wapi_configured():
        return 'wapi'
    if _webhook_configured():
        return 'webhook'
    return ''


def notifications_enabled() -> bool:
    return bool(
        getattr(settings, 'WHATSAPP_NOTIFICATIONS_ENABLED', False)
        and _clean(getattr(settings, 'WHATSAPP_GROUP_JID', ''))
        and active_provider()
    )


def render_new_ticket_message(ticket: Ticket) -> str:
    template = (
        getattr(settings, 'WHATSAPP_TEMPLATE_NEW_TICKET', '🚨 {urgencia} - {solicitante}\n📄 {title}')
        or '🚨 {urgencia} - {solicitante}\n📄 {title}'
    )
    return template.format(
        urgencia=ticket.get_priority_display(),
        solicitante=ticket.created_by.username,
        title=ticket.title,
        chamado=ticket.id,
    )


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float | tuple[float, float] | int) -> tuple[int, dict | None]:
    data = json.dumps(payload).encode('utf-8')
    req = request.Request(url, data=data, headers=headers, method='POST')
    with request.urlopen(req, timeout=timeout) as response:
        status_code = getattr(response, 'status', None) or response.getcode()
        raw = response.read()
    if not raw:
        return int(status_code), None
    try:
        return int(status_code), json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return int(status_code), None


def _notify_group_new_ticket_webhook(ticket: Ticket) -> bool:
    payload = {
        'event': 'new_ticket',
        'group_jid': _clean(getattr(settings, 'WHATSAPP_GROUP_JID', '')),
        'message': render_new_ticket_message(ticket),
        'ticket': {
            'id': ticket.id,
            'title': ticket.title,
            'priority': ticket.priority,
            'priority_display': ticket.get_priority_display(),
            'status': ticket.status,
            'created_by': ticket.created_by.username,
        },
    }
    headers = {'Content-Type': 'application/json'}
    token = _clean(getattr(settings, 'WHATSAPP_WEBHOOK_TOKEN', ''))
    if token:
        headers['Authorization'] = f'Bearer {token}'

    status_code, _response_data = _post_json(
        _clean(getattr(settings, 'WHATSAPP_WEBHOOK_URL', '')),
        payload,
        headers,
        int(getattr(settings, 'WHATSAPP_WEBHOOK_TIMEOUT_SECONDS', 10) or 10),
    )
    if 200 <= status_code < 300:
        return True
    logger.warning('Webhook WhatsApp respondeu com status inesperado: %s', status_code)
    return False


def _notify_group_new_ticket_wapi(ticket: Ticket) -> bool:
    url = (
        f"{_clean(getattr(settings, 'WAPI_BASE_URL', 'https://api.w-api.app/v1')).rstrip('/')}"
        f"/message/send-text?instanceId={_clean(getattr(settings, 'WAPI_INSTANCE', ''))}"
    )
    payload = {
        'token': _clean(getattr(settings, 'WAPI_TOKEN', '')),
        'phone': _clean(getattr(settings, 'WHATSAPP_GROUP_JID', '')),
        'message': render_new_ticket_message(ticket),
    }
    headers = {
        'Authorization': f"Bearer {_clean(getattr(settings, 'WAPI_TOKEN', ''))}",
        'Content-Type': 'application/json',
    }
    timeout = (
        float(getattr(settings, 'WAPI_SEND_CONNECT_TIMEOUT', 6.0) or 6.0),
        float(getattr(settings, 'WAPI_SEND_READ_TIMEOUT', 20.0) or 20.0),
    )
    status_code, response_data = _post_json(url, payload, headers, timeout)
    if not (200 <= status_code < 300):
        logger.warning('W-API respondeu com status inesperado: %s', status_code)
        return False
    if not isinstance(response_data, dict):
        return True

    status = _clean(str(response_data.get('status') or response_data.get('state') or '')).lower()
    message_id = _clean(str(response_data.get('messageId') or response_data.get('insertedId') or ''))
    if status in WAPI_SUCCESS_STATUSES or message_id:
        return True

    logger.warning('W-API retornou payload inesperado para chamado #%s: %s', ticket.id, response_data)
    return False


def notify_group_new_ticket(ticket: Ticket) -> bool:
    if not notifications_enabled():
        return False
    if not bool(getattr(settings, 'WHATSAPP_SEND_GROUP_ON_NEW_TICKET', True)):
        return False

    provider = active_provider()
    try:
        if provider == 'wapi':
            return _notify_group_new_ticket_wapi(ticket)
        if provider == 'webhook':
            return _notify_group_new_ticket_webhook(ticket)
        logger.warning('Nenhum provider de WhatsApp configurado para o chamado #%s.', ticket.id)
        return False
    except (error.HTTPError, error.URLError, TimeoutError, ValueError) as exc:
        logger.warning('Falha ao enviar notificacao WhatsApp do chamado #%s via %s: %s', ticket.id, provider, exc)
        return False
