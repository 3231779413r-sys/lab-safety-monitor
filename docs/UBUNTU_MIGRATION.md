# Ubuntu 迁移部署说明

本文档说明如何把“碳丝聚合车间操作人员危险行为识别系统”从当前 Windows 开发环境迁移到 Ubuntu 服务器。目标不是只把程序跑起来，而是让接手开发人员能理解迁移涉及的组件、配置来源、启动顺序和常见故障定位方式。

## 1. 目标运行架构

Ubuntu 上建议按下面方式部署：

```text
浏览器
  |
  v
Next.js 前端 :3000
  |
  | HTTP / WebSocket
  v
FastAPI 后端 :8000
  |
  +-- PostgreSQL：业务数据、事件、人员、摄像头配置
  |
  +-- MinIO：违规快照图片对象存储
  |
  +-- Hikvision HCNetSDK：海康摄像头 SDK 登录、取流、配置读取和设置
  |
  +-- AI 模型权重：YOLO / SAM 等检测模型
```

推荐服务器目录。核心原则是：系统环境只安装基础底座，项目依赖、模型、数据和 SDK 都放到独立目录，避免 Ubuntu 系统环境被污染。

```text
/opt/lab-safety-monitor
├── app
│   ├── backend
│   │   └── .venv
│   └── frontend
├── data
│   └── minio
├── models
│   ├── ppe_detector
│   ├── sam2
│   └── sam3
├── hikvision-sdk
│   ├── lib
│   └── include
└── logs
```

### 环境隔离原则

Ubuntu 本机部署时要尽量避免把所有东西都装进系统环境。建议按下面方式分层：

```text
系统层：
Ubuntu apt 安装的基础工具、PostgreSQL、MinIO、RabbitMQ、NVIDIA 驱动、ffmpeg、OpenCV 系统库。

Python 层：
后端单独使用 app/backend/.venv 虚拟环境，不要把 Python 依赖装到系统 Python。

Node 层：
前端使用项目自己的 node_modules，Node.js 版本固定为 22，pnpm 版本固定为 9.15。

模型和数据层：
模型权重放到 /opt/lab-safety-monitor/models，业务数据放到 /opt/lab-safety-monitor/data。

SDK 层：
海康 Linux SDK 单独放到 /opt/lab-safety-monitor/hikvision-sdk/lib，通过 HIKVISION_SDK_DIR 指向。
```

推荐 `.env` 中显式指定这些路径：

```env
BASE_DIR=/opt/lab-safety-monitor/app/backend
DATA_DIR=/opt/lab-safety-monitor/data
WEIGHTS_DIR=/opt/lab-safety-monitor/models
HIKVISION_SDK_DIR=/opt/lab-safety-monitor/hikvision-sdk/lib
CAMERA_CAPTURE_BACKEND=hikvision_sdk
```

这样做的好处是：

```text
升级 Python 依赖不会影响系统 Python。
升级前端依赖不会影响其他 Node 项目。
模型权重和业务数据不会跟代码发布混在一起。
海康 SDK 可以独立替换 Linux 版本，不需要复制到系统库目录。
备份时可以清楚地区分代码、数据、模型和 SDK。
```

## 2. 需要迁移的内容

从 Windows 迁移到 Ubuntu 时，至少要确认这些内容：

```text
项目代码：backend、frontend、docs 等目录
后端配置：backend/.env
前端配置：frontend/.env.local
数据库数据：PostgreSQL 数据库或导出的 SQL
MinIO 数据：违规快照对象文件和桶配置
模型权重：/opt/lab-safety-monitor/models 或 .env 中指定的模型路径
海康 SDK：必须换成 Linux x64 版本，不能直接复用 Windows DLL
摄像头账号：IP、端口、用户名、密码、通道号、码流类型
```

特别注意：当前 Windows SDK 路径下的 `.dll` 文件不能在 Ubuntu 使用。Ubuntu 需要海康官方 Linux x64 HCNetSDK，里面核心文件通常是 `.so` 动态库，例如 `libhcnetsdk.so`。

## 3. Ubuntu 基础环境

建议使用 Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS。先安装基础依赖：

