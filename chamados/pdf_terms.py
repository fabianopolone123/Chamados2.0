from __future__ import annotations

import re
import struct
import textwrap
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from django.utils import timezone


PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 34
TERM_BOTTOM_LIMIT = 96
SIDERTEC_GREEN = (19, 120, 67)
BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_PATH = BASE_DIR / 'Logo Verde.png'


def _pdf_escape(value: str) -> bytes:
    raw = str(value or '').encode('cp1252', errors='replace')
    raw = raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
    raw = raw.replace(b'\r', b' ').replace(b'\n', b' ')
    return b'(' + raw + b')'


def _safe_filename(value: str, fallback: str = 'colaborador') -> str:
    cleaned = re.sub(r'[^A-Za-z0-9_-]+', '_', value or fallback).strip('_')
    return cleaned or fallback


def _format_date_br(value, fallback: str = '-') -> str:
    if not value:
        return fallback
    return timezone.localtime(value).strftime('%d/%m/%Y') if hasattr(value, 'hour') else value.strftime('%d/%m/%Y')


def _user_display(user) -> str:
    if not user:
        return 'Sidertec / TI'
    full_name = user.get_full_name().strip() if hasattr(user, 'get_full_name') else ''
    return full_name or getattr(user, 'username', '') or 'Sidertec / TI'


def _field_file_path(field_file) -> Path | None:
    if not field_file:
        return None
    try:
        path = Path(field_file.path)
    except (NotImplementedError, ValueError):
        return None
    return path if path.exists() else None


@dataclass
class PdfCanvas:
    commands: list[bytes] = field(default_factory=list)
    pages: list[list[bytes]] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)

    def rect(self, x, y, w, h, stroke=(209, 213, 219), fill=None):
        if fill:
            self.commands.append(f'{fill[0] / 255:.3f} {fill[1] / 255:.3f} {fill[2] / 255:.3f} rg'.encode())
            self.commands.append(f'{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f'.encode())
        if stroke:
            self.commands.append(f'{stroke[0] / 255:.3f} {stroke[1] / 255:.3f} {stroke[2] / 255:.3f} RG'.encode())
            self.commands.append(f'{x:.2f} {y:.2f} {w:.2f} {h:.2f} re S'.encode())

    def line(self, x1, y1, x2, y2, color=(17, 24, 39), width=0.8):
        self.commands.append(f'{color[0] / 255:.3f} {color[1] / 255:.3f} {color[2] / 255:.3f} RG {width:.2f} w'.encode())
        self.commands.append(f'{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S'.encode())

    def text(self, x, y, value, size=10, font='F1', color=(17, 24, 39)):
        self.commands.append(f'{color[0] / 255:.3f} {color[1] / 255:.3f} {color[2] / 255:.3f} rg'.encode())
        self.commands.append(
            b'BT /' + font.encode() + f' {size:.2f} Tf {x:.2f} {y:.2f} Td '.encode() + _pdf_escape(value) + b' Tj ET'
        )

    def wrapped_text(self, x, y, value, max_chars=90, size=9.5, font='F1', line_height=13, color=(17, 24, 39)):
        lines = []
        for paragraph in str(value or '').splitlines() or ['']:
            wrapped = textwrap.wrap(paragraph, width=max_chars) or ['']
            lines.extend(wrapped)
        for line in lines:
            self.text(x, y, line, size=size, font=font, color=color)
            y -= line_height
        return y

    def image(self, path: Path, x, y, w, h):
        image_data = _load_pdf_image(path)
        name = f'Im{len(self.images) + 1}'
        self.images.append({'name': name, **image_data})
        self.commands.append(f'q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q'.encode())
        return image_data

    def new_page(self):
        self.pages.append(self.commands)
        self.commands = []

    def build(self) -> bytes:
        page_streams = [*self.pages, self.commands]
        page_count = len(page_streams)
        xobject_entries = b''
        image_objects = []
        image_start_id = 3 + (page_count * 2)
        for index, image in enumerate(self.images, start=image_start_id):
            encoded_image = image['encoded']
            xobject_entries += f'/{image["name"]} {index} 0 R '.encode()
            image_objects.append(
                (
                    f'<< /Type /XObject /Subtype /Image /Width {image["width"]} /Height {image["height"]} '
                    f'/ColorSpace /{image["color_space"]} /BitsPerComponent 8 /Filter /{image["filter"]} '
                    f'/Length {len(encoded_image)} >>'
                ).encode()
                + b'\nstream\n'
                + encoded_image
                + b'\nendstream'
            )
        xobject_resource = b''
        if xobject_entries:
            xobject_resource = b' /XObject << ' + xobject_entries + b'>>'
        page_ids = list(range(3, 3 + page_count))
        content_ids = list(range(3 + page_count, 3 + (page_count * 2)))
        font_resource = (
            b'/Resources << /Font << '
            b'/F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> '
            b'/F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >> '
            b'>>'
            + xobject_resource
            + b' >>'
        )
        page_objects = [
            (
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] '
                + font_resource
                + f' /Contents {content_id} 0 R >>'.encode()
            )
            for content_id in content_ids
        ]
        content_objects = []
        for page_commands in page_streams:
            stream = b'\n'.join(page_commands)
            content_objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
        objects = [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [' + b' '.join(f'{page_id} 0 R'.encode() for page_id in page_ids) + b'] /Count ' + str(page_count).encode() + b' >>',
            *page_objects,
            *content_objects,
            *image_objects,
        ]
        pdf = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
        xref_offset = len(pdf)
        pdf += f'xref\n0 {len(objects) + 1}\n'.encode()
        pdf += b'0000000000 65535 f \n'
        for offset in offsets[1:]:
            pdf += f'{offset:010d} 00000 n \n'.encode()
        pdf += (
            b'trailer\n'
            + f'<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n'.encode()
        )
        return pdf


