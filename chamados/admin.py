from django.contrib import admin

from .models import Ticket, TicketUpdate


class TicketUpdateInline(admin.TabularInline):
    model = TicketUpdate
    extra = 0
    readonly_fields = ('author', 'message', 'status_to', 'created_at')
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status', 'priority', 'created_by', 'assigned_to', 'updated_at')
    list_filter = ('status', 'priority', 'created_at')
    search_fields = ('title', 'description', 'created_by__username', 'assigned_to__username')
    inlines = [TicketUpdateInline]


@admin.register(TicketUpdate)
class TicketUpdateAdmin(admin.ModelAdmin):
    list_display = ('id', 'ticket', 'author', 'status_to', 'created_at')
    search_fields = ('ticket__title', 'author__username', 'message')
    list_filter = ('status_to', 'created_at')
