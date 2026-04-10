from django.conf import settings


def is_ti_user(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    group_name = (getattr(settings, 'TI_GROUP_NAME', 'TI') or 'TI').strip()
    return user.groups.filter(name__iexact=group_name).exists()