def _load_pdf_image(path: Path) -> dict:
    data = path.read_bytes()
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        png_data = _load_png_rgb(path)
        return {
            **png_data,
            'encoded': zlib.compress(png_data['rgb']),
            'filter': 'FlateDecode',
            'color_space': 'DeviceRGB',
        }
    if data.startswith(b'\xff\xd8'):
        width, height, components = _jpeg_dimensions(data)
        color_space = 'DeviceGray' if components == 1 else 'DeviceCMYK' if components == 4 else 'DeviceRGB'
        return {
            'width': width,
            'height': height,
            'encoded': data,
            'filter': 'DCTDecode',
            'color_space': color_space,
        }
    raise ValueError(f'Formato de imagem nao suportado no PDF: {path}')


def _load_png_rgb(path: Path) -> dict:
    data = path.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError(f'Arquivo de logo invalido: {path}')

    pos = 8
    width = height = bit_depth = color_type = None
    compressed_parts = []
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        chunk_type = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += length + 12
        if chunk_type == b'IHDR':
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack('>IIBBBBB', chunk)
            if bit_depth != 8 or color_type not in (2, 6) or compression or filter_method or interlace:
                raise ValueError('Logo PNG precisa ser RGB/RGBA 8 bits sem interlace.')
        elif chunk_type == b'IDAT':
            compressed_parts.append(chunk)
        elif chunk_type == b'IEND':
            break

    channels = 4 if color_type == 6 else 3
    stride = width * channels
    raw = zlib.decompress(b''.join(compressed_parts))
    rows = []
    cursor = 0
    previous = [0] * stride
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scanline = list(raw[cursor:cursor + stride])
        cursor += stride
        reconstructed = _png_unfilter(scanline, previous, channels, filter_type)
        rows.append(reconstructed)
        previous = reconstructed

    rgb = bytearray()
    for row in rows:
        for offset in range(0, len(row), channels):
            r, g, b = row[offset], row[offset + 1], row[offset + 2]
            if channels == 4:
                alpha = row[offset + 3] / 255
                r = round(r * alpha + 255 * (1 - alpha))
                g = round(g * alpha + 255 * (1 - alpha))
                b = round(b * alpha + 255 * (1 - alpha))
            rgb.extend((r, g, b))

    return {'width': width, 'height': height, 'rgb': bytes(rgb)}


