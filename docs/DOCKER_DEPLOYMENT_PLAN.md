# FunWithCam Docker 一键部署方案

更新日期：2026-10-13

## 1. 目标

最终希望家长只需要：

1. 安装并启动 Docker Desktop；
2. 下载 FunWithCam 部署包；
3. 执行一次 `./deploy.sh`；
4. 打开浏览器完成摄像头、家长账号、孩子身份和动作基准配置；
5. 此后服务开机自动运行。

部署过程不再要求用户单独安装 Python、OpenCV、MediaPipe、FFmpeg、
sherpa-onnx、go2rtc 或手工执行数据库迁移。

第一版部署目标是当前 MacBook（Apple Silicon，`arm64`），同时为后续
Linux `amd64` / `arm64` 主机预留兼容能力。

## 2. 先说明：Docker 能解决什么，不能直接解决什么

Docker 很适合封装：

- Python 3.11 与 Python 包；
- FastAPI Web 服务；
- OpenCV、MediaPipe 和 FFmpeg；
- 人脸识别、姿态识别和本地语音识别模型；
- SQLite 数据库迁移；
- go2rtc；
- 固定的启动命令、健康检查和自动重启策略。

Docker 不会自动解决：

- 摄像头账号、密码和家庭局域网地址；
- 摄像头本身断网或不再输出媒体包；
- macOS 的 `say` 命令，因为 Linux 容器中不存在该命令；
- MacBook 麦克风和扬声器的透明透传；
- 首次人脸录入、动作基准采集和家长密码设置；
- 数据备份、模型授权和版本升级兼容。

因此，“一键部署”应理解为：

> 一条命令启动完整软件栈，然后在 Web 首次设置向导中完成家庭专属配置。

## 3. 使用者需要了解的最少 Docker 知识

### 3.1 镜像与容器

- **镜像（Image）**：已经打包好的应用、系统库和依赖，类似安装包。
- **容器（Container）**：镜像运行后的实例，类似已经启动的应用进程。
- 删除并重建容器不应丢失家庭数据，数据必须放在持久化目录中。

### 3.2 Dockerfile

`Dockerfile` 描述如何制作 FunWithCam 镜像，当前包含：

- Python 3.11 Linux 基础环境；
- FFmpeg 和 OpenCV 所需系统库；
- 项目 Python 依赖；
- FunWithCam 代码；
- 必需模型或模型下载/校验逻辑；
- 非 root 运行用户；
- 默认启动命令。

普通使用者不需要理解每一条 Dockerfile 指令，只需要在开发阶段知道：
修改依赖后通常要重新构建镜像。

### 3.3 Docker Compose

Compose 用一个 `compose.yaml` 管理多个服务。FunWithCam 推荐至少包含：

- `go2rtc`：连接小米摄像头，提供视频和音频流；
- `funwithcam`：Web、提醒、语音识别、人脸与学习中监测。

常用命令最终应简化为脚本，但排障时需要知道：

```bash
docker compose up -d
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

`down` 只停止并删除容器，不能附带 `-v`，否则可能删除由 Docker 管理的
持久化卷。

### 3.4 端口

建议对宿主机开放：

| 端口 | 服务 | 用途 |
| --- | --- | --- |
| `8000` | FunWithCam | 家长 Web |
| `1984` | go2rtc | 本机调试和流状态 |
| `8554` | go2rtc | RTSP 调试；应用容器内部可直接访问 |

正式配置中可将 go2rtc 的 `1984` 仅绑定到 `127.0.0.1`，避免同一局域网的
其他设备直接打开无鉴权的 go2rtc 管理页面。FunWithCam 的 `8000` 才对家庭
局域网开放。

### 3.5 容器网络

最容易踩坑的一点：

> 容器里的 `127.0.0.1` 只代表该容器自己，不代表 Mac，也不代表另一个容器。

当前本机配置是：

```yaml
video:
  stream_url: rtsp://127.0.0.1:8554/study_room
```

容器化后应改为：

```yaml
video:
  stream_url: rtsp://go2rtc:8554/study_room
