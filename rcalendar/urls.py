from django.conf.urls import url, include
from rest_framework import routers
from . import views

router = routers.DefaultRouter()
router.register(r'organization', views.OrganizationViewSet)
router.register(r'manager', views.ManagerViewSet)
router.register(r'resource', views.ResourceViewSet)
router.register(r'interval', views.IntervalViewSet)

urlpatterns = [
    url(r'^', include(router.urls)),
]
