# FunWithCam 新 Mac 冷启动部署

本文档只描述“新电脑、重新配置”的冷启动模式。旧电脑数据迁移暂不纳入
一键流程。

## 1. 三类动作不要混用

| 动作 | 在哪里执行 | 用途 |
|---|---|---|
| GitHub Actions 构建并推送镜像 | GitHub 云端 | 正式发布 FunWithCam 新版本 |
| `./deploy.sh` | 新 Mac | 下载镜像、初始化配置、创建账号并启动 |
| `docker save ...` | 已经拥有镜像的旧 Mac/发布机 | 可选的离线安装包制作 |

以下命令不是新 Mac 的初始化步骤：

```bash
docker save \
  funwithcam:local \
  go2rtc-xiaomi-audio:local \
  | gzip > backups/funwithcam-images-arm64.tar.gz
```

它只能把本机已经存在的镜像打成离线文件，不能从源码生成镜像，也不会启动
服务。正常联网部署使用 GHCR，不需要运行这条命令。

## 2. 正式部署需要哪些镜像

新 Mac 实际运行两个业务镜像，二者都发布到 GitHub Container Registry
（GHCR）：

| 镜像 | 地址 | 包含内容 |
|---|---|---|
| FunWithCam | `ghcr.io/easonzs/funwithcam:latest` | Python 3.11、应用代码、SQLite 库、OpenCV、MediaPipe、FFmpeg、espeak-ng、人脸与语音模型 |
| go2rtc | `ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-ptz.1` | go2rtc 运行程序、小米 CS2 payload 修复、`chuangmi.camera.061a01` 的 40ms Opus 适配、PTZ 云台控制 API |

镜像从 GHCR 拉取后保存在 Docker Desktop 管理的本地镜像存储中，可通过
`docker image ls` 查看；它们不是普通文件，不位于项目的 `runtime/` 目录。
`runtime/` 只保存配置和家庭数据。需要制作离线文件时才使用 `docker save`。

官方 1.9.14 的 Xiaomi CS2 发送代码会把媒体头误写进音频 payload，表现为
接口成功但摄像头播放静态噪音。修正版同时为
`chuangmi.camera.061a01` 使用 40ms Opus 封包，并在镜像构建时运行对应
Go 回归测试。仓库中的 `.github/workflows/publish-image.yml` 会在
`main` 分支相关代码更新或推送 `v*` 标签时构建 FunWithCam 和修正版 go2rtc
的 `linux/arm64`、`linux/amd64` 镜像。

`python:3.11-slim-bookworm`、`golang:1.24.9-alpine` 和
`alexxit/go2rtc:1.9.14` 是 GitHub Actions 构建镜像时使用的基础镜像，不是
新 Mac 需要额外启动的第三、第四个服务。Go 模块由固定的 Go 1.24.9 构建阶段
通过 `GOPROXY=https://proxy.golang.org,direct` 下载并编译；最终 go2rtc
运行镜像只复制编译后的二进制，不包含 Go 工具链。普通新 Mac 不安装 Go。

首次发布后的可见性有两种选择：

- **Public**：新 Mac 可以匿名拉取，最符合傻瓜式部署；但任何人也可以下载
  镜像并查看其中的应用代码和模型。
- **Private**：不公开镜像；新 Mac 首次部署前需要先执行
  `docker login ghcr.io`。

无论选择哪种方式，镜像都不包含家庭 SQLite、摄像头 Token、人脸资料或
`.env`，这些内容只存在于部署电脑的 `runtime/`。不要在未确认代码公开范围
前直接把 Package 改为 Public。

在 GitHub Actions 首次成功、Package 可见性配置完成之前，这个地址只是预定
地址，不能认为镜像已经可下载。

截至 2026 年 10 月 18 日，本机对上述 GHCR 地址执行匿名 manifest 检查均返回
`unauthorized`。这只能说明镜像目前不能匿名拉取，可能是 Package 仍为
Private，也可能是对应标签尚未成功发布；不能据此认定公开冷启动已经可用。
发布验收必须同时满足：

1. GitHub Actions 的两个镜像构建任务均成功；
2. GHCR 中能看到上述标签及 `linux/arm64`、`linux/amd64` 两个平台；
3. 若 Package 保持 Private，新 Mac 已执行 `docker login ghcr.io`；
4. 在新 Mac 上执行 `docker pull` 能分别拉取两个镜像。

## 3. 新 Mac 冷启动

前提：

- 安装并启动 Docker Desktop；
- 新 Mac 与小米摄像头处于同一家庭局域网；
- 可以访问 GitHub 和 GHCR。

如果 GHCR Package 保持 Private，先创建具备 `read:packages` 权限的 GitHub
Personal Access Token，然后执行：

```bash
printf '%s' '<TOKEN>' | docker login ghcr.io -u '<GITHUB_USERNAME>' --password-stdin
```

