# SentinelVision

车间/实验室安全监管系统。系统采用前后端分离与推理 Worker 解耦架构，围绕“人员识别、PPE 合规检测、监管事件判定、证据留存、实时告警、历史追溯”构建完整业务闭环。

当前仓库的标准运行方式为 Docker Compose 统一拉起，前端、API、采集 worker、批量 GPU 检测 worker、批量 GPU 身份增强 worker、PostgreSQL、MinIO、RabbitMQ、Nginx 都运行在容器中。

## 系统概述

系统面向车间/实验室安全监管场景，核心目标包括：

- 对实时视频流中的人员进行检测、跟踪、身份识别与跨帧稳定关联
- 对安全帽、口罩、安全背心等 PPE 穿戴状态进行识别
- 对未巡检、区域漏巡、违规闯入、区域超员、车间超员等监管事件进行业务判定
- 对事件生成快照、短视频、实时告警、历史记录与统计报表
- 支持员工、外来人员、来访预约、巡检排班、摄像头点位、监管规则的统一配置与管理

## 核心能力总览

- 海康 SDK 官方取流，支持 RTSP 回退
- GPU 推理 Worker 常驻运行，前后端与推理解耦
- 实时视频监控、人员框叠加、告警弹窗、WebSocket 实时推送
- PPE 合规检测、人员跟踪、人脸识别、跨帧身份稳定
- 员工管理、外来人员管理、外来预约管理、人脸库管理
- 巡检排班、巡检人员识别、未巡检/区域漏巡判定
- 违规闯入、区域超员、车间超员等监管业务规则
- 事件快照、事件前后短视频、对象存储、历史检索、统计分析
- Docker Compose 一键部署，支持采集与推理解耦、批量 GPU Worker、PostgreSQL、MinIO、RabbitMQ、Nginx

## 系统架构

### 部署拓扑

- `frontend`：Next.js 前端，容器内监听 `3000`
- `backend-api`：FastAPI 主接口，容器内监听 `8000`
- `monitor-worker`：摄像头常驻采集、运行时处理、结果回填，容器内监听 `8001`
- `monitor-worker-1`：第二个摄像头采集分片实例，与 `monitor-worker` 按 camera_id 哈希分摊负载
- `inference-worker`：RabbitMQ 消费 + 批量 GPU 检测推理
- `identity-worker`：RabbitMQ 消费 + 批量 GPU 人脸/ReID 特征提取
- `postgres`：业务数据库，宿主机映射 `5432`
- `minio`：对象存储，保存快照、短视频、人脸图片，宿主机映射 `9000/9001`
- `rabbitmq`：推理队列中间件，宿主机映射 `5672/15672`
- `nginx`：统一入口，宿主机映射 `80`

### 逻辑分层

- 前端展示层：页面、图表、监控画面、告警交互、配置表单
- API 中台层：认证、人员管理、摄像头管理、监管配置、事件检索、统计接口
- 推理执行层：视频采集、队列投递、批量检测、批量身份增强、检测跟踪、身份识别、事件判定、媒体留存
- 数据存储层：PostgreSQL 结构化数据，MinIO 对象数据
- 实时消息层：WebSocket + PostgreSQL 通知中继 + RabbitMQ 推理队列

## 核心 AI 视觉算法

### 1. 人体检测与基础跟踪

- 模型：`YOLOv8m`
- 代码位置：`backend/app/ml/person_detector.py`
- 功能：
  - 检测视频帧中的人员目标
  - 输出人员框 `box`
  - 支持原生 tracking，生成基础 `track_id`
  - 仅保留 COCO `person` 类

### 2. PPE 与安全事件检测

- 模型：`YOLOv11` 定制模型
- 代码位置：`backend/app/ml/yolov11_detector.py`
- 功能：
  - 识别正向 PPE 类别，如：
    - `hardhat`
    - `mask`
    - `safety_vest`
  - 识别违规类别，如：
    - `no_hardhat`
    - `no_mask`
    - `no_safety_vest`
  - 支持多尺度检测与 NMS 合并
  - 兼容 PyTorch 和 ONNX 推理方式

