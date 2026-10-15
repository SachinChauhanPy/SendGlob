from django.urls import path

from . import views

app_name = 'opsconsole'

urlpatterns = [
    # Phase A/B: read-only surfaces
    path('', views.dashboard, name='dashboard'),
    path('health/', views.health, name='health'),
    path('activity/', views.activity_monitor, name='activity'),
    path('capacity/', views.capacity, name='capacity'),
    path('audit/', views.audit, name='audit'),
    path('alerts/<int:pk>/acknowledge/', views.acknowledge_alert, name='acknowledge_alert'),

    # Phase C: global controls
    path('controls/', views.controls_home, name='controls'),
    path('controls/flag/<str:key>/', views.toggle_flag, name='toggle_flag'),
    path('controls/throttle/', views.set_throttle, name='set_throttle'),

    # Phase C: user management
    path('users/', views.users_list, name='users'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/grant/', views.grant_credits, name='grant_credits'),
    path('users/<int:pk>/active/', views.set_user_active, name='set_user_active'),
    path('users/<int:pk>/force-logout/', views.force_logout, name='force_logout'),
    path('users/<int:pk>/impersonate/', views.impersonate_start, name='impersonate_start'),
    path('impersonate/stop/', views.impersonate_stop, name='impersonate_stop'),

    # Phase C: campaign control
    path('campaigns/', views.campaigns_list, name='campaigns'),
    path('campaigns/<int:pk>/', views.campaign_detail, name='campaign_detail'),
    path('campaigns/<int:pk>/<str:action>/', views.campaign_action, name='campaign_action'),

    # Contact messages (public pages plan s.4)
    path('messages/', views.messages_list, name='messages'),
    path('messages/<int:pk>/status/', views.message_set_status, name='message_set_status'),
]
