class EventDispatchMiddleware(object):
    events = []  # события в ожидании отправки с ответом

    @classmethod
    def process_request(cls, request):
        cls.events = []      # очищаем перед каждым ответом

    @classmethod
    def push_event_to_responce(cls, **kwargs):
        cls.events.append(kwargs)
