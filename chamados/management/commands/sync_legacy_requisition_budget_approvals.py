from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chamados.models import Requisition, RequisitionBudget, RequisitionUpdate


QUOTE_ID_RE = re.compile(r'\[ERP-TI-QUOTE-ID:(\d+)\]')


class Command(BaseCommand):
    help = (
        'Sincroniza os orcamentos aprovados das requisicoes importadas '
        'usando core_requisitionquote.is_selected do banco ERP-TI legado.'
    )

    status_rank = {
        Requisition.Status.NAO_APROVADA: -1,
        Requisition.Status.PENDENTE_APROVACAO: 0,
        Requisition.Status.APROVADA: 1,
        Requisition.Status.PARCIALMENTE_ENTREGUE: 2,
        Requisition.Status.ENTREGUE: 3,
    }

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
        selected_quote_ids = self._load_selected_quote_ids(source_path)

        processed = 0
        approved = 0
        already_approved = 0
        requisitions_promoted = 0
        missing_quote = 0
        not_selected = 0

        budgets = RequisitionBudget.objects.select_related('requisition', 'requisition__requested_by').filter(
            notes__contains='[ERP-TI-QUOTE-ID:'
        )
        for budget in budgets.iterator():
            processed += 1
            quote_id = self._extract_quote_id(budget.notes)
            if quote_id is None:
                missing_quote += 1
                continue
            if quote_id not in selected_quote_ids:
                not_selected += 1
                continue
            if budget.approval_status == RequisitionBudget.ApprovalStatus.APROVADO:
                already_approved += 1
                continue

            self.stdout.write(f'Orcamento #{budget.id} / quote legado {quote_id}: pendente -> aprovado')
            approved += 1

            requisition = budget.requisition
            should_promote_requisition = (
                self.status_rank.get(requisition.status, 0)
                < self.status_rank[Requisition.Status.APROVADA]
            )
            if should_promote_requisition:
                requisitions_promoted += 1

            if dry_run:
                continue

            budget.approval_status = RequisitionBudget.ApprovalStatus.APROVADO
            budget.save(update_fields=['approval_status', 'updated_at'])

            if should_promote_requisition:
                previous_status = requisition.status
                requisition.status = Requisition.Status.APROVADA
                requisition.save(update_fields=['status', 'updated_at'])
                RequisitionUpdate.objects.create(
                    requisition=requisition,
                    author=requisition.requested_by,
                    message=(
                        f'Orcamento aprovado sincronizado do legado ERP-TI '
                        f'(quote_id={quote_id}): "{previous_status}" -> "{requisition.status}".'
                    ),
                    status_to=requisition.status,
                )

        mode_label = 'simulacao' if dry_run else 'execucao'
        self.stdout.write(self.style.SUCCESS(f'Sincronizacao de orcamentos aprovados concluida ({mode_label}).'))
        self.stdout.write(f'  orcamentos importados processados: {processed}')
        self.stdout.write(f'  orcamentos marcados como aprovados: {approved}')
        self.stdout.write(f'  orcamentos ja aprovados: {already_approved}')
        self.stdout.write(f'  requisicoes promovidas para aprovada: {requisitions_promoted}')
        self.stdout.write(f'  quotes legado nao encontrados no marcador local: {missing_quote}')
        self.stdout.write(f'  quotes legado nao selecionados: {not_selected}')

    def _load_selected_quote_ids(self, source_path: Path) -> set[int]:
        con = sqlite3.connect(str(source_path))
        con.row_factory = sqlite3.Row
        try:
            cur = con.cursor()
            table_exists = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_requisitionquote'"
            ).fetchone()
            if table_exists is None:
                raise CommandError('Tabela core_requisitionquote nao encontrada no banco legado.')

            rows = cur.execute('SELECT id FROM core_requisitionquote WHERE is_selected = 1').fetchall()
            return {int(row['id']) for row in rows}
        finally:
            con.close()

    def _extract_quote_id(self, notes: str) -> int | None:
        match = QUOTE_ID_RE.search(notes or '')
        if not match:
            return None
        return int(match.group(1))
