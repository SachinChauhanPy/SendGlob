from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = 'billing'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    # The standalone pack/plan listings were replaced by the public /pricing/
    # page (single pricing source of truth). Permanent redirects preserve any
    # bookmarked or external links; checkout endpoints stay here.
    path('packs/', RedirectView.as_view(pattern_name='pages:pricing', permanent=True),
         name='packs'),
    path('packs/<int:pk>/checkout/', views.checkout_pack, name='checkout_pack'),
    path('plans/', RedirectView.as_view(pattern_name='pages:pricing', permanent=True),
         name='plans'),
    path('plans/<int:pk>/checkout/', views.checkout_plan, name='checkout_plan'),
    path('checkout/complete/', views.checkout_complete, name='checkout_complete'),
    path('history/', views.history, name='history'),
    path('receipts/<int:pk>/download/', views.receipt_download, name='receipt_download'),
    path('subscription/', views.manage_subscription, name='manage_subscription'),
    path('subscription/cancel/', views.cancel_subscription, name='cancel_subscription'),
    path('subscription/change/<int:pk>/', views.change_plan, name='change_plan'),
    path('subscription/change/cancel/', views.cancel_plan_change, name='cancel_plan_change'),
    path('profile/', views.profile, name='profile'),
    path('webhook/razorpay/', views.razorpay_webhook, name='razorpay_webhook'),
]
