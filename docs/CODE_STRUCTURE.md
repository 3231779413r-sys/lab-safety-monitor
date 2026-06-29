# 代码结构说明

本文档面向技术交付和后续接手开发人员，重点说明本项目的代码如何组织、各模块负责什么、主要调用链路如何串联，以及常见修改应优先查看哪些位置。

本文档不展开部署命令、环境变量、模型权重安装、完整 API 参数表或用户操作说明。

## 1. 项目整体结构

项目采用前后端分离结构，后端负责 API、认证、视频流处理、AI 检测、事件持久化和实时消息推送；前端负责页面展示、数据请求、监控视图、告警弹窗和管理界面。

```text
lab-safety-monitor/
|-- backend/                 # FastAPI 后端、AI 检测、数据库模型与迁移
|   |-- app/
|   |   |-- main.py           # FastAPI 应用入口，注册路由，初始化数据库和检测流水线
|   |   |-- api/              # API 依赖与路由
|   |   |-- core/             # 配置、数据库、认证安全、WebSocket 管理
|   |   |-- models/           # SQLAlchemy 数据模型
|   |   |-- schemas/          # Pydantic 请求/响应结构
|   |   |-- services/         # 业务服务、持久化、事件去重、摄像头运行态
|   |   `-- ml/               # 视频处理和 AI 检测流水线
|   `-- alembic/              # 数据库迁移
|-- frontend/                 # Next.js 前端
|   |-- src/
|   |   |-- app/              # App Router 页面与布局
|   |   |-- components/       # 业务组件、布局组件、基础 UI 组件
|   |   |-- hooks/            # 前端自定义 hooks
|   |   |-- lib/              # API Client、React Query hooks、工具函数
|   |   `-- providers/        # Auth、Query、WebSocket、Theme 等全局 Provider
|   `-- public/               # 静态资源
|-- data/                     # 数据目录
`-- README.md                 # 项目说明
```

整体代码可以按三条主线理解：

- 前端页面主线：`src/app` 页面调用 `src/lib/queries.ts`，queries 再调用 `src/lib/api.ts` 访问后端。
- 后端业务主线：`app/main.py` 注册 `api/routes`，路由通过依赖拿到数据库会话，再读写 `models` 或调用 `services`。
- AI 检测主线：视频帧进入 `ml/stream_processor.py`，交给 `ml/pipeline.py` 和检测器处理，结果再由 `services/persistence.py` 入库并通过 WebSocket 推送。

## 2. 后端代码结构

### 2.1 应用入口

`backend/app/main.py` 是后端入口文件，主要负责：

- 创建 FastAPI 应用。
- 在 lifespan 中初始化数据库。
- 创建视频、权重、快照等运行目录。
- 初始化全局检测流水线。
- 注册事件、人员、统计、视频流、摄像头、认证、用户和 WebSocket 路由。

后端启动后的主结构可以简化理解为：

```python
app = FastAPI(lifespan=lifespan)

async def lifespan(app):
    await init_db()
    pipeline = get_pipeline()
    pipeline.initialize()
    load_known_person_embeddings()
    yield

app.include_router(events_router, prefix="/api")
app.include_router(cameras_router, prefix="/api")
app.include_router(ws_router, prefix="/api")
```

### 2.2 core：基础设施层

`backend/app/core/` 放的是全局基础能力：

- `config.py`：集中管理配置项，其他模块通过 `settings` 读取。
- `database.py`：创建 SQLAlchemy 异步数据库连接和 session。
- `security.py`：认证相关的密码、Token 或安全工具。
- `websocket.py`：维护 WebSocket 连接列表，并提供广播方法。

这些文件通常不直接处理业务逻辑，而是给路由层、服务层和 ML 流水线提供基础能力。

### 2.3 api/routes：接口入口层

`backend/app/api/routes/` 是后端 HTTP/WebSocket 入口，每个文件对应一组业务 API：

- `auth.py`：登录、注册、当前用户等认证接口。
- `users.py`：用户管理接口。
- `events.py`：事件查询、违规图库、快照访问。
- `persons.py`：人员查询和人员信息维护。
- `stats.py`：仪表盘统计数据。
- `stream.py`：视频上传、视频处理任务、通用实时流。
- `cameras.py`：摄像头 CRUD、测试、启停、实时画面。
- `websocket.py`：实时告警 WebSocket 端点。

典型请求处理链路如下：

```python
@router.get("/events")
async def get_events(db = Depends(get_database)):
    query = build_sqlalchemy_query(filters)
    rows = await db.execute(query)
    return pydantic_response(rows)
