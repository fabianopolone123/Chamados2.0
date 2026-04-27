from django.contrib import admin

from .models import (
    CompletedServiceEntry,
    Insumo,
    Requisition,
    RequisitionBudget,
    RequisitionUpdate,
    Ticket,
    TicketAttendance,
    TicketPending,
    TicketUpdate,
)


class TicketUpdateInline(admin.TabularInline):
    model = TicketUpdate
    extra = 0
    readonly_fields = ('author', 'message', 'status_to', 'created_at')
    can_delete = False


class TicketAttendanceInline(admin.TabularInline):
    model = TicketAttendance
    extra = 0
    readonly_fields = ('attendant', 'started_at', 'ended_at', 'end_action', 'note', 'created_at')
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status', 'priority', 'created_by', 'updated_at')
    list_filter = ('status', 'priority', 'created_at')
    search_fields = ('title', 'description', 'created_by__username')
    inlines = [TicketUpdateInline, TicketAttendanceInline]


@admin.register(TicketUpdate)
class TicketUpdateAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'author', 'status_to', 'created_at')
    search_fields = ('ticket__title', 'author__username', 'message')
    list_filter = ('status_to', 'created_at')


@admin.register(TicketAttendance)
class TicketAttendanceAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'attendant', 'started_at', 'ended_at', 'end_action')
    search_fields = ('ticket__title', 'attendant__username', 'note')
    list_filter = ('end_action', 'started_at')


@admin.register(TicketPending)
class TicketPendingAdmin(admin.ModelAdmin):
    list_display = ('id', 'attendant', 'updated_at', 'created_at')
    search_fields = ('attendant__username', 'content')
    list_filter = ('updated_at', 'created_at')


class RequisitionUpdateInline(admin.TabularInline):
    model = RequisitionUpdate
    extra = 0
    readonly_fields = ('author', 'message', 'status_to', 'created_at')
    can_delete = False


class RequisitionBudgetInline(admin.TabularInline):
    model = RequisitionBudget
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    fields = ('title', 'amount', 'parent_budget', 'evidence_file', 'notes', 'created_at', 'updated_at')


@admin.register(Requisition)
class RequisitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'kind', 'status', 'requested_by', 'updated_at')
    search_fields = ('code', 'title', 'request_text', 'requested_by__username')
    list_filter = ('kind', 'status', 'created_at')
    inlines = [RequisitionBudgetInline, RequisitionUpdateInline]


@admin.register(RequisitionUpdate)
class RequisitionUpdateAdmin(admin.ModelAdmin):
    list_display = ('id', 'requisition', 'author', 'status_to', 'created_at')
    search_fields = ('requisition__code', 'requisition__title', 'author__username', 'message')
    list_filter = ('status_to', 'created_at')


@admin.register(RequisitionBudget)
class RequisitionBudgetAdmin(admin.ModelAdmin):
    list_display = ('id', 'requisition', 'title', 'amount', 'parent_budget', 'updated_at')
    search_fields = ('requisition__code', 'requisition__title', 'title', 'notes')
    list_filter = ('updated_at',)


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'date', 'quantity', 'name', 'department', 'created_at')
    search_fields = ('item', 'name', 'department')
    list_filter = ('date', 'department')


@admin.register(CompletedServiceEntry)
class CompletedServiceEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'service_name', 'company', 'amount', 'created_by', 'updated_at')
    search_fields = ('service_name', 'company', 'description', 'created_by__username')
    list_filter = ('created_at', 'updated_at')