### 3. 人体分割与掩码生成

- 模型：`SAM3` / `SAM2`
- 代码位置：
  - `backend/app/ml/sam3_segmenter.py`
  - `backend/app/ml/sam2_segmenter.py`
  - `backend/app/ml/hybrid_detector.py`
- 功能：
  - 为人员目标生成更精确的人体掩码
  - 通过掩码与空间包含关系提升 PPE 归属判断精度
  - `SAM2` 支持视频传播，减少逐帧重复分割成本
  - 当 SAM 不可用时可回退到框级近似掩码

### 4. 人脸检测与识别

- 模型：`InsightFace buffalo_l`
- 代码位置：`backend/app/ml/face_recognition.py`
- 功能：
  - 基于 InsightFace 完成人脸检测
  - 提取 512 维人脸特征向量
  - 对员工、外来人员、外来预约的人脸库进行匹配
  - 输出：
    - `person_id`
    - `person_name`
    - `subject_type`
    - `allowed_camera_ids`
    - `appointment_start/end`
  - 支持整帧检测与按人员框回退检测

### 5. 人员 ReID 与跨帧身份稳定

- 模型：`OSNet`
- 索引：`FAISS`
- 代码位置：`backend/app/ml/osnet_reid.py`
- 功能：
  - 提取人员外观特征
  - 建立 worker 级全局身份图库
  - 当人脸不可见时，通过外观特征维持身份一致性
  - 支持：
    - 身份重命名
    - 未知人员稳定 ID
    - 已识别人员特征入库
    - 相机内/跨帧身份回溯

### 6. 多目标跟踪

- 算法：`DeepSORT` 思路实现
- 代码位置：`backend/app/ml/tracker.py`
- 功能：
  - 使用 Kalman Filter 做位置预测
  - 使用 Hungarian Assignment 做轨迹分配
  - 结合 IOU 与外观特征进行匹配
  - 维护 `tentative / confirmed / deleted` 轨迹状态

### 7. 姿态估计与动作标签

- 模型：`YOLO Pose`
- 代码位置：
  - `backend/app/ml/pose_detector.py`
  - `backend/app/ml/action_analyzer.py`
  - `backend/app/ml/pose_action_filter.py`
- 功能：
  - 输出 COCO 17 点关键点
  - 基于规则推断人员姿态状态：
    - `standing`
    - `bending`
    - `crouching`
    - `fallen`
  - 支持基于关键点的动作标签平滑
  - 当前规则分析中包含 `hand_near_mouth` 等姿态动作线索

### 8. 时序稳定与误报抑制

- 模块：`TemporalFilter`
- 代码位置：`backend/app/ml/temporal_filter.py`
- 功能：
  - 使用滑动窗口抑制单帧抖动
  - 支持二值连续帧稳定判定
  - 支持置信度融合：
    - `ema`
    - `mean`
    - `max`
  - 通过迟滞清除机制降低闪断误报

### 9. 混合检测主管道

- 模块：`DetectionPipeline` + `HybridDetector`
- 代码位置：
  - `backend/app/ml/pipeline.py`
  - `backend/app/ml/hybrid_detector.py`
- 功能：
  - 协调人体检测、分割、PPE 检测、人脸识别、ReID、姿态分析、时序融合
  - 生成：
    - `persons`
    - `violations`
    - `events`
    - `annotated_frame`

## 核心监管业务逻辑实现机制

### 1. 事件分类体系

当前系统危险事件分为“已落地事件”和“预留事件类型”两部分。

已落地事件包括：

- PPE 类事件
  - 未佩戴安全帽
  - 未佩戴口罩
  - 未穿戴安全背心
  - 未穿戴防护鞋
  - 未佩戴防护手套
  - 未佩戴护目镜
  - 未佩戴防毒口罩
