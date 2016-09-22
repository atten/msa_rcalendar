from rest_framework import permissions
from .models import ApiKey, Manager, Resource


class HasValidApiKey(permissions.BasePermission):
    def has_permission(self, request, view):
        try:
            val = request.GET.get('api_key') or request.data.get('api_key')
            app = ApiKey.objects.get(key=val, is_active=True).app
            request.app = app       # put found app to request
            return True
        except (ApiKey.DoesNotExist, ValueError):
            return False


class IntervalPermission(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        author_msa_id = request.GET.get('author_id')
        if not author_msa_id:
            return False

        manager_qs = Manager.objects.filter(msa_id=author_msa_id, app=request.app)       # maybe author is manager?
        if manager_qs.exists():
            manager = manager_qs[0]
            if obj.manager == manager:
                return True

        resource_qs = Resource.objects.filter(msa_id=author_msa_id, app=request.app)       # maybe author is resource?
        if resource_qs.exists():
            resource = resource_qs[0]
            if obj.resource == resource and obj.kind == obj.Kind_Unavailable:
                return True

        return False
