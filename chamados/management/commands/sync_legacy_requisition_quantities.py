from __future__ import annotations

import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from chamados.models import RequisitionBudget


QUOTE_ID_RE = re.compile(r'\[ERP-TI-QUOTE-ID:(\d+)\]')


class Command(BaseCommand):
    help = (
        'Sincroniza a quantidade dos orcamentos importados de requisicoes '
        'usando core_requisitionquote.quantity do banco ERP-TI legado.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            default='erp-ti-db.sqlite3',
            help='Caminho do banco SQLite legado. Default: erp-ti-db.sqlite3',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Mostra o que seria alterado sem gravar no banco.',
        )

    def handle(self, *args, **options):
        source_path = Path(options['source']).resolve()
        if not source_path.exists():
            raise CommandError(f'Banco legado nao encontrado: {source_path}')

        dry_run = bool(options['dry_run'])
        legacy_quantities = self._load_legacy_quantities(source_path)

        processed = 0
        updated = 0
        unchanged = 0
        missing_quote = 0
        invalid_quantity = 0

        budgets = RequisitionBudget.objects.filter(notes__contains='[ERP-TI-QUOTE-ID:')
        for budget in budgets.iterator():
            processed += 1
            quote_id = self._extract_quote_id(budget.notes)
            if quote_id is None or quote_id not in legacy_quantities:
                missing_quote += 1
                continue

            quantity = self._parse_positive_int(legacy_quantities[quote_id])
            if quantity is None:
                invalid_quantity += 1
                continue

            if budget.quantity == quantity:
                unchanged += 1
                continue

            self.stdout.write(
                f'Orcamento #{budget.id} / quote legado {quote_id}: '
                f'{budget.quantity} -> {quantity}'
            )
            updated += 1
            if not dry_run:
                budget.quantity = quantity
                budget.save(update_fields=['quantity', 'updated_at'])

        mode_label = 'simulacao' if dry_run else 'execucao'
        self.stdout.write(self.style.SUCCESS(f'Sincronizacao de quantidades concluida ({mode_label}).'))
        self.stdout.write(f'  orcamentos importados processados: {processed}')
        self.stdout.write(f'  quantidades atualizadas: {updated}')
        self.stdout.write(f'  quantidades ja corretas: {unchanged}')
        self.stdout.write(f'  quotes legado nao encontrados: {missing_quote}')
        self.stdout.write(f'  quantidades invalidas no legado: {invalid_quantity}')

    def _load_legacy_quantities(self, source_path: Path) -> dict[int, Any]:
        con = sqlite3.connect(str(source_path))
        con.row_factory = sqlite3.Row
        try:
            cur = con.cursor()
            table_exists = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_requisitionquote'"
            ).fetchone()
            if table_exists is None:
                raise CommandError('Tabela core_requisitionquote nao encontrada no banco legado.')

            rows = cur.execute('SELECT id, quantity FROM core_requisitionquote').fetchall()
            return {int(row['id']): row['quantity'] for row in rows}
        finally:
            con.close()

    def _extract_quote_id(self, notes: str) -> int | None:
        match = QUOTE_ID_RE.search(notes or '')
        if not match:
            return None
        return int(match.group(1))

    def _parse_positive_int(self, raw: Any) -> int | None:
        text = (str(raw).strip() if raw is not None else '')
        if not text:
            return None
        text = text.replace(',', '.')
        try:
            value = int(Decimal(text))
        except (InvalidOperation, ValueError):
            return None
        return value if value > 0 else None
