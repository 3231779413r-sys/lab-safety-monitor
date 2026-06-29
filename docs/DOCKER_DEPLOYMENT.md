# Docker 部署教程

本文档说明如何在 Ubuntu 服务器上安装 Docker，并使用 Docker / Docker Compose 部署本系统需要的基础服务和应用服务。目标是把 PostgreSQL、MinIO、RabbitMQ、FastAPI 后端、Next.js 前端尽量统一放到 Docker 中管理，方便迁移、重启、备份和交付。

文档默认使用 Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS。示例目录以 `/opt/lab-safety-monitor` 为准。

## 1. 部署目标架构

推荐最终架构：

```text
浏览器
  |
  | http://服务器IP:3000
  v
frontend 容器：Next.js
  |
  | http://服务器IP:8000 或反向代理地址
  v
backend 容器：FastAPI / Uvicorn
  |
  +-- postgres 容器：业务数据库
  |
  +-- minio 容器：违规事件快照图片
  |
  +-- rabbitmq 容器：消息队列，预留给通知、告警、异步任务
  |
  +-- 宿主机挂载：模型权重目录
  |
  +-- 宿主机挂载：海康 Linux SDK .so 动态库目录
```

有两种部署方式：

```text
半容器化：
PostgreSQL、MinIO、RabbitMQ 在 Docker 中运行，后端和前端仍在宿主机运行。

全容器化：
PostgreSQL、MinIO、RabbitMQ、后端、前端全部由 Docker Compose 管理。
```

推荐生产环境使用全容器化，服务关系更清晰，重启和迁移更方便。

## 2. 重要概念

Docker Compose 网络里，容器之间不要用 `localhost` 互相访问。`localhost` 在容器内部表示“当前容器自己”，不是宿主机，也不是其他容器。

如果后端在宿主机运行：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@localhost:5432/sentinelvision
MINIO_ENDPOINT=localhost:9000
RABBITMQ_URL=amqp://lab:rabbitmq-password@localhost:5672/
```

如果后端在 Docker 容器里运行：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@postgres:5432/sentinelvision
MINIO_ENDPOINT=minio:9000
RABBITMQ_URL=amqp://lab:rabbitmq-password@rabbitmq:5672/
```

前端也要特别注意：浏览器运行在用户电脑上，不在 Docker 网络里。所以前端页面访问后端时，不能写 `http://backend:8000`。浏览器应该访问服务器 IP、域名或 Nginx 地址：

```env
NEXT_PUBLIC_API_URL=http://服务器IP:8000
NEXT_PUBLIC_WS_URL=ws://服务器IP:8000
```

## 3. Ubuntu 安装 Docker

### 3.1 使用 Docker 官方源安装

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证：

```bash
docker --version
docker compose version
sudo docker run hello-world
```

把当前用户加入 `docker` 组：

```bash
sudo usermod -aG docker $USER
```

执行后需要退出 SSH 并重新登录。重新登录后验证：

```bash
docker ps
```

### 3.2 使用阿里云 Docker apt 源安装

如果服务器访问 Docker 官方源慢，可以把 Docker apt 源换成阿里云：

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker-aliyun.gpg
sudo chmod a+r /etc/apt/keyrings/docker-aliyun.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker-aliyun.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证：

```bash
docker --version
docker compose version
```

## 4. 配置 Docker 镜像加速

Docker apt 源解决的是 Docker 程序本身下载安装速度。镜像加速解决的是 `docker pull postgres`、`docker pull minio/minio` 这类镜像拉取速度。

创建或修改 `/etc/docker/daemon.json`：
加入阿里云镜像加速：
示例配置：
https://***.mirrors.aliyuncs.com
```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://registry.cn-hangzhou.aliyuncs.com",
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF
```

重启 Docker：

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

验证配置：

```bash
docker info | grep -A 10 "Registry Mirrors"
```

如果公司或学校有自己的镜像仓库，优先使用内部镜像源。

## 5. 服务器目录规划

创建部署目录：

