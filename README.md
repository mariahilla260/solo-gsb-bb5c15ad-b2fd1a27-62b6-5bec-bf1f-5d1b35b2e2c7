# 字幕时间轴三方合并 API

供字幕团队合并时间轴修订的 HTTP 服务。接收同一条时间轴的三个版本——
`base`（共同祖先）、`current`（我方修订）、`incoming`（对方修订）——
按 cue id 执行三方合并，检测字段级冲突、删除/修改冲突与时间轴重叠。
`POST /resolve` 进一步支持对识别出的冲突提交人工决策，输出解决后的时间轴。
`POST /retime` 则按 source/target 时间锚点对 cue 时间轴做分段线性校时（重映射）。
`POST /splice` 按一组选区从现有 cue 时间轴上剪取字幕片段，跨边界裁断后依选区顺序
（选区可乱序、重叠或重复）拼接成一条新时间轴，并保留原 id 与选区序号。
`POST /insert` 把一条新 cue 插入现有时间轴，与之碰撞的后续字幕保持时长自动顺延；
通过 `locked_ids` 声明不可移动的 cue，顺延触及锁定 cue 或更早 cue 与新 cue 相交时整次拒绝。

技术栈：Python 3.11 · FastAPI · Pydantic v2 · Uvicorn，全部依赖与启动初始化均在容器内完成。

## 一条命令启动

```bash
docker compose up --build -d
```

服务地址与端口：

- 服务地址：**http://localhost:8000**
- 健康检查：http://localhost:8000/health
- 交互式 API 文档（Swagger UI）：http://localhost:8000/docs
- OpenAPI Schema：http://localhost:8000/openapi.json

停止服务：`docker compose down`；查看日志：`docker compose logs -f`。

> 不使用 Compose 时也可：
> `docker build -t subtitle-merge . && docker run --rm -p 8000:8000 subtitle-merge`

## 接口

### GET /health

健康检查，返回 `200 {"status":"ok"}`。

### POST /merge

请求体：

| 字段       | 类型    | 说明            |
| ---------- | ------- | --------------- |
| `base`     | Cue[]   | 共同祖先版本    |
| `current`  | Cue[]   | 当前方修订      |
| `incoming` | Cue[]   | 外部方修订      |

Cue 结构（时间单位为秒，支持小数）：

```json
{"id": "cue-01", "start": 12.5, "end": 15.0, "text": "你好", "speaker": null}
```

- `id`：非空字符串，合并键；同一版本内不允许重复。
- `start` / `end`：非负数字，且 `end > start`。
- `text`：字幕文本。
- `speaker`：可选说话人；缺省与 `null` 等价。

#### 合并规则（相对 base）

1. 只有一侧修改某字段 → 采纳该侧的值；
2. 两侧都未改 → 保留 base 的值；
3. 两侧改成相同值 → 采纳该值；
4. 两侧改成不同值 → 记录**字段级冲突**（含 base/current/incoming 三侧取值）；
5. 一侧删除、另一侧修改同一 cue → 记录 **delete_modify** cue 级冲突（删除与修改不可自动调和）；一侧删除而另一侧未改时，删除直接生效；两侧都删除则干净删除；
6. 两侧各自新增同一 id：内容一致则采纳，不一致记录 **add_add** 冲突并列出差异字段；
7. 无任何冲突时，结果按 `start` 升序排序（平局依次按 `end`、`id`）；
8. 排序后检查时间轴：只要存在区间重叠（严格相交，重叠时长 > 0；仅首尾相接 `end == start` 不算重叠），返回**所有**重叠 cue id 对及重叠区间，**不输出合并版本**；
9. 合并冲突优先于重叠检查：存在冲突时只报告冲突。

#### 响应

- `200` 合并成功：

```json
{
  "status": "merged",
  "cues": [ { "id": "a", "start": 0.0, "end": 2.0, "text": "hello", "speaker": null } ],
  "count": 1
}
```

- `409 status=conflict`：cue 级冲突（删除/修改、同 id 新增不一致、合并后非法区间）与字段级冲突：

