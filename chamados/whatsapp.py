from __future__ import annotations

import json
import logging
from urllib import error, request

from django.conf import settings

from .models import Ticket


logger = logging.getLogger(__name__)


def notifications_enabled() -> bool:
    return bool(
        getattr(settings, 'WHATSAPP_NOTIFICATIONS_ENABLED', False)
        and (getattr(settings, 'WHATSAPP_WEBHOOK_URL', '') or '').strip()
        and (getattr(settings, 'WHATSAPP_GROUP_JID', '') or '').strip()
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


def notify_group_new_ticket(ticket: Ticket) -> bool:
    if not notifications_enabled():
        return False
    if not bool(getattr(settings, 'WHATSAPP_SEND_GROUP_ON_NEW_TICKET', True)):
        return False

    payload = {
        'event': 'new_ticket',
        'group_jid': (getattr(settings, 'WHATSAPP_GROUP_JID', '') or '').strip(),
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

    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
    }
    token = (getattr(settings, 'WHATSAPP_WEBHOOK_TOKEN', '') or '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'

    req = request.Request(
        (getattr(settings, 'WHATSAPP_WEBHOOK_URL', '') or '').strip(),
        data=data,
        headers=headers,
        method='POST',
    )
    timeout = int(getattr(settings, 'WHATSAPP_WEBHOOK_TIMEOUT_SECONDS', 10) or 10)

    try:
        with request.urlopen(req, timeout=timeout) as response:
            status_code = getattr(response, 'status', None) or response.getcode()
            if 200 <= int(status_code) < 300:
                return True
            logger.warning('Webhook WhatsApp respondeu com status inesperado: %s', status_code)
            return False
    except (error.HTTPError, error.URLError, TimeoutError, ValueError) as exc:
        logger.warning('Falha ao enviar notificacao WhatsApp do chamado #%s: %s', ticket.id, exc)
        return False
