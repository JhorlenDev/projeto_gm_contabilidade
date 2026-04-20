from django.urls import path

from .views import login_page, panel_page

urlpatterns = [
    path("", login_page, name="login"),
    path("painel/", panel_page, name="painel"),
    path("index.html", login_page, name="login-html"),
    path("painel.html", panel_page, name="painel-html"),
]
