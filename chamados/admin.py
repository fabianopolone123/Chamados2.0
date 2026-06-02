from django.contrib import admin

from .models import (
    CompletedServiceAttachment,
    CompletedServiceEntry,
    ContractAttachment,
    ContractEntry,
    EquipmentLoan,
    EquipmentLoanAttendantSignature,
    EquipmentLoanItem,
    EquipmentLoanPhoto,
    GoogleWorkspaceEmail,
    Insumo,
    PhoneExtension,
    Requisition,
    RequisitionBudget,
    RequisitionBudgetAttachment,
    RequisitionUpdate,
    SoftwareAsset,
    SoftwareLicense,
    Ticket,
    TicketAttendance,
    TicketFailureType,
    TicketPending,
    TicketUpdate,
    TicketUpdateAttachment,
    TiResponsibility,
)


class TicketUpdateAttachmentInline(admin.TabularInline):
    model = TicketUpdateAttachment
    extra = 0
    readonly_fields = ('uploaded_at',)


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
    list_display = ('id', 'title', 'status', 'priority', 'failure_type', 'created_by', 'updated_at')
    list_filter = ('status', 'priority', 'failure_type', 'created_at')
    search_fields = ('title', 'description', 'created_by__username')
    inlines = [TicketUpdateInline, TicketAttendanceInline]


@admin.register(TicketFailureType)
class TicketFailureTypeAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_at')
    search_fields = ('name',)


@admin.register(TicketUpdate)
class TicketUpdateAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'author', 'status_to', 'created_at')
    search_fields = ('ticket__title', 'author__username', 'message')
    list_filter = ('status_to', 'created_at')
    inlines = (TicketUpdateAttachmentInline,)


@admin.register(TicketUpdateAttachment)
class TicketUpdateAttachmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'update', 'file', 'uploaded_at')
    search_fields = ('update__ticket__title', 'update__message', 'file')
    list_filter = ('uploaded_at',)


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


@admin.register(TiResponsibility)
class TiResponsibilityAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'created_by', 'updated_at')
    search_fields = ('title', 'description', 'assignees__username')
    list_filter = ('created_at', 'updated_at')
    filter_horizontal = ('assignees',)


class SoftwareLicenseInline(admin.TabularInline):
    model = SoftwareLicense
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SoftwareAsset)
class SoftwareAssetAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'license_quantity', 'created_by', 'updated_at')
    search_fields = ('name', 'notes', 'licenses__serial', 'licenses__linked_email', 'licenses__assigned_user')
    list_filter = ('created_at', 'updated_at')
    inlines = (SoftwareLicenseInline,)


@admin.register(SoftwareLicense)
class SoftwareLicenseAdmin(admin.ModelAdmin):
    list_display = ('id', 'software', 'assigned_user', 'linked_email', 'expiration_type', 'expires_at', 'payment_method', 'card_final', 'updated_at')
    search_fields = ('software__name', 'serial', 'linked_email', 'assigned_user', 'payment_method')
    list_filter = ('software', 'expiration_type', 'expires_at', 'created_at')


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


class RequisitionBudgetAttachmentInline(admin.TabularInline):
    model = RequisitionBudgetAttachment
    extra = 0
    readonly_fields = ('uploaded_at',)
    fields = ('file', 'uploaded_at')


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
    inlines = (RequisitionBudgetAttachmentInline,)


@admin.register(Insumo)
class InsumoAdmin(admin.ModelAdmin):
    list_display = ('id', 'item', 'date', 'quantity', 'name', 'department', 'created_at')
    search_fields = ('item', 'name', 'department')
    list_filter = ('date', 'department')


@admin.register(GoogleWorkspaceEmail)
class GoogleWorkspaceEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'status', 'last_sign_in', 'storage_used', 'license_code')
    search_fields = ('email', 'first_name', 'last_name', 'status', 'license_code')
    list_filter = ('status', 'license_code', 'last_imported_at')


@admin.register(PhoneExtension)
class PhoneExtensionAdmin(admin.ModelAdmin):
    list_display = ('id', 'department', 'name', 'phone', 'extension', 'email', 'created_by', 'updated_at')
    search_fields = ('name', 'department', 'phone', 'extension', 'email')
    list_filter = ('department', 'created_at', 'updated_at')


class CompletedServiceAttachmentInline(admin.TabularInline):
    model = CompletedServiceAttachment
    extra = 0


@admin.register(CompletedServiceEntry)
class CompletedServiceEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'service_name', 'company', 'service_date', 'amount', 'created_by', 'updated_at')
    search_fields = ('service_name', 'company', 'description', 'created_by__username')
    list_filter = ('service_date', 'created_at', 'updated_at')
    inlines = (CompletedServiceAttachmentInline,)


class ContractAttachmentInline(admin.TabularInline):
    model = ContractAttachment
    extra = 0


@admin.register(ContractEntry)
class ContractEntryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'payment_schedule', 'amount', 'contract_start', 'contract_end', 'finished_at', 'created_by', 'updated_at')
    search_fields = ('name', 'notes', 'payment_method', 'created_by__username')
    list_filter = ('payment_schedule', 'contract_start', 'contract_end', 'finished_at', 'created_at')
    inlines = (ContractAttachmentInline,)


@admin.register(EquipmentLoan)
class EquipmentLoanAdmin(admin.ModelAdmin):
    list_display = ('id', 'collaborator_name', 'collaborator_company', 'equipment_type', 'patrimony_tag', 'documentation_ok', 'loan_date', 'created_by')
    search_fields = ('collaborator_name', 'collaborator_company', 'equipment_type', 'equipment_serial', 'patrimony_tag')
    list_filter = ('documentation_ok', 'loan_date', 'created_at')


@admin.register(EquipmentLoanItem)
class EquipmentLoanItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'loan', 'equipment_type', 'equipment_brand', 'equipment_model', 'equipment_serial', 'patrimony_tag')
    search_fields = ('loan__collaborator_name', 'equipment_type', 'equipment_brand', 'equipment_model', 'equipment_serial', 'patrimony_tag')


@admin.register(EquipmentLoanAttendantSignature)
class EquipmentLoanAttendantSignatureAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'created_by', 'updated_at')
    search_fields = ('name', 'created_by__username')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('authorization_password_hash',)


@admin.register(EquipmentLoanPhoto)
class EquipmentLoanPhotoAdmin(admin.ModelAdmin):
    list_display = ('id', 'loan', 'image', 'uploaded_at')
    search_fields = ('loan__collaborator_name', 'loan__equipment_type', 'image')
    list_filter = ('uploaded_at',)