```

这里的 `go2rtc` 是 Compose 服务名。第一版优先使用 Compose 默认的 bridge
网络，不默认启用 `network_mode: host`：

- FunWithCam 只需通过服务名访问 go2rtc；
- 当前小米源使用摄像头固定局域网 IP 和 TCP，可由 go2rtc 容器主动连接；
- bridge 网络在 macOS、Linux 上更一致；
- 如果后续使用依赖 UDP、广播发现、HomeKit 或 WebRTC 的摄像头，再单独验证
  host networking。

### 3.6 持久化目录

以下内容不能只写在容器内部：

```text
runtime/
├── config/
│   ├── funwithcam.yaml
│   └── go2rtc.yaml
├── data/
│   ├── funwithcam.db
│   ├── calibrations/
│   ├── face_debug/
│   └── checkins/
├── models/
└── backups/
```

建议第一版使用 bind mount，方便家长备份和开发排障：

- `runtime/data` 挂载为 `/app/data`；
- `runtime/config/funwithcam.yaml` 挂载为只读配置；
- `runtime/config/go2rtc.yaml` 挂载到 go2rtc 的 `/config/go2rtc.yaml`；
- 人脸模型和语音模型直接放入 FunWithCam 镜像，新 Mac 不再维护
  `runtime/models`。

### 3.7 配置与密钥

普通非敏感参数可以放在 `.env` 或 FunWithCam YAML 中，例如：

- Web 端口；
- 时区；
- 日志等级；
- 数据目录；
- 是否启用语音识别。

以下内容不能提交到 Git，也不能写进镜像：

- 小米摄像头账号、令牌和设备凭据；
- 家长密码；
- 未来可能增加的 API Token 或证书。

小米凭据建议保存在：

```text
runtime/config/go2rtc.yaml
```

文件权限设置为仅当前用户可读。后续可以进一步使用 Compose secrets，让敏感
文件只挂载给 go2rtc 服务。

### 3.8 健康检查与自动重启

“进程存在”不等于“服务可用”。至少需要三个层次：

1. **存活检查**：FunWithCam Web 进程能否响应；
2. **就绪检查**：数据库、模型和配置是否可用；
3. **媒体检查**：go2rtc 的视频/音频包是否持续增长。

当前 `/health` 只返回 Web 存活状态。后续应增加：

```text
/health/live
/health/ready
/health/media
```

Compose 使用 `healthcheck` 和 `depends_on: condition: service_healthy` 控制启动
顺序，并使用：

```yaml
restart: unless-stopped
```

实现 Docker Desktop 重启后的自动恢复。

### 3.9 日志

排障时最重要的命令：

```bash
docker compose ps
docker compose logs --tail=200 funwithcam
docker compose logs --tail=200 go2rtc
```

应用日志必须明确区分：

- Web 正常但视频源异常；
- go2rtc 可访问但媒体包停止；
- 模型不存在；
- 摄像头身份验证失败；
- 音频输入失败；
- TTS 输出失败；
- SQLite 写入失败。

## 4. 当前服务的依赖盘点

### 4.1 FunWithCam 应用

| 类型 | 当前依赖 |
| --- | --- |
| 运行时 | Python 3.11 |
| Web | FastAPI、Uvicorn、Jinja2 |
| 视觉 | OpenCV contrib、MediaPipe、NumPy |
| 人脸 | YuNet、SFace ONNX 模型 |
| 语音识别 | sherpa-onnx 中文流式模型 |
| 摄像头音频 | FFmpeg |
| 打卡视频 | FFmpeg |
| 数据库 | SQLite |
| 配置 | PyYAML |
| 语音输出 | `espeak-ng` 生成音频，经 go2rtc 推送到摄像头扬声器 |

当前 `models` 目录约 115 MB，其中包括人脸模型和本地中文语音识别模型。

### 4.2 go2rtc

go2rtc 负责：

- 使用小米私有地址连接摄像头；
- 向 FunWithCam 提供 RTSP 视频；
- 向语音识别提供摄像头音频；
- 向打卡功能提供可录制的音视频流；
- 暴露媒体包数，帮助判断摄像头是否真的持续输出。

正式部署不直接使用官方 go2rtc 1.9.14，而使用
`ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-ptz.1`。GitHub Actions 以
`golang:1.24.9-alpine` 为构建阶段，通过 Go Modules 下载依赖、应用仓库中的
Xiaomi 音频与 PTZ 补丁并执行 Go 测试；最终运行阶段基于
`alexxit/go2rtc:1.9.14`，只复制编译后的 go2rtc 二进制。因此新 Mac 只拉取
最终镜像，不需要安装 Go，也不会在启动时下载 Go 依赖。两个最终镜像均构建
`linux/amd64` 和 `linux/arm64`。

### 4.3 必须持久化的数据

| 数据 | 是否私密 | 是否必须备份 |
| --- | --- | --- |
| SQLite 数据库 | 是 | 是 |
| 人脸特征 | 是 | 是 |
| 动作与光线基准 | 是 | 是 |
| 校准原图和标注图 | 是 | 建议 |
| 打卡视频 | 是 | 按家庭策略 |
| 人脸调试图 | 高度敏感 | 排障后可清理 |
| go2rtc 小米凭据 | 高度敏感 | 是，需加密保存 |
| 模型文件 | 否 | 可重新下载 |

## 5. 推荐的一键部署架构

```text
家庭局域网
    │
    ├── 小米摄像头 <摄像头局域网IP>
    │       │ 视频 + 摄像头麦克风 + 可选扬声器回传
    │       ▼
    ├── go2rtc 容器
    │       ├── 内部 RTSP：go2rtc:8554/study_room
    │       └── 本机调试：http://127.0.0.1:1984
    │
    └── FunWithCam 容器
            ├── Web：http://MacBook局域网IP:8000
            ├── SQLite：/app/data/funwithcam.db
            ├── 视觉检测、人脸识别、学习中监测
            ├── 摄像头音频语音识别
            ├── 打卡视频录制
            └── TTS 生成与播放
