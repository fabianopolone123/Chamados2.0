from django.urls import path

from .views import TicketCreateView, TicketDetailView, TicketListView, TicketTriageView

urlpatterns = [
    path('', TicketListView.as_view(), name='chamados_list'),
    path('novo/', TicketCreateView.as_view(), name='chamados_new'),
    path('<int:ticket_id>/', TicketDetailView.as_view(), name='chamados_detail'),
    path('<int:ticket_id>/atendimento/', TicketTriageView.as_view(), name='chamados_update'),
]
