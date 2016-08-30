from rest_framework.exceptions import ValidationError


class FormError(ValidationError):

    def __init__(self, field, text):
        field = field or 'non_field_errors'
        super().__init__({field: [text, ]})
