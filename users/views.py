from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.contrib.auth.views import LogoutView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import RedirectView

from .forms import SidertecAuthenticationForm


class SidertecLoginView(LoginView):
    template_name = 'users/login.html'
    authentication_form = SidertecAuthenticationForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        messages.error(self.request, 'Nao foi possivel autenticar no AD. Verifique usuario e senha.')
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse_lazy('chamados_list')


class SidertecLogoutView(LogoutView):
    next_page = reverse_lazy('login')


class LoginSuccessView(LoginRequiredMixin, RedirectView):
    permanent = False
    pattern_name = 'chamados_list'
