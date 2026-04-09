from django.urls import path

from .views import LoginSuccessView, SidertecLoginView, SidertecLogoutView

urlpatterns = [
    path('', SidertecLoginView.as_view(), name='login'),
    path('sucesso/', LoginSuccessView.as_view(), name='login_success'),
    path('logout/', SidertecLogoutView.as_view(), name='logout'),
]
