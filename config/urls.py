"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
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

# FOR DOCUMENTATION :>
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

from api.views import HealthDetailView, ReadinessView, LivenessView
urlpatterns = [
    path("healthz", LivenessView.as_view(), name="healthz"),
    path("healthz/", LivenessView.as_view(), name="healthz-slash"),
    path("health/", HealthDetailView.as_view(), name="detail"),
    path("health/live/", LivenessView.as_view(), name="live"),
    path("health/ready/", ReadinessView.as_view(), name="ready"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("api.urls")), 
    #Documentation :>
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]

"""
Swagger: http://127.0.0.1:8000/api/docs/swagger/
Redoc: http://127.0.0.1:8000/api/docs/redoc/
"""


