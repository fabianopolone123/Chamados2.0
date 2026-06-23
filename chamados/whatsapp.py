from __future__ import annotations

import json
import logging
from urllib import error, request

from django.conf import settings

from .models import Ticket


logger = logging.getLogger(__name__)

WAPI_SUCCESS_STATUSES = {'success', 'sent', 'ok', 'queued'}
_DEFAULT_TEMPLATE = '🚨 {urgencia} - {solicitante}\n📄 {title}'
_DEFAULT_WAPI_BASE_URL = 'https://api.w-api.app/v1'


def _clean(value: str) -> str:
    return (value or '').strip()


def _cfg():
    """Retorna WhatsAppConfig do banco se existir, ou None. Nao cria automaticamente."""
    from .models import WhatsAppConfig  # noqa: PLC0415
    return WhatsAppConfig.objects.filter(pk=1).first()


def _get(field: str, setting_name: str, default='') -> str:
    """Lê campo do banco; se vazio ou sem registro, cai no settings.py."""
    cfg = _cfg()
    if cfg is not None:
        db_value = _clean(str(getattr(cfg, field, '') or ''))
        if db_value:
            return db_value
    return _clean(str(getattr(settings, setting_name, default) or default))


def _get_bool(field: str, setting_name: str, default: bool) -> bool:
    cfg = _cfg()
    if cfg is not None:
        return bool(getattr(cfg, field, default))
    return bool(getattr(settings, setting_name, default))


def _wapi_configured() -> bool:
    return bool(_get('wapi_token', 'WAPI_TOKEN') and _get('wapi_instance', 'WAPI_INSTANCE'))


def _webhook_configured() -> bool:
    return bool(_get('webhook_url', 'WHATSAPP_WEBHOOK_URL'))


def active_provider() -> str:
    configured = _get('provider', 'WHATSAPP_PROVIDER').lower()
    if configured in {'wapi', 'webhook'}:
        return configured
    if _wapi_configured():
        return 'wapi'
    if _webhook_configured():
        return 'webhook'
    return ''


def notifications_enabled() -> bool:
    return bool(
        _get_bool('notifications_enabled', 'WHATSAPP_NOTIFICATIONS_ENABLED', False)
        and _get('group_jid', 'WHATSAPP_GROUP_JID')
        and active_provider()
    )


def _requester_label(ticket: Ticket) -> str:
    full_name = (ticket.created_by.get_full_name() or '').strip()
    if full_name:
        return full_name
    return ticket.created_by.username.replace('.', ' ').replace('_', ' ').strip().title()


def render_new_ticket_message(ticket: Ticket) -> str:
    template = (
        _get('template_new_ticket', 'WHATSAPP_TEMPLATE_NEW_TICKET', _DEFAULT_TEMPLATE)
        or _DEFAULT_TEMPLATE
    )
    template = template.replace('\\n', '\n')
    return template.format(
        urgencia=ticket.get_priority_display(),
        solicitante=_requester_label(ticket),
        title=ticket.title,
        chamado=ticket.id,
    )