```bash
sudo mkdir -p /opt/lab-safety-monitor
sudo chown -R $USER:$USER /opt/lab-safety-monitor
cd /opt/lab-safety-monitor
```

推荐目录：

```text
/opt/lab-safety-monitor
├── backend
├── frontend
├── docker
│   ├── docker-compose.yml
│   ├── .env.docker
│   ├── backend.Dockerfile
│   └── frontend.Dockerfile
├── docker-data
│   ├── postgres
│   ├── minio
│   └── rabbitmq
├── hikvision-sdk
│   └── lib
└── logs
```

其中：

```text
docker-data/postgres：PostgreSQL 数据
docker-data/minio：MinIO 图片对象数据
docker-data/rabbitmq：RabbitMQ 数据
hikvision-sdk/lib：海康 Linux SDK 的 .so 文件目录
backend/weights：AI 模型权重目录
```

生产环境一定要把 `docker-data` 纳入备份。

## 6. 基础服务 Compose 部署

如果你暂时只想把 PostgreSQL、MinIO、RabbitMQ 放到 Docker，可以先创建基础服务 Compose 文件。

创建目录：

```bash
mkdir -p /opt/lab-safety-monitor/docker
mkdir -p /opt/lab-safety-monitor/docker-data/postgres
mkdir -p /opt/lab-safety-monitor/docker-data/minio
mkdir -p /opt/lab-safety-monitor/docker-data/rabbitmq
cd /opt/lab-safety-monitor/docker
```

创建 `docker-compose.yml`：

```yaml
services:
  postgres:
    image: postgres:16
    container_name: lab-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: sentinelvision
      POSTGRES_PASSWORD: strong-password
      POSTGRES_DB: sentinelvision
      TZ: Asia/Shanghai
    ports:
      - "5432:5432"
    volumes:
      - ../docker-data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sentinelvision -d sentinelvision"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio:latest
    container_name: lab-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin-password
      TZ: Asia/Shanghai
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - ../docker-data/minio:/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:3.13-management
    container_name: lab-rabbitmq
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: lab
      RABBITMQ_DEFAULT_PASS: rabbitmq-password
      TZ: Asia/Shanghai
    ports:
      - "5672:5672"
      - "15672:15672"
    volumes:
      - ../docker-data/rabbitmq:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
```

启动：

```bash
docker compose up -d
docker compose ps
```

查看日志：

```bash
docker compose logs -f postgres
docker compose logs -f minio
docker compose logs -f rabbitmq
```

## 7. PostgreSQL 说明

PostgreSQL 用来保存用户、摄像头、人员、违规事件、统计数据等业务数据。

连接测试：

```bash
docker exec -it lab-postgres psql -U sentinelvision -d sentinelvision
```

进入后可以执行：

```sql
\dt
\q
```

如果后端运行在宿主机，`backend/.env` 使用：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@localhost:5432/sentinelvision
```

如果后端运行在 Docker，使用：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@postgres:5432/sentinelvision
```

注意：PostgreSQL 容器第一次启动时会初始化用户名、密码和数据库。初始化完成后，如果你只修改 Compose 里的 `POSTGRES_PASSWORD`，旧数据目录不会自动改密码。需要进入数据库修改密码，或者删除旧 volume 后重新初始化。

修改密码示例：

```bash
docker exec -it lab-postgres psql -U sentinelvision -d sentinelvision
```

```sql
ALTER USER sentinelvision WITH PASSWORD 'new-strong-password';
\q
```

## 8. MinIO 说明

MinIO 用来保存违规事件快照图片。后端把图片上传到 MinIO，数据库只保存图片对象的元数据，例如 bucket、object key、content type、size。

访问控制台：

```text
http://服务器IP:9001
```

示例账号：

```text
用户名：minioadmin
密码：minioadmin-password
```

建议创建桶：

```text
lab-safety-monitor
```

对象路径建议：

```text
snapshots/{camera_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg
```

如果后端运行在宿主机：

