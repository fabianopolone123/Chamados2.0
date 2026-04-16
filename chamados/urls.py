from django.urls import path

from .views import (
    ClosedTicketsDataView,
    InsumosView,
    RequisitionHubView,
    RequisitionSaveView,
    RequisitionStatusUpdateView,
    TicketCreateView,
    TicketDetailView,
    TicketDeleteView,
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
    path('insumos/', InsumosView.as_view(), name='chamados_insumos'),
    path('pendencias/', TicketPendingListView.as_view(), name='chamados_pending_list'),
    path('pendencias/<int:pending_id>/apagar/', TicketPendingDeleteView.as_view(), name='chamados_pending_delete'),
    path('pendencias/<int:pending_id>/criar-chamado/', TicketPendingCreateTicketView.as_view(), name='chamados_pending_create_ticket'),
    path('fechados/dados/', ClosedTicketsDataView.as_view(), name='chamados_closed_data'),
    path('<int:ticket_id>/excluir/', TicketDeleteView.as_view(), name='chamados_delete'),
    path('<int:ticket_id>/', TicketDetailView.as_view(), name='chamados_detail'),
    path('<int:ticket_id>/atendimento/', TicketTimerActionView.as_view(), name='chamados_action'),
]