不要把 Token 写入仓库、`.env` 或部署文档。

执行：

```bash
git clone git@github.com:easonzs/funWithCam.git
cd funWithCam
./deploy.sh
```

`deploy.sh` 会依次完成：

1. 检查 Docker Desktop；
2. 从 `.env.example` 生成 `.env`，已存在时绝不覆盖；
3. 创建 `runtime/config`、`runtime/data` 和 `runtime/audio`；
4. 从 GHCR 分别拉取 FunWithCam 和定制 go2rtc 两个镜像；
5. 启动 go2rtc，并打开其本地管理页面；
6. 引导通过 `Add -> Xiaomi` 完成小米登录、验证码和摄像头选择；
7. 要求视频流固定命名为 `study_room`；
8. 启动 FunWithCam；
9. 首次创建 `parent` 家长账号，密码只写入 SQLite 的哈希，不写入 `.env`；
10. 通过 FFprobe 实际读取 `study_room`，确认视频和音频轨道均可用；
11. 输出 Web 地址和排障命令。

初始化后访问：

```text
http://127.0.0.1:8000
```

手机访问时使用新 Mac 的家庭局域网 IP：

```text
http://<新Mac局域网IP>:8000
```

## 4. 配置和数据保存在哪里

`.env` 保存非敏感的部署参数：

- 镜像地址；
- Web、go2rtc 端口；
- 家长账号名；
- 时区。

小米 Token、设备地址和视频流配置保存在：

```text
runtime/config/go2rtc.yaml
```

FunWithCam 设置保存在：

```text
runtime/config/funwithcam.yaml
```

账号哈希、日程、作业、学习记录和执行历史保存在：

```text
runtime/data/funwithcam.db
```

校准图、人脸资料和后续生成的数据也位于 `runtime/data/`。这些目录挂载到
容器中，因此替换镜像和重建容器不会删除家庭数据。

人脸模型、语音模型、Python、SQLite 库、OpenCV、MediaPipe、FFmpeg 和语音
识别依赖均放入 FunWithCam 镜像，新 Mac 不再单独下载模型或安装 Python。

## 5. 小米数据流不通过时

部署脚本会区分两类失败：

- 没有 `study_room`：说明 go2rtc 尚未保存摄像头配置；
- 已有 `study_room`，但读取不到视频或音频：说明摄像头连接、局域网、Token
  或音轨仍有问题。

按顺序检查：

```bash
open http://127.0.0.1:1984
docker compose logs --tail=100 go2rtc
cat runtime/config/go2rtc.yaml
./scripts/diagnose.sh
```

还应确认新 Mac 和摄像头处于同一局域网、摄像头 IP 可达。修复后重新运行
`./deploy.sh` 即可；已有 `.env`、账号和家庭数据不会被覆盖。

## 6. 有新代码时如何更新

不要登录运行中的容器复制代码。容器内修改在重建后会丢失，也无法可靠回滚。

正确流程：

1. 开发机修改代码并通过测试；
2. 推送 Git；
3. GitHub Actions 生成新镜像并推送 GHCR；
4. 新 Mac 执行：

```bash
./update.sh
```

`update.sh` 会拉取 Git 中最新部署文件、分别拉取两个镜像并替换容器，同时
保留 `runtime/` 中所有家庭数据。更新后会继续检查 go2rtc API、FunWithCam
健康状态和 `study_room` 视频/音频轨道；容器已启动但媒体检查失败时不会报告
“更新成功”。

如果现有 `.env` 使用 `alexxit/go2rtc:1.9.14`、`fix.1` 或 `fix.2`，脚本会
自动切换到 `fix.3`；其他用户自定义的 go2rtc 镜像不会被覆盖。

开发调试时可显式从当前源码构建：

```bash
./deploy.sh --build
```

该选项不是面向普通用户的新 Mac 安装路径。它会额外拉取
`python:3.11-slim-bookworm`、`golang:1.24.9-alpine` 和
`alexxit/go2rtc:1.9.14` 等构建基础镜像，并在本机下载 Python/Go 依赖。

如果浏览器或终端可以访问 Docker Hub，但 Docker 构建停在
`load metadata for docker.io/library/golang` 并最终
`context deadline exceeded`，说明 Docker Desktop 虚拟机没有使用宿主机代理，
不是 Golang 标签不存在。应在 Docker Desktop 中配置 HTTP/HTTPS 代理并重启，
或让本机代理允许来自 Docker 虚拟机的连接。普通正式部署不要用 `--build`
绕过 GHCR 发布问题。

## 7. 当前尚未包含的能力

迁移模式暂不集成到 `deploy.sh`。后续迁移将以冷启动流程为基础，在启动前
导入经过校验和备份的 `runtime/` 数据包。