```

接手开发时，如果要新增或修改一个后端接口，通常先找 `api/routes` 中对应业务文件，再看它是否直接访问 `models`，或是否调用了 `services`。

### 2.4 models 与 schemas：数据结构层

`backend/app/models/` 是数据库模型层，定义事件、人员、用户、摄像头等实体如何映射到数据库表。

`backend/app/schemas/` 是 Pydantic 数据结构层，主要用于接口请求和响应。当前用户相关结构放在 `schemas/user.py`，部分路由也会在文件内部定义局部响应模型，例如 `events.py` 中的 `EventResponse`、`EventsListResponse`。

当接口字段、数据库字段或前端类型不一致时，应同时检查：

- 后端路由中的 Pydantic response model。
- `models/` 中的 SQLAlchemy 字段。
- 前端 `frontend/src/lib/api.ts` 中对应 TypeScript interface。

### 2.5 services：业务服务层

`backend/app/services/` 放的是跨接口或跨模块复用的业务逻辑：

- `event_service.py`：事件创建、关闭、快照保存等事件持久化操作。
- `person_service.py`：人员查询、创建、统计更新、embedding 读取。
- `camera_service.py`：摄像头连接、地址构造、配置读取/写入等。
- `camera_runtime.py`：摄像头运行态管理。
- `deduplication.py`：违规事件去重，避免持续违规重复创建事件。
- `persistence.py`：把检测结果转成数据库事件，并触发 WebSocket 广播。

其中 `PersistenceManager` 是 AI 检测结果进入业务系统的关键节点。它接收每帧检测结果，判断是否需要创建或关闭事件，然后写入数据库并广播告警。

简化流程如下：

```python
async def persist_frame_results(result, snapshot_frame):
    for person in result["persons"]:
        violations = collect_stable_violations(person)
        should_create, ended_event = dedup_manager.should_create_event(...)

        if ended_event:
            await event_service.close_event(...)

        if should_create:
            event = await event_service.create_event(...)
            dedup_manager.register_event(...)
            await ws_manager.broadcast(build_alert_message(event))

    await session.commit()
```

## 3. AI 检测代码结构

`backend/app/ml/` 是视频和模型推理相关代码，整体目标是把输入视频帧转换为结构化检测结果。

主要模块职责如下：

- `stream_processor.py`：实时视频流处理器，将画面显示帧率和 AI 处理帧率解耦；后台线程处理帧，异步提交持久化任务。
- `pipeline.py`：检测主流水线，协调检测器、PPE 关联、时序过滤、事件候选结果和画面标注。
- `detector_factory.py`：检测器工厂，负责创建或复用检测器实例。
- `hybrid_detector.py`：组合人员检测、分割和 PPE 检测能力。
- `person_detector.py`：人员检测与跟踪。
- `yolov11_detector.py`：PPE 检测。
- `sam2_segmenter.py` / `sam3_segmenter.py`：人员或目标分割。
- `temporal_filter.py`：对连续帧检测结果做平滑和稳定判断。
- `face_recognition.py`、`person_gallery.py`：人员识别和人员图库相关能力。
- `mask_utils.py`：检测结果可视化绘制工具。

核心检测链路可以这样理解：

```python
def process_frame(frame, video_source):
    detections = detector.detect(frame)
    persons = detections["persons"]
    ppe = detections["ppe_detections"]
    violations = detections["violation_detections"]

    persons = detector.associate_ppe_to_persons(persons, ppe, violations)

    for person in persons:
        filter_result = temporal_filter.update(person_id, person["missing_ppe"])

        if filter_result["is_violation"]:
            result["events"].append(build_event_candidate(person))

    result["annotated_frame"] = draw_annotations(frame, persons)
    return result
