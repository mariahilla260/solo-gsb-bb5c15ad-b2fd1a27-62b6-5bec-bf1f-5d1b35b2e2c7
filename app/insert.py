"""把一条新 cue 插入现有时间轴，并让碰撞的后续字幕顺延。

给定源 cue 列表（id 唯一、时间轴不得重叠）、一条新 cue 与一组不可移动
（锁定）的 cue id：

* 源 cue 按 ``start`` 升序（平局依次按 ``end``、``id``）排序后，新 cue
  插在所有起点不小于新起点的源 cue 之前；起点更早的源 cue 保持原位；
* 起点更早的源 cue 若与新 cue 严格相交（交集时长 > 0，首尾相接不算），
  整次插入拒绝，不输出部分结果；
* 后续 cue 保持先后顺序与自身时长，仅在与（顺延之后的）前一条严格相交时
  整体右移到前一条的 end，碰撞可沿时间轴逐条向后传递；
* 若某个锁定 cue 因此需要移动，整次插入拒绝并返回该锁定 id
  （首个需要移动的锁定 cue），不输出部分结果；锁定 cue 不需要移动时
  留在原位不受影响。

参数错误（源 cue id 重复、源时间轴重叠、新 cue id 已存在、锁定 id 不属于
源列表）单独收集，同样不输出部分结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .merge import find_overlaps
from .models import Cue, MovedCue


@dataclass
class InsertResult:
    """insert_cue 的结果。

    成功时填充 cues / moved；参数错误时填充 duplicated_ids / overlaps /
    existing_id / unknown_locked；插入被拒时填充 reason / cue_id /
    locked_id。任一错误情况下 cues 为空、不输出部分结果。
    """

    cues: list[Cue] = field(default_factory=list)
    moved: list[MovedCue] = field(default_factory=list)
    duplicated_ids: list[str] = field(default_factory=list)
    overlaps: list = field(default_factory=list)
    existing_id: str | None = None
    unknown_locked: list[str] = field(default_factory=list)
    reason: str | None = None
    cue_id: str | None = None
    locked_id: str | None = None


def _duplicate_ids(cues: list[Cue]) -> list[str]:
    """按排序顺序返回出现超过一次的全部 cue id（去重）。"""
    ids = [cue.id for cue in cues]
    return sorted({cue_id for cue_id in ids if ids.count(cue_id) > 1})


def insert_cue(cues: list[Cue], new_cue: Cue, locked: list[str]) -> InsertResult:
    """把 new_cue 插入 cues，碰撞的后续 cue 逐条顺延。

    先统一校验参数（源 id 重复、源时间轴重叠、新 id 已存在、锁定 id
    未知），任一存在即返回错误、不做插入；再处理更早 cue 与新 cue 的
    相交拒绝；最后从插入位置起逐条处理后续 cue 的碰撞右移，期间遇到
    需要移动的锁定 cue 即拒绝（返回首个此类 id）。成功时返回完整时间轴
    与每条实际移动 cue 的原/新起止时间。
    """
    duplicated = _duplicate_ids(cues)
    overlaps = [] if duplicated else find_overlaps(cues)

    source_ids = {cue.id for cue in cues}
    existing_id = new_cue.id if new_cue.id in source_ids else None
    # 锁定 id 去重后按首次出现顺序报告全部未知 id
    unknown_locked: list[str] = []
    for cue_id in dict.fromkeys(locked):
        if cue_id not in source_ids and cue_id not in unknown_locked:
            unknown_locked.append(cue_id)

    if duplicated or overlaps or existing_id is not None or unknown_locked:
        return InsertResult(
            duplicated_ids=duplicated,
            overlaps=overlaps,
            existing_id=existing_id,
            unknown_locked=unknown_locked,
        )

    locked_set = set(locked)
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.id))

    # bisect 语义：插在首个 start >= new_cue.start 的源 cue 之前
    insert_at = 0
    while insert_at < len(ordered) and ordered[insert_at].start < new_cue.start:
        insert_at += 1

    # 源时间轴不重叠：更早的 cue 中只有紧邻插入点的一条可能与新 cue 相交
    if insert_at > 0 and ordered[insert_at - 1].end > new_cue.start:
        return InsertResult(reason="overlap_previous", cue_id=ordered[insert_at - 1].id)

    timeline: list[Cue] = [
        cue.model_copy(deep=True) for cue in ordered[:insert_at]
    ]
    timeline.append(new_cue.model_copy(deep=True))

    moved: list[MovedCue] = []
    prev_end = new_cue.end
    for cue in ordered[insert_at:]:
        original = cue.model_copy(deep=True)
        new_start = cue.start
        new_end = cue.end
        if new_start < prev_end:
            # 与前一条严格相交（首尾相接 new_start == prev_end 不移动）：
            # 保持时长，整体右移到前一条 end
            shift = prev_end - new_start
            new_start = prev_end
            new_end = new_end + shift
            if cue.id in locked_set:
                return InsertResult(reason="locked_move", locked_id=cue.id)
            moved.append(
                MovedCue(
                    id=cue.id,
                    old_start=original.start,
                    old_end=original.end,
                    new_start=new_start,
                    new_end=new_end,
                )
            )
        shifted = Cue(
            id=original.id,
            start=new_start,
            end=new_end,
            text=original.text,
            speaker=original.speaker,
        )
        timeline.append(shifted)
        prev_end = shifted.end

    return InsertResult(cues=timeline, moved=moved)
