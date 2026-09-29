"""按选区列表从现有 Cue 时间轴剪取字幕并顺序拼接成新时间轴。

给定源 cue 列表与一组选区（闭开区间 ``[start, end)``，单位秒）：

* 对每个选区，取每条源 cue 与该选区**严格相交**（交集时长 > 0，
  仅首尾相接不算相交）的部分；跨选区边界的 cue 在边界处裁断；
* 输出时间 = 此前全部选区时长之和 + 交集起点相对本选区起点的偏移；
  选区中未命中任何 cue 的空白同样计入总时长；
* 选区可乱序、重叠或重复，严格按请求列表顺序处理；
* 每条片段保留原 ``text`` / ``speaker``，并记录原 cue id（``source_id``）
  与来源选区下标（``selection_index``，从 0 开始）；同一原 cue 被多个
  选区（或同一选区因源轴重叠，但那种情况会直接报错）命中时生成不同 id，
  格式为 ``"{原 id}#{选区下标}"``。

源时间轴参数错误（cue id 重复、源 cue 区间重叠）时收集全部错误，
不输出部分结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .merge import find_overlaps
from .models import Cue, Selection, SplicedCue


@dataclass
class SpliceResult:
    """splice_cues 的结果。

    duplicated_ids 非空或 overlaps 非空表示源时间轴参数错误，
    此时 cues 为空、duration 为 0，不输出部分结果。
    """

    cues: list[SplicedCue] = field(default_factory=list)
    duration: float = 0.0
    duplicated_ids: list[str] = field(default_factory=list)
    overlaps: list = field(default_factory=list)


def _duplicate_ids(cues: list[Cue]) -> list[str]:
    """按排序顺序返回出现超过一次的全部 cue id（去重）。"""
    ids = [cue.id for cue in cues]
    return sorted({cue_id for cue_id in ids if ids.count(cue_id) > 1})


def splice_cues(cues: list[Cue], selections: list[Selection]) -> SpliceResult:
    """按 selections 顺序从 cues 剪取并拼接 cue 片段。

    先统一校验源时间轴（重复 id / 区间重叠），任一存在即返回错误、
    不输出部分结果；否则按选区顺序逐条剪取，输出片段按新时间轴的
    开始时间排序（平局依次按新终点、原 id），并返回总时长
    （各选区时长之和，含空白）。
    """
    duplicated = _duplicate_ids(cues)
    overlaps = [] if duplicated else find_overlaps(cues)
    if duplicated or overlaps:
        return SpliceResult(duplicated_ids=duplicated, overlaps=overlaps)

    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.id))

    result: list[SplicedCue] = []
    offset = 0.0
    for index, selection in enumerate(selections):
        for cue in ordered:
            if cue.start >= selection.end:
                # 后续 cue 起点更晚，不可能再与本选区相交
                break
            clip_start = max(cue.start, selection.start)
            clip_end = min(cue.end, selection.end)
            if clip_end <= clip_start:
                # 不相交或仅首尾相接：严格相交才输出
                continue
            result.append(
                SplicedCue(
                    id=f"{cue.id}#{index}",
                    start=offset + (clip_start - selection.start),
                    end=offset + (clip_end - selection.start),
                    text=cue.text,
                    speaker=cue.speaker,
                    source_id=cue.id,
                    selection_index=index,
                )
            )
        offset += selection.end - selection.start

    result.sort(key=lambda cue: (cue.start, cue.end, cue.source_id))
    return SpliceResult(cues=result, duration=offset)