def _jpeg_dimensions(data: bytes) -> tuple[int, int, int]:
    pos = 2
    start_of_frame_markers = {0xC0, 0xC1, 0xC2}
    while pos < len(data):
        while pos < len(data) and data[pos] != 0xFF:
            pos += 1
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if pos + 2 > len(data):
            break
        segment_length = struct.unpack('>H', data[pos:pos + 2])[0]
        segment_start = pos + 2
        segment_end = pos + segment_length
        if marker in start_of_frame_markers:
            precision = data[segment_start]
            if precision != 8:
                raise ValueError('Imagem JPG precisa ter 8 bits por componente.')
            height = struct.unpack('>H', data[segment_start + 1:segment_start + 3])[0]
            width = struct.unpack('>H', data[segment_start + 3:segment_start + 5])[0]
            components = data[segment_start + 5]
            return width, height, components
        pos = segment_end
    raise ValueError('Nao foi possivel ler as dimensoes da imagem JPG.')


def _png_unfilter(scanline: list[int], previous: list[int], channels: int, filter_type: int) -> list[int]:
    result = []
    for index, value in enumerate(scanline):
        left = result[index - channels] if index >= channels else 0
        up = previous[index]
        upper_left = previous[index - channels] if index >= channels else 0
        if filter_type == 0:
            restored = value
        elif filter_type == 1:
            restored = value + left
        elif filter_type == 2:
            restored = value + up
        elif filter_type == 3:
            restored = value + ((left + up) // 2)
        elif filter_type == 4:
            restored = value + _png_paeth(left, up, upper_left)
        else:
            raise ValueError(f'Filtro PNG nao suportado: {filter_type}')
        result.append(restored & 0xFF)
    return result


def _png_paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def equipment_loan_term_filename(loan, kind='emprestimo') -> str:
    collaborator = _safe_filename(loan.collaborator_name)
    return f'termo_{kind}_{collaborator}_{loan.id}.pdf'


def _loan_equipment_items(loan):
    try:
        items = list(loan.items.all())
    except Exception:
        items = []
    return items or [loan]


def _equipment_summary_rows(loan):
    rows = []
    for index, item in enumerate(_loan_equipment_items(loan), start=1):
        label = getattr(item, 'equipment_label', '') or '-'
        serial = getattr(item, 'equipment_serial', '') or '-'
        patrimony = getattr(item, 'patrimony_tag', '') or '-'
        accessories = getattr(item, 'accessories', '') or 'Nenhum acessorio informado.'
        rows.append(
            (
                f'Equipamento {index}',
                f'{label}\nSerie: {serial} | Patrimonio / etiqueta: {patrimony}\nAcessorios: {accessories}',
            )
        )
    return rows


def build_equipment_loan_pdf(loan, generated_by=None) -> bytes:
    pdf = PdfCanvas()
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    y = PAGE_HEIGHT - MARGIN
    generated_at = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')

    def close_page(next_y: float | None = None) -> float:
        _draw_rubric_footer(pdf, generated_at)
        pdf.new_page()
        return next_y if next_y is not None else PAGE_HEIGHT - MARGIN

    def ensure_space(current_y: float, needed_height: float) -> float:
        if current_y - needed_height < TERM_BOTTOM_LIMIT:
            return close_page()
        return current_y

    _draw_header(
        pdf,
        y,
        fill=(15, 23, 42),
        detail_color=(226, 232, 240),
        title_lines=('TERMO DE RESPONSABILIDADE', 'GUARDA E USO DE EQUIPAMENTO'),
    )
    y -= 112

    expected_return = _format_date_br(loan.expected_return_date, 'Indeterminada')
    intro = (
        'Pelo presente termo, a Sidertec registra o empréstimo em comodato do equipamento abaixo '
        'ao colaborador identificado neste documento, que declara receber o bem em boas condições '
        'de uso e se compromete a zelar, conservar e devolver quando solicitado.'
    )
    y = pdf.wrapped_text(x, y, intro, max_chars=104, size=9.5, line_height=13) - 10

    y = _draw_section_paginated(pdf, y, 'Dados do colaborador', [
        ('Nome', loan.collaborator_name),
        ('Empresa', loan.collaborator_company),
        ('Documento / CPF', loan.collaborator_document or '-'),
        ('E-mail', loan.collaborator_email or '-'),
        ('Telefone', loan.collaborator_phone or '-'),
    ], ensure_space)
    y = _draw_section_paginated(pdf, y, 'Equipamentos emprestados', _equipment_summary_rows(loan), ensure_space)
    y = _draw_section_paginated(pdf, y, 'Condições do empréstimo', [
        ('Data do empréstimo', _format_date_br(loan.loan_date)),
        ('Data prevista para devolução', expected_return),
        ('Observações internas', loan.notes or '-'),
    ], ensure_space)

    y = ensure_space(y, 126)
    pdf.text(x, y - 4, 'Responsabilidades do colaborador', size=10.2, font='F2', color=(15, 23, 42))
    y -= 20
    responsibility_terms = (
        '1 - Se os equipamentos forem danificados ou inutilizados por mau uso, negligência ou extravio, '
        'deverá ressarcir a SIDERTEC o valor de mercado dos mesmos e será de responsabilidade do '
        'colaborador a aquisição imediata dos novos equipamentos;\n'
        '2 - Em caso de dano, inutilização ou extravio dos equipamentos, o colaborador deverá comunicar '
        'imediatamente à SIDERTEC - TI / Marcelo (16) 99111-2251.\n'
        '3 - Os equipamentos emprestados estarão sujeitos a acesso, monitoramento e inspeção pela '
        'SIDERTEC quando houver necessidade.'
    )
    y = _draw_wrapped_text_paginated(
        pdf,
        x,
        y,
        responsibility_terms,
        ensure_space,
        max_chars=104,
        size=8.5,
        line_height=11.2,
    ) - 24
    y = ensure_space(y, 86)
    attendant_signature_path = _field_file_path(loan.attendant_signature)
    sig_profile = getattr(loan, 'attendant_signature_profile', None)
    sig_x_offset = (getattr(sig_profile, 'signature_x_offset', 0) or 0) + (getattr(loan, 'attendant_signature_x_offset', 0) or 0)
    sig_y_offset = (getattr(sig_profile, 'signature_y_offset', 0) or 0) + (getattr(loan, 'attendant_signature_y_offset', 0) or 0)
    _draw_signature(pdf, x + 22, y, 205, loan.collaborator_name, 'Assinatura do colaborador')
    _draw_signature(
        pdf,
        right - 250,
        y,
        205,
        _user_display(generated_by),
        'Responsável TI pelo empréstimo',
        image_path=attendant_signature_path,
        image_x_offset=sig_x_offset,
        image_y_offset=sig_y_offset,
    )
    _draw_rubric_footer(pdf, generated_at)
    return pdf.build()


def build_equipment_return_pdf(loan, generated_by=None) -> bytes:
    pdf = PdfCanvas()
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    y = PAGE_HEIGHT - MARGIN

    _draw_header(
        pdf,
        y,
        fill=(20, 83, 45),
        detail_color=(220, 252, 231),
        title_lines=('TERMO DE DEVOLUÇÃO', 'DE EQUIPAMENTO EMPRESTADO'),
    )
    y -= 112

    returned_by = loan.returned_by or generated_by
    returned_at = _format_date_br(loan.returned_at, '____/____/________')
    status_mark = 'X' if loan.returned else ' '
    pdf.rect(x, y - 48, right - x, 48, stroke=(187, 247, 208), fill=(240, 253, 244))
    pdf.text(x + 16, y - 20, f'[{status_mark}] Equipamento marcado como DEVOLVIDO', size=11, font='F2', color=(20, 83, 45))
    pdf.text(x + 16, y - 38, f'Data da devolução: {returned_at}', size=10, font='F1', color=(22, 101, 52))
    y -= 70

    y = _draw_section(pdf, y, 'Dados do colaborador', [
        ('Nome', loan.collaborator_name),
        ('Empresa', loan.collaborator_company),
        ('Documento / CPF', loan.collaborator_document or '-'),
        ('E-mail', loan.collaborator_email or '-'),
        ('Telefone', loan.collaborator_phone or '-'),
    ])
    y = _draw_section(pdf, y, 'Equipamentos devolvidos', _equipment_summary_rows(loan))
    y = _draw_section(pdf, y, 'Técnico responsável pela conferência', [
        ('Técnico da TI', _user_display(returned_by)),
        ('Departamento', 'TI'),
        ('Data da conferência', returned_at),
    ])

    pdf.text(x, y - 2, 'Condição na devolução', size=11, font='F2')
    y -= 22
    for option in (
        'Equipamento devolvido em bom estado de conservação',
        'Acessórios devolvidos junto ao equipamento',
        'Necessita avaliação técnica / possui observações',
    ):
        pdf.text(x + 10, y, '[  ] ' + option, size=9.5)
        y -= 17
    y -= 5
    pdf.text(x, y, 'Observações:', size=10, font='F2')
    y -= 18
    for _ in range(3):
        pdf.line(x, y, right, y, color=(156, 163, 175), width=0.6)
        y -= 20

    declaration = (
        'As partes declaram que a devolução foi registrada para controle patrimonial e conferência '
        'do Departamento de TI.'
    )
    y = pdf.wrapped_text(x, y - 4, declaration, max_chars=104, size=9.3, line_height=12.5) - 28
    attendant_signature_path = _field_file_path(loan.attendant_signature)
    sig_profile = getattr(loan, 'attendant_signature_profile', None)
    sig_x_offset = (getattr(sig_profile, 'signature_x_offset', 0) or 0) + (getattr(loan, 'attendant_signature_x_offset', 0) or 0)
    sig_y_offset = (getattr(sig_profile, 'signature_y_offset', 0) or 0) + (getattr(loan, 'attendant_signature_y_offset', 0) or 0)
    _draw_signature(pdf, x + 22, y, 205, loan.collaborator_name, 'Assinatura do colaborador')
    _draw_signature(
        pdf,
        right - 250,
        y,
        205,
        _user_display(returned_by),
        'Assinatura do técnico da TI',
        image_path=attendant_signature_path,
        image_x_offset=sig_x_offset,
        image_y_offset=sig_y_offset,
    )
    generated_at = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    pdf.text(x, 31, f'Termo gerado pelo sistema em {generated_at}.', size=8, color=(75, 85, 99))
    return pdf.build()


def _wrapped_lines(value: str, max_chars: int) -> list[str]:
    lines = []
    for paragraph in str(value or '').splitlines() or ['']:
        lines.extend(textwrap.wrap(paragraph, width=max_chars) or [''])
    return lines


def _draw_wrapped_text_paginated(
    pdf: PdfCanvas,
    x: float,
    y: float,
    value: str,
    ensure_space,
    max_chars=90,
    size=9.5,
    font='F1',
    line_height=13,
    color=(17, 24, 39),
) -> float:
    for line in _wrapped_lines(value, max_chars):
        y = ensure_space(y, line_height + 4)
        pdf.text(x, y, line, size=size, font=font, color=color)
        y -= line_height
    return y


def _draw_section_paginated(pdf: PdfCanvas, y: float, title: str, rows: list[tuple[str, str]], ensure_space) -> float:
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    y = ensure_space(y, 34)
    pdf.text(x, y, title, size=11, font='F2', color=(15, 23, 42))
    y -= 16
    for label, value in rows:
        value = str(value or '-')
        line_count = len(_wrapped_lines(value, 70))
        row_height = max(28, 18 + (line_count * 11))
        y = ensure_space(y, row_height + 8)
        pdf.rect(x, y - row_height + 5, right - x, row_height, stroke=(203, 213, 225), fill=(248, 250, 252))
        pdf.rect(x, y - row_height + 5, 148, row_height, stroke=(203, 213, 225), fill=(241, 245, 249))
        pdf.text(x + 8, y - 12, label, size=8.8, font='F2', color=(51, 65, 85))
        pdf.wrapped_text(x + 158, y - 12, value, max_chars=70, size=8.8, line_height=11)
        y -= row_height
    return y - 12


def _draw_rubric_footer(pdf: PdfCanvas, generated_at: str):
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    pdf.text(x, 31, f'Termo gerado pelo sistema em {generated_at}.', size=8, color=(75, 85, 99))
    rubrica_width = 116
    pdf.line(right - rubrica_width, 43, right, 43, color=(17, 24, 39), width=0.7)
    pdf.text(right - rubrica_width, 30, 'Rubrica', size=8, color=(75, 85, 99))


def _draw_section(pdf: PdfCanvas, y: float, title: str, rows: list[tuple[str, str]]) -> float:
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    pdf.text(x, y, title, size=11, font='F2', color=(15, 23, 42))
    y -= 16
    for label, value in rows:
        value = str(value or '-')
        row_height = 28 if len(value) < 95 and '\n' not in value else 42
        pdf.rect(x, y - row_height + 5, right - x, row_height, stroke=(203, 213, 225), fill=(248, 250, 252))
        pdf.rect(x, y - row_height + 5, 148, row_height, stroke=(203, 213, 225), fill=(241, 245, 249))
        pdf.text(x + 8, y - 12, label, size=8.8, font='F2', color=(51, 65, 85))
        pdf.wrapped_text(x + 158, y - 12, value, max_chars=70, size=8.8, line_height=11)
        y -= row_height
    return y - 12


def _draw_header(pdf: PdfCanvas, y: float, fill: tuple[int, int, int], detail_color: tuple[int, int, int], title_lines: tuple[str, str]):
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    pdf.rect(x, y - 88, right - x, 88, stroke=None, fill=fill)
    _draw_sidertec_logo(pdf, x + 14, y - 17)
    pdf.text(right - 126, y - 24, 'Departamento de TI', size=9.5, font='F1', color=detail_color)
    pdf.text(x + 200, y - 42, title_lines[0], size=12.8, font='F2', color=(255, 255, 255))
    pdf.text(x + 200, y - 61, title_lines[1], size=12.8, font='F2', color=(255, 255, 255))


def _draw_sidertec_logo(pdf: PdfCanvas, x: float, y: float):
    if LOGO_PATH.exists():
        pdf.rect(x, y - 54, 166, 54, stroke=(226, 232, 240), fill=(255, 255, 255))
        pdf.image(LOGO_PATH, x + 8, y - 47, 150, 45)
        return

    # Fallback simplificado caso o arquivo do logo nao esteja no servidor.
    pdf.rect(x, y - 54, 166, 54, stroke=(226, 232, 240), fill=(255, 255, 255))
    pdf.rect(x + 10, y - 43, 30, 30, stroke=None, fill=SIDERTEC_GREEN)
    pdf.text(x + 16, y - 36, 'S', size=24, font='F2', color=(255, 255, 255))
    pdf.text(x + 48, y - 25, 'SIDERTEC', size=20, font='F2', color=SIDERTEC_GREEN)
    pdf.text(x + 50, y - 40, 'TECNOLOGIA EM ESTRUTURAS METÁLICAS', size=5.8, font='F2', color=SIDERTEC_GREEN)
    pdf.line(x + 50, y - 31, x + 151, y - 31, color=SIDERTEC_GREEN, width=0.6)


def _draw_signature(
    pdf: PdfCanvas,
    x: float,
    y: float,
    width: float,
    name: str,
    caption: str,
    image_path: Path | None = None,
    image_x_offset: int = 0,
    image_y_offset: int = 0,
):
    if image_path:
        try:
            image_info = _load_pdf_image(image_path)
            box_width = width - 56
            box_height = 38
            scale = min(box_width / image_info['width'], box_height / image_info['height'])
            draw_width = image_info['width'] * scale
            draw_height = image_info['height'] * scale
            draw_x = x + 28 + ((box_width - draw_width) / 2) + image_x_offset
            draw_y = y + 6 + ((box_height - draw_height) / 2) + image_y_offset
            name_id = f'Im{len(pdf.images) + 1}'
            pdf.images.append({'name': name_id, **image_info})
            pdf.commands.append(
                f'q {draw_width:.2f} 0 0 {draw_height:.2f} {draw_x:.2f} {draw_y:.2f} cm /{name_id} Do Q'.encode()
            )
        except (OSError, ValueError, IndexError, zlib.error):
            pass
    pdf.line(x, y, x + width, y, color=(17, 24, 39), width=0.8)
    pdf.text(x, y - 14, name, size=9, font='F2')
    pdf.text(x, y - 28, caption, size=8.2, color=(75, 85, 99))