- 监管业务类事件
  - 未巡检
  - 区域漏巡
  - 违规闯入
  - 区域超员
  - 车间超员

当前代码中已预留、但尚未形成完整业务闭环的事件类型包括：

- 超时驻留
- 盲区驻留
- 人员跌倒

### 2. 摄像头级检测范围控制

每个摄像头可分别配置：

- `camera_detection_scope`
  - 由摄像头/画面检测链路直接支持的事件范围
- `backend_detection_scope`
  - 由后端业务规则追加判定的事件范围

系统会对人员检测结果进行二次过滤，只保留该摄像头已启用的危险事件类型。

### 3. 人员身份与监管范围绑定

系统按照身份类型对监管逻辑进行差异化处理：

- 员工 `employee`
  - 可绑定监管范围
  - 可录入人脸
  - 参与巡检排班
- 外来人员档案 `external_person`
  - 可绑定监管事件范围
  - 可绑定允许出现的摄像头
- 外来预约 `external_registration`
  - 具有开始/结束时间
  - 具有允许摄像头范围
- 未知人员 `unknown`
  - 归入“其他人员监管范围”

### 4. 违规闯入判定机制

系统通过以下条件组合判断“违规闯入”：

- 未知人员出现在启用了该事件的点位
- 外来预约已过期或不在预约时间内
- 外来人员/预约人员出现在不允许的监控画面中

一旦命中规则，会为当前人员强制追加 `unauthorized_intrusion` 事件。

### 5. 巡检与区域漏巡机制

系统支持巡检监管闭环：

- 岗位为“巡检人员”的员工在巡逻区摄像头中被识别到时，记为巡检到岗
- 按监管设置中的：
  - 起始时间
  - 巡检周期（小时）
  - 巡检摄像头集合
  生成巡检窗口
- 若整个巡检窗口内无人完成巡检，产生：
  - `missed_inspection`
- 若部分巡检区未覆盖，产生：
  - `area_missed_inspection`

### 6. 区域超员机制

- 每个摄像头可配置独立的多边形区域
- 可设置区域人数上限
- 系统统计多边形内人员数量
- 超过阈值时，对区域内人员追加：
  - `area_overcapacity`

### 7. 车间超员机制

- 来自全局监管设置
- 基于所有在线摄像头的当前人数汇总
- 超过全局上限时，触发：
  - `workshop_overcapacity`

当前实现是全局总人数判定，而非单个摄像头局部判定。

### 8. 事件时序稳定、去重与冷却

系统通过 `TemporalFilter + DeduplicationManager` 控制告警风暴：

- 连续多帧违规才确认事件
- 同一人持续违规不重复刷库
- 违规结束时关闭事件，回填持续帧数与结束时间
- 同一身份/同一事件类型支持冷却时间控制
- 规则变化显著时才新建事件

### 9. 证据留存机制

违规事件确认后，系统会生成：

- 事件快照
- 事件前后短视频片段
- 实时告警消息
- 历史事件记录

### 10. 实时推送机制

- 后端通过 WebSocket 对前端推送：
  - 新违规事件
  - 违规更新事件
  - 系统类消息
- 前端监控页支持实时 toast 告警与告警列表

## 数据存储和业务中台设计

### 1. PostgreSQL 结构化数据

系统核心结构化数据存储在 PostgreSQL 中，主要表包括：

- `users`
  - 登录用户、管理员
- `persons`
  - 员工档案、人脸特征、统计信息
- `external_persons`
  - 外来人员基础档案
- `external_personnel_registrations`
  - 外来预约记录、预约时段、允许区域、监管范围
- `visitor_registrations`
  - 来访批次登记
- `video_sources`
  - 摄像头点位、连接信息、检测范围、超员区域配置
- `compliance_events`
  - 违规事件主表、快照/视频地址、时序状态、危险事件分类
- `shift_schedules`
  - 巡检排班
- `supervision_settings`
  - 全局监管策略
- `job_title_options`
  - 岗位下拉选项
- `inspection_window_patrol_records`
  - 巡检窗口到岗记录

