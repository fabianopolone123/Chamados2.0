import sqlite3
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from chamados.models import Requisition, RequisitionUpdate


class Command(BaseCommand):
    help = (
        "Sincroniza o status das requisicoes importadas do ERP-TI legado, "
        "promovendo no banco atual as que ja estavam aprovadas/parcialmente recebidas/recebidas."
    )

    approved_status_map = {
        'approved': Requisition.Status.APROVADA,
        'partially_received': Requisition.Status.PARCIALMENTE_ENTREGUE,
        'received': Requisition.Status.ENTREGUE,
    }

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
            help='Somente mostra o que seria alterado, sem gravar no banco atual.',
        )

    def handle(self, *args, **options):
        source_path = Path(options['source']).expanduser()
        dry_run = bool(options['dry_run'])

        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        if not source_path.exists():
            raise CommandError(f'Banco legado nao encontrado: {source_path}')

        connection = sqlite3.connect(source_path)
        connection.row_factory = sqlite3.Row

        try:
            cursor = connection.cursor()
            table_exists = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_requisition'"
            ).fetchone()
            if not table_exists:
                raise CommandError('Tabela core_requisition nao encontrada no banco legado.')

            rows = cursor.execute(
                """
                SELECT id, status, approved_at, partially_received_at, received_at
                FROM core_requisition
                ORDER BY id
                """
            ).fetchall()
        finally:
            connection.close()

        processed = 0
        changed = 0
        dates_synced = 0
        not_found = 0
        skipped = 0

        for row in rows:
            legacy_status = (row['status'] or '').strip().lower()
            target_status = self.approved_status_map.get(legacy_status)
            if not target_status:
                skipped += 1
                continue

            legacy_id = int(row['id'])
            requisition = (
                Requisition.objects.filter(code=f'LEG-REQ-{legacy_id:05d}').first()
                or Requisition.objects.filter(request_text__contains=f'[ERP-TI-REQ-ID:{legacy_id}]').first()
            )
            if requisition is None:
                not_found += 1
                continue

            processed += 1
            current_rank = self.status_rank.get(requisition.status, 0)
            target_rank = self.status_rank[target_status]

            approved_at = self._parse_date(row['approved_at']) or requisition.approved_at
            partially_received_at = (
                self._parse_date(row['partially_received_at']) or requisition.partially_received_at
            )
            received_at = self._parse_date(row['received_at']) or requisition.received_at

            update_fields = []
            status_changed = False
            previous_status = requisition.status

            if current_rank < target_rank:
                requisition.status = target_status
                update_fields.append('status')
                status_changed = True

            if approved_at and requisition.approved_at != approved_at:
                requisition.approved_at = approved_at
                update_fields.append('approved_at')

            if target_status == Requisition.Status.PARCIALMENTE_ENTREGUE and partially_received_at:
                if requisition.partially_received_at != partially_received_at:
                    requisition.partially_received_at = partially_received_at
                    update_fields.append('partially_received_at')

            if target_status == Requisition.Status.ENTREGUE:
                if partially_received_at and requisition.partially_received_at != partially_received_at:
                    requisition.partially_received_at = partially_received_at
                    update_fields.append('partially_received_at')
                if received_at and requisition.received_at != received_at:
                    requisition.received_at = received_at
                    update_fields.append('received_at')

            if not update_fields:
                continue

            if dry_run:
                changed += 1 if status_changed else 0
                dates_synced += 0 if status_changed else 1
                continue

            requisition.save(update_fields=list(dict.fromkeys(update_fields + ['updated_at'])))
            if status_changed:
                RequisitionUpdate.objects.create(
                    requisition=requisition,
                    author=requisition.requested_by,
                    message=(
                        f'Status sincronizado do legado ERP-TI (id={legacy_id}): '
                        f'"{previous_status}" -> "{requisition.status}".'
                    ),
                    status_to=requisition.status,
                )
                changed += 1
            else:
                dates_synced += 1

        mode_label = 'simulacao' if dry_run else 'execucao'
        self.stdout.write(self.style.SUCCESS(f'Sincronizacao de requisicoes legado concluida ({mode_label}).'))
        self.stdout.write(f'  requisicoes legado aprovadas processadas: {processed}')
        self.stdout.write(f'  status promovidos: {changed}')
        self.stdout.write(f'  datas sincronizadas sem mudar status: {dates_synced}')
        self.stdout.write(f'  requisicoes importadas nao encontradas: {not_found}')
        self.stdout.write(f'  requisicoes legado ignoradas (sem aprovacao): {skipped}')

    def _parse_date(self, raw_value):
        if not raw_value:
            return None
        raw_text = str(raw_value).strip()
        if not raw_text:
            return None
        try:
            return datetime.fromisoformat(raw_text).date()
        except ValueError:
            try:
                return datetime.strptime(raw_text, '%Y-%m-%d').date()
            except ValueError:
                return None