```bash
sudo apt update
sudo apt install -y git curl wget unzip build-essential pkg-config
sudo apt install -y python3.11 python3.11-venv python3-pip
sudo apt install -y ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1
sudo apt install -y postgresql postgresql-contrib
```

如果服务器需要使用 Docker 运行 MinIO，也安装 Docker：

```bash
sudo apt install -y ca-certificates gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

执行 `usermod` 后需要重新登录一次终端，普通用户才可以直接执行 `docker`。

如果使用 NVIDIA GPU，先确认驱动可用：

```bash
nvidia-smi
```

如果 `nvidia-smi` 不可用，先不要急着启动 AI 检测。需要先安装匹配的 NVIDIA 驱动和 CUDA 运行环境，否则 PyTorch 可能只能跑 CPU，实时检测性能会明显下降。

## 4. Node.js 与 pnpm

前端是 Next.js，建议使用 Node.js 22 和 pnpm 9.15：

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
corepack enable
corepack prepare pnpm@9.15.0 --activate
node -v
pnpm -v
```

如果服务器无法访问外网，需要提前准备 Node.js、pnpm 以及前端依赖缓存，或者在内网配置 npm 镜像源。

## 5. 项目代码放置

创建部署目录：

```bash
sudo mkdir -p /opt/lab-safety-monitor
sudo chown -R $USER:$USER /opt/lab-safety-monitor
mkdir -p /opt/lab-safety-monitor/app
cd /opt/lab-safety-monitor/app
```

如果使用 Git：

```bash
git clone <your-repo-url> .
```

如果使用压缩包迁移，解压后确保目录结构类似：

```text
/opt/lab-safety-monitor/app
├── backend
├── frontend
├── docs
└── README.md
```

## 6. 后端 Python 环境

进入后端目录并创建虚拟环境：

```bash
cd /opt/lab-safety-monitor/app/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip uv
uv sync
```

如果需要安装 SAM2 相关依赖：

```bash
uv sync --extra sam2
```

首次迁移建议不要一开始就打开 SAM、多尺度检测和高检测帧率。先用轻量配置把“摄像头取流 -> YOLO 检测 -> 事件入库 -> 快照上传 -> 前端展示”这条主链路跑通，再逐步打开重功能。

## 7. PostgreSQL 配置

创建数据库用户和数据库：

```bash
sudo -u postgres psql
```

在 `psql` 中执行：

```sql
CREATE USER sentinelvision WITH PASSWORD 'strong-password';
CREATE DATABASE sentinelvision OWNER sentinelvision;
GRANT ALL PRIVILEGES ON DATABASE sentinelvision TO sentinelvision;
\q
```

后端 `.env` 中的数据库连接要与这里一致：

```env
DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@localhost:5432/sentinelvision
```

执行数据库迁移：

```bash
cd /opt/lab-safety-monitor/app/backend
source .venv/bin/activate
alembic upgrade head
```

验证迁移状态：

```bash
alembic current
```

如果是从旧服务器迁移真实数据，可以使用 `pg_dump` 和 `psql`：

```bash
# 旧环境导出
pg_dump -h <old-host> -U <old-user> -d sentinelvision -Fc -f sentinelvision.dump

# Ubuntu 新环境恢复
pg_restore -h localhost -U sentinelvision -d sentinelvision --clean --if-exists sentinelvision.dump
```

恢复真实数据后，仍建议执行一次：

```bash
alembic upgrade head
```

这样可以补齐新版本代码需要的表结构字段。

## 8. MinIO 配置

违规事件快照建议全部保存到 MinIO。单机部署可以用 Docker 启动 MinIO：

```bash
mkdir -p /opt/lab-safety-monitor/data/minio

docker run -d \
  --name lab-minio \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v /opt/lab-safety-monitor/data/minio:/data \
  minio/minio server /data --console-address ":9001"
```

检查 MinIO 是否可用：

```bash
curl http://localhost:9000/minio/health/live
```

后端配置示例：

```env
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=lab-safety-monitor
MINIO_SECURE=false
```

生产环境不要继续使用 `minioadmin/minioadmin`。建议单独创建访问密钥，只给后端使用，并限制 MinIO 控制台 `9001` 端口的访问范围。