```json
{
  "status": "conflict",
  "cue_conflicts": [
    {
      "cue_id": "a",
      "type": "delete_modify",
      "side": "current",
      "fields": ["text"],
      "current": null,
      "incoming": { "id": "a", "start": 0.0, "end": 2.0, "text": "hello!", "speaker": null }
    }
  ],
  "field_conflicts": [
    { "cue_id": "b", "field": "text", "base": "hi", "current": "one", "incoming": "two" }
  ]
}
```

- `409 status=overlap`：无冲突但时间轴重叠，不返回合并结果：

```json
{
  "status": "overlap",
  "overlaps": [
    { "cue_ids": ["a", "b"], "start": 4.0, "end": 5.0, "overlap": 1.0 }
  ]
}
```

- `422`：请求体校验失败（重复 id、`end <= start` 等）。

### POST /resolve

在与 `/merge` 完全相同的三方合并之上，携带人工冲突决策并返回解决后的时间轴。
冲突识别复用同一套三方合并逻辑；只有被 `/merge` 报告为冲突的位置才接受决策。

请求体在 `base` / `current` / `incoming` 之外增加两个字段（均可省略，默认为空列表）：

| 字段            | 类型           | 说明                                                                                |
| --------------- | -------------- | ----------------------------------------------------------------------------------- |
| `field_choices` | FieldChoice[]  | 字段级冲突决策，按 `cue_id` + `field` 定位                                          |
| `cue_choices`   | CueChoice[]    | cue 级冲突决策（`delete_modify` / `add_add` / `invalid_interval`），按 `cue_id` 定位 |

```json
{
  "base":    [{"id": "a", "start": 0, "end": 2, "text": "hi"}],
  "current": [{"id": "a", "start": 0, "end": 2, "text": "one"}],
  "incoming":[{"id": "a", "start": 0, "end": 2, "text": "two"}],
  "field_choices": [{"cue_id": "a", "field": "text", "choice": "current"}],
  "cue_choices": []
}
```

- **FieldChoice**：`{"cue_id", "field", "choice"}`
  - `field`：`start` / `end` / `text` / `speaker`；
  - `choice`：`base` / `current` / `incoming` —— 该字段采纳哪一侧的值；
  - 同一条 cue 上未发生冲突的字段仍按三方合并规则自动取值。
- **CueChoice**：`{"cue_id", "choice"}`
  - `choice`：`current` / `incoming` —— 整条 cue 采用该侧版本；`delete` —— 不输出该 cue；
  - `delete_modify` 中选择了已删除的一侧时，效果等同 `delete`。

#### 决策校验（先于应用，任一不过即 `422`，不应用任何决策）

1. **重复（duplicate）**：同一 `(cue_id, field)` 出现多条字段决策，或同一 `cue_id` 出现多条 cue 决策；
2. **未知（unknown）**：决策针对的冲突不存在（该 cue/字段本来就没有冲突，或 cue id 不存在）；
3. **类型不匹配（type_mismatch）**：字段决策发给了 cue 级冲突，或 cue 决策发给了字段级冲突；
4. **遗漏（missing）**：三方合并识别出的每个冲突都必须给出对应决策，缺一不可；
5. 枚举值非法、请求体结构错误同样返回 `422`。
6. **无冲突时只允许空决策**：没有任何冲突时携带任何决策都会得到 `unknown`；空决策（或省略两个字段）则直接走后续校验并返回结果。

#### 应用后的统一校验

决策全部合法时才应用，并对最终时间轴统一校验：

- 某条 cue 形成非法区间（`end <= start`，例如两端点分别选用了不同侧）→ `409`，列出该 cue id；
- 时间轴存在严格重叠（首尾相接不算）→ `409`，列出所有重叠区间；
- 两种问题可能同时报告；任一存在都**不输出部分结果**；
- 全部合法时，结果按 `start` 升序（平局依次按 `end`、`id`）返回。

#### 响应

- `200` 解决成功：

```json
{
  "status": "resolved",
  "cues": [ { "id": "a", "start": 0.0, "end": 2.0, "text": "one", "speaker": null } ],
  "count": 1
}
```

- `422 status=decision_error`：决策无效（下例为对不存在冲突的字段下发了决策）：

```json
{
  "status": "decision_error",
  "errors": [
    { "cue_id": "a", "field": "start", "type": "unknown", "message": "cue a 的字段 start 不存在字段级冲突" }
  ]
}
```

  错误类型取值：`duplicate` / `unknown` / `type_mismatch` / `missing`；字段决策错误带 `field`，cue 决策错误的 `field` 为 `null`。

