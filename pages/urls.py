from django.urls import path

from . import views

app_name = 'pages'

urlpatterns = [
    path('pricing/', views.pricing, name='pricing'),
    path('about/', views.about, name='about'),
    path('security/', views.security, name='security'),
    path('contact/', views.contact, name='contact'),
    path('legal/terms/', views.terms, name='terms'),
    path('legal/privacy/', views.privacy, name='privacy'),
    path('legal/refund/', views.refund, name='refund'),
    path('legal/acceptable-use/', views.acceptable_use, name='acceptable_use'),
    path('robots.txt', views.robots_txt, name='robots'),
    path('sitemap.xml', views.sitemap_xml, name='sitemap'),
]
