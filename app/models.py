"""请求与响应的 Pydantic 模型。

时间单位统一为秒（支持小数）。一条 cue 表示一个字幕区间：
``start`` 为区间起点，``end`` 为区间终点（必须大于 ``start``），
``text`` 为字幕文本，``speaker`` 为可选的说话人。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# 参与三方合并的字段（id 是合并键，不在此列）
MERGE_FIELDS: tuple[str, ...] = ("start", "end", "text", "speaker")


class Cue(BaseModel):
    """单条字幕。"""

    id: str = Field(..., min_length=1, description="字幕唯一标识，三方合并以此为准")
    start: float = Field(..., ge=0, description="开始时间（秒）")
    end: float = Field(..., ge=0, description="结束时间（秒），必须大于 start")
    text: str = Field(..., description="字幕文本")
    speaker: Optional[str] = Field(None, description="说话人（可选）")

    @model_validator(mode="after")
    def _check_interval(self) -> "Cue":
        if self.end <= self.start:
            raise ValueError("end 必须大于 start")
        return self


class MergeRequest(BaseModel):
    """POST /merge 的请求体：base / current / incoming 三版 cue 列表。"""

    base: list[Cue] = Field(default_factory=list, description="共同祖先版本")
    current: list[Cue] = Field(default_factory=list, description="当前方（我方）修订")
    incoming: list[Cue] = Field(default_factory=list, description="外部方修订")

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "MergeRequest":
        for name in ("base", "current", "incoming"):
            ids = [cue.id for cue in getattr(self, name)]
            if len(ids) != len(set(ids)):
                duplicated = sorted({cue_id for cue_id in ids if ids.count(cue_id) > 1})
                raise ValueError(f"{name} 中存在重复的 cue id: {', '.join(duplicated)}")
        return self


class CueConflict(BaseModel):
    """单条 cue 级别的冲突说明。"""

    cue_id: str
    type: Literal["delete_modify", "add_add", "invalid_interval"] = Field(
        ...,
        description=(
            "delete_modify：一侧删除另一侧修改；"
            "add_add：两侧各自新增同一 id；"
            "invalid_interval：字段可合并但合并结果不是合法区间（end <= start）"
        ),
    )
    side: Optional[Literal["current", "incoming"]] = Field(
        None, description="delete_modify 时发生删除的一侧"
    )
    fields: list[str] = Field(
        default_factory=list,
        description="add_add 时两侧取值不一致的字段",
    )
    current: Optional[Cue] = Field(None, description="current 侧该 cue 的内容（若存在）")
    incoming: Optional[Cue] = Field(None, description="incoming 侧该 cue 的内容（若存在）")


class FieldConflict(BaseModel):
    """同一 cue 存活状态下，两侧对同一字段给出不同新值的字段级冲突。"""

    cue_id: str
    field: Literal["start", "end", "text", "speaker"]
    base: Optional[object] = Field(None, description="共同祖先中的原值")
    current: Optional[object] = Field(None, description="current 侧的新值")
    incoming: Optional[object] = Field(None, description="incoming 侧的新值")


class Overlap(BaseModel):
    """一对重叠 cue 及其重叠区间。"""

    cue_ids: list[str] = Field(..., min_length=2, max_length=2)
    start: float = Field(..., description="重叠区间起点（秒）")
    end: float = Field(..., description="重叠区间终点（秒）")
    overlap: float = Field(..., description="重叠时长（秒）")


class MergeConflictResponse(BaseModel):
    """存在合并冲突时的 409 响应。"""

    status: Literal["conflict"] = "conflict"
    cue_conflicts: list[CueConflict] = Field(default_factory=list)
    field_conflicts: list[FieldConflict] = Field(default_factory=list)


class MergeOverlapResponse(BaseModel):
    """无合并冲突、但合并结果时间轴存在重叠时的 409 响应（不输出合并版本）。"""

    status: Literal["overlap"] = "overlap"
    overlaps: list[Overlap]


class MergeSuccessResponse(BaseModel):
    """合并成功：结果已按开始时间排序。"""

    status: Literal["merged"] = "merged"
    cues: list[Cue]
    count: int


class FieldChoice(BaseModel):
    """字段级冲突的人工决策：为 (cue_id, field) 选择一侧取值。"""

    cue_id: str = Field(..., min_length=1, description="存在字段级冲突的 cue id")
    field: Literal["start", "end", "text", "speaker"] = Field(
        ..., description="存在冲突的字段"
    )
    choice: Literal["base", "current", "incoming"] = Field(
        ..., description="采纳哪一侧的值"
    )


class CueChoice(BaseModel):
    """cue 级冲突的人工决策：整条 cue 保留某一侧或删除。"""

    cue_id: str = Field(..., min_length=1, description="存在 cue 级冲突的 cue id")
    choice: Literal["current", "incoming", "delete"] = Field(
        ...,
        description=(
            "current/incoming：整条 cue 采用该侧版本（若该侧已删除则等同于删除）；"
            "delete：不输出该 cue"
        ),
    )


class ResolveRequest(MergeRequest):
    """POST /resolve 的请求体：在三方合并之上携带人工决策。"""

    field_choices: list[FieldChoice] = Field(
        default_factory=list, description="字段级冲突决策（按 cue_id + field 定位）"
    )
    cue_choices: list[CueChoice] = Field(
        default_factory=list, description="cue 级冲突决策（按 cue_id 定位）"
    )


class DecisionError(BaseModel):
    """单条无法接受的决策说明（422）。"""

    cue_id: str
    field: Optional[Literal["start", "end", "text", "speaker"]] = Field(
        None, description="字段决策时的字段；cue 决策为 null"
    )
    type: Literal["duplicate", "unknown", "type_mismatch", "missing"] = Field(
        ...,
        description=(
            "duplicate：同一目标存在多条决策；"
            "unknown：决策针对的冲突不存在；"
            "type_mismatch：决策类型与冲突层级不符（字段/cue 混用）；"
            "missing：某个冲突没有给出决策"
        ),
    )
    message: str


class ResolveDecisionErrorResponse(BaseModel):
    """决策无效时的 422 响应：不应用任何决策、不输出部分结果。"""

    status: Literal["decision_error"] = "decision_error"
    errors: list[DecisionError]


class ResolveTimelineErrorResponse(BaseModel):
    """应用决策后统一校验未通过的 409 响应：不输出部分结果。"""

    status: Literal["timeline_error"] = "timeline_error"
    invalid_intervals: list[str] = Field(
        default_factory=list,
        description="应用决策后 end <= start 的 cue id 列表",
    )
    overlaps: list[Overlap] = Field(default_factory=list, description="时间轴重叠区间")


class ResolveSuccessResponse(BaseModel):
    """冲突全部解决且时间轴合法：结果已按开始时间排序。"""

    status: Literal["resolved"] = "resolved"
    cues: list[Cue]
    count: int


class TimeAnchor(BaseModel):
    """一对时间锚点：source 时间轴上的位置对应 target 时间轴上的位置（秒）。"""

    source: float = Field(..., ge=0, description="源时间轴锚点（秒），非负")
    target: float = Field(..., ge=0, description="目标时间轴锚点（秒），非负")


class RetimeRequest(BaseModel):
    """POST /retime 的请求体：cues 与至少两个 source/target 锚点。"""

    cues: list[Cue] = Field(
        default_factory=list, description="待校时 cue 列表，输出保持输入顺序"
    )
    anchors: list[TimeAnchor] = Field(
        ...,
        min_length=2,
        description=(
            "按 source 升序给出的锚点对，至少两个；"
            "source、target 两列均须非负且严格递增"
        ),
    )

    @model_validator(mode="after")
    def _check_anchors_strictly_increasing(self) -> "RetimeRequest":
        sources = [anchor.source for anchor in self.anchors]
        targets = [anchor.target for anchor in self.anchors]
        if any(curr <= prev for prev, curr in zip(sources, sources[1:])):
            raise ValueError("anchors 的 source 必须严格递增（按升序提供，不得相等或重复）")
        if any(curr <= prev for prev, curr in zip(targets, targets[1:])):
            raise ValueError("anchors 的 target 必须严格递增（不得相等或重复）")
        return self


class RetimeErrorResponse(BaseModel):
    """校时后出现负时间或非法区间时的 409 响应：不输出部分结果。"""

    status: Literal["retime_error"] = "retime_error"
    cue_ids: list[str] = Field(
        ...,
        description=(
            "映射后起点/终点为负、或区间非法（end <= start）的全部相关 cue id"
            "（按输入中的首次出现顺序）"
        ),
    )


class RetimeSuccessResponse(BaseModel):
    """校时成功：cues 保持输入顺序返回。"""

    status: Literal["retimed"] = "retimed"
    cues: list[Cue]
    count: int


class Selection(BaseModel):
    """源时间轴上的一个闭开时间选区 [start, end)（秒）。

    选区可乱序、重叠或重复，按请求列表中的顺序依次剪取与拼接；
    端点非负且 end 必须大于 start。
    """

    start: float = Field(..., ge=0, description="选区起点（秒），非负")
    end: float = Field(..., ge=0, description="选区终点（秒），必须大于 start")

    @model_validator(mode="after")
    def _check_interval(self) -> "Selection":
        if self.end <= self.start:
            raise ValueError("selection 的 end 必须大于 start")
        return self


class SpliceRequest(BaseModel):
    """POST /splice 的请求体：源 cue 列表与按序拼接的选区列表（均可空）。"""

    cues: list[Cue] = Field(
        default_factory=list,
        description="源 cue 列表；id 不得重复，时间轴不得重叠",
    )
    selections: list[Selection] = Field(
        default_factory=list,
        description=(
            "选区列表，按列表顺序拼接（可乱序、重叠或重复）；"
            "空列表返回空 cue 列表与零时长"
        ),
    )


class SplicedCue(Cue):
    """剪取拼接到新时间轴上的一条 cue 片段。

    在 Cue 的 start/end/text/speaker 之外保留溯源信息：
    ``source_id`` 为来源原 cue 的 id，``selection_index`` 为来源选区
    在请求列表中的下标（从 0 开始）。``id`` 为新生成的唯一 id，
    同一原 cue 被多个选区命中时各片段 id 不同。
    """

    id: str = Field(
        ...,
        min_length=1,
        description="生成的新 cue id，格式为 {原 cue id}#{选区下标}，全局唯一",
    )
    source_id: str = Field(..., description="该片段来源的原 cue id")
    selection_index: int = Field(
        ..., ge=0, description="来源选区在请求 selections 中的下标（从 0 开始）"
    )


class SpliceSuccessResponse(BaseModel):
    """剪取拼接成功：cues 已按新时间轴的开始时间排序。"""

    status: Literal["spliced"] = "spliced"
    cues: list[SplicedCue]
    count: int
    duration: float = Field(
        ...,
        ge=0,
        description=(
            "拼接后新时间轴总时长（秒），等于各选区时长之和；"
            "选区中的空白（未命中任何 cue 的时间）同样计入"
        ),
    )


class SpliceParamErrorResponse(BaseModel):
    """源 cue id 重复或源时间轴重叠时的 422 响应：不输出部分结果。"""

    status: Literal["splice_error"] = "splice_error"
    duplicated_ids: list[str] = Field(
        default_factory=list,
        description="源时间轴中重复的全部 cue id（排序去重）",
    )
    overlaps: list[Overlap] = Field(
        default_factory=list, description="源时间轴上的全部重叠区间"
    )


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: Literal["ok"] = "ok"
