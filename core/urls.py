"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from core.views import landing

# Brand the admin to match the product name (core is not an INSTALLED_APP, so an
# admin.py here would not be auto-discovered; setting it on import is reliable).
admin.site.site_header = f"{settings.APP_NAME} Administration"
admin.site.site_title = f"{settings.APP_NAME} Admin"
admin.site.index_title = f"{settings.APP_NAME} Management"

urlpatterns = [
    path('admin/', admin.site.urls),

    # Public marketing landing page - lives at / and redirects authenticated
    # users to the dashboard so they never see it again after signing in.
    path('', landing, name='landing'),

    # Public pages: pricing (and, per the public pages plan, about/security/
    # legal/contact in later phases), mounted at the root.
    path('', include('pages.urls')),

    # Authenticated app (dashboard, campaigns, templates, tracking pixel).
    # Previously mounted at '', moved under /dashboard/ so the landing page
    # can own the root URL.
    path('dashboard/', include('emails.urls')),
    path('accounts/', include('accounts.urls')),
    path('documents/', include('documents.urls')),

    # Staff-only operations console (separate from end-user routes and Django
    # admin). Non-staff users get a 404, so its existence is not advertised.
    path('ops/', include('opsconsole.urls')),

    # Billing: credits, packs, plans, receipts, and the Razorpay webhook.
    path('billing/', include('billing.urls')),

    # django-allauth social login and auth
    path('accounts/', include('allauth.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    # Dev-only convenience serving of MEDIA_URL. SECURITY: in production the web
    # server must NOT serve sensitive subtrees (signatures/, generated_documents/,
    # document_templates/) publicly - those are delivered only through the
    # ownership-checked views documents.signature_image, emails.recipient_document
    # and documents.document_template_background. Stored filenames are randomized
    # as defense in depth. See docs/media-security.md (B-047, B-049).
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error views
handler404 = 'core.views.error_404'
handler500 = 'core.views.error_500'
handler403 = 'core.views.error_403'
handler400 = 'core.views.error_400'