- `409 status=timeline_error`：应用决策后非法区间或重叠，不返回时间轴：

```json
{
  "status": "timeline_error",
  "invalid_intervals": ["a"],
  "overlaps": [
    { "cue_ids": ["a", "b"], "start": 3.0, "end": 4.0, "overlap": 1.0 }
  ]
}
```

#### 典型流程

先调用 `POST /merge` 取得 `field_conflicts` / `cue_conflicts`（或直接调 `/resolve`），
按返回的 `cue_id`（及 `field`）逐项给出决策后调用 `POST /resolve`；
决策导致的非法区间/重叠修正后重新提交即可。

```bash
curl -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "base":    [{"id":"a","start":0,"end":10,"text":"keep"}],
    "current": [{"id":"a","start":8,"end":9,"text":"cur"}],
    "incoming":[{"id":"a","start":1,"end":3,"text":"inc"}],
    "field_choices": [
      {"cue_id":"a","field":"start","choice":"incoming"},
      {"cue_id":"a","field":"end","choice":"current"},
      {"cue_id":"a","field":"text","choice":"base"}
    ]
  }'
```

起点取 incoming（1）、终点取 current（9）、文本保留 base，得到合法区间：

```json
{"status":"resolved","cues":[{"id":"a","start":1.0,"end":9.0,"text":"keep","speaker":null}],"count":1}
```

### POST /retime

按 **source/target 时间锚点**对整条 cue 列表做分段线性校时：给定至少两组成对锚点
（`source` 为原时间轴上的位置、`target` 为新时间轴上的位置，单位均为秒），
把每条 cue 的 `start`、`end` 分别从 source 时间轴重映射到 target 时间轴。
`id`、`text`、`speaker` 与**输入顺序**保持不变，仅重算时间。

请求体：

| 字段      | 类型         | 说明                                                                 |
| --------- | ------------ | -------------------------------------------------------------------- |
| `cues`    | Cue[]        | 待校时 cue 列表（可空）；结构同 `/merge` 的 Cue，要求 `end > start`   |
| `anchors` | TimeAnchor[] | **至少 2 个**时间锚点，按 `source` 升序给出；两列时间均须非负且严格递增 |

TimeAnchor 结构：

```json
{"source": 10.0, "target": 20.0}
```

映射规则：

1. source 时间落在相邻两锚点之间时，按所在区间两端**线性插值**；
2. 早于第一个锚点或晚于最后一个锚点时，沿最近的（首段 / 末段）区间**线性外推**——
   外推可能得到负时间；
3. source 恰好等于锚点位置时，精确映射到对应 `target`；
4. 每条 cue 的 `start`、`end` **各自独立**计算，因此一条 cue 跨越锚点时，
   其两端可分别取自不同区间（插值斜率不同）。

#### 错误与响应

- 锚点不足两个、source/target 为负或不是严格递增、cue 本身 `end <= start` 等请求体问题 → `422`；
- 对所有 cue 统一计算后，只要**任一映射时间为负**或某条 cue **区间非法**（映射后 `end <= start`，
  含浮点四舍五入导致两端相等的极端情形），返回 `409`，`cue_ids` 列出**全部**相关 cue id
  （按输入中的首次出现顺序，同一 id 只列一次），**不输出任何部分结果**；
- 全部合法时返回 `200`，`cues` 严格保持输入顺序（**不**按时间排序）。

调用样例（`http://localhost:8000/retime`）：

```bash
curl -X POST http://localhost:8000/retime \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [
      {"id":"cue-01","start":1.0,"end":3.0,"text":"你好","speaker":null},
      {"id":"cue-02","start":12.0,"end":14.0,"text":"世界","speaker":"alice"}
    ],
    "anchors": [
      {"source":0,"target":0},
      {"source":10,"target":20},
      {"source":20,"target":25}
    ]
  }'
```

锚点在 0–10s 段为 2 倍速、10–20s 段为 0.5 倍速；`cue-01` 落在首段（1→2、3→6），
`cue-02` 落在次段（12→21、14→22），`id` / `text` / `speaker` 与顺序保持不变：

