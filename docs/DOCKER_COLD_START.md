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

## 2. 正式镜像放在哪里

FunWithCam 计划发布到 GitHub Container Registry：

```text
ghcr.io/easonzs/funwithcam:latest
```

go2rtc 使用 FunWithCam 发布的 Xiaomi 音频修正版：

```text
ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-fix.2
```

官方 1.9.14 的 Xiaomi CS2 发送代码会把媒体头误写进音频 payload，表现为
接口成功但摄像头播放静态噪音。修正版同时为
`chuangmi.camera.061a01` 使用 40ms Opus 封包，并在镜像构建时运行对应
Go 回归测试。仓库中的 `.github/workflows/publish-image.yml` 会在
`main` 分支相关代码更新或推送 `v*` 标签时构建 FunWithCam 和修正版 go2rtc
的 `linux/arm64`、`linux/amd64` 镜像。

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

## 3. 新 Mac 冷启动

前提：

- 安装并启动 Docker Desktop；
- 新 Mac 与小米摄像头处于同一家庭局域网；
- 可以访问 GitHub 和 GHCR。

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
4. 从 GHCR 拉取 FunWithCam 镜像，从 Docker Hub 拉取 go2rtc；
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

`update.sh` 会拉取 Git 中最新部署文件、拉取新镜像并替换容器，同时保留
`runtime/` 中所有家庭数据。

如果现有 `.env` 仍精确使用 `alexxit/go2rtc:1.9.14`，脚本会自动切换到
Xiaomi 扬声器噪音修正版；其他用户自定义的 go2rtc 镜像不会被覆盖。

开发调试时可显式从当前源码构建：

```bash
./deploy.sh --build
```

该选项不是面向普通用户的新 Mac 安装路径。

## 7. 当前尚未包含的能力

迁移模式暂不集成到 `deploy.sh`。后续迁移将以冷启动流程为基础，在启动前
导入经过校验和备份的 `runtime/` 数据包。
