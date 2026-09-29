"""把一条新 cue 插入现有时间轴，并让碰撞的后续字幕顺延。

规则：

* 源 cue id 必须唯一、源时间轴不得重叠（首尾相接允许）；新 cue 的 id
  不得已存在；锁定 id 必须属于源列表。以上任一不满足即参数无效，
  收集全部问题、不输出部分结果。
* 源 cue 按起点排序（平局依次按 end、id）后，在所有起点不小于新 cue
  起点的源 cue（“后续 cue”）之前插入新 cue；更早的源 cue 保持原位。
* 若某个更早的源 cue 与新 cue 严格相交（重叠时长 > 0，仅首尾相接不算），
  整次拒绝——更早的 cue 不允许移动，新 cue 无法插入。
* 后续 cue 保持先后顺序与各自时长，仅在与前一条（先处理新 cue）
  严格相交时整体右移到前一条的 end；碰撞因此逐条向后传递。
* 若顺延要求某个锁定 cue 移动，返回这些锁定 id（按时间轴顺序、去重），
  不输出部分结果。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from .merge import find_overlaps
from .models import Cue, MovedCue, Overlap


@dataclass
class InsertResult:
    """insert_cue 的结果（以下字段按结果类型填充）。

    参数无效：duplicated_ids / overlaps / existing_id / unknown_locked
    中至少一个非空（或 existing_id 非 None），其余为空；
    更早 cue 相交：preceding_overlap 非 None；
    锁定 cue 需要移动：locked_ids 非空；
    全部成功：cues / moved 有值。
    任何失败情况下 cues 与 moved 均为空，不输出部分结果。
    """

    cues: list[Cue] = field(default_factory=list)
    moved: list[MovedCue] = field(default_factory=list)
    duplicated_ids: list[str] = field(default_factory=list)
    overlaps: list = field(default_factory=list)
    existing_id: str | None = None
    unknown_locked: list[str] = field(default_factory=list)
    preceding_overlap: Overlap | None = None
    locked_ids: list[str] = field(default_factory=list)

    @property
    def param_error(self) -> bool:
        return bool(
            self.duplicated_ids
            or self.overlaps
            or self.existing_id is not None
            or self.unknown_locked
        )


def _duplicate_ids(cues: list[Cue]) -> list[str]:
    """按排序顺序返回出现超过一次的全部 cue id（去重）。"""
    ids = [cue.id for cue in cues]
    return sorted({cue_id for cue_id in ids if ids.count(cue_id) > 1})


def insert_cue(
    cues: list[Cue], new_cue: Cue, locked_ids: list[str]
) -> InsertResult:
    """把 new_cue 插入 cues，碰撞的后续 cue 顺延；locked_ids 不可移动。

    先统一做参数校验（源 id 唯一、源轴不重叠、新 id 不存在、锁定 id
    均属于源列表），任一不过即收集全部问题返回；随后检查更早 cue 是否
    与新 cue 相交；再按“仅在与前一条相交时右移到前一条 end”的规则
    逐条顺延后续 cue，若需移动任何锁定 cue 则收集其 id、不输出结果。
    """
    # —— 参数校验（收集全部问题）——
    duplicated = _duplicate_ids(cues)
    overlaps = [] if duplicated else find_overlaps(cues)

    existing_id = new_cue.id if any(cue.id == new_cue.id for cue in cues) else None

    source_ids = {cue.id for cue in cues}
    unknown_locked = sorted({cue_id for cue_id in locked_ids if cue_id not in source_ids})

    if duplicated or overlaps or existing_id is not None or unknown_locked:
        return InsertResult(
            duplicated_ids=duplicated,
            overlaps=overlaps,
            existing_id=existing_id,
            unknown_locked=unknown_locked,
        )

    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.id))
    starts = [cue.start for cue in ordered]
    split = bisect_left(starts, new_cue.start)
    preceding = ordered[:split]
    following = ordered[split:]
    locked = set(locked_ids)

    # —— 更早的 cue 保持原位；与新 cue 严格相交即整次拒绝 ——
    if preceding:
        last = preceding[-1]
        overlap_start = max(last.start, new_cue.start)
        overlap_end = min(last.end, new_cue.end)
        if overlap_end > overlap_start:
            return InsertResult(
                preceding_overlap=Overlap(
                    cue_ids=[last.id, new_cue.id],
                    start=overlap_start,
                    end=overlap_end,
                    overlap=overlap_end - overlap_start,
                )
            )

    # —— 后续 cue 保持先后与时长，碰撞逐条右移 ——
    prev_end = new_cue.end
    result_following: list[Cue] = []
    moved: list[MovedCue] = []
    moved_locked: list[str] = []

    for cue in following:
        new_start = cue.start
        if cue.start < prev_end:
            # 与前一条严格相交（首尾相接 cue.start == prev_end 不移动）：
            # 整体右移，时长不变
            new_start = prev_end
        if new_start != cue.start:
            duration = cue.end - cue.start
            new_end = new_start + duration
            moved.append(
                MovedCue(
                    id=cue.id,
                    original_start=cue.start,
                    original_end=cue.end,
                    start=new_start,
                    end=new_end,
                )
            )
            if cue.id in locked and cue.id not in moved_locked:
                moved_locked.append(cue.id)
            result_following.append(
                Cue(
                    id=cue.id,
                    start=new_start,
                    end=new_end,
                    text=cue.text,
                    speaker=cue.speaker,
                )
            )
            prev_end = new_end
        else:
            # 未与前一条相交：碰撞链到此结束，后续 cue 不再可能需要移动
            result_following.append(cue.model_copy(deep=True))
            prev_end = cue.end

    if moved_locked:
        return InsertResult(locked_ids=moved_locked)

    final_timeline = [
        *[cue.model_copy(deep=True) for cue in preceding],
        new_cue.model_copy(deep=True),
        *result_following,
    ]
    return InsertResult(cues=final_timeline, moved=moved)
