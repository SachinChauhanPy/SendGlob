from django.urls import path
from documents import views

app_name = 'documents'

urlpatterns = [
    path('templates/', views.document_list, name='list'),
    path('templates/new/', views.document_create, name='create'),
    path('templates/<int:pk>/edit/', views.document_edit, name='edit'),
    path('templates/<int:pk>/delete/', views.document_delete, name='delete'),
    path('templates/<int:pk>/preview/', views.document_preview, name='preview'),
    path('templates/<int:pk>/fields/', views.document_template_fields, name='template_fields'),
    path('templates/<int:pk>/background/', views.document_template_background, name='template_background'),
    path('campaign/new/', views.document_campaign_create, name='campaign_create'),

    path('signatures/', views.signature_list, name='signature_list'),
    path('signatures/new/', views.signature_create, name='signature_create'),
    path('signatures/<int:pk>/', views.signature_detail, name='signature_detail'),
    path('signatures/<int:pk>/image/<str:variant>/', views.signature_image, name='signature_image'),
    path('signatures/<int:pk>/edit/', views.signature_edit, name='signature_edit'),
    path('signatures/<int:pk>/save-edit/', views.signature_save_edit, name='signature_save_edit'),
    path('signatures/<int:pk>/reprocess/', views.signature_reprocess, name='signature_reprocess'),
    path('signatures/<int:pk>/delete/', views.signature_delete, name='signature_delete'),
]
