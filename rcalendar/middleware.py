class EventDispatchMiddleware(object):
    """
    Миддлварь, хранящий список пользовательских событий, очищающийся для каждого нового request.
    """
    events = []  # события в ожидании отправки с ответом

    @classmethod
    def process_request(cls, request):
        cls.events = []      # очищаем перед каждым ответом

    @classmethod
    def push_event_to_response(cls, **kwargs):
        cls.events.append(kwargs)