```json
{
  "status": "retimed",
  "cues": [
    {"id": "cue-01", "start": 2.0, "end": 6.0, "text": "你好", "speaker": null},
    {"id": "cue-02", "start": 21.0, "end": 22.0, "text": "世界", "speaker": "alice"}
  ],
  "count": 2
}
```

外推得到负时间时的 `409`（不返回 cues）：

```bash
curl -X POST http://localhost:8000/retime \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [{"id":"a","start":0,"end":11,"text":"hi"}],
    "anchors": [{"source":10,"target":5},{"source":20,"target":15}]
  }'
```

锚点斜率为 1，`start=0` 沿首段外推到 -5（为负）：

```json
{"status":"retime_error","cue_ids":["a"]}
```

### POST /splice

按请求中的**选区列表**从现有 Cue 时间轴上剪取字幕，并**依列表顺序**拼接成一条新时间轴。
选区可乱序、重叠或重复；每条源 cue 仅取与选区**严格相交**（交集时长 > 0，仅首尾相接不算）
的部分，跨过选区边界的 cue 在边界处裁断。

请求体：

| 字段         | 类型        | 说明                                                                                |
| ------------ | ----------- | ----------------------------------------------------------------------------------- |
| `cues`       | Cue[]       | 源 cue 列表（可空）；结构同 `/merge` 的 Cue，要求 `end > start`，**id 不得重复**、时间轴**不得重叠** |
| `selections` | Selection[] | 选区列表（可空），按列表顺序处理；空列表返回空 cue 列表与零时长                     |

Selection 结构（闭开区间 `[start, end)`，单位秒）：

```json
{"start": 5.0, "end": 12.0}
```

剪取与拼接规则：

1. 对每个选区，取每条源 cue 与该选区严格相交的部分；cue 横跨选区两端时两端都裁断；
2. 片段在新时间轴上的时间 = **此前全部选区时长之和** + 交集起点相对**本选区起点**的偏移；
3. **选区中的空白**（未命中任何 cue 的时间，含整条选区都在源时间轴之外）同样计入总时长；
4. 选区可乱序、重叠或重复，严格按请求列表顺序拼接，不做去重或合并；
5. 片段保留原 `text` / `speaker`，并通过 `source_id` 保留原 cue id、
   `selection_index` 保留来源选区下标（**从 0 开始**）；
6. 同一原 cue 被多个选区命中时，为每个片段生成不同的新 `id`，
   格式为 `"{原 id}#{选区下标}"`（同一次请求内全局唯一）；
7. 输出 cue 按新时间轴的 `start` 升序排列（平局依次按 `end`、`source_id`）；
8. 总时长 `duration` = 各选区时长之和（含空白）；空选区列表时为空列表、`0`。

#### 错误（不输出部分结果）

- 选区端点为负、`end <= start`，或源 cue 本身非法（`end <= start`）→ `422`（请求体校验）；
- **源 cue id 重复**或**源时间轴重叠**（首尾相接不算）→ `422 status=splice_error`，
  返回 `duplicated_ids` 与/或 `overlaps`，不返回 cue 列表。

调用样例（`http://localhost:8000/splice`）：

```bash
curl -X POST http://localhost:8000/splice \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [
      {"id":"cue-01","start":0.0,"end":10.0,"text":"你好","speaker":"alice"},
      {"id":"cue-02","start":15.0,"end":20.0,"text":"世界","speaker":null}
    ],
    "selections": [
      {"start":5.0,"end":12.0},
      {"start":0.0,"end":10.0},
      {"start":18.0,"end":19.0}
    ]
  }'
```

三个选区时长分别为 7s、10s、1s（总时长 18s）：`cue-01` 在选区 0 中留下 5–10
（映射到新轴 0–5，10–12 为空白）；在选区 1 中留下整段（偏移 7，映射到 7–17）；
`cue-02` 在选区 2 中留下 18–19（偏移 17，映射到 17–18）：

```json
{
  "status": "spliced",
  "cues": [
    {"id": "cue-01#0", "start": 0.0, "end": 5.0, "text": "你好", "speaker": "alice",
     "source_id": "cue-01", "selection_index": 0},
    {"id": "cue-01#1", "start": 7.0, "end": 17.0, "text": "你好", "speaker": "alice",
     "source_id": "cue-01", "selection_index": 1},
    {"id": "cue-02#2", "start": 17.0, "end": 18.0, "text": "世界", "speaker": null,
     "source_id": "cue-02", "selection_index": 2}
  ],
  "count": 3,
  "duration": 18.0
}
```

