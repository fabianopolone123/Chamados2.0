import logging
import os
import re
import unicodedata
from io import BytesIO
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from openpyxl import load_workbook

from .models import TicketAttendance, TicketAutoPauseReview

logger = logging.getLogger(__name__)

MONTH_TOKENS = {
    1: ('jan', 'janeiro', '01'),
    2: ('fev', 'fevereiro', '02'),
    3: ('mar', 'marco', '03'),
    4: ('abr', 'abril', '04'),
    5: ('mai', 'maio', '05'),
    6: ('jun', 'junho', '06'),
    7: ('jul', 'julho', '07'),
    8: ('ago', 'agosto', '08'),
    9: ('set', 'setembro', '09'),
    10: ('out', 'outubro', '10'),
    11: ('nov', 'novembro', '11'),
    12: ('dez', 'dezembro', '12'),
}


def _normalize(value: str) -> str:
    raw = (value or '').strip().lower()
    base = unicodedata.normalize('NFKD', raw).encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'\s+', ' ', base)
    return base


def _build_header_map(cells: list[str]) -> dict[str, int]:
    wanted = {
        'ti': None,
        'data': None,
        'contato': None,
        'setor': None,
        'notificacao': None,
        'prioridade': None,
        'falha': None,
        'acao': None,
        'fechado': None,
        'tempo': None,
        'acao_eficaz': None,
    }
    for idx, raw in enumerate(cells, start=1):
        key = _normalize(str(raw or ''))
        if key == 'ti':
            wanted['ti'] = idx
        elif key == 'data':
            wanted['data'] = idx
        elif key == 'contato':
            wanted['contato'] = idx
        elif key == 'setor':
            wanted['setor'] = idx
        elif key == 'notificacao':
            wanted['notificacao'] = idx
        elif key == 'prioridade':
            wanted['prioridade'] = idx
        elif key == 'falha':
            wanted['falha'] = idx
        elif key in {'acao / correcao', 'acao/correcao', 'acao correcao'}:
            wanted['acao'] = idx
        elif key == 'fechado':
            wanted['fechado'] = idx
        elif key == 'tempo':
            wanted['tempo'] = idx
        elif key == 'acao eficaz':
            wanted['acao_eficaz'] = idx
    return wanted


def _resolve_sheet(workbook, event_dt: datetime):
    best_sheet = workbook.active
    best_score = -1
    month_tokens = MONTH_TOKENS.get(event_dt.month, ())
    year_text = str(event_dt.year)
    for sheet in workbook.worksheets:
        normalized = _normalize(sheet.title)
        score = 0
        if year_text in normalized:
            score += 3
        if any(token in normalized for token in month_tokens):
            score += 3
        if score > best_score:
            best_score = score
            best_sheet = sheet
    return best_sheet


def _find_header(sheet):
    for row_idx in range(1, 8):
        raw = [sheet.cell(row=row_idx, column=col).value for col in range(1, 30)]
        header_map = _build_header_map(raw)
        if header_map['data'] and header_map['contato'] and header_map['notificacao']:
            return row_idx, header_map

    return 1, {
        'ti': 1,
        'data': 2,
        'contato': 3,
        'setor': 4,
        'notificacao': 5,
        'prioridade': 6,
        'falha': 7,
        'acao': 8,
        'fechado': 9,
        'tempo': 10,
        'acao_eficaz': 11,
    }


def _find_next_row(sheet, header_row: int, header_map: dict[str, int]) -> int:
    key_cols = [header_map[k] for k in ('data', 'contato', 'notificacao', 'fechado') if header_map.get(k)]
    if not key_cols:
        return header_row + 1
    row = header_row + 1
    while True:
        has_value = any((sheet.cell(row=row, column=col).value not in (None, '')) for col in key_cols)
        if not has_value:
            return row
        row += 1


def _format_dt(dt: datetime) -> str:
    return timezone.localtime(dt).strftime('%d/%m/%Y %H:%M')


def _format_duration(opened_at: datetime, closed_at: datetime) -> str:
    seconds = int(max((closed_at - opened_at).total_seconds(), 0))
    minutes = seconds // 60
    hours = minutes // 60
    mins = minutes % 60
    return f'{hours:02d}:{mins:02d}'


def _looks_like_windows_drive_path(value: str) -> bool:
    return bool(re.match(r'^[A-Za-z]:[\\/]', (value or '').strip()))