```env
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin-password
MINIO_BUCKET=lab-safety-monitor
MINIO_SECURE=false
```

如果后端运行在 Docker：

```env
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin-password
MINIO_BUCKET=lab-safety-monitor
MINIO_SECURE=false
```

生产环境建议：

```text
不要使用默认 root 账号给后端长期访问。
在 MinIO 控制台创建单独 access key。
不要把 9001 控制台端口暴露到公网。
图片访问尽量走后端生成的 presigned URL。
```

## 9. RabbitMQ 说明

RabbitMQ 是消息队列。当前系统如果还没有强依赖 RabbitMQ，也可以先部署作为基础设施预留，后续用于：

```text
违规事件异步通知
短信、邮件、企业微信、钉钉告警
事件图片后处理
异步报表生成
多摄像头任务调度
```

管理后台：

```text
http://服务器IP:15672
```

示例账号：

```text
用户名：lab
密码：rabbitmq-password
```

连接地址：

```env
RABBITMQ_URL=amqp://lab:rabbitmq-password@localhost:5672/
```

如果后端运行在 Docker：

```env
RABBITMQ_URL=amqp://lab:rabbitmq-password@rabbitmq:5672/
```

生产环境建议不要把 `5672` 和 `15672` 暴露到公网。如果必须远程访问管理后台，建议通过 VPN、堡垒机或 Nginx 加认证访问。

## 10. 后端 Dockerfile 示例

后端是 FastAPI，启动命令是：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

可以在 `/opt/lab-safety-monitor/docker/backend.Dockerfile` 中写：

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -U pip uv

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen

COPY backend/ ./

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

如果需要 SAM2：

```dockerfile
RUN uv sync --frozen --extra sam2
```

如果需要 GPU，普通 `python:3.11-slim` 不一定合适。建议使用 NVIDIA CUDA 基础镜像，并安装 NVIDIA Container Toolkit。后端项目使用 PyTorch CUDA 12.8 wheel，服务器驱动需要能支持对应 CUDA 运行时。

GPU 服务器需要安装：

```bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

验证 GPU 容器：

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

## 11. 海康 SDK 与后端容器

如果后端容器要使用海康 SDK，必须使用 Linux x64 版 HCNetSDK。Windows 的 `.dll` 不能放进 Linux 容器使用。

推荐目录：

```text
/opt/lab-safety-monitor/hikvision-sdk/lib
├── libhcnetsdk.so
├── libPlayCtrl.so
└── 其他 .so 依赖
```

Compose 中把 SDK 目录挂载进后端容器：

```yaml
volumes:
  - ../hikvision-sdk/lib:/opt/hikvision/lib:ro
```

后端环境变量：

```env
CAMERA_CAPTURE_BACKEND=hikvision_sdk
HIKVISION_SDK_DIR=/opt/hikvision/lib
HIKVISION_SDK_PORT=8000
```

如果容器启动时报找不到 `.so`，可以在 Compose 里增加：

```yaml
environment:
  LD_LIBRARY_PATH: /opt/hikvision/lib
```

也可以在后端 Dockerfile 里写入动态库路径，但用环境变量更灵活。

## 12. 前端 Dockerfile 示例

前端是 Next.js，`package.json` 中命令是：

```bash
pnpm build
pnpm start
```

可以在 `/opt/lab-safety-monitor/docker/frontend.Dockerfile` 中写：

```dockerfile
FROM node:22-slim AS deps

WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-slim AS builder

WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_WS_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL

RUN pnpm build

FROM node:22-slim AS runner

WORKDIR /app
ENV NODE_ENV=production

RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

COPY --from=builder /app ./

EXPOSE 3000

CMD ["pnpm", "start"]
```

注意：`NEXT_PUBLIC_API_URL` 和 `NEXT_PUBLIC_WS_URL` 会被前端代码使用。浏览器访问后端时不能用 `http://backend:8000`，因为 `backend` 只是 Docker 内部服务名，浏览器解析不了。

生产示例：

