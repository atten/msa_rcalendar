from rest_framework import serializers
from .models import Organization, Interval, ResourceMembership, ScheduleInterval
from .fields import MsaIdRelatedField


class ScheduleIntervalSerializer(serializers.ModelSerializer):

    class Meta:
        model = ScheduleInterval
        fields = ('start', 'end', 'day_of_week')


class ResourceMembershipScheduleSerializer(serializers.ModelSerializer):
    schedule_intervals = ScheduleIntervalSerializer(many=True)

    class Meta:
        model = ResourceMembership
        fields = ('schedule_intervals',)


class ResourceMembershipShortSerializer(serializers.ModelSerializer):
    serializer_related_field = MsaIdRelatedField

    class Meta:
        model = ResourceMembership
        fields = ('resource', 'has_schedule')


class OrganizationSerializer(serializers.ModelSerializer):
    manager_ids = serializers.SerializerMethodField()
    resource_members = ResourceMembershipShortSerializer(many=True)

    @staticmethod
    def get_manager_ids(obj):
        return obj.managers.values_list('msa_id', flat=True)

    class Meta:
        model = Organization
        fields = ('manager_ids', 'resource_members')


class IntervalSerializer(serializers.ModelSerializer):
    serializer_related_field = MsaIdRelatedField

    class Meta:
        model = Interval
        fields = ('id', 'start', 'end', 'kind', 'resource', 'organization', 'manager', 'comment')

    def to_representation(self, instance):
        """добавляет к представлению kind в виде строки и объект, если есть"""
        ret = super().to_representation(instance)
        ret['kind'] = instance.get_kind_display()
        obj_id = instance.get_object(msa_id_only=True)
        if obj_id:
            ret['object'] = obj_id
        return ret
