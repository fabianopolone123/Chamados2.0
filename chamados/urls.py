from django.urls import path

from .views import (
    RequisitionHubView,
    RequisitionSaveView,
    RequisitionStatusUpdateView,
    TicketCreateView,
    TicketDetailView,
    TicketListView,
    TicketPendingCreateTicketView,
    TicketPendingDeleteView,
    TicketPendingListView,
    TicketTimerActionView,
)

urlpatterns = [
    path('', TicketListView.as_view(), name='chamados_list'),
    path('novo/', TicketCreateView.as_view(), name='chamados_new'),
    path('requisicoes/', RequisitionHubView.as_view(), name='chamados_requisicoes'),
    path('requisicoes/salvar/', RequisitionSaveView.as_view(), name='chamados_requisicoes_save'),
    path('requisicoes/<int:requisition_id>/status/', RequisitionStatusUpdateView.as_view(), name='chamados_requisicoes_status'),
    path('pendencias/', TicketPendingListView.as_view(), name='chamados_pending_list'),
    path('pendencias/<int:pending_id>/apagar/', TicketPendingDeleteView.as_view(), name='chamados_pending_delete'),
    path('pendencias/<int:pending_id>/criar-chamado/', TicketPendingCreateTicketView.as_view(), name='chamados_pending_create_ticket'),
    path('<int:ticket_id>/', TicketDetailView.as_view(), name='chamados_detail'),
    path('<int:ticket_id>/atendimento/', TicketTimerActionView.as_view(), name='chamados_action'),
]