```

第一版不需要拆分数据库、Web、视觉和语音识别为多个容器。它们共享状态较多，
继续保留在一个 FunWithCam 进程中更简单，也符合当前家庭单机部署规模。

## 6. 语音输入和输出怎么处理

### 6.1 语音输入：推荐摄像头模式

容器化后默认使用：

```yaml
speech:
  input_source: camera
```

理由：

- 音频已经在 go2rtc 流里；
- FFmpeg 可以直接在容器中读取；
- 不需要把 MacBook 麦克风透传给 Linux 容器；
- 更适合未来迁移到 Linux 小主机或 NAS。

保留 `macbook` 输入模式作为非 Docker 开发模式。Docker Desktop 下直接透传
Mac 麦克风会显著增加部署复杂度，不应作为一键部署默认路径。

### 6.2 语音输出：摄像头模式已实现

Docker 默认使用：

```yaml
audio:
  output_source: camera
  command: ["espeak-ng", "-v", "cmn", "{text}"]
  camera_api_url: http://go2rtc:1984
  camera_stream: study_room
  camera_audio_dir: /app/audio
  camera_go2rtc_audio_dir: /media
```

FunWithCam 容器使用 `espeak-ng` 在 `/app/audio` 生成临时 WAV；Compose 将
宿主机 `runtime/audio` 同时挂载给 FunWithCam 的 `/app/audio` 和 go2rtc 的
`/media`。应用先将音频转换为 48kHz 双声道 WAV，再调用 go2rtc
`/api/streams` 显式编码为 Opus 并播放。go2rtc 对该目录使用只读挂载即可，
因为音频文件由 FunWithCam 写入。

当前 Xiaomi 流已确认具有 `audio, sendonly, OPUS/48000/2` 反向音频通道，
并已在真实设备完成播放验证。家长可在“配置 → 摄像头与声音”查看通道状态并
点击“播放测试语音”；结果和输出来源会写入执行历史。

官方 go2rtc 1.9.14 的 Xiaomi CS2 `WritePacket` 实现会把媒体头再次复制到
payload，导致接口返回成功但摄像头只播放静态噪音。Compose 默认使用
`ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-ptz.1`：它基于固定上游提交，
应用 payload 修复，并为 `chuangmi.camera.061a01` 使用 40ms Opus 封包；
镜像构建时会运行对应 Go 回归测试。

非 Docker 的 macOS 安装仍可设置 `audio.output_source: macbook`，继续使用
本机 `say`。Docker Desktop 不透传 MacBook 扬声器，因此容器默认固定使用
`camera`。

## 7. 当前容器实现与仍需注意的边界

### 7.1 缺少正式人脸时 Web 仍可启动

`funwithcam serve` 会先启动 Web。数据库尚未录入正式孩子或家长身份时，后台
提醒线程进入等待并定期重试，不会再导致整个容器退出。`/health/ready` 会通过
`setup_required` 和 `missing_identities` 明确报告缺少的资料。

完整启用提醒仍需按顺序完成：

```text
启动 Web
  ↓