桶设计建议：

```text
桶名：lab-safety-monitor

对象路径：
snapshots/{camera_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg
```

这样做的好处是：

```text
按摄像头和日期分区，后期清理和归档方便
数据库只需要保存 bucket、object_key、content_type、size 等元数据
前端访问图片时由后端生成 presigned URL，避免公开 MinIO 账号密码
```

如果从旧环境迁移 MinIO 数据，可以复制旧 MinIO 的数据目录，或者使用 `mc mirror`：

```bash
mc alias set oldminio http://<old-host>:9000 <old-access-key> <old-secret-key>
mc alias set newminio http://<new-host>:9000 <new-access-key> <new-secret-key>
mc mirror oldminio/lab-safety-monitor newminio/lab-safety-monitor
```

## 9. 模型权重迁移

默认模型权重建议放在后端目录下：

```text
/opt/lab-safety-monitor/models
├── ppe_detector
│   └── YOLOv8 Finetuning for PPE detection.pt
├── sam2
│   └── sam2.1_hiera_base_plus.pt
└── sam3
    └── sam3.pt
```

如果权重放在其他目录，需要在 `.env` 中修改路径：

```env
WEIGHTS_DIR=/opt/lab-safety-monitor/models
YOLOV11_MODEL_PATH=/opt/lab-safety-monitor/models/ppe_detector/best.pt
SAM2_MODEL_PATH=/opt/lab-safety-monitor/models/sam2/sam2.1_hiera_base_plus.pt
SAM3_MODEL_PATH=/opt/lab-safety-monitor/models/sam3/sam3.pt
```

首次上线建议使用保守配置：

```env
USE_SAM2=false
USE_SAM3=false
MULTI_SCALE_ENABLED=false
SHOW_MASKS=false
LIVE_STREAM_PROCESS_FPS=2
```

等确认摄像头、数据库、MinIO、前端都稳定后，再逐步提高 `LIVE_STREAM_PROCESS_FPS`，并按 GPU 显存情况决定是否开启 SAM。

## 10. 海康 Hikvision SDK

Ubuntu 不能直接使用 Windows 目录里的海康 SDK。需要下载海康官方 Linux x64 版本 HCNetSDK，并放到类似目录：

```text
/opt/lab-safety-monitor/hikvision-sdk
├── lib
│   ├── libhcnetsdk.so
│   ├── libPlayCtrl.so
│   └── 其他 .so 依赖
└── include
```

配置动态库搜索路径：

```bash
echo "/opt/lab-safety-monitor/hikvision-sdk/lib" | sudo tee /etc/ld.so.conf.d/hikvision.conf
sudo ldconfig
ldconfig -p | grep hcnetsdk
```

后端 `.env` 中设置：

```env
CAMERA_CAPTURE_BACKEND=hikvision_sdk
HIKVISION_SDK_DIR=/opt/lab-safety-monitor/hikvision-sdk/lib
HIKVISION_SDK_PORT=8000
```

网络检查：

```bash
ping <camera-ip>
nc -vz <camera-ip> 8000
```

如果摄像头 SDK 登录失败，优先检查：

```text
摄像头 IP 是否能 ping 通
SDK 端口是否是 8000
账号密码是否正确
摄像头是否允许 SDK / 平台接入
Linux SDK 版本和摄像头固件是否兼容
HIKVISION_SDK_DIR 是否指向 .so 所在目录
ldconfig 后是否能找到 libhcnetsdk.so
```

## 11. 后端 .env 推荐配置

在 Ubuntu 上复制模板：

```bash
cd /opt/lab-safety-monitor/app/backend
cp .env.example .env
```

建议生产初始配置如下，先追求稳定：

