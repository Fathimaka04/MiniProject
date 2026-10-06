"""Root URL configuration for gazeassist_web."""
from django.contrib import admin
from django.urls import include, path

from board import views

admin.site.site_header = "GazeAssist administration"
admin.site.site_title = "GazeAssist admin"
admin.site.index_title = "Manage patients, caregivers and activity"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", views.CaregiverLoginView.as_view(), name="login"),
    path("accounts/logout/", views.CaregiverLogoutView.as_view(), name="logout"),
    path("accounts/register/", views.RegisterView.as_view(), name="register"),
    path("api/", include("board.api.urls")),
    path("", include("board.urls")),
]
