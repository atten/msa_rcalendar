from .middleware import EventDispatchMiddleware


def append_events_data():
    """
    Декоратор для функций ViewSet'ов, добавляющий в результат Response.data
    список пользовательских событий, содержащихся в EventDispatchMiddleware.
    """
    def inner(fn):
        def inner2(view, request, *args, **kwargs):
            ret = fn(view, request, *args, **kwargs)
            d = EventDispatchMiddleware.events
            if not ret.data:
                ret.data = {}
            if d:
                ret.data['events'] = d
            return ret
        return inner2

    return inner