def _normalize_windows_unc_input(value: str) -> str:
    raw = (value or '').strip()
    if raw.startswith('\\\\'):
        return raw
    if re.match(r'^[^\\/:]+\\[^\\]+', raw):
        return '\\\\' + raw.lstrip('\\')
    return raw


def _looks_like_windows_unc_path(value: str) -> bool:
    return _normalize_windows_unc_input(value).startswith('\\\\')


def _translate_windows_drive_path(value: str) -> str:
    raw = (value or '').strip()
    match = re.match(r'^([A-Za-z]):[\\/](.*)$', raw)
    if not match:
        return ''
    mount_root = (getattr(settings, 'CHAMADOS_WINDOWS_DRIVE_MOUNT_ROOT', '/mnt') or '/mnt').strip()
    suffix = (match.group(2) or '').replace('\\', '/').lstrip('/')
    translated = Path(mount_root) / match.group(1).lower()
    for part in [item for item in suffix.split('/') if item]:
        translated /= part
    return str(translated)


def _translate_windows_unc_path(value: str) -> str:
    raw = _normalize_windows_unc_input(value)
    match = re.match(r'^\\\\[^\\]+\\([^\\]+)\\?(.*)$', raw)
    if not match:
        return ''
    mount_root = (getattr(settings, 'CHAMADOS_WINDOWS_DRIVE_MOUNT_ROOT', '/mnt') or '/mnt').strip()
    share_name = (match.group(1) or '').strip().lower()
    suffix = (match.group(2) or '').replace('\\', '/').lstrip('/')
    translated = Path(mount_root) / share_name
    for part in [item for item in suffix.split('/') if item]:
        translated /= part
    return str(translated)


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return '{' + str(key) + '}'


def _normalize_username(value: str) -> str:
    raw = (value or '').strip()
    if not raw:
        return ''
    if '\\' in raw:
        raw = raw.split('\\', 1)[1]
    if '@' in raw:
        raw = raw.split('@', 1)[0]
    return raw.strip()


def _build_attendant_path_context(attendant) -> dict[str, str]:
    full_name = attendant.get_full_name().strip() or (attendant.username or '').strip()
    first_name = full_name.split()[0].strip() if full_name else ''
    username = (attendant.username or '').strip()
    username_local = _normalize_username(username)
    year = timezone.localtime(timezone.now()).year
    return {
        'username': username,
        'username_local': username_local,
        'first_name': first_name,
        'full_name': full_name,
        'year': str(year),
        'year_short': str(year)[-2:],
    }


def _render_attendant_path_template(template: str, attendant) -> str:
    raw = (template or '').strip()
    if not raw:
        return ''
    try:
        return raw.format_map(_SafeFormatDict(_build_attendant_path_context(attendant))).strip()
    except Exception:
        logger.exception('Falha ao renderizar template de planilha para %s', attendant.username)
        return raw


def get_attendant_workbook_path_candidates(attendant) -> list[str]:
    candidates = [
        _render_attendant_path_template(getattr(settings, 'CHAMADOS_XLSX_SERVER_PATH_TEMPLATE', ''), attendant),
        (getattr(settings, 'CHAMADOS_XLSX_SERVER_PATH', '') or '').strip(),
        _render_attendant_path_template(getattr(settings, 'CHAMADOS_XLSX_PATH_TEMPLATE', ''), attendant),
        (getattr(settings, 'CHAMADOS_XLSX_PATH', '') or '').strip(),
    ]
    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = (candidate or '').strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)
    return unique_candidates


def get_attendant_default_workbook_path(attendant) -> str:
    candidates = get_attendant_workbook_path_candidates(attendant)
    return candidates[0] if candidates else ''


def _resolve_workbook_path(*, attendant, workbook_path: str) -> tuple[Path, list[str]]:
    raw = (workbook_path or '').strip()
    configured_default = (getattr(settings, 'CHAMADOS_XLSX_PATH', '') or '').strip()
    configured_server_default = (getattr(settings, 'CHAMADOS_XLSX_SERVER_PATH', '') or '').strip()
    attendant_candidates = get_attendant_workbook_path_candidates(attendant)

    candidates: list[str] = []
    if raw:
        candidates.append(raw)
    if (
        attendant_candidates
        and (
            not raw
            or raw == configured_default
            or raw == configured_server_default
            or _looks_like_windows_drive_path(raw)
            or _looks_like_windows_unc_path(raw)
        )
    ):
        candidates.extend(attendant_candidates)

    if raw and os.name != 'nt' and _looks_like_windows_drive_path(raw):
        translated = _translate_windows_drive_path(raw)
        if translated:
            candidates.append(translated)
    if raw and os.name != 'nt' and _looks_like_windows_unc_path(raw):
        translated_unc = _translate_windows_unc_path(raw)
        if translated_unc:
            candidates.append(translated_unc)

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = (candidate or '').strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)

    for candidate in unique_candidates:
        candidate_path = Path(candidate)
        if candidate_path.exists():
            return candidate_path, unique_candidates

    if unique_candidates:
        return Path(unique_candidates[0]), unique_candidates
    return Path(raw), unique_candidates