### 2. `compliance_events` 事件主表设计

事件表不仅保存“发生了什么”，也保存“如何追溯证据”：

- 人员标识
  - `person_id`
  - `person_name`
  - `track_id`
- 位置标识
  - `camera_id`
  - `camera_ids`
  - `camera_name`
  - `video_source`
- 违规内容
  - `missing_ppe`
  - `action_violations`
  - `danger_event_types`
- 时序信息
  - `start_frame`
  - `end_frame`
  - `duration_frames`
  - `is_ongoing`
- 证据介质
  - `snapshot_*`
  - `video_*`

### 3. MinIO 对象存储设计

MinIO 用于存储非结构化对象数据，默认包含三个桶：

- `lab-safety-monitor`
  - 事件快照
- `lab-safety-videos`
  - 事件短视频
- `lab-safety-faces`
  - 员工/外来人员/预约人员人脸原图

对象访问方式：

- API 代理访问：
  - `/api/events/objects/{bucket}/{object_key}`
- 前端图片/视频预览统一走 API 代理，不直接暴露内部桶路径

### 4. 人脸特征与媒体双存储

系统将人脸数据拆分为两类：

- 数据库：
  - 特征向量 `face_embedding`
  - 缩略图 `thumbnail`
- MinIO：
  - 原始人脸图片
  - 对应存储桶、对象键、内容类型、大小

这样可以兼顾识别效率与证据留存。

### 5. API 中台与 Worker 解耦设计

- `backend-api`
  - 负责管理型接口、查询型接口、认证、前端网关能力
- `monitor-worker`
  - 负责摄像头拉流、帧采样、运行时检测编排、结果消费、实时处理
- `inference-worker`
  - 负责从 RabbitMQ 批量消费多路帧并执行 GPU 推理

API 通过内部 token 调用 Worker 的内部接口，例如：

- 摄像头运行状态
- 直播流
- 人脸测试
- 实时人员框结果
- 摄像头配置下发

### 6. 实时消息中台

- 通过 PostgreSQL 通知中继 + WebSocket 管理器向前端广播
- 用于实时监控中心告警、事件更新、系统状态同步

## 系统功能展示

前端基于 Next.js 构建，当前主要页面如下。

### 1. 登录页 `/login`

- 用户名/邮箱 + 密码登录
- 管理员账号默认由环境变量初始化

### 2. 实时监控中心 `/monitor`

- 多摄像头实时画面展示
- 在线状态显示
- 监控框叠加人员身份
- WebSocket 实时告警
- 最新违规列表
- 单摄像头聚焦查看

### 3. 可视化数据展示 `/dashboard`

- 今日违规数
- 近 7 天/30 天趋势
- 事件类型分布
- 高发人员排行
- 高发摄像头排行
- 最新违规快照轮播

### 4. 事件记录 `/events`

- 按日期、时间段、摄像头、事件类型、人员姓名筛选
- 查看历史违规记录
- 查看快照
- 查看短视频
- 支持分页检索

### 5. 监控管理 `/cameras`

- 新增/编辑/删除摄像头
- 海康 SDK / RTSP 配置
- 摄像头启停、连通性测试
- 配置摄像头级事件范围
- 配置区域超员多边形与阈值
- 查看摄像头状态与基础信息

### 6. 人员管理 `/persons`

- 员工档案管理
- 外来人员档案管理
- 人脸录入
- 岗位选择
- 监管事件范围配置
- 允许出现摄像头配置
- 巡检排班管理
- 当日排班与历史排班查看

### 7. 监管配置 `/supervision`

- 访客登记
- 外来预约登记
- 全局监管设置
- 未知人员监管范围配置
- 区域漏巡开关与周期配置
- 区域漏巡参与摄像头设置
- 盲区驻留阈值预留配置
- 车间超员开关与人数阈值配置
- 告警冷却时间配置

### 8. 人脸测试 `/face-test`

