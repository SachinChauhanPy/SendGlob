from django.urls import path
from emails import views

app_name = 'emails'

urlpatterns = [
    # Dashboard
    path('', views.dashboard, name='dashboard'),
    
    # Campaigns
    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/new/', views.campaign_create, name='campaign_create'),
    path('campaigns/<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:pk>/edit/', views.campaign_edit, name='campaign_edit'),
    path('campaigns/<int:pk>/status/', views.campaign_status_api, name='campaign_status_api'),
    path('campaigns/<int:pk>/queue/', views.campaign_queue, name='campaign_queue'),
    path('campaigns/<int:pk>/cancel/', views.campaign_cancel, name='campaign_cancel'),
    path('campaigns/<int:pk>/unschedule/', views.campaign_unschedule, name='campaign_unschedule'),
    path('campaigns/<int:pk>/restart/', views.campaign_restart, name='campaign_restart'),
    path('campaigns/<int:pk>/delete/', views.campaign_delete, name='campaign_delete'),
    path('recipients/<int:pk>/document/', views.recipient_document, name='recipient_document'),

    # Templates
    path('templates/', views.template_list, name='template_list'),
    path('templates/new/', views.template_create, name='template_create'),
    path('templates/<int:pk>/edit/', views.template_edit, name='template_edit'),
    path('templates/<int:pk>/delete/', views.template_delete, name='template_delete'),
    path('templates/<int:pk>/clone/', views.template_clone, name='template_clone'),
    path('templates/preview/', views.template_preview, name='template_preview'),
    path('templates/generate-ai/', views.template_generate_ai, name='template_generate_ai'),
    
    # Tracking
    path('track/<uuid:tracking_uuid>/pixel.png', views.email_open_track, name='email_open_track'),
]
