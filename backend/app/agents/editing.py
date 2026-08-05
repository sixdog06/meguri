"""行程编辑（#9）：增、删、改序、换天四种操作的纯结构变换。

只改结构（不动 legs/checks/narrations——调用方随后经 revalidate
管线重建与重算）。错误分两档：UnknownSeichiError（id 不存在 → 404）、
InvalidEditError（参数非法 → 422），HTTP 状态映射在 API 边界。
"""

from typing import Literal

from pydantic import BaseModel

from app.adapters.ports import Seichi
from app.agents.planner import ItineraryDay, ItinerarySnapshot


class Edit(BaseModel):
    """一次编辑操作（API 请求体与域层共用的唯一 schema）。"""

    type: Literal["add", "remove", "reorder", "move_day"]
    seichi_id: str | None = None  # add / remove / move_day
    day: int | None = None  # add（目标天）/ reorder
    to_day: int | None = None  # move_day
    seichi_ids: list[str] | None = None  # reorder（当天全量新顺序）


class UnknownSeichiError(Exception):
    """操作引用的圣地 id 不存在（行程中或候选集中）。"""


class InvalidEditError(Exception):
    """编辑参数非法（目标天不存在、改序集合不符、重复添加等）。"""


def _require(value, message: str):
    if value is None:
        raise InvalidEditError(message)
    return value


def _find_stop(snapshot: ItinerarySnapshot, seichi_id: str) -> tuple[ItineraryDay, int]:
    for day in snapshot.days:
        for i, s in enumerate(day.seichi):
            if str(s.id) == seichi_id:
                return day, i
    raise UnknownSeichiError(f"行程中不存在圣地：{seichi_id}")


def _find_day(snapshot: ItinerarySnapshot, day_number: int) -> ItineraryDay:
    for day in snapshot.days:
        if day.day == day_number:
            return day
    raise InvalidEditError(f"不存在第 {day_number} 天")


def apply_edit(
    snapshot: ItinerarySnapshot, edit: Edit, candidates: list[Seichi]
) -> None:
    """就地应用一次编辑操作（结构变换，不含重算）。"""
    if edit.type == "remove":
        seichi_id = _require(edit.seichi_id, "remove 需要 seichi_id")
        day, i = _find_stop(snapshot, seichi_id)
        day.seichi.pop(i)

    elif edit.type == "add":
        seichi_id = _require(edit.seichi_id, "add 需要 seichi_id")
        day = _find_day(snapshot, _require(edit.day, "add 需要 day"))
        if any(str(s.id) == seichi_id for d in snapshot.days for s in d.seichi):
            raise InvalidEditError(f"圣地 {seichi_id} 已在行程中")
        candidate = next((c for c in candidates if str(c.id) == seichi_id), None)
        if candidate is None:
            raise UnknownSeichiError(f"候选集中不存在圣地：{seichi_id}")
        day.seichi.append(candidate)

    elif edit.type == "reorder":
        day = _find_day(snapshot, _require(edit.day, "reorder 需要 day"))
        ids = _require(edit.seichi_ids, "reorder 需要 seichi_ids")
        by_id = {str(s.id): s for s in day.seichi}
        if sorted(map(str, ids)) != sorted(by_id):
            raise InvalidEditError("seichi_ids 必须与当天圣地集合一致")
        day.seichi = [by_id[str(i)] for i in ids]

    elif edit.type == "move_day":
        seichi_id = _require(edit.seichi_id, "move_day 需要 seichi_id")
        to_day = _require(edit.to_day, "move_day 需要 to_day")
        source, i = _find_stop(snapshot, seichi_id)
        target = _find_day(snapshot, to_day)
        if source is target:
            raise InvalidEditError("已在目标天")
        target.seichi.append(source.seichi.pop(i))
