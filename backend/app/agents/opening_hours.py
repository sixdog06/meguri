"""OSM opening_hours 的子集解析（#6 时间校验用）。

只支持常见简单形式（覆盖大多数设施标签），复杂/无法解析一律返回 None
（= 未知，不误标）：
  "24/7"
  "Mo-Fr 09:00-17:00" / "Tu-Su 09:00-17:00" / "09:00-17:00"（无日部分=每天）
  "Mo-Fr 09:00-12:00,13:00-17:00"（多时段）
"""

import re
from datetime import time

_WEEKDAYS = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
_DAY_PART = re.compile(
    rf"^({'|'.join(_WEEKDAYS)})(?:-({'|'.join(_WEEKDAYS)}))?$"
)
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _day_matches(part: str, weekday: int) -> bool | None:
    """日部分（如 "Mo-Fr"）是否覆盖 weekday；无法解析返回 None。"""
    days = part.split(",")
    for d in days:
        m = _DAY_PART.match(d.strip())
        if m is None:
            return None
        start = _WEEKDAYS[m.group(1)]
        if m.group(2) is None:
            if weekday == start:
                return True
        else:
            end = _WEEKDAYS[m.group(2)]
            if start <= end and start <= weekday <= end:
                return True
            if start > end and (weekday >= start or weekday <= end):  # 跨周（如 Sa-Mo）
                return True
    return False


def is_open(opening_hours: str, at: time, weekday: int) -> bool | None:
    """判断某时刻是否开放；无法解析返回 None（未知）。

    weekday: 周一=0 … 周日=6（datetime.weekday() 约定）。
    """
    text = opening_hours.strip()
    if text == "24/7":
        return True
    saw_time = False
    day_excluded = False  # 有日部分规则但该日不在开放日内 → 视为闭馆
    for rule in text.split(";"):
        rule = rule.strip()
        if not rule or "off" in rule.lower():
            continue  # 闭馆规则等复杂形式不解析
        # 分离日部分与时间部分
        parts = rule.split(" ", 1)
        if len(parts) == 2 and _DAY_PART.match(parts[0].split(",")[0].strip()):
            day_part, time_part = parts
            match = _day_matches(day_part, weekday)
            if match is None:
                return None
            if not match:
                day_excluded = True
                continue
        else:
            time_part = rule  # 无日部分 = 每天
        for span in time_part.split(","):
            m = _TIME_RANGE.match(span.strip())
            if m is None:
                return None
            saw_time = True
            open_t = time(int(m.group(1)), int(m.group(2)))
            close_t = time(int(m.group(3)), int(m.group(4)))
            if open_t <= at < close_t:
                return True
    if not saw_time:
        return False if day_excluded else None
    return False