```

实时视频流不是每一帧都完整跑 AI 检测，而是由 `StreamProcessor` 控制处理频率：

```python
while camera_is_open:
    frame = read_frame()

    if should_process_this_frame:
        processor.submit_frame(frame, video_source)

    latest_result = processor.get_latest_result(fallback_frame=frame)
    yield encode_as_mjpeg(latest_result["annotated_frame"])
```

这个结构让前端看到的画面保持相对流畅，同时把较重的模型推理控制在较低频率。

## 4. 前端代码结构

### 4.1 app：页面与路由

`frontend/src/app/` 使用 Next.js App Router 组织页面。

主要页面包括：

- `login/page.tsx`：登录页。
- `(authenticated)/layout.tsx`：登录后页面统一套用 `AppLayout`。
- `(authenticated)/dashboard/page.tsx`：仪表盘。
- `(authenticated)/monitor/page.tsx`：实时监控。
- `(authenticated)/cameras/page.tsx`：摄像头管理。
- `(authenticated)/events/page.tsx`：事件列表。
- `(authenticated)/persons/page.tsx`：人员管理。
- `(authenticated)/analysis/page.tsx`：分析页。
- `(authenticated)/settings/page.tsx`：设置页。
- `(authenticated)/users/page.tsx`：用户管理。

新增页面时，优先在 `src/app/(authenticated)/` 下创建对应路由，并复用已有布局和组件。

### 4.2 components：组件层

`frontend/src/components/` 分为三类：

- 页面业务组件：如 `events-table.tsx`、`persons-table.tsx`、`video-player.tsx`、`stats-card.tsx`。
- 布局组件：如 `app-layout.tsx`、`layout/sidebar.tsx`、`page-header.tsx`。
- 基础 UI 组件：`components/ui/` 下的 button、card、dialog、table、tabs 等。

违规告警相关组件集中在 `components/violation-dialog/`，用于展示实时违规弹窗、快照、PPE 标签和 toast。

### 4.3 lib：前端数据访问层

`frontend/src/lib/api.ts` 是前端访问后端的集中封装，包含：

- `API_BASE_URL`。
- 后端返回数据的 TypeScript interface。
- `ApiClient` 类。
- 统计、事件、人员、视频、摄像头等 API 方法。

`frontend/src/lib/queries.ts` 基于 TanStack Query 封装 hooks，负责：

- 定义 query keys。
- 调用 `api.ts`。
- 设置刷新间隔。
- 在 mutation 成功后刷新相关缓存。

页面通常不直接写 `fetch`，而是走下面的链路：

```typescript
page.tsx
  -> useEvents()
  -> api.getEvents()
  -> GET /api/events
```

### 4.4 providers：全局状态与上下文

`frontend/src/providers/` 放全局 Provider：

- `query-provider.tsx`：提供 TanStack Query Client。
- `auth-provider.tsx`：管理登录状态、Token、本地存储和用户信息刷新。
- `websocket-provider.tsx`：连接后端 `/api/ws`，维护连接状态和最近一条实时消息。
- `theme-provider.tsx`：主题相关能力。

应用根布局 `src/app/layout.tsx` 目前包裹了 `QueryProvider` 和 `AuthProvider`，并挂载全局 toast。需要使用 WebSocket 的页面或布局，应确认是否已经被 `WebSocketProvider` 包裹。

前端实时告警链路可以简化为：

```typescript
WebSocketProvider connects to ws://backend/api/ws

ws.onmessage = (event) => {
  const alert = JSON.parse(event.data)
  setLastMessage(alert)
}

violation dialog / toast components read lastMessage
show alert and snapshot
```

## 5. 核心调用链路

### 5.1 普通业务接口链路

以事件列表为例，代码链路如下：

```text
frontend page
  -> frontend/src/lib/queries.ts
  -> frontend/src/lib/api.ts
  -> backend/app/api/routes/events.py
  -> backend/app/models/event.py
  -> database