源时间轴重叠时的 `422`（不返回 cues）：

```bash
curl -X POST http://localhost:8000/splice \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [
      {"id":"a","start":0,"end":5,"text":"first"},
      {"id":"b","start":3,"end":8,"text":"second"}
    ],
    "selections": [{"start":0,"end":10}]
  }'
```

```json
{
  "status": "splice_error",
  "duplicated_ids": [],
  "overlaps": [
    {"cue_ids": ["a", "b"], "start": 3.0, "end": 5.0, "overlap": 2.0}
  ]
}
```

### POST /insert

把一条**新 cue** 插入现有时间轴：源 cue 按起点排序后，新 cue 插到所有
「起点不小于新 cue 起点」的源 cue 之前；与新 cue 碰撞的后续 cue 保持先后顺序
与各自时长自动右移，碰撞可逐条向后传递。`locked_ids` 声明不可移动的 cue，
顺延一旦要求锁定 cue 移动即整次拒绝。

请求体：

| 字段         | 类型     | 说明                                                                                          |
| ------------ | -------- | --------------------------------------------------------------------------------------------- |
| `cues`       | Cue[]    | 现有时间轴（可空）；结构同 `/merge` 的 Cue，要求 `end > start`、**id 不得重复**、cue 之间**不得重叠** |
| `cue`        | Cue      | 待插入的新 cue（单个对象，非列表）；其 `id` **不得已存在**于 `cues`                            |
| `locked_ids` | string[] | 不可移动的 cue id 列表（可省略，默认空）；每个 id **必须属于** `cues`                           |

插入与顺延规则：

1. 源 cue 按 `start` 升序（平局依次按 `end`、`id`）排序；起点 **< 新 cue start** 的为
   「更早 cue」，起点 **≥ 新 cue start** 的为「后续 cue」，新 cue 位于两组之间；
2. 更早 cue **保持原位**：若其中某条与新 cue **严格相交**（重叠时长 > 0，仅首尾相接
   `end == start` 不算），整次拒绝（`409 preceding_overlap`）——更早 cue 不参与顺延；
3. 后续 cue 逐条处理：仅当与**前一条**（第一条的前一条是新 cue）严格相交时，
   整体右移到前一条的 `end`，区间**时长不变**；首尾相接不移动；
4. 移动可逐条传递：后一条与移动后的前一条相交则继续右移；一旦某条无需移动
   （与前一条首尾相接或已有间隙），碰撞链即结束，再往后的 cue 全部保持原位；
5. 若顺延要求**任何锁定 cue** 移动，收集全部受影响锁定 id（按时间轴顺序、去重），
   整次拒绝（`409 locked_move`），**不输出部分结果**；
6. 成功时返回完整时间轴（按开始时间排序），以及 `moved`：每条**实际被移动** cue 的
   `original_start` / `original_end`（原起止）与 `start` / `end`（新起止）；
   新 cue 与未移动 cue 不出现在 `moved` 中。

#### 错误（不输出部分结果）

- 新 cue 或源 cue 时间为负、`end <= start` 等请求体结构问题 → `422`（FastAPI 校验）；
- **参数无效** → `422 status=insert_param_error`，一次性收集全部问题并给出 `message`：
  - `duplicated_ids`：源 cue id 重复（排序去重）；
  - `overlaps`：源时间轴重叠区间（源 id 重复时不再重复计算重叠）；
  - `existing_id`：新 cue id 已存在于源列表；
  - `unknown_locked`：`locked_ids` 中不属于源列表的 id（排序去重）；
- **更早 cue 与新 cue 相交** → `409 status=insert_error, reason=preceding_overlap`，
  返回 `cue_id`（相交的更早 cue）及 `overlap` 重叠区间；
- **顺延要求锁定 cue 移动** → `409 status=insert_error, reason=locked_move`，
  返回需要移动的全部 `locked_ids`。

调用样例（`http://localhost:8000/insert`）——碰撞逐条传递：

