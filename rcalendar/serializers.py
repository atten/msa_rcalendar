from rest_framework import serializers
from .models import Organization, Manager, Resource, Interval, ResourceScheduleInterval
from .fields import MsaIdRelatedField


class OrganizationSerializer(serializers.ModelSerializer):
    manager_ids = serializers.SerializerMethodField()
    fulltime_resource_ids = serializers.SerializerMethodField()
    parttime_resource_ids = serializers.SerializerMethodField()

    @staticmethod
    def get_manager_ids(obj):
        return obj.managers.values_list('msa_id', flat=True)

    @staticmethod
    def get_fulltime_resource_ids(obj):
        return obj.get_resource_ids(fulltime=True, parttime=False, msa_ids=True)

    @staticmethod
    def get_parttime_resource_ids(obj):
        return obj.get_resource_ids(fulltime=False, parttime=True, msa_ids=True)

    class Meta:
        model = Organization
        fields = ('manager_ids', 'fulltime_resource_ids', 'parttime_resource_ids')


class ManagerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Manager


class IntervalSerializer(serializers.ModelSerializer):
    serializer_related_field = MsaIdRelatedField

    class Meta:
        model = Interval
        fields = ('id', 'start', 'end', 'kind', 'resource', 'organization', 'manager', 'comment', 'is_extendable')

    def to_representation(self, instance):
        """добавляет к представлению kind в виде строки и объект, если есть"""
        ret = super().to_representation(instance)
        ret['kind'] = instance.get_kind_display()
        obj_id = instance.get_object(msa_id_only=True)
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