```env
DEBUG=False
APP_ENV=prod
SQL_ECHO=false

DATABASE_URL=postgresql+asyncpg://sentinelvision:strong-password@localhost:5432/sentinelvision
JWT_SECRET_KEY=请替换成随机长字符串

MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=lab-safety-monitor
MINIO_SECURE=false

CAMERA_CAPTURE_BACKEND=hikvision_sdk
HIKVISION_SDK_DIR=/opt/lab-safety-monitor/hikvision-sdk/lib
HIKVISION_SDK_PORT=8000

LIVE_STREAM_DISPLAY_FPS=20
CAMERA_MONITOR_DISPLAY_FPS=20
LIVE_STREAM_PROCESS_FPS=2
LIVE_STREAM_QUEUE_SIZE=2
LIVE_STREAM_INTERPOLATE=false

CAMERA_MONITOR_ENABLED=true
CAMERA_MONITOR_MAX_CAMERAS=4

USE_SAM2=false
USE_SAM3=false
MULTI_SCALE_ENABLED=false
SHOW_MASKS=false
```

这里要理解两个帧率：

```text
LIVE_STREAM_PROCESS_FPS：AI 检测帧率，影响 GPU/CPU 压力和事件检测频率
LIVE_STREAM_DISPLAY_FPS / CAMERA_MONITOR_DISPLAY_FPS：前端显示目标帧率，影响浏览器观看流畅度
```

稳定性优先时，建议检测帧率先低一些，例如 2 到 5 FPS；显示帧率可以是 15 到 25 FPS。检测帧率不需要等于显示帧率。

## 12. 前端配置

进入前端目录：

```bash
cd /opt/lab-safety-monitor/app/frontend
pnpm install
```

创建或修改 `frontend/.env.local`：

```env
NEXT_PUBLIC_API_URL=http://服务器IP:8000
NEXT_PUBLIC_WS_URL=ws://服务器IP:8000
```

构建：

```bash
pnpm build
```

开发方式启动：

```bash
pnpm dev
```

生产方式启动：

```bash
pnpm start
```

如果前端通过 Nginx 反向代理访问后端，需要同步检查后端 CORS 配置，确保前端域名在允许列表中。

## 13. 手动启动顺序

推荐首次迁移时用手动方式启动，方便看日志。

启动 MinIO：

```bash
docker start lab-minio
```

启动后端：

```bash
cd /opt/lab-safety-monitor/app/backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动前端：

```bash
cd /opt/lab-safety-monitor/app/frontend
pnpm start
```

验证：

```bash
curl http://localhost:8000/health
curl http://localhost:9000/minio/health/live
curl http://localhost:3000
```

## 14. systemd 后台服务

手动启动确认稳定后，再使用 systemd 托管。

后端服务文件 `/etc/systemd/system/lab-safety-backend.service`：

```ini
[Unit]
Description=Lab Safety Monitor Backend
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/lab-safety-monitor/app/backend
Environment="PATH=/opt/lab-safety-monitor/app/backend/.venv/bin"
Environment="LD_LIBRARY_PATH=/opt/lab-safety-monitor/hikvision-sdk/lib"
ExecStart=/opt/lab-safety-monitor/app/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
User=ubuntu

[Install]
WantedBy=multi-user.target
```

启用后端：

```bash
sudo systemctl daemon-reload
sudo systemctl enable lab-safety-backend
sudo systemctl start lab-safety-backend
sudo journalctl -u lab-safety-backend -f
```

前端服务文件 `/etc/systemd/system/lab-safety-frontend.service`：

```ini
[Unit]
Description=Lab Safety Monitor Frontend
After=network.target

