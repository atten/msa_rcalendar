from rest_framework import permissions
from django.conf import settings
from .models import ApiKey, Manager, Resource


class HasValidApiKey(permissions.BasePermission):
    def has_permission(self, request, view):
        if settings.DEBUG:
            return True
        val = request.GET.get('api_key') or request.data.get('api_key')
        return ApiKey.objects.filter(key=val, is_active=True).exists()


class IntervalPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        author_id = request.GET.get('author_id')
        if not author_id:
            return False

        manager_qs = Manager.objects.filter(id=author_id)       # maybe author is manager?
        if manager_qs.exists():
            manager = manager_qs[0]
            if obj.manager == manager:
                return True
            if obj.organization in manager.organizations.all():
                return True

        resource_qs = Resource.objects.filter(id=author_id)       # maybe author is resource?
        if resource_qs.exists():
            resource = resource_qs[0]
            if obj.resource == resource and not obj.manager_id:
                return True

        return False