```bash
curl -X POST http://localhost:8000/insert \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [
      {"id":"a","start":0,"end":1,"text":"早"},
      {"id":"b","start":3,"end":5,"text":"二"},
      {"id":"c","start":5,"end":7,"text":"三"},
      {"id":"d","start":7,"end":9,"text":"四"}
    ],
    "cue": {"id":"n","start":2,"end":4,"text":"新"},
    "locked_ids": ["a"]
  }'
```

新 cue `n`（2–4）先把 `b` 从 3–5 右移到 4–6，`c` 再被推到 6–8，`d` 被推到 8–10；
各条时长不变，`a` 未参与移动，返回完整时间轴与每条被移动 cue 的原/新起止：

```json
{
  "status": "inserted",
  "cues": [
    {"id":"a","start":0.0,"end":1.0,"text":"早","speaker":null},
    {"id":"n","start":2.0,"end":4.0,"text":"新","speaker":null},
    {"id":"b","start":4.0,"end":6.0,"text":"二","speaker":null},
    {"id":"c","start":6.0,"end":8.0,"text":"三","speaker":null},
    {"id":"d","start":8.0,"end":10.0,"text":"四","speaker":null}
  ],
  "count": 5,
  "moved": [
    {"id":"b","original_start":3.0,"original_end":5.0,"start":4.0,"end":6.0},
    {"id":"c","original_start":5.0,"original_end":7.0,"start":6.0,"end":8.0},
    {"id":"d","original_start":7.0,"original_end":9.0,"start":8.0,"end":10.0}
  ]
}
```

顺延触及锁定 cue 时的 `409`（不返回 cues / moved）：

```bash
curl -X POST http://localhost:8000/insert \
  -H 'Content-Type: application/json' \
  -d '{
    "cues": [
      {"id":"a","start":0,"end":1,"text":"早"},
      {"id":"b","start":3,"end":5,"text":"二"}
    ],
    "cue": {"id":"n","start":2,"end":4,"text":"新"},
    "locked_ids": ["b"]
  }'
```

```json
{
  "status": "insert_error",
  "reason": "locked_move",
  "locked_ids": ["b"],
  "message": "顺延要求移动锁定 cue（不可移动）: b"
}
```

更早 cue 与新 cue 相交时的 `409`：

```json
{
  "status": "insert_error",
  "reason": "preceding_overlap",
  "cue_id": "a",
  "overlap": {"cue_ids": ["a", "n"], "start": 4.0, "end": 5.0, "overlap": 1.0},
  "message": "更早的 cue a 与新 cue n 相交（4.0–5.0），更早的 cue 不允许移动，整次插入被拒绝"
}
```

参数无效（下例同时存在新 id 已存在与未知锁定 id）时的 `422`：

```json
{
  "status": "insert_param_error",
  "duplicated_ids": [],
  "overlaps": [],
  "existing_id": "a",
  "unknown_locked": ["x"],
  "message": "新 cue id 已存在: a；锁定 id 不属于源列表: x"
}
```

## 快速试用

```bash
curl -X POST http://localhost:8000/merge \
  -H 'Content-Type: application/json' \
  -d '{
    "base":    [{"id":"a","start":0,"end":2,"text":"hi"}],
    "current": [{"id":"a","start":0,"end":2,"text":"hello"}],
    "incoming":[{"id":"a","start":0.5,"end":2,"text":"hi"}]
  }'
```

两侧改动不同字段（current 改文本、incoming 改起点），自动合并为：

```json
{"status":"merged","cues":[{"id":"a","start":0.5,"end":2.0,"text":"hello","speaker":null}],"count":1}
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── main.py       # FastAPI 路由（/health、/merge、/resolve、/retime、/splice、/insert、/）
│   ├── merge.py      # 三方合并、冲突决策应用与重叠检测纯逻辑
│   ├── retime.py     # 时间锚点分段线性校时纯逻辑
│   ├── splice.py     # 选区剪取与顺序拼接纯逻辑
│   ├── insert.py     # 新 cue 插入与碰撞顺延纯逻辑
│   └── models.py     # Pydantic 请求/响应模型
├── tests/
│   └── test_api.py   # 端到端用例（unittest + TestClient）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## 本地开发（不使用容器）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```