def _contact_name(attendance: TicketAttendance) -> str:
    creator = attendance.ticket.created_by
    if not creator:
        return '-'
    full_name = creator.get_full_name().strip()
    return full_name or creator.username or '-'


def _department_label(attendance: TicketAttendance) -> str:
    creator = attendance.ticket.created_by
    email = ((creator.email if creator else '') or '').strip()
    if '@' in email:
        return email.split('@', 1)[1]
    return ''


def _pending_auto_pause_reviews_count(attendant) -> int:
    return TicketAutoPauseReview.objects.filter(
        attendance__attendant=attendant,
        completed_at__isnull=True,
    ).count()


def _ticket_id_from_cell(value):
    if value in (None, ''):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)

    normalized = str(value).strip().lstrip('#')
    if normalized.isdigit():
        return int(normalized)
    return None


def _existing_ticket_ids_in_workbook(workbook) -> set[int]:
    ticket_ids = set()
    for sheet in workbook.worksheets:
        header_row, header_map = _find_header(sheet)
        ticket_col = header_map.get('ti')
        if not ticket_col:
            continue

        for row_idx in range(header_row + 1, sheet.max_row + 1):
            cell_ticket_id = _ticket_id_from_cell(sheet.cell(row=row_idx, column=ticket_col).value)
            if cell_ticket_id is not None:
                ticket_ids.add(cell_ticket_id)
    return ticket_ids


def _eligible_attendances(attendant) -> list[TicketAttendance]:
    return list(
        TicketAttendance.objects.filter(
            attendant=attendant,
            ended_at__isnull=False,
        )
        .filter(Q(auto_pause_review__isnull=True) | Q(auto_pause_review__completed_at__isnull=False))
        .select_related('ticket__created_by', 'attendant')
        .order_by('ticket_id', 'ended_at', 'id')
    )


def _format_duration_seconds(total_seconds: int) -> str:
    safe_seconds = max(int(total_seconds or 0), 0)
    minutes = safe_seconds // 60
    hours = minutes // 60
    mins = minutes % 60
    return f'{hours:02d}:{mins:02d}'


def _build_ticket_export_rows(attendant, existing_ticket_ids: set[int]) -> list[dict]:
    grouped: dict[int, list[TicketAttendance]] = {}
    for attendance in _eligible_attendances(attendant):
        if attendance.ticket_id in existing_ticket_ids:
            continue
        grouped.setdefault(attendance.ticket_id, []).append(attendance)

    rows = []
    for ticket_id, attendances in grouped.items():
        ordered = sorted(attendances, key=lambda item: (item.ended_at, item.id))
        first = ordered[0]
        last = ordered[-1]
        total_seconds = sum(
            max(int((item.ended_at - item.started_at).total_seconds()), 0)
            for item in ordered
            if item.ended_at
        )
        notes = []
        seen_notes = set()
        for item in ordered:
            note = (item.note or '').strip()
            if note and note not in seen_notes:
                seen_notes.add(note)
                notes.append(note)

        rows.append(
            {
                'ticket': last.ticket,
                'attendant': attendant,
                'started_at': first.started_at,
                'ended_at': last.ended_at,
                'note': '\n'.join(notes),
                'duration': _format_duration_seconds(total_seconds),
                'attendance_ids': [item.id for item in ordered],
            }
        )

    return sorted(rows, key=lambda item: (item['ended_at'], item['ticket'].id))


def _write_ticket_rows_to_workbook(workbook, rows: list[dict]) -> None:
    for row in rows:
        sheet = _resolve_sheet(workbook, timezone.localtime(row['ended_at']))
        header_row, header_map = _find_header(sheet)
        target_row = _find_next_row(sheet, header_row, header_map)
        ticket = row['ticket']

        values = {
            'ti': ticket.id,
            'data': _format_dt(row['started_at']),
            'contato': _contact_name_for_ticket(ticket),
            'setor': _department_label_for_ticket(ticket),
            'notificacao': ticket.description or '',
            'prioridade': ticket.get_priority_display(),
            'falha': ticket.get_failure_type_display(),
            'acao': row['note'],
            'fechado': _format_dt(row['ended_at']),
            'tempo': row['duration'],
            'acao_eficaz': '',
        }

        for key, col in header_map.items():
            if not col or key not in values:
                continue
            sheet.cell(row=target_row, column=col, value=values[key])


