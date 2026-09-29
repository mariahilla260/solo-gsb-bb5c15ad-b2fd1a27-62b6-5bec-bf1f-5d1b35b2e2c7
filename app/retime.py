"""按 source/target 时间锚点对 cue 时间轴做分段线性校时。

锚点以 source 升序给出，定义源时间轴到目标时间轴的映射：

* source 时间落在某对相邻锚点区间内时，按该区间两端做线性插值；
* 早于第一个锚点或晚于最后一个锚点时，沿最近的（首段/末段）区间
  线性外推，因此外推结果可能为负；
* source 恰好落在锚点位置时，精确映射到对应 target；
* 每条 cue 的 start、end 相互独立地计算，因此一条 cue 跨越锚点时，
  其两端可以分别取自不同区间。

对所有 cue 统一计算完成后再统一校验：只要任一映射时间为负或某条 cue
形成非法区间（mapped_end <= mapped_start），即收集全部相关 cue id，
不输出部分结果。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from .models import Cue, TimeAnchor


@dataclass(frozen=True)
class _AnchorTable:
    """已排序的锚点表及其 source → target 映射。"""

    sources: list[float]
    targets: list[float]
    exact: dict[float, float]

    @classmethod
    def from_anchors(cls, anchors: list[TimeAnchor]) -> "_AnchorTable":
        ordered = sorted(anchors, key=lambda anchor: anchor.source)
        sources = [anchor.source for anchor in ordered]
        targets = [anchor.target for anchor in ordered]
        return cls(sources, targets, dict(zip(sources, targets)))

    def map_time(self, t: float) -> float:
        """把一个 source 时间映射到 target 时间（插值/外推）。"""
        if t in self.exact:
            # 命中锚点：精确取对应 target，避免浮点误差
            return self.exact[t]

        pos = bisect_left(self.sources, t)
        if pos == 0:
            # 早于首锚点：沿首段区间外推
            idx0, idx1 = 0, 1
        elif pos == len(self.sources):
            # 晚于末锚点：沿末段区间外推
            idx0, idx1 = len(self.sources) - 2, len(self.sources) - 1
        else:
            idx0, idx1 = pos - 1, pos

        s0, s1 = self.sources[idx0], self.sources[idx1]
        t0, t1 = self.targets[idx0], self.targets[idx1]
        ratio = (t - s0) / (s1 - s0)
        return t0 + ratio * (t1 - t0)


@dataclass
class RetimeResult:
    """retime_cues 的结果。

    cue_ids 非空表示存在映射为负或区间非法的 cue，
    此时 cues 为空、不输出部分结果。
    """

    cues: list[Cue] = field(default_factory=list)
    cue_ids: list[str] = field(default_factory=list)


def retime_cues(cues: list[Cue], anchors: list[TimeAnchor]) -> RetimeResult:
    """按锚点对每条 cue 的 start/end 独立做分段线性校时。

    id、text、speaker 原样保留；输出保持输入顺序。
    任一映射时间为负（< 0）或形成非法区间（end <= start）时，
    返回全部相关 cue id（按输入顺序去重），cues 为空。
    """
    table = _AnchorTable.from_anchors(anchors)

    mapped: list[tuple[Cue, float, float]] = []
    for cue in cues:
        mapped.append((cue, table.map_time(cue.start), table.map_time(cue.end)))

    bad_ids: list[str] = []
    for cue, new_start, new_end in mapped:
        is_bad = new_start < 0 or new_end < 0 or new_end <= new_start
        if is_bad and cue.id not in bad_ids:
            bad_ids.append(cue.id)
    if bad_ids:
        return RetimeResult(cues=[], cue_ids=bad_ids)

    retimed = [
        Cue(
            id=cue.id,
            start=new_start,
            end=new_end,
            text=cue.text,
            speaker=cue.speaker,
        )
        for cue, new_start, new_end in mapped
    ]
    return RetimeResult(cues=retimed, cue_ids=[])