```env
NEXT_PUBLIC_API_URL=http://192.168.1.100:8000
NEXT_PUBLIC_WS_URL=ws://192.168.1.100:8000
```

如果使用 Nginx 和域名：

```env
NEXT_PUBLIC_API_URL=https://lab.example.com
NEXT_PUBLIC_WS_URL=wss://lab.example.com
```

## 13. 全量 Docker Compose 示例

下面是完整示例，包含 PostgreSQL、MinIO、RabbitMQ、后端、前端。

假设目录：

```text
/opt/lab-safety-monitor
├── backend
├── frontend
├── docker
│   ├── docker-compose.yml
│   ├── backend.Dockerfile
│   └── frontend.Dockerfile
├── docker-data
├── hikvision-sdk
└── logs
```

`/opt/lab-safety-monitor/docker/docker-compose.yml`：

```yaml
services:
  postgres:
    image: postgres:16
    container_name: lab-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: sentinelvision
      POSTGRES_PASSWORD: strong-password
      POSTGRES_DB: sentinelvision
      TZ: Asia/Shanghai
    volumes:
      - ../docker-data/postgres:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sentinelvision -d sentinelvision"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio:latest
    container_name: lab-minio
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin-password
      TZ: Asia/Shanghai
    volumes:
      - ../docker-data/minio:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:3.13-management
    container_name: lab-rabbitmq
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: lab
      RABBITMQ_DEFAULT_PASS: rabbitmq-password
      TZ: Asia/Shanghai
    volumes:
      - ../docker-data/rabbitmq:/var/lib/rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: ..
      dockerfile: docker/backend.Dockerfile
    container_name: lab-backend
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    environment:
      DEBUG: "False"
      APP_ENV: prod
      SQL_ECHO: "false"
      DATABASE_URL: postgresql+asyncpg://sentinelvision:strong-password@postgres:5432/sentinelvision
      JWT_SECRET_KEY: please-change-to-a-long-random-secret
      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin-password
      MINIO_BUCKET: lab-safety-monitor
      MINIO_SECURE: "false"
      CAMERA_CAPTURE_BACKEND: hikvision_sdk
      HIKVISION_SDK_DIR: /opt/hikvision/lib
      HIKVISION_SDK_PORT: "8000"
      LD_LIBRARY_PATH: /opt/hikvision/lib
      LIVE_STREAM_DISPLAY_FPS: "20"
      CAMERA_MONITOR_DISPLAY_FPS: "20"
      LIVE_STREAM_PROCESS_FPS: "2"
      CAMERA_MONITOR_MAX_CAMERAS: "4"
      USE_SAM2: "false"
      USE_SAM3: "false"
      MULTI_SCALE_ENABLED: "false"
      SHOW_MASKS: "false"
    volumes:
      - ../backend/weights:/app/weights
      - ../data:/data
      - ../hikvision-sdk/lib:/opt/hikvision/lib:ro
    ports:
      - "8000:8000"

  frontend:
    build:
      context: ..
      dockerfile: docker/frontend.Dockerfile
      args:
        NEXT_PUBLIC_API_URL: http://服务器IP:8000
        NEXT_PUBLIC_WS_URL: ws://服务器IP:8000
    container_name: lab-frontend
    restart: unless-stopped
    depends_on:
      - backend
    ports:
      - "3000:3000"
```

启动：

```bash
cd /opt/lab-safety-monitor/docker
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

## 14. 后端数据库迁移

第一次启动后，需要执行 Alembic 迁移：

```bash
cd /opt/lab-safety-monitor/docker
docker compose exec backend uv run alembic upgrade head
```

检查迁移状态：

```bash
docker compose exec backend uv run alembic current
```

如果日志出现类似 `column xxx does not exist`，通常说明数据库结构没有迁移到最新版本，先执行：

```bash
docker compose exec backend uv run alembic upgrade head
```

## 15. MinIO 桶初始化

方式一：通过控制台创建。

```text
访问：http://服务器IP:9001
登录：minioadmin / minioadmin-password
创建桶：lab-safety-monitor
```

方式二：使用 MinIO Client 容器创建。

```bash
docker run --rm --network docker_default minio/mc sh -c "
  mc alias set local http://minio:9000 minioadmin minioadmin-password &&
  mc mb -p local/lab-safety-monitor || true
