from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from chamados.models import (
    Insumo,
    Requisition,
    RequisitionBudget,
    RequisitionUpdate,
    Ticket,
    TicketAttendance,
    TicketPending,
    TicketUpdate,
)


@dataclass
class Counters:
    users_created: int = 0
    tickets_created: int = 0
    ticket_updates_created: int = 0
    ticket_attendances_created: int = 0
    requisitions_created: int = 0
    requisition_updates_created: int = 0
    requisition_budgets_created: int = 0
    tickets_skipped_existing: int = 0
    requisitions_skipped_existing: int = 0
    pendings_created: int = 0
    pendings_skipped_existing: int = 0
    pendings_skipped_done: int = 0
    insumos_created: int = 0
    insumos_skipped_existing: int = 0


class Command(BaseCommand):
    help = "Importa dados de chamados e requisicoes de um banco legado ERP-TI (SQLite)."

    ticket_priority_map = {
        "baixa": Ticket.Priority.BAIXA,
        "media": Ticket.Priority.MEDIA,
        "alta": Ticket.Priority.ALTA,
        "critica": Ticket.Priority.CRITICA,
        "programada": Ticket.Priority.PROGRAMADA,
        "nao_classificado": Ticket.Priority.MEDIA,
    }

    ticket_status_map = {
        "pendente": Ticket.Status.ABERTO,
        "em_atendimento": Ticket.Status.EM_ATENDIMENTO,
        "fechado": Ticket.Status.FECHADO,
        "cancelado": Ticket.Status.FECHADO,
    }

    requisition_status_map = {
        "pending_approval": Requisition.Status.PENDENTE_APROVACAO,
        "approved": Requisition.Status.APROVADA,
        "rejected": Requisition.Status.NAO_APROVADA,
        "partially_received": Requisition.Status.PARCIALMENTE_ENTREGUE,
        "received": Requisition.Status.ENTREGUE,
    }

    requisition_kind_map = {
        "physical": Requisition.Kind.FISICA,
        "digital": Requisition.Kind.DIGITAL,
    }

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            default="erp-ti-db.sqlite3",
            help="Caminho do banco SQLite legado. Default: erp-ti-db.sqlite3",
        )
        parser.add_argument(
            "--owner-username",
            default="",
            help="Usuario local para ser dono de itens sem autor mapeado.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula a importacao sem gravar no banco.",
        )

    def handle(self, *args, **options):
        source_path = Path(options["source"]).resolve()
        if not source_path.exists():
            raise CommandError(f"Banco legado nao encontrado: {source_path}")

        owner_username = (options["owner_username"] or "").strip()
        dry_run = bool(options["dry_run"])

        self.stdout.write(self.style.NOTICE(f"Fonte legado: {source_path}"))
        self.stdout.write(self.style.NOTICE(f"Dry-run: {'sim' if dry_run else 'nao'}"))

        User = get_user_model()
        owner = self._resolve_owner_user(User, owner_username)
        ti_group, _ = Group.objects.get_or_create(
            name=(getattr(settings, "TI_GROUP_NAME", "TI") or "TI").strip()
        )

        source_con = sqlite3.connect(str(source_path))
        source_con.row_factory = sqlite3.Row
        source_cur = source_con.cursor()

        counters = Counters()
        local_users: dict[str, Any] = {}
        imported_ticket_by_legacy_id: dict[int, Ticket] = {}
        imported_requisition_by_legacy_id: dict[int, Requisition] = {}

        try:
            with transaction.atomic():
                old_auth_users = self._load_user_rows(
                    source_cur,
                    "auth_user",
                    ["id", "username", "first_name", "last_name", "email", "is_active"],
                )
                old_erp_users = self._load_user_rows(
                    source_cur,
                    "core_erpuser",
                    ["id", "username", "full_name", "email", "is_active"],
                )

                self._import_tickets(
                    source_cur=source_cur,
                    owner=owner,
                    ti_group=ti_group,
                    old_auth_users=old_auth_users,
                    old_erp_users=old_erp_users,
                    local_users=local_users,
                    counters=counters,
                    imported_ticket_by_legacy_id=imported_ticket_by_legacy_id,
                )

                self._import_requisitions(
                    source_cur=source_cur,
                    owner=owner,
                    old_auth_users=old_auth_users,
                    old_erp_users=old_erp_users,
                    local_users=local_users,
                    counters=counters,
                    imported_requisition_by_legacy_id=imported_requisition_by_legacy_id,
                )

                self._import_pendencias(
                    source_cur=source_cur,
                    owner=owner,
                    old_erp_users=old_erp_users,
                    local_users=local_users,
                    counters=counters,
                )

                self._import_insumos(
                    source_cur=source_cur,
                    counters=counters,
                )

                if dry_run:
                    transaction.set_rollback(True)
        finally:
            source_con.close()

        self._print_summary(counters, dry_run=dry_run)

    def _resolve_owner_user(self, User, owner_username: str):
        if owner_username:
            owner = User.objects.filter(username=owner_username).first()
            if owner is None:
                raise CommandError(
                    f"Usuario owner nao encontrado no sistema atual: {owner_username}"
                )
            return owner

        owner = User.objects.filter(is_superuser=True).order_by("id").first()
        if owner is not None:
            return owner

        owner = User.objects.order_by("id").first()
        if owner is not None:
            return owner

        raise CommandError("Nao existe usuario no banco atual para ser owner da importacao.")

    def _table_exists(self, cur, table_name: str) -> bool:
        return (
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            is not None
        )

    def _load_user_rows(self, cur, table_name: str, columns: list[str]) -> dict[int, dict[str, Any]]:
        if not self._table_exists(cur, table_name):
            return {}
        select_columns = ", ".join(columns)
        rows = cur.execute(f"SELECT {select_columns} FROM {table_name}").fetchall()
        data: dict[int, dict[str, Any]] = {}
        for row in rows:
            data[int(row["id"])] = {k: row[k] for k in columns}
        return data

    def _split_full_name(self, full_name: str) -> tuple[str, str]:
        parts = [p for p in (full_name or "").strip().split(" ") if p]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0][:150], ""
        return parts[0][:150], " ".join(parts[1:])[:150]

    def _get_or_create_local_user(
        self,
        *,
        username: str,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        is_active: bool = True,
        local_users: dict[str, Any],
        counters: Counters,
        ti_group: Group | None = None,
    ):
        User = get_user_model()
        normalized = (username or "").strip()[:150]
        if not normalized:
            return None

        cached = local_users.get(normalized)
        if cached is not None:
            if ti_group is not None:
                cached.groups.add(ti_group)
            return cached

        user, created = User.objects.get_or_create(
            username=normalized,
            defaults={
                "first_name": (first_name or "").strip()[:150],
                "last_name": (last_name or "").strip()[:150],
                "email": (email or "").strip()[:254],
                "is_active": bool(is_active),
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
            counters.users_created += 1
        else:
            update_fields = []
            if (not user.first_name) and first_name:
                user.first_name = first_name[:150]
                update_fields.append("first_name")
            if (not user.last_name) and last_name:
                user.last_name = last_name[:150]
                update_fields.append("last_name")
            if (not user.email) and email:
                user.email = email[:254]
                update_fields.append("email")
            if update_fields:
                user.save(update_fields=update_fields)

        if ti_group is not None:
            user.groups.add(ti_group)

        local_users[normalized] = user
        return user

    def _parse_datetime(self, raw: Any):
        text = (str(raw).strip() if raw is not None else "")
        if not text:
            return None
        parsed = parse_datetime(text)
        if parsed is None:
            as_date = parse_date(text)
            if as_date is None:
                return None
            parsed = timezone.datetime.combine(as_date, timezone.datetime.min.time())
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def _parse_date(self, raw: Any):
        text = (str(raw).strip() if raw is not None else "")
        if not text:
            return None
        parsed = parse_date(text)
        if parsed is not None:
            return parsed
        dt = self._parse_datetime(text)
        if dt is None:
            return None
        return timezone.localtime(dt).date()

    def _parse_decimal(self, raw: Any, default: str = "0.00") -> Decimal:
        text = (str(raw).strip() if raw is not None else "")
        if not text:
            return Decimal(default)
        text = text.replace(",", ".")
        try:
            return Decimal(text).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return Decimal(default)

    def _parse_positive_int(self, raw: Any, default: int = 1) -> int:
        text = (str(raw).strip() if raw is not None else "")
        if not text:
            return default
        text = text.replace(",", ".")
        try:
            value = int(Decimal(text))
        except (InvalidOperation, ValueError):
            return default
        return value if value > 0 else default

    def _status_to_ticket(self, legacy_status: str) -> str:
        key = (legacy_status or "").strip().lower()
        return self.ticket_status_map.get(key, Ticket.Status.ABERTO)

    def _priority_to_ticket(self, legacy_urgency: str) -> str:
        key = (legacy_urgency or "").strip().lower()
        return self.ticket_priority_map.get(key, Ticket.Priority.MEDIA)

    def _status_to_requisition(self, legacy_status: str) -> str:
        key = (legacy_status or "").strip().lower()
        return self.requisition_status_map.get(key, Requisition.Status.PENDENTE_APROVACAO)

    def _kind_to_requisition(self, legacy_kind: str) -> str:
        key = (legacy_kind or "").strip().lower()
        return self.requisition_kind_map.get(key, Requisition.Kind.FISICA)

    def _import_tickets(
        self,
        *,
        source_cur,
        owner,
        ti_group: Group,
        old_auth_users: dict[int, dict[str, Any]],
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
        imported_ticket_by_legacy_id: dict[int, Ticket],
    ):
        if not self._table_exists(source_cur, "core_ticket"):
            self.stdout.write(self.style.WARNING("Tabela core_ticket nao encontrada."))
            return

        rows = source_cur.execute("SELECT * FROM core_ticket ORDER BY id").fetchall()
        for row in rows:
            legacy_id = int(row["id"])
            marker = f"[ERP-TI-ID:{legacy_id}]"
            existing = Ticket.objects.filter(description__contains=marker).first()
            if existing is not None:
                imported_ticket_by_legacy_id[legacy_id] = existing
                counters.tickets_skipped_existing += 1
                continue

            created_by = owner
            legacy_created = old_auth_users.get(row["created_by_id"] or -1)
            if legacy_created:
                created_by = self._get_or_create_local_user(
                    username=legacy_created.get("username") or "",
                    first_name=legacy_created.get("first_name") or "",
                    last_name=legacy_created.get("last_name") or "",
                    email=legacy_created.get("email") or "",
                    is_active=bool(legacy_created.get("is_active", True)),
                    local_users=local_users,
                    counters=counters,
                ) or owner

            description = (row["description"] or "").strip()
            attachment = (row["attachment"] or "").strip()
            ticket_type = (row["ticket_type"] or "").strip()
            last_failure = (row["last_failure_type"] or "").strip()
            if attachment:
                description = (
                    f"{description}\n\nAnexo legado: {attachment}".strip()
                )
            metadata = f"Tipo legado: {ticket_type or '-'} | Falha legado: {last_failure or '-'}"
            description = f"{description}\n\n{metadata}\n{marker}".strip()

            mapped_status = self._status_to_ticket(row["status"] or "")
            created_at = self._parse_datetime(row["created_at"]) or timezone.now()
            updated_at = self._parse_datetime(row["updated_at"]) or created_at
            closed_at = updated_at if mapped_status == Ticket.Status.FECHADO else None

            ticket = Ticket.objects.create(
                title=(row["title"] or f"Chamado legado #{legacy_id}")[:180],
                description=description,
                priority=self._priority_to_ticket(row["urgency"] or ""),
                status=mapped_status,
                created_by=created_by,
            )
            Ticket.objects.filter(pk=ticket.pk).update(
                created_at=created_at,
                updated_at=updated_at,
                closed_at=closed_at,
            )
            ticket.refresh_from_db()
            counters.tickets_created += 1
            imported_ticket_by_legacy_id[legacy_id] = ticket

            resolution = (row["resolution"] or "").strip()
            if resolution:
                update = TicketUpdate.objects.create(
                    ticket=ticket,
                    author=created_by,
                    message=f"Resolucao legado:\n{resolution}",
                    status_to=ticket.status,
                )
                TicketUpdate.objects.filter(pk=update.pk).update(created_at=updated_at)
                counters.ticket_updates_created += 1

        self._import_ticket_timeline_events(
            source_cur=source_cur,
            owner=owner,
            ti_group=ti_group,
            old_auth_users=old_auth_users,
            old_erp_users=old_erp_users,
            local_users=local_users,
            counters=counters,
            imported_ticket_by_legacy_id=imported_ticket_by_legacy_id,
        )
        self._import_ticket_worklogs(
            source_cur=source_cur,
            owner=owner,
            ti_group=ti_group,
            old_erp_users=old_erp_users,
            local_users=local_users,
            counters=counters,
            imported_ticket_by_legacy_id=imported_ticket_by_legacy_id,
        )
        self._import_ticket_attendance_cycles(
            source_cur=source_cur,
            owner=owner,
            ti_group=ti_group,
            old_erp_users=old_erp_users,
            local_users=local_users,
            counters=counters,
            imported_ticket_by_legacy_id=imported_ticket_by_legacy_id,
        )

    def _import_ticket_timeline_events(
        self,
        *,
        source_cur,
        owner,
        ti_group: Group,
        old_auth_users: dict[int, dict[str, Any]],
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
        imported_ticket_by_legacy_id: dict[int, Ticket],
    ):
        if not self._table_exists(source_cur, "core_tickettimelineevent"):
            return
        rows = source_cur.execute("SELECT * FROM core_tickettimelineevent ORDER BY id").fetchall()
        for row in rows:
            legacy_event_id = int(row["id"])
            ticket = imported_ticket_by_legacy_id.get(int(row["ticket_id"] or -1))
            if ticket is None:
                continue
            marker = f"[ERP-TI-EVENT:{legacy_event_id}]"
            if TicketUpdate.objects.filter(ticket=ticket, message__contains=marker).exists():
                continue

            author = owner
            legacy_ti = old_erp_users.get(row["actor_ti_id"] or -1)
            if legacy_ti and legacy_ti.get("username"):
                first_name, last_name = self._split_full_name(legacy_ti.get("full_name") or "")
                author = self._get_or_create_local_user(
                    username=legacy_ti.get("username") or "",
                    first_name=first_name,
                    last_name=last_name,
                    email=legacy_ti.get("email") or "",
                    is_active=bool(legacy_ti.get("is_active", True)),
                    local_users=local_users,
                    counters=counters,
                    ti_group=ti_group,
                ) or owner
            else:
                legacy_user = old_auth_users.get(row["actor_user_id"] or -1)
                if legacy_user and legacy_user.get("username"):
                    author = self._get_or_create_local_user(
                        username=legacy_user.get("username") or "",
                        first_name=legacy_user.get("first_name") or "",
                        last_name=legacy_user.get("last_name") or "",
                        email=legacy_user.get("email") or "",
                        is_active=bool(legacy_user.get("is_active", True)),
                        local_users=local_users,
                        counters=counters,
                    ) or owner

            from_status = (row["from_status"] or "").strip()
            to_status = (row["to_status"] or "").strip()
            note = (row["note"] or "").strip()
            event_type = (row["event_type"] or "").strip()
            status_to = self._status_to_ticket(to_status) if to_status else ""
            message = (
                f"Evento legado ({event_type or 'timeline'}): {from_status or '-'} -> {to_status or '-'}\n"
                f"{note or 'Sem observacao.'}\n"
                f"{marker}"
            )
            update = TicketUpdate.objects.create(
                ticket=ticket,
                author=author,
                message=message,
                status_to=status_to,
            )
            created_at = self._parse_datetime(row["created_at"])
            if created_at is not None:
                TicketUpdate.objects.filter(pk=update.pk).update(created_at=created_at)
            counters.ticket_updates_created += 1

    def _import_ticket_worklogs(
        self,
        *,
        source_cur,
        owner,
        ti_group: Group,
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
        imported_ticket_by_legacy_id: dict[int, Ticket],
    ):
        if not self._table_exists(source_cur, "core_ticketworklog"):
            return
        rows = source_cur.execute("SELECT * FROM core_ticketworklog ORDER BY id").fetchall()
        for row in rows:
            legacy_log_id = int(row["id"])
            ticket = imported_ticket_by_legacy_id.get(int(row["ticket_id"] or -1))
            if ticket is None:
                continue
            marker = f"[ERP-TI-WORKLOG:{legacy_log_id}]"
            if TicketUpdate.objects.filter(ticket=ticket, message__contains=marker).exists():
                continue

            author = owner
            legacy_ti = old_erp_users.get(row["attendant_id"] or -1)
            if legacy_ti and legacy_ti.get("username"):
                first_name, last_name = self._split_full_name(legacy_ti.get("full_name") or "")
                author = self._get_or_create_local_user(
                    username=legacy_ti.get("username") or "",
                    first_name=first_name,
                    last_name=last_name,
                    email=legacy_ti.get("email") or "",
                    is_active=bool(legacy_ti.get("is_active", True)),
                    local_users=local_users,
                    counters=counters,
                    ti_group=ti_group,
                ) or owner

            action_text = (row["action_text"] or "").strip()
            failure_type = (row["failure_type"] or "").strip()
            priority_label = (row["priority_label"] or "").strip()
            message = (
                f"Worklog legado ({failure_type or '-'}/{priority_label or '-'}):\n"
                f"{action_text or 'Sem descricao.'}\n"
                f"{marker}"
            )
            update = TicketUpdate.objects.create(
                ticket=ticket,
                author=author,
                message=message,
                status_to="",
            )
            created_at = self._parse_datetime(row["closed_at"]) or self._parse_datetime(row["created_at"])
            if created_at is not None:
                TicketUpdate.objects.filter(pk=update.pk).update(created_at=created_at)
            counters.ticket_updates_created += 1

    def _import_ticket_attendance_cycles(
        self,
        *,
        source_cur,
        owner,
        ti_group: Group,
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
        imported_ticket_by_legacy_id: dict[int, Ticket],
    ):
        if not self._table_exists(source_cur, "core_ticketattendantcycle"):
            return
        rows = source_cur.execute("SELECT * FROM core_ticketattendantcycle ORDER BY id").fetchall()
        for row in rows:
            legacy_cycle_id = int(row["id"])
            marker = f"[ERP-TI-CYCLE:{legacy_cycle_id}]"
            ticket = imported_ticket_by_legacy_id.get(int(row["ticket_id"] or -1))
            if ticket is None:
                continue
            if TicketAttendance.objects.filter(ticket=ticket, note__contains=marker).exists():
                continue

            attendant = owner
            legacy_ti = old_erp_users.get(row["attendant_id"] or -1)
            if legacy_ti and legacy_ti.get("username"):
                first_name, last_name = self._split_full_name(legacy_ti.get("full_name") or "")
                attendant = self._get_or_create_local_user(
                    username=legacy_ti.get("username") or "",
                    first_name=first_name,
                    last_name=last_name,
                    email=legacy_ti.get("email") or "",
                    is_active=bool(legacy_ti.get("is_active", True)),
                    local_users=local_users,
                    counters=counters,
                    ti_group=ti_group,
                ) or owner

            started_at = self._parse_datetime(row["current_cycle_started_at"]) or self._parse_datetime(
                row["updated_at"]
            )
            if started_at is None:
                started_at = timezone.now()
            ended_at = self._parse_datetime(row["updated_at"])
            if ended_at is not None and ended_at < started_at:
                ended_at = None

            end_action = ""
            if ended_at is not None:
                end_action = TicketAttendance.EndAction.STOP

            attendance = TicketAttendance.objects.create(
                ticket=ticket,
                attendant=attendant,
                started_at=started_at,
                ended_at=ended_at,
                end_action=end_action,
                note=f"Ciclo importado do legado. {marker}",
            )
            TicketAttendance.objects.filter(pk=attendance.pk).update(created_at=started_at)
            counters.ticket_attendances_created += 1

    def _import_requisitions(
        self,
        *,
        source_cur,
        owner,
        old_auth_users: dict[int, dict[str, Any]],
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
        imported_requisition_by_legacy_id: dict[int, Requisition],
    ):
        # Mantemos assinatura consistente com tickets para facilitar evolucao futura.
        _ = old_auth_users, old_erp_users, local_users

        if not self._table_exists(source_cur, "core_requisition"):
            self.stdout.write(self.style.WARNING("Tabela core_requisition nao encontrada."))
            return

        quote_rows_by_req: dict[int, list[sqlite3.Row]] = {}
        if self._table_exists(source_cur, "core_requisitionquote"):
            for q_row in source_cur.execute("SELECT * FROM core_requisitionquote ORDER BY id").fetchall():
                req_id = int(q_row["requisition_id"] or -1)
                quote_rows_by_req.setdefault(req_id, []).append(q_row)

        quote_attachments: dict[int, list[str]] = {}
        if self._table_exists(source_cur, "core_requisitionquoteattachment"):
            for a_row in source_cur.execute(
                "SELECT quote_id, file FROM core_requisitionquoteattachment ORDER BY id"
            ).fetchall():
                quote_id = int(a_row["quote_id"] or -1)
                path = (a_row["file"] or "").strip()
                if path:
                    quote_attachments.setdefault(quote_id, []).append(path)

        rows = source_cur.execute("SELECT * FROM core_requisition ORDER BY id").fetchall()
        for row in rows:
            legacy_id = int(row["id"])
            code = f"LEG-REQ-{legacy_id:05d}"
            existing = Requisition.objects.filter(code=code).first()
            if existing is not None:
                imported_requisition_by_legacy_id[legacy_id] = existing
                counters.requisitions_skipped_existing += 1
                continue

            title = (row["title"] or row["request"] or f"Requisicao legado #{legacy_id}")[:180]
            request_text_parts = [
                (row["request"] or "").strip(),
                f"Quantidade: {row['quantity']}" if row["quantity"] is not None else "",
                f"Valor unitario: {row['unit_value']}" if row["unit_value"] is not None else "",
                f"Valor total legado: {row['total_value']}" if row["total_value"] is not None else "",
                f"Tipo legado: {(row['req_type'] or '').strip()}" if row["req_type"] else "",
                f"Local: {(row['location'] or '').strip()}" if row["location"] else "",
                f"Link legado: {(row['link'] or '').strip()}" if row["link"] else "",
                f"Nota fiscal: {(row['invoice'] or '').strip()}" if row["invoice"] else "",
                f"[ERP-TI-REQ-ID:{legacy_id}]",
            ]
            request_text = "\n".join([part for part in request_text_parts if part]).strip()

            requisition = Requisition.objects.create(
                code=code,
                title=title,
                kind=self._kind_to_requisition(row["kind"] or ""),
                request_text=request_text,
                status=self._status_to_requisition(row["status"] or ""),
                requested_by=owner,
                requested_at=self._parse_date(row["requested_at"]),
                approved_at=self._parse_date(row["approved_at"]),
                partially_received_at=self._parse_date(row["partially_received_at"]),
                received_at=self._parse_date(row["received_at"]),
            )
            created_at = self._parse_datetime(row["created_at"])
            updated_at = self._parse_datetime(row["updated_at"]) or created_at
            if created_at is not None:
                Requisition.objects.filter(pk=requisition.pk).update(
                    created_at=created_at,
                    updated_at=updated_at or created_at,
                )
                requisition.refresh_from_db()

            counters.requisitions_created += 1
            imported_requisition_by_legacy_id[legacy_id] = requisition

            update = RequisitionUpdate.objects.create(
                requisition=requisition,
                author=owner,
                message=f"Requisicao importada do legado ERP-TI (id={legacy_id}).",
                status_to=requisition.status,
            )
            if created_at is not None:
                RequisitionUpdate.objects.filter(pk=update.pk).update(created_at=created_at)
            counters.requisition_updates_created += 1

            self._import_requisition_quotes(
                requisition=requisition,
                quote_rows=quote_rows_by_req.get(legacy_id, []),
                quote_attachments=quote_attachments,
                counters=counters,
                updated_at=updated_at,
            )

    def _import_requisition_quotes(
        self,
        *,
        requisition: Requisition,
        quote_rows: list[sqlite3.Row],
        quote_attachments: dict[int, list[str]],
        counters: Counters,
        updated_at,
    ):
        if not quote_rows:
            return

        by_parent: dict[int | None, list[sqlite3.Row]] = {}
        for row in quote_rows:
            parent_id = row["parent_id"]
            key = int(parent_id) if parent_id is not None else None
            by_parent.setdefault(key, []).append(row)

        created_by_legacy_id: dict[int, RequisitionBudget] = {}

        def build_notes(row: sqlite3.Row) -> str:
            parts = []
            if row["link"]:
                parts.append(f"Link legado: {str(row['link']).strip()}")
            if row["quantity"] is not None:
                parts.append(f"Quantidade: {row['quantity']}")
            if row["payment_method"]:
                parts.append(f"Pagamento: {str(row['payment_method']).strip()}")
            if row["payment_installments"] is not None:
                parts.append(f"Parcelas: {row['payment_installments']}")
            if row["is_selected"] is not None:
                parts.append(f"Selecionado legado: {bool(row['is_selected'])}")
            for attachment_path in quote_attachments.get(int(row["id"]), []):
                parts.append(f"Anexo legado: {attachment_path}")
            parts.append(f"[ERP-TI-QUOTE-ID:{int(row['id'])}]")
            return "\n".join(parts)

        def create_budget_row(row: sqlite3.Row, parent_budget: RequisitionBudget | None):
            budget = RequisitionBudget.objects.create(
                requisition=requisition,
                parent_budget=parent_budget,
                title=(row["name"] or f"Orcamento legado #{int(row['id'])}")[:160],
                amount=self._parse_decimal(row["value"]),
                quantity=self._parse_positive_int(row["quantity"]),
                freight_amount=self._parse_decimal(row["freight"]),
                approval_status=(
                    RequisitionBudget.ApprovalStatus.APROVADO
                    if bool(row["is_selected"])
                    else RequisitionBudget.ApprovalStatus.PENDENTE
                ),
                notes=build_notes(row),
                evidence_file=(row["photo"] or "").strip() or None,
            )
            if updated_at is not None:
                RequisitionBudget.objects.filter(pk=budget.pk).update(
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            counters.requisition_budgets_created += 1
            created_by_legacy_id[int(row["id"])] = budget

        for root_row in by_parent.get(None, []):
            create_budget_row(root_row, None)

        pending = [k for k in by_parent.keys() if k is not None]
        loops = 0
        while pending and loops < 10:
            loops += 1
            next_pending = []
            for parent_legacy_id in pending:
                parent_budget = created_by_legacy_id.get(parent_legacy_id)
                if parent_budget is None:
                    next_pending.append(parent_legacy_id)
                    continue
                for sub_row in by_parent.get(parent_legacy_id, []):
                    create_budget_row(sub_row, parent_budget)
            if len(next_pending) == len(pending):
                break
            pending = next_pending

        for parent_legacy_id in pending:
            for sub_row in by_parent.get(parent_legacy_id, []):
                create_budget_row(sub_row, None)

    def _import_pendencias(
        self,
        *,
        source_cur,
        owner,
        old_erp_users: dict[int, dict[str, Any]],
        local_users: dict[str, Any],
        counters: Counters,
    ):
        if not self._table_exists(source_cur, "core_pendencia"):
            return

        rows = source_cur.execute("SELECT * FROM core_pendencia ORDER BY id").fetchall()
        for row in rows:
            is_done = bool(row["is_done"])
            if is_done:
                counters.pendings_skipped_done += 1
                continue

            description = (row["description"] or "").strip()
            if not description:
                continue

            attendant = owner
            legacy_ti = old_erp_users.get(row["attendant_id"] or -1)
            if legacy_ti and legacy_ti.get("username"):
                first_name, last_name = self._split_full_name(legacy_ti.get("full_name") or "")
                attendant = self._get_or_create_local_user(
                    username=legacy_ti.get("username") or "",
                    first_name=first_name,
                    last_name=last_name,
                    email=legacy_ti.get("email") or "",
                    is_active=bool(legacy_ti.get("is_active", True)),
                    local_users=local_users,
                    counters=counters,
                    ti_group=None,
                ) or owner

            exists = TicketPending.objects.filter(
                attendant=attendant,
                content=description,
            ).exists()
            if exists:
                counters.pendings_skipped_existing += 1
                continue

            pending = TicketPending.objects.create(
                attendant=attendant,
                content=description,
            )
            created_at = self._parse_datetime(row["created_at"])
            updated_at = self._parse_datetime(row["updated_at"]) or created_at
            if created_at is not None:
                TicketPending.objects.filter(pk=pending.pk).update(
                    created_at=created_at,
                    updated_at=updated_at or created_at,
                )
            counters.pendings_created += 1

    def _import_insumos(
        self,
        *,
        source_cur,
        counters: Counters,
    ):
        if not self._table_exists(source_cur, "core_insumo"):
            return

        rows = source_cur.execute("SELECT * FROM core_insumo ORDER BY id").fetchall()
        for row in rows:
            legacy_id = int(row["id"])
            if Insumo.objects.filter(legacy_id=legacy_id).exists():
                counters.insumos_skipped_existing += 1
                continue

            entry_date = self._parse_date(row["date"]) or timezone.localdate()
            quantity = self._parse_decimal(row["quantity"], default="0.00")
            created = Insumo.objects.create(
                item=(row["item"] or "").strip()[:120] or f"Insumo legado #{legacy_id}",
                date=entry_date,
                quantity=quantity,
                name=(row["name"] or "").strip()[:200] or "Legado",
                department=(row["department"] or "").strip()[:120],
                legacy_id=legacy_id,
            )
            created_at = self._parse_datetime(row["created_at"])
            if created_at is not None:
                Insumo.objects.filter(pk=created.pk).update(created_at=created_at)
            counters.insumos_created += 1

    def _print_summary(self, counters: Counters, *, dry_run: bool):
        title = "Resumo (simulado)" if dry_run else "Resumo importacao"
        self.stdout.write(self.style.SUCCESS(title))
        self.stdout.write(f"  usuarios criados: {counters.users_created}")
        self.stdout.write(f"  chamados criados: {counters.tickets_created}")
        self.stdout.write(
            f"  chamados ignorados (ja importados): {counters.tickets_skipped_existing}"
        )
        self.stdout.write(f"  updates de chamados criados: {counters.ticket_updates_created}")
        self.stdout.write(
            f"  ciclos de atendimento criados: {counters.ticket_attendances_created}"
        )
        self.stdout.write(f"  requisicoes criadas: {counters.requisitions_created}")
        self.stdout.write(
            f"  requisicoes ignoradas (ja importadas): {counters.requisitions_skipped_existing}"
        )
        self.stdout.write(
            f"  updates de requisicoes criados: {counters.requisition_updates_created}"
        )
        self.stdout.write(
            f"  orcamentos de requisicoes criados: {counters.requisition_budgets_created}"
        )
        self.stdout.write(f"  pendencias criadas: {counters.pendings_created}")
        self.stdout.write(
            f"  pendencias ignoradas (ja importadas): {counters.pendings_skipped_existing}"
        )
        self.stdout.write(
            f"  pendencias ignoradas (concluidas no legado): {counters.pendings_skipped_done}"
        )
        self.stdout.write(f"  insumos criados: {counters.insumos_created}")
        self.stdout.write(
            f"  insumos ignorados (ja importados): {counters.insumos_skipped_existing}"
        )