创建家长账号
  ↓
配置并验证摄像头
  ↓
录入孩子和家长身份
  ↓
录入动作与光线基准
  ↓
系统进入正式运行
```

### 7.2 容器使用独立配置模板

`config/config.docker.yaml` 已使用 Compose 服务名和容器内路径：

```yaml
video:
  stream_url: rtsp://go2rtc:8554/study_room

calibration:
  image_dir: /app/data/calibrations

speech:
  model_path: /app/models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23

identity:
  detector_model: /app/models/face_detection_yunet_2023mar.onnx
  recognizer_model: /app/models/face_recognition_sface_2021dec.onnx
  debug_dir: /app/data/face_debug
```

部署脚本首次执行时将该模板复制到
`runtime/config/funwithcam.yaml`，后续执行会保留家庭现有配置。

### 7.3 健康检查边界

当前已提供 `/health/live` 和 `/health/ready`。Compose 用 `live` 判断进程
是否存活；部署脚本另外执行 `media-check --require-audio`，确认
`study_room` 的视频和音频可以读取。

仍未提供持续查询媒体包增长的 `/health/media`，因此运行一段时间后的摄像头
断流主要依赖执行历史、日志和 `scripts/diagnose.sh` 排查。

### 7.4 模型交付方式

人脸模型由仓库复制进镜像；sherpa-onnx 模型在构建镜像时下载并执行 SHA-256
校验。普通新 Mac 只拉取已构建镜像，不在 `deploy.sh` 阶段重复下载模型文件。
开发者使用 `./deploy.sh --build` 时才会在本机构建过程中访问模型下载地址。
本机构建还会从 Docker Hub 拉取固定的 Go 和 go2rtc 基础镜像；Docker Desktop
使用独立虚拟机网络，宿主机浏览器或 `curl` 能访问 Docker Hub，并不代表
Docker daemon 已经配置相同代理。正式新 Mac 部署只拉取 GHCR 成品镜像，不
经过 Go/Python 构建阶段。

## 8. 目标 Compose 结构（设计示例）

当前仓库已经按下面的结构实现 Compose。go2rtc 容器内 API 必须监听 `:1984`，
宿主机映射仍限制为 `127.0.0.1`，这样既支持容器间调用，也不会直接向局域网
开放无鉴权管理接口。

```yaml
name: funwithcam

services:
  go2rtc:
    image: ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-ptz.1
    restart: unless-stopped
    ports:
      - "127.0.0.1:1984:1984"
      - "127.0.0.1:8554:8554"
    volumes:
      - ./runtime/config/go2rtc.yaml:/config/go2rtc.yaml
      - ./runtime/audio:/media:ro
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "-", "http://localhost:1984/api/streams"]
      interval: 10s
      timeout: 5s
      retries: 6

  funwithcam:
    image: ghcr.io/easonzs/funwithcam:<固定版本>
    restart: unless-stopped
    depends_on:
      go2rtc:
        condition: service_healthy
    ports:
      - "${WEB_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:-8000}:8000"
    volumes:
      - ./runtime/data:/app/data
      - ./runtime/config/funwithcam.yaml:/app/config/config.yaml:ro
      - ./runtime/audio:/app/audio
    environment:
      TZ: Asia/Shanghai
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health/ready"]
      interval: 15s
      timeout: 5s
      retries: 6