"
```

注意：`docker_default` 是 Compose 默认网络名，实际名称可能是“目录名_default”。可以用下面命令查看：

```bash
docker network ls
```

如果后端代码已经实现自动创建桶，也仍建议上线前手动确认桶存在。

## 16. 启动、停止和升级

启动全部服务：

```bash
docker compose up -d
```

重新构建并启动：

```bash
docker compose up -d --build
```

查看服务：

```bash
docker compose ps
```

查看全部日志：

```bash
docker compose logs -f
```

查看单个服务日志：

```bash
docker compose logs -f backend
docker compose logs -f postgres
docker compose logs -f minio
docker compose logs -f rabbitmq
docker compose logs -f frontend
```

重启单个服务：

```bash
docker compose restart backend
```

停止服务但保留数据：

```bash
docker compose down
```

停止服务并删除 volume 要非常谨慎。如果使用的是绑定目录 `../docker-data`，`docker compose down -v` 不会删除绑定目录，但仍不建议随便执行。

## 17. 数据备份与恢复

### 17.1 PostgreSQL 备份

备份：

```bash
cd /opt/lab-safety-monitor/docker
docker compose exec postgres pg_dump -U sentinelvision -d sentinelvision -Fc > sentinelvision.dump
```

恢复：

```bash
cat sentinelvision.dump | docker compose exec -T postgres pg_restore -U sentinelvision -d sentinelvision --clean --if-exists
```

也可以直接备份目录：

```bash
tar -czf postgres-data.tar.gz -C /opt/lab-safety-monitor/docker-data postgres
```

数据库运行中更推荐使用 `pg_dump`，一致性更好。

### 17.2 MinIO 备份

方式一：备份数据目录：

```bash
tar -czf minio-data.tar.gz -C /opt/lab-safety-monitor/docker-data minio
```

方式二：使用 `mc mirror`：

```bash
mc alias set local http://服务器IP:9000 minioadmin minioadmin-password
mc mirror local/lab-safety-monitor ./lab-safety-monitor-backup
```

恢复时可以反向 mirror：

```bash
mc mirror ./lab-safety-monitor-backup local/lab-safety-monitor
```

### 17.3 RabbitMQ 备份

如果只是预留 RabbitMQ，还没有实际业务队列，可以主要备份配置。后续真正使用后，需要备份：

```text
docker-data/rabbitmq 数据目录
exchange / queue / binding definitions
用户和权限配置
```

导出 definitions：

```bash
curl -u lab:rabbitmq-password http://localhost:15672/api/definitions > rabbitmq-definitions.json
```

## 18. 端口和安全建议

默认端口：

```text
3000：前端 Next.js
8000：后端 FastAPI
5432：PostgreSQL
9000：MinIO API
9001：MinIO Console
5672：RabbitMQ AMQP
15672：RabbitMQ Management
```

生产建议：

```text
前端 3000 可以通过 Nginx 暴露为 80/443。
后端 8000 可以通过 Nginx 反向代理。
PostgreSQL 5432 不建议开放公网。
RabbitMQ 5672 不建议开放公网。
RabbitMQ 15672 不建议开放公网。
MinIO 9001 控制台不建议开放公网。
MinIO 9000 如果只给后端用，也不建议开放公网。
```

如果使用 UFW：

```bash
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
sudo ufw allow 9000/tcp
sudo ufw allow 9001/tcp
sudo ufw status
```

更严格的生产配置只开放：

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

## 19. 后端对接检查

全容器化后，进入 Docker 目录：

```bash
cd /opt/lab-safety-monitor/docker
```

检查后端健康：

```bash
curl http://localhost:8000/health
```

检查前端：

```bash
curl http://localhost:3000
```

检查数据库迁移：

```bash
docker compose exec backend uv run alembic current
```

检查系统状态接口：

```bash
curl http://localhost:8000/api/system/status
```

检查 MinIO：

```bash
curl http://localhost:9000/minio/health/live
```

检查 RabbitMQ：

```bash
curl -u lab:rabbitmq-password http://localhost:15672/api/overview
```

## 20. 常见问题

### 20.1 Docker 拉镜像很慢

检查 `/etc/docker/daemon.json` 是否配置镜像加速：

```bash
docker info | grep -A 10 "Registry Mirrors"
```

如果没有生效：

```bash
sudo systemctl restart docker
```

### 20.2 后端容器连不上 PostgreSQL

如果后端也在 Docker 中，`DATABASE_URL` 不能用 `localhost`：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@postgres:5432/sentinelvision
```

