from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.contrib.auth.views import LogoutView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from cofre.services import user_can_access_vault

from .forms import SidertecAuthenticationForm


class SidertecLoginView(LoginView):
    template_name = 'users/login.html'
    authentication_form = SidertecAuthenticationForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        messages.error(self.request, 'Nao foi possivel autenticar no AD. Verifique usuario e senha.')
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy('login_success')


class SidertecLogoutView(LogoutView):
    next_page = reverse_lazy('login')


class LoginSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'users/success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_access_vault'] = user_can_access_vault(self.request.user)
        context['is_superuser'] = bool(getattr(self.request.user, 'is_superuser', False))
        return context