```

正式文件中应固定经过验证的镜像版本，不直接使用不可预测的 `latest`。

## 9. 一键部署脚本应做什么

建议提供：

```text
deploy.sh
update.sh
backup.sh
restore.sh
diagnose.sh
```

### 9.1 `deploy.sh`

执行顺序：

1. 检查 Docker CLI 和 Docker daemon；
2. 检查 CPU 架构；
3. 检查端口 `8000`、`1984`、`8554` 是否冲突；
4. 创建 `runtime/config`、`runtime/data`、`runtime/audio`；
5. 从示例生成配置，但绝不覆盖已有配置；
6. 提示录入摄像头必要参数，或启动后进入 Web 设置；
7. 执行 `docker compose config` 验证最终配置；
8. 默认拉取 GHCR 正式镜像；只有 `--build` 才本地构建；
9. 启动 go2rtc 并引导通过 WebUI 配置小米摄像头；
10. 执行 `docker compose up -d`；
11. 等待健康检查；
12. 输出局域网访问地址和排障命令。

脚本必须可以重复执行，并遵循：

- 不覆盖现有数据库；
- 不删除校准图片、人脸特征和打卡视频；
- 不在控制台打印摄像头密钥；
- 任一步失败都说明具体原因；
- 不自动执行 `docker compose down -v`。

### 9.2 `diagnose.sh`

至少输出：

- Docker 是否运行；
- 两个容器是否启动、是否健康；
- 端口是否监听；
- go2rtc 是否存在 `study_room`；
- 视频与音频 packet 是否增长；
- FunWithCam 是否能读取最新帧；
- 模型文件是否齐全；
- SQLite 是否可读写；
- 最近 100 行去敏日志。

这可以避免把“go2rtc 页面能打开”误判为“摄像头流正常”。

### 9.3 `backup.sh`

备份范围：

- SQLite 数据库；
- `-wal` 和 `-shm` 文件的一致性处理，或先执行 SQLite 在线备份；
- 校准图片；
- 打卡视频；
- 配置文件；
- go2rtc 凭据。

备份包应加密，因为包含儿童人脸特征、学习记录和摄像头凭据。

## 10. Web 首次设置向导

为了让非技术用户真正可用，应增加 `/setup`：

### 步骤一：家长账号

- 输入用户名和密码；
- 显示密码强度；
- 创建后才能进入其他设置。

### 步骤二：摄像头

- 选择“小米摄像头”；
- 输入设备信息；
- 由后端生成 go2rtc 配置；
- 显示视频包、音频包是否持续增加；
- 显示实时截图供家长确认。

注意：如果 go2rtc 配置由 Web 修改，需要设计安全的配置写入与 go2rtc 重载
方式。FunWithCam 容器不应获得 Docker Socket 权限。

### 步骤三：语音输入和输出

- 默认选择摄像头麦克风；
- 测试语音识别；
- 选择“MacBook 播报代理”或“摄像头扬声器”；
- 播放测试话术并由家长确认。

### 步骤四：身份

- 录入孩子；
- 录入常进入书桌区域的家长；
- 分别进行验证；
- 不要求用户执行 CLI 命令。

### 步骤五：动作与光线基准

- 复用现有四步基准采集；
- 每一步展示确认图片；
- 完成后才启用正式监测。

### 步骤六：完成

- 展示局域网访问地址；
- 展示数据目录与备份按钮；
- 展示所有组件健康状态；
- 将系统状态从 `SETUP_REQUIRED` 切换为 `READY`。

## 11. 分阶段实施计划

### 阶段 A：容器化前置改造

- [x] 增加未初始化状态报告；
- [x] 正式人脸缺失时允许 Web 启动；
- [x] 将音频输出抽象为统一出口，而不是固定 shell 命令；
- [x] 增加容器配置模板和容器内路径；
- [ ] 增加 `/health/media`（`/health/live`、`/health/ready` 已完成）；
- [x] 数据和模型目录支持容器路径；
- [ ] 增加数据库安全关闭与备份命令。

### 阶段 B：基础 Docker 原型

- [x] 编写兼容 `arm64` / `amd64` 基础镜像的 Dockerfile；
- [x] 安装 FFmpeg、espeak-ng 和必要 Linux 系统库；
- [x] 编写 `.dockerignore`，排除数据库、密钥、调试照片和本地虚拟环境；
- [x] 编写 `compose.yaml`；
- [x] 容器内部使用 `go2rtc` 服务名；
- [ ] 验证 Apple Silicon 原生 `linux/arm64`；
- [ ] 验证 Web、视频、人脸、姿势、光线、摄像头语音识别和打卡录像。

### 阶段 C：一键部署和首次设置

- [x] 编写可重复执行并保留现有配置和数据的 `deploy.sh`；
- [ ] 实现 Web `/setup`；
- [x] 自动生成运行配置并验证 Compose；
- [x] 镜像构建时下载语音模型、校验哈希并失败重试；
- [ ] 端口冲突和目录权限检查；
- [x] 提供 `diagnose.sh`。

### 阶段 D：语音输出容器化

- [x] 验证 Xiaomi + go2rtc 双向音频；
- [x] 使用 Debian `espeak-ng` 生成中文离线音频；
- [x] 容器中生成音频；
- [x] 推送音频到摄像头扬声器；
- [x] 非 Docker macOS 保留 `say` 输出；
- [x] 播报期间暂停语音输入提交，避免再次识别系统播报。

### 阶段 E：发布与升级

- [ ] 构建 `linux/arm64` 和 `linux/amd64` 镜像；
- [ ] 使用版本号和镜像摘要，不依赖 `latest`；
- [ ] 升级前自动备份；
- [ ] 数据库迁移失败时停止新版本并保留旧数据；
- [ ] 提供回滚说明；
- [ ] 记录隐私、数据保留和删除策略。

## 12. 建议的验收标准

### 全新安装

- 空目录执行一次 `./deploy.sh`；
- 无需安装 Python、FFmpeg、OpenCV 或 go2rtc；
- Web 能进入首次设置；
- 配置摄像头后视频/音频包持续增加；
- 完成身份和基准录入后服务进入正式状态。

### 重启

- 重启 Docker Desktop 后两个服务自动恢复；
- SQLite、身份、日程、基准和打卡视频不丢失；
- 不需要重新配置摄像头。

### 功能

- 首次提醒、第二次提醒正常；
- Web 和语音开始/暂停/继续/结束正常；
- 学习中姿势、光线和离座监测正常；
- 摄像头音频语音识别正常；
- 打卡视频可以保存；
- 语音输出真实可听见。

### 故障

- 摄像头断流时 Web 仍可访问，并明确显示媒体异常；
- go2rtc 重启后应用可以恢复读取；
- 语音模型缺失时不影响 Web 基本管理；
- 数据库只读或磁盘不足时给出明确错误；
- 备份可以在另一台机器恢复。

## 13. 当前机器的实际情况

本次检查结果：

- Mac 为 Apple Silicon `arm64`；
- Docker CLI 和 Docker Compose 已安装；
- Docker Desktop daemon 当前没有运行；
- 当前 FFmpeg 已安装在 Mac，但 Docker 镜像仍需自行安装 FFmpeg；
- 当前项目模型约 115 MB；
- 当前家庭数据约 12 MB，不含未来增长的打卡视频；
- 当前 go2rtc 是宿主机二进制进程，配置位于
  `/Users/zhangsong05/go2rtc.yaml`；
- 现有 go2rtc 文件包含敏感小米凭据，迁移时只能复制到私密 runtime 目录，
  不能提交到 Git 或打入镜像。

## 14. 推荐的下一步

Dockerfile、Compose、部署脚本、未初始化 Web、健康检查和摄像头语音输出均已
进入当前实现。下一步重点是完成新 Mac 的全流程验收与发布自动化：

1. 在干净 Mac 上从 GHCR 冷启动；
2. 验证摄像头配置、家长账号、人脸和基准录入；
3. 验证视频、摄像头收音、摄像头播音和打卡录像；
4. 验证 `update.sh` 升级和升级前备份；
5. 为正式镜像增加版本标签和回滚演练。

## 15. 官方参考资料

- Docker Compose 快速开始：
  https://docs.docker.com/compose/gettingstarted/
- Docker 存储与持久化：
  https://docs.docker.com/engine/storage/
- Compose 启动顺序与健康检查：
  https://docs.docker.com/compose/how-tos/startup-order/
- Compose secrets：
  https://docs.docker.com/compose/how-tos/use-secrets/
- Docker Desktop 网络：
  https://docs.docker.com/desktop/features/networking/
- 多架构镜像：
  https://docs.docker.com/build/building/multi-platform/
- go2rtc Docker：
  https://github.com/AlexxIT/go2rtc/blob/master/docker/README.md
- go2rtc 双向音频和向摄像头播放音频：
  https://github.com/AlexxIT/go2rtc/blob/master/README.md