检查 postgres 容器：

```bash
docker compose ps postgres
docker compose logs postgres
```

### 20.3 前端页面访问后端失败

前端给浏览器用的地址不能写 Docker 服务名：

```env
错误：NEXT_PUBLIC_API_URL=http://backend:8000
正确：NEXT_PUBLIC_API_URL=http://服务器IP:8000
```

如果用了 Nginx 和域名：

```env
NEXT_PUBLIC_API_URL=https://lab.example.com
NEXT_PUBLIC_WS_URL=wss://lab.example.com
```

### 20.4 PostgreSQL 修改密码不生效

PostgreSQL 初始化后，账号密码已经写入数据目录。修改 Compose 环境变量不会自动修改旧数据库密码。

解决方式：

```bash
docker exec -it lab-postgres psql -U sentinelvision -d sentinelvision
```

```sql
ALTER USER sentinelvision WITH PASSWORD 'new-strong-password';
\q
```

### 20.5 MinIO 桶不存在

登录控制台：

```text
http://服务器IP:9001
```

创建：

```text
lab-safety-monitor
```

或者使用 `mc mb` 创建。

### 20.6 后端找不到海康 SDK

确认挂载：

```bash
docker compose exec backend ls -lah /opt/hikvision/lib
```

确认环境变量：

```bash
docker compose exec backend printenv | grep HIKVISION
docker compose exec backend printenv | grep LD_LIBRARY_PATH
```

Linux 容器必须使用 `.so`：

```text
正确：libhcnetsdk.so、libPlayCtrl.so
错误：HCNetSDK.dll、PlayCtrl.dll
```

### 20.7 GPU 容器无法使用 CUDA

检查宿主机：

```bash
nvidia-smi
```

检查 Docker：

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

如果失败，先修 NVIDIA 驱动和 NVIDIA Container Toolkit。

### 20.8 容器重启后数据丢失

检查 Compose 是否挂载了持久化目录：

```yaml
volumes:
  - ../docker-data/postgres:/var/lib/postgresql/data
  - ../docker-data/minio:/data
  - ../docker-data/rabbitmq:/var/lib/rabbitmq
```

不要只依赖容器内部文件系统。容器删除后，内部数据会丢。

## 21. 推荐部署顺序

建议按下面顺序执行：

```text
1. 安装 Docker 和 Docker Compose
2. 配置 Docker 镜像加速
3. 创建 /opt/lab-safety-monitor 目录
4. 放置项目代码
5. 准备 docker-data 持久化目录
6. 先启动 PostgreSQL、MinIO、RabbitMQ
7. 确认 PostgreSQL 能连接
8. 确认 MinIO 控制台能访问，并创建 bucket
9. 确认 RabbitMQ 管理后台能访问
10. 构建后端镜像
11. 启动后端容器
12. 执行 alembic upgrade head
13. 构建前端镜像
14. 启动前端容器
15. 浏览器访问前端页面
16. 测试实时监控、事件入库、快照上传
17. 再考虑 Nginx、HTTPS、防火墙、安全加固
```

上线时不要一开始就打开最高检测帧率、SAM、多尺度和多摄像头。先用保守配置跑通主链路，再逐步提高性能参数。