def _contact_name_for_ticket(ticket) -> str:
    creator = ticket.created_by
    if not creator:
        return '-'
    full_name = creator.get_full_name().strip()
    return full_name or creator.username or '-'


def _department_label_for_ticket(ticket) -> str:
    creator = ticket.created_by
    email = ((creator.email if creator else '') or '').strip()
    if '@' in email:
        return email.split('@', 1)[1]
    return ''


def _mark_export_rows(rows: list[dict], exported_path: str) -> None:
    attendance_ids = [
        attendance_id
        for row in rows
        for attendance_id in row['attendance_ids']
    ]
    if not attendance_ids:
        return
    now = timezone.now()
    TicketAttendance.objects.filter(id__in=attendance_ids).update(
        exported_at=now,
        exported_path=exported_path,
    )


def _spreadsheet_export_blocker(attendant):
    pending_reviews = _pending_auto_pause_reviews_count(attendant)
    if pending_reviews:
        return (
            False,
            0,
            'Existem pausas automaticas pendentes para este atendente. '
            'Conclua essas revisoes antes de preencher a planilha.',
        )
    return None


def export_attendant_logs_to_excel(*, attendant, workbook_path: str) -> tuple[bool, int, str]:
    blocker = _spreadsheet_export_blocker(attendant)
    if blocker:
        return blocker

    path, tried_candidates = _resolve_workbook_path(attendant=attendant, workbook_path=workbook_path)
    if not path.exists():
        tried_text = ', '.join(tried_candidates[:3]) if tried_candidates else str(path)
        return (
            False,
            0,
            'Arquivo nao encontrado. '
            f'Tentativas: {tried_text}. '
            'Se o sistema estiver no Ubuntu, use um caminho acessivel pelo servidor '
            'ou configure CHAMADOS_XLSX_SERVER_PATH/CHAMADOS_XLSX_SERVER_PATH_TEMPLATE.',
        )

    try:
        wb = load_workbook(path)
        rows = _build_ticket_export_rows(attendant, _existing_ticket_ids_in_workbook(wb))
        if not rows:
            return True, 0, 'Nenhum chamado novo para exportar. Todos os chamados do atendente ja constam na planilha.'
        _write_ticket_rows_to_workbook(wb, rows)
        wb.save(path)
    except PermissionError:
        return False, 0, f'Sem permissao para gravar na planilha: {path}'
    except Exception as exc:
        logger.exception('Falha ao exportar atendimentos de %s para planilha', attendant.username)
        return False, 0, f'Falha ao preencher planilha: {exc}'

    _mark_export_rows(rows, str(path))
    return True, len(rows), f'{len(rows)} chamado(s) novo(s) exportado(s) com sucesso.'


def export_attendant_logs_to_uploaded_workbook(*, attendant, uploaded_file) -> tuple[bool, int, str, bytes | None, str]:
    blocker = _spreadsheet_export_blocker(attendant)
    if blocker:
        ok, count, detail = blocker
        return ok, count, detail, None, ''

    original_name = Path(getattr(uploaded_file, 'name', '') or 'chamados.xlsx').name
    if not original_name.lower().endswith('.xlsx'):
        return False, 0, 'Selecione uma planilha no formato .xlsx.', None, ''

    try:
        wb = load_workbook(uploaded_file)
        rows = _build_ticket_export_rows(attendant, _existing_ticket_ids_in_workbook(wb))
        if not rows:
            return True, 0, 'Nenhum chamado novo para exportar. Todos os chamados do atendente ja constam na planilha.', None, ''
        _write_ticket_rows_to_workbook(wb, rows)
        output = BytesIO()
        wb.save(output)
    except Exception as exc:
        logger.exception('Falha ao preencher planilha enviada para %s', attendant.username)
        return False, 0, f'Falha ao preencher planilha enviada: {exc}', None, ''

    download_name = f'preenchida-{original_name}'
    _mark_export_rows(rows, f'upload:{original_name}')
    return (
        True,
        len(rows),
        f'{len(rows)} chamado(s) novo(s) exportado(s) com sucesso.',
        output.getvalue(),
        download_name,
    )
