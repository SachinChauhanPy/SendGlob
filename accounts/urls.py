from django.urls import path
from accounts import views

app_name = 'accounts'

urlpatterns = [
    path('settings/', views.account_settings, name='settings'),
    path('smtp/', views.smtp_config_list, name='smtp_list'),
    path('smtp/new/', views.smtp_config_create, name='smtp_create'),
    path('smtp/<int:pk>/edit/', views.smtp_config_edit, name='smtp_edit'),
    path('smtp/<int:pk>/delete/', views.smtp_config_delete, name='smtp_delete'),
    path('smtp/<int:pk>/test/', views.smtp_config_test, name='smtp_test'),
]
