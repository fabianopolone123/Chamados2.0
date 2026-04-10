from cofre.services import user_can_access_vault


def app_shell(request):
    user = getattr(request, 'user', None)
    is_authenticated = bool(user and getattr(user, 'is_authenticated', False))
    can_access_vault = user_can_access_vault(user) if is_authenticated else False
    is_superuser = bool(is_authenticated and getattr(user, 'is_superuser', False))
    return {
        'app_can_access_vault': can_access_vault,
        'app_is_superuser': is_superuser,
    }
