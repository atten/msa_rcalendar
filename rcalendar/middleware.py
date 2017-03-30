class EventDispatchMiddleware:
    """
    Хранит список пользовательских событий.
    """
    events = []  # события в ожидании отправки с ответом

    @classmethod
    def process_request(cls, request):
        """Очищает список событий перед каждым ответом."""
        cls.events = []      #

    @classmethod
    def push_event_to_response(cls, **kwargs):
        """Добавляет словарь kwargs в список пользовательских событий."""
        cls.events.append(kwargs)