[Service]
WorkingDirectory=/opt/lab-safety-monitor/app/frontend
ExecStart=/usr/bin/pnpm start
Restart=always
RestartSec=5
User=ubuntu
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```

启用前端：

```bash
sudo systemctl daemon-reload
sudo systemctl enable lab-safety-frontend
sudo systemctl start lab-safety-frontend
sudo journalctl -u lab-safety-frontend -f
```

## 15. 端口和防火墙

默认端口：

```text
3000：Next.js 前端
8000：FastAPI 后端
9000：MinIO API
9001：MinIO 控制台
5432：PostgreSQL，通常只允许本机访问
摄像头 8000：海康 SDK 端口
```

如果启用了 Ubuntu 防火墙：

```bash
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
sudo ufw allow 9000/tcp
sudo ufw allow 9001/tcp
sudo ufw status
```

生产环境更推荐只开放前端端口或 Nginx 的 80/443，由 Nginx 反向代理后端，MinIO 控制台不要直接暴露到公网。

## 16. 验证清单

建议按下面顺序验证，这样出问题时更容易定位：

```text
1. PostgreSQL 能连接，alembic current 正常
2. 后端能启动，curl /health 正常
3. MinIO 能访问，/minio/health/live 正常
4. 后端能创建或访问 MinIO 桶
5. 模型权重路径存在，后端日志没有权重加载错误
6. Hikvision SDK 能初始化，能登录摄像头
7. /api/cameras 能返回摄像头列表
8. 摄像头配置页能读取配置
9. 实时监控页能看到画面
10. 产生违规事件后，数据库有事件记录，MinIO 有快照图片
11. 历史数据中心能分页查看事件和图片
12. 稳定运行后，再逐步提高检测帧率或开启 SAM
```

常用命令：

```bash
cd /opt/lab-safety-monitor/app/backend
source .venv/bin/activate
alembic current
curl http://localhost:8000/health
curl http://localhost:8000/api/cameras
curl http://localhost:9000/minio/health/live
```

## 17. 常见问题定位

`libhcnetsdk.so` 找不到：

```text
检查 HIKVISION_SDK_DIR 是否指向 Linux SDK 的 lib 目录。
检查 /etc/ld.so.conf.d/hikvision.conf 是否写入 SDK lib 路径。
执行 sudo ldconfig 后，再用 ldconfig -p | grep hcnetsdk 确认。
```

摄像头登录失败：

```text
检查摄像头 IP、SDK 端口 8000、用户名、密码、通道号。
确认摄像头后台允许 SDK 或平台接入。
确认服务器和摄像头在同一网络或路由可达。
```

实时画面卡顿：

```text
先降低 LIVE_STREAM_PROCESS_FPS，例如 2 或 5。
关闭 USE_SAM2、USE_SAM3、MULTI_SCALE_ENABLED、SHOW_MASKS。
确认不是摄像头码流质量或网络丢包导致。
确认前端显示帧率和 AI 检测帧率不是被错误绑定成同一个值。
```

违规快照不显示：

```text
检查 MinIO 是否运行。
检查 MINIO_BUCKET、MINIO_ENDPOINT、MINIO_ACCESS_KEY、MINIO_SECRET_KEY。
检查数据库中事件是否保存了 snapshot_bucket 和 snapshot_object_key。
检查后端生成 presigned URL 时是否报错。
```

数据库字段不存在：

```text
通常是代码已经更新，但数据库迁移没有执行。
进入 backend 后执行 alembic upgrade head。
如果是生产数据库，执行前先备份。
```

前端无法访问后端：

```text
检查 NEXT_PUBLIC_API_URL 和 NEXT_PUBLIC_WS_URL。
检查后端 CORS 配置。
检查防火墙、Nginx 反向代理和端口开放情况。
```

PyTorch 没有使用 GPU：

```bash
python - <<'PY'
import torch
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU only")
PY
```

如果输出 `False`，需要检查 NVIDIA 驱动、CUDA 版本和当前安装的 PyTorch 版本是否匹配。

## 18. 推荐迁移顺序

最终建议按这个顺序执行：

```text
1. 准备 Ubuntu 基础依赖、Python、Node.js、pnpm、Docker
2. 放置项目代码到 /opt/lab-safety-monitor
3. 安装后端 Python 依赖
4. 安装前端依赖并完成构建
5. 创建 PostgreSQL 数据库并执行 alembic upgrade head
6. 启动 MinIO，并确认桶和访问密钥
7. 复制模型权重，并先关闭 SAM 和多尺度检测
8. 安装 Linux 版 Hikvision HCNetSDK，并配置 ldconfig
9. 配置 backend/.env 和 frontend/.env.local
10. 手动启动后端和前端，逐项验证接口、页面和摄像头
11. 确认实时监控和违规快照链路稳定
12. 改成 systemd 服务后台运行
13. 再根据服务器性能逐步提高检测帧率、摄像头数量和高级模型功能
```

迁移时最重要的原则是：先跑通主链路，再追求高帧率和更多模型能力。这样即使出现问题，也能快速判断是环境、SDK、数据库、对象存储，还是 AI 计算性能导致的。
