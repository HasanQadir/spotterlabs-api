from django.urls import path
from .views import RouteView, RouteMapView, RouteViewerView

urlpatterns = [
    path("route/", RouteView.as_view(), name="route"),
    path("route/map/", RouteMapView.as_view(), name="route-map"),
    path("route/view/", RouteViewerView.as_view(), name="route-viewer"),
]
