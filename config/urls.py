from django.contrib import admin
from django.conf import settings
from django.contrib.staticfiles.views import serve as serve_staticfiles
from django.urls import include, path
from django.conf.urls.static import static
from django.urls import re_path
from django.views.static import serve as serve_media
import sys

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("app.api_urls")),
    path("", include("app.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if not settings.DEBUG and "runserver" in sys.argv:
    urlpatterns += [
        re_path(r"^static/(?P<path>.*)$", serve_staticfiles, kwargs={"insecure": True}),
        re_path(r"^media/(?P<path>.*)$", serve_media, kwargs={"document_root": settings.MEDIA_ROOT}),
    ]