- 上传图片做人脸比对
- 从实时摄像头取帧做人脸比对
- 查看最佳匹配对象、匹配分、余弦相似度
- 用于调试人脸库质量与识别效果

### 9. 系统设置 `/settings`

- 前端主题设置
- 本地通知、声音、自动刷新设置
- 动画开关
- 本地缓存偏好管理

## 当前已实现但容易遗漏的功能

除常见“检测 + 告警 + 存档”外，当前系统还包含以下能力：

- 摄像头运行时状态查询
- 内部 Worker 健康检查
- 摄像头直播与实时人员框分离接口
- 事件快照与短视频对象代理访问
- 人脸库热刷新
- 外来预约时间窗判断
- 巡检到岗自动记账
- 违规事件时序去重与关闭
- 未知人员稳定 ID 维护
- 运行时全局超员轮询任务
- 支持 Docker 模式下管理员自动初始化

## 项目结构

```text
backend/
  app/
    api/
    core/
    ml/
    models/
    services/
  alembic/
  weights/

frontend/
  src/
    app/
    components/
    lib/
    providers/

data/
logs/
nginx/
docker-compose.yml
.env.docker
```

## 容器化部署与开发运维规范

### 1. 环境准备

- Docker Engine
- Docker Compose v2
- NVIDIA 驱动
- NVIDIA Container Toolkit
- 海康 SDK 目录放置在仓库根目录

### 2. 环境变量

默认使用根目录 `.env.docker`。

关键变量包括：

- `DATABASE_URL`
- `WORKER_INTERNAL_BASE_URL`
- `WORKER_INTERNAL_BASE_URLS`
- `WORKER_INTERNAL_TOKEN`
- `CAMERA_MONITOR_SHARD_COUNT`
- `INFERENCE_BACKEND`
- `IDENTITY_BACKEND`
- `RABBITMQ_FRAME_QUEUE`
- `RABBITMQ_RESULT_QUEUE`
- `RABBITMQ_IDENTITY_QUEUE`
- `RABBITMQ_IDENTITY_RESULT_QUEUE`
- `MINIO_ENDPOINT`
- `MINIO_BUCKET`
- `MINIO_VIDEO_BUCKET`
- `MINIO_FACE_BUCKET`
- `CAMERA_CAPTURE_BACKEND`
- `HIKVISION_SDK_DIR`
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- `NVIDIA_VISIBLE_DEVICES`

### 3. 启动方式

当前默认部署会启动以下服务：

- `frontend`
- `backend-api`
- `monitor-worker`
- `monitor-worker-1`
- `inference-worker`
- `identity-worker`
- `postgres`
- `minio`
- `rabbitmq`
- `nginx`

如服务器已经安装 Docker Compose v2 插件，使用：

```bash
docker compose up -d --build
```

如果当前环境的 `docker` 命令不带 `compose` 子命令，可以直接使用 Compose 插件二进制：

```bash
env DOCKER_CONFIG=/home/inspur/.docker /home/inspur/.docker/cli-plugins/docker-compose --env-file .env.docker up -d --build
```

### 4. 访问地址

- 前端：`http://localhost` 或 `http://服务器IP`
- 健康检查：`http://localhost/health` 或 `http://服务器IP/health`
- 系统状态：`http://localhost/api/system/status` 或 `http://服务器IP/api/system/status`
- MinIO Web 控制台：`http://localhost:9001` 或 `http://服务器IP:9001`
- MinIO API：`http://localhost:9000` 或 `http://服务器IP:9000`
- RabbitMQ Web 管理台：`http://localhost:15672` 或 `http://服务器IP:15672`
- RabbitMQ AMQP：`localhost:5672` 或 `服务器IP:5672`

默认管理员账号：

- 用户名：`admin`
- 密码：`Admin123456`

MinIO 默认账号：

- 用户名：`minioadmin`
- 密码：`.env.docker` 中的 `MINIO_ROOT_PASSWORD`

RabbitMQ 默认账号：

- 用户名：`lab`
- 密码：`rabbitmq-password`