def _normalize_timeout(timeout: float | tuple[float, float] | int) -> float | int:
    if isinstance(timeout, tuple):
        values = [float(item) for item in timeout if item is not None]
        return max(values) if values else 10.0
    return timeout


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float | tuple[float, float] | int) -> tuple[int, dict | None]:
    data = json.dumps(payload).encode('utf-8')
    req = request.Request(url, data=data, headers=headers, method='POST')
    with request.urlopen(req, timeout=_normalize_timeout(timeout)) as response:
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
        'group_jid': _get('group_jid', 'WHATSAPP_GROUP_JID'),
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
    token = _get('webhook_token', 'WHATSAPP_WEBHOOK_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'

    timeout = int(getattr(settings, 'WHATSAPP_WEBHOOK_TIMEOUT_SECONDS', 10) or 10)
    status_code, _response_data = _post_json(
        _get('webhook_url', 'WHATSAPP_WEBHOOK_URL'),
        payload,
        headers,
        timeout,
    )
    if 200 <= status_code < 300:
        return True
    logger.warning('Webhook WhatsApp respondeu com status inesperado: %s', status_code)
    return False


def _notify_group_new_ticket_wapi(ticket: Ticket) -> bool:
    base_url = (
        _get('wapi_base_url', 'WAPI_BASE_URL', _DEFAULT_WAPI_BASE_URL).rstrip('/')
        or _DEFAULT_WAPI_BASE_URL
    )
    instance = _get('wapi_instance', 'WAPI_INSTANCE')
    token = _get('wapi_token', 'WAPI_TOKEN')
    url = f'{base_url}/message/send-text?instanceId={instance}'
    payload = {
        'token': token,
        'phone': _get('group_jid', 'WHATSAPP_GROUP_JID'),
        'message': render_new_ticket_message(ticket),
    }
    headers = {
        'Authorization': f'Bearer {token}',
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


def send_test_message(sent_by: str = '') -> tuple[bool, str]:
    """Envia mensagem de teste para o grupo configurado. Retorna (sucesso, mensagem_erro)."""
    provider = active_provider()
    if not provider:
        return False, 'Nenhum provider configurado (W-API ou Webhook).'
    if not _get('group_jid', 'WHATSAPP_GROUP_JID'):
        return False, 'JID do grupo nao configurado.'

    label = _clean(sent_by) or 'sistema'
    message = f'✅ Teste de notificacao enviado por {label}. Configuracao de WhatsApp funcionando.'

    try:
        if provider == 'wapi':
            base_url = (
                _get('wapi_base_url', 'WAPI_BASE_URL', _DEFAULT_WAPI_BASE_URL).rstrip('/')
                or _DEFAULT_WAPI_BASE_URL
            )
            instance = _get('wapi_instance', 'WAPI_INSTANCE')
            token = _get('wapi_token', 'WAPI_TOKEN')
            url = f'{base_url}/message/send-text?instanceId={instance}'
            payload = {
                'token': token,
                'phone': _get('group_jid', 'WHATSAPP_GROUP_JID'),
                'message': message,
            }
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            }
            timeout = (
                float(getattr(settings, 'WAPI_SEND_CONNECT_TIMEOUT', 6.0) or 6.0),
                float(getattr(settings, 'WAPI_SEND_READ_TIMEOUT', 20.0) or 20.0),
            )
            status_code, response_data = _post_json(url, payload, headers, timeout)
            if not (200 <= status_code < 300):
                return False, f'W-API retornou status {status_code}.'
            return True, ''

        if provider == 'webhook':
            headers = {'Content-Type': 'application/json'}
            token = _get('webhook_token', 'WHATSAPP_WEBHOOK_TOKEN')
            if token:
                headers['Authorization'] = f'Bearer {token}'
            payload = {
                'event': 'test',
                'group_jid': _get('group_jid', 'WHATSAPP_GROUP_JID'),
                'message': message,
            }
            timeout = int(getattr(settings, 'WHATSAPP_WEBHOOK_TIMEOUT_SECONDS', 10) or 10)
            status_code, _ = _post_json(
                _get('webhook_url', 'WHATSAPP_WEBHOOK_URL'),
                payload,
                headers,
                timeout,
            )
            if not (200 <= status_code < 300):
                return False, f'Webhook retornou status {status_code}.'
            return True, ''

    except (error.HTTPError, error.URLError, TimeoutError, ValueError) as exc:
        return False, str(exc)

    return False, 'Provider desconhecido.'


def notify_group_new_ticket(ticket: Ticket) -> bool:
    if not notifications_enabled():
        return False
    if not _get_bool('send_group_on_new_ticket', 'WHATSAPP_SEND_GROUP_ON_NEW_TICKET', True):
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
