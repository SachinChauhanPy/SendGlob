from django.shortcuts import redirect, render

from core.pricing import get_public_pricing


def landing(request):
    """Public landing page at /.

    Authenticated users have no business seeing the marketing page - send them
    straight to the dashboard. Guests get the full sales pitch.
    """
    if request.user.is_authenticated:
        return redirect('emails:dashboard')
    return render(request, 'landing.html', {'pricing': get_public_pricing(request)})


def error_404(request, exception):
    response = render(request, 'errors/404.html', status=404)
    return response

def error_500(request):
    response = render(request, 'errors/500.html', status=500)
    return response

def error_403(request, exception=None):
    response = render(request, 'errors/403.html', status=403)
    return response

def error_400(request, exception=None):
    response = render(request, 'errors/400.html', status=400)
    return response