### 5. 健康检查与运行验证

建议至少检查以下项：

```bash
docker compose ps
docker compose logs -f backend-api
docker compose logs -f monitor-worker
docker compose logs -f inference-worker
docker compose logs -f identity-worker
curl http://127.0.0.1/health
curl http://127.0.0.1/api/system/status
```

当前主 Compose 文件已为 `inference-worker` 和 `identity-worker` 申请 GPU，`monitor-worker` / `monitor-worker-1` 只负责采集、调度、跟踪与规则处理。API 层会根据 `camera_id` 将请求稳定路由到对应分片。首次部署后建议同时验证两个 GPU Worker 容器内 CUDA 是否可见。

### 6. 代码更新规范

- 修改 `backend/app`：
  - 通常执行：`docker compose restart backend-api monitor-worker monitor-worker-1 inference-worker identity-worker`
- 修改前端页面：
  - 需要重新构建前端镜像
- 修改依赖、Dockerfile、底层运行库：
  - 需要重新 build 对应服务

常用命令：

```bash
docker compose restart backend-api monitor-worker monitor-worker-1 inference-worker identity-worker
docker compose up -d --build frontend
docker compose up -d --build
```

### 7. 构建缓存

当前 Dockerfile 已使用 BuildKit 缓存：

- 后端 API：`pip cache`
- Worker：`uv cache + pip cache`
- 前端：`pnpm store cache`

### 8. MinIO 运维说明

默认对象桶：

- `lab-safety-monitor`
- `lab-safety-videos`
- `lab-safety-faces`

Web 访问：

- 控制台：`http://localhost:9001` 或 `http://服务器IP:9001`
- API 端点：`http://localhost:9000` 或 `http://服务器IP:9000`

默认账号：

- 用户名：`minioadmin`
- 密码：`.env.docker` 中的 `MINIO_ROOT_PASSWORD`

容器内访问：

```bash
docker compose exec minio sh
```

前端与后端不直接暴露桶路径，对象预览默认走 API 代理：

- `/api/events/objects/{bucket}/{object_key}`

### 9. RabbitMQ 运维说明

当前部署使用 RabbitMQ 作为采集与批量推理之间的消息中间件，默认队列包括：

- `lab-safety.inference.frames`
- `lab-safety.inference.results`
- `lab-safety.identity.frames`
- `lab-safety.identity.results`

Web 访问：

- Management：`http://localhost:15672` 或 `http://服务器IP:15672`
- AMQP：`localhost:5672` 或 `服务器IP:5672`

默认账号：

- 用户名：`lab`
- 密码：`rabbitmq-password`

### 10. 数据库运维说明

宿主机连接 PostgreSQL：

- Host: `127.0.0.1`
- Port: `5432`
- User: `postgres`
- Password: `change-postgres-password`
- Database: `sentinelvision`

数据库初始化在服务启动时自动执行。需要手工迁移时执行：

```bash
alembic upgrade head
```

### 11. 海康 SDK 说明

宿主机目录：

```text
./HCNetSDKV6.1.11.5_build20251204_linux64_ZH/库文件
```

容器内目录：

```text
/opt/hikvision-sdk
```

默认优先使用海康 SDK 拉流，RTSP 为回退方案。

## 常见问题

### 1. 改了代码但页面或接口没变化

- 后端代码改动后先重启 `backend-api` 和 `monitor-worker`
- 前端页面改动后需要重新 build `frontend`

### 2. 登录后是否会自动过期

Docker 环境默认 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES=0`，表示不自动过期。

### 3. 为什么部分全局事件没有短视频

- 区域漏巡、未巡检、车间超员等规则类事件通常没有对应单路视频证据
- 摄像头实时违规更适合生成事件前后短视频

### 4. 为什么区域漏巡不是实时触发

区域漏巡按巡检窗口结算，不是逐帧触发。

### 5. 为什么车间超员只报一次

系统包含事件去重与冷却机制，避免同一状态连续刷出重复告警。
