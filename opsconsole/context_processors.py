"""Expose the active impersonation session to all templates so a persistent
banner can warn the operator they are viewing as another user."""


def impersonation(request):
    operator = getattr(request, 'impersonator', None)
    if not operator:
        return {'impersonation': None}
    return {'impersonation': {
        'operator_email': getattr(operator, 'email', ''),
        'target_email': getattr(getattr(request, 'user', None), 'email', ''),
    }}
