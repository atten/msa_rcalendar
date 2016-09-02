from rest_framework import serializers
from .models import Organization, Manager, Resource, Interval, ResourceScheduleInterval


class OrganizationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Organization
        fields = ('id', 'managers', 'fulltime_resources', 'parttime_resources')


class ManagerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Manager


class IntervalSerializer(serializers.ModelSerializer):

    class Meta:
        model = Interval
        fields = ('id', 'start', 'end', 'kind', 'resource', 'organization', 'manager', 'comment', 'is_extendable')

    def to_representation(self, instance):
        """добавляет к представлению kind в виде строки и объект, если есть"""
        ret = super().to_representation(instance)
        ret['kind'] = instance.get_kind_display()
        obj_id = instance.get_object(id_only=True)
        if obj_id:
            ret['object'] = obj_id
        return ret


class ResourceScheduleIntervalSerializer(serializers.ModelSerializer):

    class Meta:
        model = ResourceScheduleInterval
        fields = ('start', 'end', 'day_of_week')


class ResourceSerializer(serializers.ModelSerializer):

    class Meta:
        model = Resource


class ResourceScheduleSerializer(serializers.ModelSerializer):
    schedule_intervals = ResourceScheduleIntervalSerializer(many=True)

    class Meta:
        model = Resource
        fields = ('schedule_intervals',)
