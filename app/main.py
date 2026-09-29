"""FastAPI 应用：字幕时间轴三方合并服务。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .insert import insert_cue
from .merge import find_overlaps, resolve_three_way, three_way_merge
from .models import (
    HealthResponse,
    InsertFailureResponse,
    InsertParamErrorResponse,
    InsertRequest,
    InsertSuccessResponse,
    MergeConflictResponse,
    MergeOverlapResponse,
    MergeRequest,
    MergeSuccessResponse,
    ResolveDecisionErrorResponse,
    ResolveRequest,
    ResolveSuccessResponse,
    ResolveTimelineErrorResponse,
    RetimeErrorResponse,
    RetimeRequest,
    RetimeSuccessResponse,
    SpliceParamErrorResponse,
    SpliceRequest,
    SpliceSuccessResponse,
)
from .retime import retime_cues
from .splice import splice_cues

app = FastAPI(
    title="字幕时间轴三方合并 API",
    description=(
        "POST /merge 接收 base / current / incoming 三版 cue 列表，"
        "按 cue id 进行三方合并、字段级冲突检测与时间轴重叠检测；"
        "POST /resolve 在同一三方合并之上应用人工冲突决策，"
        "输出解决后的时间轴；"
        "POST /retime 按 source/target 时间锚点对 cue 时间轴做分段线性校时；"
        "POST /splice 按选区列表从现有 cue 时间轴剪取字幕，"
        "并按选区顺序拼接成一条新时间轴；"
        "POST /insert 把一条新 cue 插入现有时间轴，"
        "碰撞的后续字幕顺延，锁定 cue 不允许被移动。"
    ),
    version=__version__,
)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """服务根路径，指向交互式文档。"""
    return {"service": "subtitle-merge", "version": __version__, "docs": "/docs"}


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """健康检查。"""
    return HealthResponse(status="ok")


@app.post(
    "/merge",
    tags=["merge"],
    response_model=MergeSuccessResponse,
    responses={
        409: {
            "model": MergeConflictResponse | MergeOverlapResponse,
            "description": (
                "合并冲突（字段级冲突、删除/修改冲突等），或无冲突但时间轴重叠。"
                "两种情况下均不输出合并版本。"
            ),
        }
    },
)
def merge(request: MergeRequest):
    """三方合并字幕 cue 列表。"""
    merged, cue_conflicts, field_conflicts = three_way_merge(
        request.base, request.current, request.incoming
    )

    if cue_conflicts or field_conflicts:
        content = MergeConflictResponse(
            cue_conflicts=cue_conflicts,
            field_conflicts=field_conflicts,
        ).model_dump()
        return JSONResponse(status_code=409, content=content)

    overlaps = find_overlaps(merged)
    if overlaps:
        content = MergeOverlapResponse(overlaps=overlaps).model_dump()
        return JSONResponse(status_code=409, content=content)

    merged.sort(key=lambda cue: (cue.start, cue.end, cue.id))
    return MergeSuccessResponse(cues=merged, count=len(merged))


@app.post(
    "/resolve",
    tags=["resolve"],
    response_model=ResolveSuccessResponse,
    responses={
        422: {
            "model": ResolveDecisionErrorResponse,
            "description": (
                "决策无效：重复、未知、类型不匹配（字段/cue 决策混用）或遗漏了冲突；"
                "此时不应用任何决策、不输出部分结果。"
                "请求体结构校验失败同样返回 422。"
            ),
        },
        409: {
            "model": ResolveTimelineErrorResponse,
            "description": (
                "应用全部决策后统一校验未通过：形成了非法区间（end <= start）"
                "或时间轴存在重叠；不输出部分结果。"
            ),
        },
    },
)
def resolve(request: ResolveRequest):
    """三方合并后按人工决策解决冲突，返回 resolved 时间轴。"""
    field_choices = [
        (item.cue_id, item.field, item.choice) for item in request.field_choices
    ]
    cue_choices = [(item.cue_id, item.choice) for item in request.cue_choices]

    result = resolve_three_way(
        request.base,
        request.current,
        request.incoming,
        field_choices,
        cue_choices,
    )

    if result.errors:
        content = ResolveDecisionErrorResponse(errors=result.errors).model_dump()
        return JSONResponse(status_code=422, content=content)

    if result.invalid_intervals or result.overlaps:
        content = ResolveTimelineErrorResponse(
            invalid_intervals=result.invalid_intervals,
            overlaps=result.overlaps,
        ).model_dump()
        return JSONResponse(status_code=409, content=content)

    return ResolveSuccessResponse(cues=result.resolved, count=len(result.resolved))


@app.post(
    "/retime",
    tags=["retime"],
    response_model=RetimeSuccessResponse,
    responses={
        409: {
            "model": RetimeErrorResponse,
            "description": (
                "某条 cue 的映射起点或终点为负，或映射后区间非法（end <= start）；"
                "返回全部相关 cue id，不输出部分结果。"
            ),
        }
    },
)
def retime(request: RetimeRequest):
    """按 source/target 时间锚点对 cue 时间轴做分段线性校时。"""
    result = retime_cues(request.cues, request.anchors)

    if result.cue_ids:
        content = RetimeErrorResponse(cue_ids=result.cue_ids).model_dump()
        return JSONResponse(status_code=409, content=content)

    return RetimeSuccessResponse(cues=result.cues, count=len(result.cues))


@app.post(
    "/splice",
    tags=["splice"],
    response_model=SpliceSuccessResponse,
    responses={
        422: {
            "model": SpliceParamErrorResponse,
            "description": (
                "源时间轴参数错误：源 cue id 重复或源时间轴存在重叠；"
                "不输出部分结果。请求体结构校验失败（选区端点为负、"
                "end <= start、cue 本身非法等）同样返回 422。"
            ),
        }
    },
)
def splice(request: SpliceRequest):
    """按选区列表从现有 cue 时间轴剪取字幕并顺序拼接成新时间轴。"""
    result = splice_cues(request.cues, request.selections)

    if result.duplicated_ids or result.overlaps:
        content = SpliceParamErrorResponse(
            duplicated_ids=result.duplicated_ids,
            overlaps=result.overlaps,
        ).model_dump()
        return JSONResponse(status_code=422, content=content)

    return SpliceSuccessResponse(
        cues=result.cues, count=len(result.cues), duration=result.duration
    )


@app.post(
    "/insert",
    tags=["insert"],
    response_model=InsertSuccessResponse,
    responses={
        422: {
            "model": InsertParamErrorResponse,
            "description": (
                "参数无效：源 cue id 重复、源时间轴重叠、新 cue id 已存在，"
                "或锁定 id 不属于源列表；不输出部分结果。"
                "请求体结构校验失败（cue 本身 end <= start、端点为负等）同样返回 422。"
            ),
        },
        409: {
            "model": InsertFailureResponse,
            "description": (
                "插入失败：起点更早的源 cue 与新 cue 相交（无法通过顺延解决），"
                "或顺延碰撞要求某个锁定 cue 移动；不输出部分结果。"
            ),
        },
    },
)
def insert(request: InsertRequest):
    """把一条新 cue 插入现有时间轴，碰撞的后续 cue 顺延。"""
    result = insert_cue(request.cues, request.new, request.locked)

    if (
        result.duplicated_ids
        or result.overlaps
        or result.existing_id is not None
        or result.unknown_locked
    ):
        content = InsertParamErrorResponse(
            duplicated_ids=result.duplicated_ids,
            overlaps=result.overlaps,
            existing_id=result.existing_id,
            unknown_locked=result.unknown_locked,
        ).model_dump()
        return JSONResponse(status_code=422, content=content)

    if result.reason is not None:
        content = InsertFailureResponse(
            reason=result.reason,
            cue_id=result.cue_id,
            locked_id=result.locked_id,
        ).model_dump()
        return JSONResponse(status_code=409, content=content)

    return InsertSuccessResponse(
        cues=result.cues, moved=result.moved, count=len(result.cues)
    )
