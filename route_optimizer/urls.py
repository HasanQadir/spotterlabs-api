from django.urls import path
from .views import RouteViewerView

urlpatterns = [
    path("route/view/", RouteViewerView.as_view(), name="route-viewer"),
]
