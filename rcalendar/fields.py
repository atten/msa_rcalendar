from rest_framework.relations import PrimaryKeyRelatedField


class MsaIdRelatedField(PrimaryKeyRelatedField):

    def use_pk_only_optimization(self):
        return False

    def to_internal_value(self, data):
        try:
            return self.get_queryset().get(msa_id=data, app=self.context['request'].app)
        except Exception:
            return super().to_internal_value(data)

    def to_representation(self, value):
        return value.msa_id