```

如果要修改事件列表字段，通常要同时检查后端 `events.py` 的响应模型、事件数据库模型、前端 `ComplianceEvent` interface 和展示组件 `events-table.tsx`。

### 5.2 视频帧到违规事件链路

实时或离线视频帧进入系统后，大致经过以下代码链路：

```text
camera/video source
  -> backend/app/ml/stream_processor.py
  -> backend/app/ml/pipeline.py
  -> backend/app/ml/detector_factory.py
  -> backend/app/ml/hybrid_detector.py
  -> backend/app/ml/temporal_filter.py
  -> backend/app/services/persistence.py
  -> backend/app/services/event_service.py
  -> backend/app/models/event.py
```

关键分工是：

- `stream_processor.py` 负责视频帧读取、降频处理、MJPEG 输出和后台处理调度。
- `pipeline.py` 负责单帧检测逻辑和检测结果结构化。
- `temporal_filter.py` 负责把单帧结果变成更稳定的连续帧判断。
- `persistence.py` 负责把稳定违规变成数据库事件。

### 5.3 违规事件到前端告警链路

当检测结果需要创建事件时，后端会同时写数据库和推送 WebSocket：

```text
PersistenceManager creates event
  -> EventService writes database
  -> ConnectionManager.broadcast(...)
  -> frontend WebSocketProvider receives message
  -> violation dialog / toast updates UI
```

对应关键文件：

- 后端广播：`backend/app/services/persistence.py`
- 后端连接管理：`backend/app/core/websocket.py`
- 后端 WS 端点：`backend/app/api/routes/websocket.py`
- 前端 WS Provider：`frontend/src/providers/websocket-provider.tsx`
- 前端告警 UI：`frontend/src/components/violation-dialog/`

## 6. 接手开发指南

按常见开发任务，可以优先从这些位置入手：

| 修改目标 | 优先查看 |
| --- | --- |
| 新增前端页面 | `frontend/src/app/(authenticated)/`、`frontend/src/components/app-layout.tsx`、`frontend/src/components/layout/sidebar.tsx` |
| 修改接口请求 | `frontend/src/lib/api.ts`、`frontend/src/lib/queries.ts` |
| 新增后端 API | `backend/app/api/routes/`、`backend/app/api/deps.py` |
| 修改数据库字段 | `backend/app/models/`、`backend/alembic/versions/`、相关前端 TypeScript interface |
| 修改事件列表或图库 | `backend/app/api/routes/events.py`、`frontend/src/components/events-table.tsx`、`frontend/src/lib/api.ts` |
| 修改实时告警 | `backend/app/services/persistence.py`、`backend/app/core/websocket.py`、`frontend/src/providers/websocket-provider.tsx`、`frontend/src/components/violation-dialog/` |
| 修改摄像头管理 | `backend/app/api/routes/cameras.py`、`backend/app/services/camera_service.py`、`backend/app/services/camera_runtime.py`、`frontend/src/app/(authenticated)/cameras/page.tsx` |
| 修改 AI 检测逻辑 | `backend/app/ml/pipeline.py`、`backend/app/ml/hybrid_detector.py`、`backend/app/ml/yolov11_detector.py`、`backend/app/ml/temporal_filter.py` |
| 修改视频流展示 | `backend/app/ml/stream_processor.py`、`backend/app/api/routes/stream.py`、`frontend/src/components/video-player.tsx` |
| 修改登录认证 | `backend/app/api/routes/auth.py`、`backend/app/core/security.py`、`frontend/src/providers/auth-provider.tsx`、`frontend/src/middleware.ts` |

## 7. 阅读代码建议

如果是第一次接手，建议按下面顺序阅读：

1. 先读 `backend/app/main.py`，理解后端启动时注册了哪些能力。
2. 再读 `frontend/src/app/layout.tsx` 和 `frontend/src/app/(authenticated)/layout.tsx`，理解前端全局 Provider 和页面布局。
3. 读 `frontend/src/lib/api.ts` 与 `frontend/src/lib/queries.ts`，建立前后端接口映射。
4. 选一个页面，例如 dashboard、events 或 cameras，从页面一路追到后端 route。
5. 最后读 `backend/app/ml/stream_processor.py`、`backend/app/ml/pipeline.py` 和 `backend/app/services/persistence.py`，理解视频检测主链路。

这份代码结构的核心阅读思路是：先建立分层，再沿真实调用链追代码。这样比逐个文件阅读更容易快速接手。
