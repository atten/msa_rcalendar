import re
from datetime import datetime, date, time, timedelta
from django.utils.timezone import get_default_timezone

from .exceptions import FormError


def datetime_from_date(d: date) -> datetime:
    """date with 00:00AM local time"""
    return datetime.combine(d, time(tzinfo=get_default_timezone()))


def parse_args(func, querydict, alloy_empty: bool, *keys: str) -> list:
    """парсит аргументы keys из querydict с помощью func (может быть parse_date, parse_time, parse_datetime)"""
    ret = []
    for key in keys:
        if alloy_empty and not querydict.get(key):
            ret.append(None)
        else:
            try:
                d = func(querydict.get(key))
                assert d is not None
                ret.append(d)
            except (TypeError, ValueError, AssertionError) as e:
                raise FormError(key, str(e))
    return ret


def str_to_int(s):
    if s is None:
        return 0
    try:
        return int(s)
    except ValueError:
        nums = re.findall(r'\d+', s)
        return int(nums[0]) if len(nums) else 0
