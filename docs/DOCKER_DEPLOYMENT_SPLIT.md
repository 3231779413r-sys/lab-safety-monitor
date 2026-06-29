# Docker 拆分部署说明

本文档对应当前仓库的 6 个容器服务：

1. `frontend`
2. `backend-api`
3. `monitor-worker`
4. `postgres`
5. `minio`
6. `nginx`

## 1. 服务职责

### `frontend`
- Next.js 前端
- 容器内监听 `3000`
- 推荐由 `nginx` 统一对外暴露

### `backend-api`
- FastAPI 接口层
- 负责登录、人员管理、相机管理、监管配置、事件查询、WebSocket
- 不运行摄像头检测循环
- 通过内部 HTTP 调用 `monitor-worker`

### `monitor-worker`
- 负责摄像头拉流、海康 SDK、模型推理、人脸识别、告警判定、事件入库
- 只给这一项分配 GPU
- 暴露内部端口 `8001`，仅供 `backend-api` 调用

### `postgres`
- 业务数据库
- 同时承担 API 与 worker 间的实时通知桥

### `minio`
- 对象存储
- 保存人脸图、抓拍图、告警快照

### `nginx`
- 统一入口
- 反代 `/` 到前端
- 反代 `/api/` 和 `/api/ws` 到 `backend-api`

## 2. 目录和挂载

当前 `docker-compose.yml` 采用以下持久化方式：

- `postgres_data`：PostgreSQL 数据卷
- `minio_data`：MinIO 数据卷
- `./data:/data`：后端数据目录
- `./backend/weights:/app/weights`：模型权重
- `./HCNetSDKV6.1.11.5_build20251204_linux64_ZH/库文件:/opt/hikvision-sdk`：海康 Linux SDK
- `./logs/api:/logs/api`：API 日志
- `./logs/worker:/logs/worker`：worker 日志

启动前建议先创建：

```bash
mkdir -p data/videos data/snapshots data/processed
mkdir -p logs/api logs/worker
```

## 3. 环境变量

复制并修改根目录 `.env.docker`，至少替换以下值：

- `POSTGRES_PASSWORD`
- `MINIO_ROOT_PASSWORD`
- `MINIO_SECRET_KEY`
- `JWT_SECRET_KEY`
- `WORKER_INTERNAL_TOKEN`

默认会在首次启动时自动创建一个管理员账号，来自 `.env.docker`：

```env
INIT_ADMIN_USERNAME=admin
INIT_ADMIN_PASSWORD=Admin123456
```

生产环境应立即改掉这组默认值。

如果通过 nginx 统一访问，保持：

```env
NEXT_PUBLIC_API_URL=
```

这样前端会直接走当前域名下的 `/api` 和 `/api/ws`。

## 4. GPU 要求

`monitor-worker` 使用 Compose 的 GPU 保留配置：

```yaml
services:
  monitor-worker:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

宿主机需要提前满足：

1. 已安装 NVIDIA 驱动
2. 已安装 Docker
3. 已安装 `nvidia-container-toolkit`

验证方式：

```bash
docker run --rm --gpus all nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04 nvidia-smi
```

当前仓库默认 `docker-compose.yml` 已为 `monitor-worker` 申请 GPU。

## 5. 启动步骤

```bash
docker compose --env-file .env.docker build
docker compose --env-file .env.docker up -d
```

查看状态：

```bash
docker compose ps
docker compose logs -f backend-api
docker compose logs -f monitor-worker
```

如果只想先验证配置渲染：

```bash
docker compose --env-file .env.docker config
```

## 6. 验证项

### 前端
- 访问 `http://服务器IP/`
- 登录页正常打开

### API
- 访问 `http://服务器IP/health`
- 返回健康状态

### 数据写入
- 新增人员
- 新增相机
- 修改监管配置

### 检测链路
- 启用相机后，`/api/cameras/runtime/all/status` 有运行状态
- 监控页面可以打开实时画面
- 检测后事件可入库
- 抓拍图可以通过 MinIO 预签名地址访问

## 7. 当前实现说明

为了不额外引入 Redis/RabbitMQ，这个拆分版本使用 PostgreSQL `pg_notify` 做 worker 到 API 的实时消息转发。这样前端 WebSocket 仍然只连 `backend-api`，但告警事件来自 `monitor-worker`。
