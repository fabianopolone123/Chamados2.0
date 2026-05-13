from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field

from django.utils import timezone


PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 34
SIDERTEC_GREEN = (19, 120, 67)


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


@dataclass
class PdfCanvas:
    commands: list[bytes] = field(default_factory=list)

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

    def build(self) -> bytes:
        stream = b'\n'.join(self.commands)
        objects = [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            (
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] '
                b'/Resources << /Font << '
                b'/F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >> '
                b'/F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >> '
                b'>> >> /Contents 4 0 R >>'
            ),
            b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
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


def equipment_loan_term_filename(loan, kind='emprestimo') -> str:
    collaborator = _safe_filename(loan.collaborator_name)
    return f'termo_{kind}_{collaborator}_{loan.id}.pdf'


def build_equipment_loan_pdf(loan, generated_by=None) -> bytes:
    pdf = PdfCanvas()
    x = MARGIN
    right = PAGE_WIDTH - MARGIN
    y = PAGE_HEIGHT - MARGIN

    _draw_header(
        pdf,
        y,
        fill=(15, 23, 42),
        detail_color=(226, 232, 240),
        title_lines=('TERMO DE RESPONSABILIDADE', 'GUARDA E USO DE EQUIPAMENTO'),
    )
    y -= 112

    expected_return = _format_date_br(loan.expected_return_date, 'Indeterminada')
    generated_at = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    intro = (
        'Pelo presente termo, a Sidertec registra o empréstimo em comodato do equipamento abaixo '
        'ao colaborador identificado neste documento, que declara receber o bem em boas condições '
        'de uso e se compromete a zelar, conservar e devolver quando solicitado.'
    )
    y = pdf.wrapped_text(x, y, intro, max_chars=104, size=9.5, line_height=13) - 10

    y = _draw_section(pdf, y, 'Dados do colaborador', [
        ('Nome', loan.collaborator_name),
        ('Empresa', loan.collaborator_company),
        ('Documento / CPF', loan.collaborator_document or '-'),
        ('E-mail', loan.collaborator_email or '-'),
        ('Telefone', loan.collaborator_phone or '-'),
    ])
    y = _draw_section(pdf, y, 'Dados do equipamento', [
        ('Equipamento', loan.equipment_label),
        ('Número de série', loan.equipment_serial or '-'),
        ('Patrimônio / etiqueta', loan.patrimony_tag or '-'),
        ('Acessórios', loan.accessories or 'Nenhum acessório informado.'),
    ])
    y = _draw_section(pdf, y, 'Condições do empréstimo', [
        ('Data do empréstimo', _format_date_br(loan.loan_date)),
        ('Data prevista para devolução', expected_return),
        ('Observações internas', loan.notes or '-'),
    ])

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
    y = pdf.wrapped_text(x, y, responsibility_terms, max_chars=104, size=8.5, line_height=11.2) - 24
    _draw_signature(pdf, x + 22, y, 205, loan.collaborator_name, 'Assinatura do colaborador')
    _draw_signature(pdf, right - 250, y, 205, _user_display(generated_by), 'Responsável TI pelo empréstimo')
    pdf.text(x, 31, f'Termo gerado pelo sistema em {generated_at}.', size=8, color=(75, 85, 99))
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
    y = _draw_section(pdf, y, 'Equipamento devolvido', [
        ('Equipamento', loan.equipment_label),
        ('Número de série', loan.equipment_serial or '-'),
        ('Patrimônio / etiqueta', loan.patrimony_tag or '-'),
        ('Acessórios', loan.accessories or 'Nenhum acessório informado.'),
    ])
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
    _draw_signature(pdf, x + 22, y, 205, loan.collaborator_name, 'Assinatura do colaborador')
    _draw_signature(pdf, right - 250, y, 205, _user_display(returned_by), 'Assinatura do técnico da TI')
    generated_at = timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')
    pdf.text(x, 31, f'Termo gerado pelo sistema em {generated_at}.', size=8, color=(75, 85, 99))
    return pdf.build()


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
    # Logo simplificado em vetor para o PDF nao depender de arquivo externo no VPS.
    pdf.rect(x, y - 54, 166, 54, stroke=(226, 232, 240), fill=(255, 255, 255))
    pdf.rect(x + 10, y - 43, 30, 30, stroke=None, fill=SIDERTEC_GREEN)
    pdf.text(x + 16, y - 36, 'S', size=24, font='F2', color=(255, 255, 255))
    pdf.text(x + 48, y - 25, 'SIDERTEC', size=20, font='F2', color=SIDERTEC_GREEN)
    pdf.text(x + 50, y - 40, 'TECNOLOGIA EM ESTRUTURAS METÁLICAS', size=5.8, font='F2', color=SIDERTEC_GREEN)
    pdf.line(x + 50, y - 31, x + 151, y - 31, color=SIDERTEC_GREEN, width=0.6)


def _draw_signature(pdf: PdfCanvas, x: float, y: float, width: float, name: str, caption: str):
    pdf.line(x, y, x + width, y, color=(17, 24, 39), width=0.8)
    pdf.text(x, y - 14, name, size=9, font='F2')
    pdf.text(x, y - 28, caption, size=8.2, color=(75, 85, 99))
