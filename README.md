# FunWithCam

FunWithCam 是一个运行在家庭局域网内的本地学习助理。

当前仓库是独立于旧原型的新实现。当前版本已经形成“家长 Web 录入 → 书桌人物出现 → 人脸身份确认 → 今日简报 → 可配置第二次提醒”的本地闭环。

## 当前进度

- 产品与业务逻辑文档；
- 技术设计文档；
- 纯业务工作流状态机；
- SQLite 初始迁移；
- 当日工作流状态持久化；
- go2rtc 视频流采集与断流重连；
- MediaPipe 人物出现检测；
- macOS `say` 语音输出；
- 摄像头到语音输出的端到端测试命令；
- YuNet + SFace 本地人脸录入和多帧身份验证；
- 测试身份与正式身份隔离保存；
- FastAPI + Jinja2 家庭局域网 Web；
- 家长密码登录、会话 Cookie 和 CSRF 防护；
- 今日作业、今日课程、临时安排和每周固定课程录入；
- 上学日/休息日默认提醒时间与当天覆盖；
- 身份、日程和 `say` 集成的首次简报与第二次提醒；
- Web 开始、休息、继续、结束学习及会话区间记录；
- 开始和继续学习前的异步视频、区域、单人、身份和光线检查；
- Web 四步录入正常坐姿、写字、阅读和光线基准；
- 多帧计算正常范围，并保存可切换查看的原图和标注图；
- 基准草稿、历史版本和学习记录所用版本关联；
- 可选的 sherpa-onnx 本地中文语音口令；
- Web 可配置的受控语义匹配、确认话术与重复询问频次；
- 结合当天日程的结束学习逐项检查；
- 可配置学校作业、视频打卡和家长确认任务类型；
- go2rtc 视频与音频打卡录制、最长时长自动保存和执行历史；
- 单元测试。

## 本地开发

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e '.[test]'
python -m unittest discover -s tests -v
```

复制并检查配置：

```bash
cp config/config.example.yaml config/config.yaml
```

`video.stream_url` 保持指向 go2rtc 的本地输出：

```bash
rtsp://127.0.0.1:8554/study_room
```

初始化数据库并测试语音：

```bash
funwithcam init-db --database data/funwithcam.db
funwithcam audio-test --config config/config.yaml
```

确认 go2rtc 已经提供 `study_room` 流后，运行摄像头端到端测试：

```bash
funwithcam camera-test --config config/config.yaml
```

测试命令会在学习区域持续检测到人物后播放一次测试语音并退出。它不写入正式的每日提醒状态。

## 家长 Web

首次使用先创建或重置家长登录：

```bash
funwithcam create-admin \
  --config config/config.yaml \
  --database data/funwithcam.db \
  --username parent
```

只启动 Web 进行录入测试：

```bash
funwithcam web \
  --config config/config.yaml \
  --database data/funwithcam.db
```

手机与 MacBook 连接同一个家庭局域网后，访问：

```text
http://<MacBook 的局域网 IP>:8000
```

页面支持：

- 今日作业、课程和临时安排；
- 完成状态；
- 每周固定课程；
- 上学日和休息日默认提醒时间；
- 今天单独覆盖第二次提醒时间；
- 今天关闭作业开始提醒。
- 重置今天的首次提醒测试状态；
- 开始、休息、继续和结束学习记录。
- 从“默认提醒设置”进入孩子动作基准录入。

## 动作与光线基准

家长登录 Web 后，在“默认提醒设置”底部点击“开始录入”。向导会依次采集：

1. 正常坐姿；
2. 正常写字；
3. 正常阅读；
4. 正常光线。

每一步默认采集约 10 秒。系统先通过正式人脸资料确认孩子，再从有效姿态画面计算中位值和正常波动范围，并挑选最接近中位状态的代表帧。页面默认显示带书桌区域、人脸位置和姿态关键点的标注图，也可以切换查看原图。

家长确认四步后才会生成新的基准版本。采集失败、取消或服务重启不会覆盖旧版本；重新采集产生的图片、已确认图片和历史版本图片都会继续保存在本机：

```text
data/calibrations/<draft_id>/
```

图片目录由本机配置控制，默认值如下：

```yaml
calibration:
  duration_seconds: 10
  timeout_seconds: 25
  min_valid_samples: 12
  image_dir: data/calibrations
```

基准录入和学习记录不能同时进行。开始一条新的学习记录时，会自动关联当时最新的基准版本。

## 本地语音口令

语音功能使用 sherpa-onnx，支持 MacBook 麦克风或 go2rtc 摄像头音频，默认关闭，不影响 Web 和摄像头提醒。

安装可选依赖：

```bash
uv pip install -e '.[speech]'
```

从 sherpa-onnx 官方模型页下载并解压
`sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23` 到：

```text
models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23
```

然后修改本机 `config/config.yaml`：

```yaml
speech:
  enabled: true
  # macbook 使用 MacBook 麦克风，camera 使用 go2rtc RTSP 音频。
  input_source: macbook
  model_path: models/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23
  sample_rate: 48000
  block_size: 8000
  device:
  command_cooldown_seconds: 2
```

将 `input_source` 改为 `camera` 后，程序会通过 FFmpeg 从
`video.stream_url` 读取摄像头音频，并转换为 16000Hz 单声道语音识别输入。
camera 模式需要 MacBook 已安装 `ffmpeg`，并使用 TCP 读取 RTSP 音频。
MacBook 的 `say` 仍然负责语音播报；播报期间会暂时丢弃输入音频，避免把系统播报再次识别为口令。

先单独检查麦克风识别结果：

```bash
funwithcam speech-test --config config/config.yaml
```

该命令只打印识别文本和匹配到的口令，不修改提醒或学习状态。

当前支持：

- “开始作业”“开始写作业”“开始学习”“开始记录”；
- “我要休息”“休息一下”；
- “继续学习”“继续写作业”；
- “结束学习”“作业写完了”；
- “今天有什么作业”；
- “今天有什么课”“今天几点上课”；
- “英语单词打卡”“英语读书打卡”“古诗打卡”。

系统使用半双工方式工作：本地 `say` 播报期间不把麦克风音频送入识别器。
家长可以在“语音口令”页面配置明确说法、关键词组合和二次确认规则，在
“交互与任务”页面配置重问频次、任务类型、完成证据和视频最长时长。

## 人脸身份

人脸模型来自 OpenCV Zoo，运行时完全在本机处理。默认只保存归一化后的人脸特征，不保存照片或视频。

先录入测试身份：

```bash
funwithcam face-enroll \
  --config config/config.yaml \
  --database data/funwithcam.db \
  --role child \
  --name child \
  --profile test
```

验证测试身份：

```bash
funwithcam face-verify \
  --config config/config.yaml \
  --database data/funwithcam.db \
  --profile test
```

验证会综合多帧结果，只输出 `child`、`parent` 或 `unknown`，不会单独触发正式提醒。

测试通过后重新录入正式身份。正式服务要求至少有一个孩子和一个常进入区域的家长：

```bash
funwithcam face-enroll --role child --name child --profile formal
funwithcam face-enroll --role parent --name parent_1 --profile formal
funwithcam face-verify --profile formal
```

录入命令会依次语音提示正面、轻微左转和轻微右转。测试身份与正式身份在数据库中隔离。

### 人脸调试照片

当前本机的 `config/config.yaml` 已开启：

```yaml
identity:
  save_debug_frames: true
  debug_dir: data/face_debug
  debug_face_limit: 30
  debug_rejected_limit: 10
```

再次运行 `face-enroll` 或 `face-verify` 时，每次命令会建立独立目录，例如：

```text
data/face_debug/face-enroll_formal_child_20261011_103000/
data/face_debug/face-verify_formal_20261011_103500/
```

目录内容：

- `*_full.jpg`：摄像头完整画面，标有人脸框、五个关键点、检脸置信度和匹配分数；
- `*_aligned.jpg`：实际送入 SFace 提取特征的对齐人脸图；
- `manifest.json`：每张图片的检测原因、身份结果、匹配分数和命令最终结果；
- `rejected_no_face`、`rejected_multiple_faces`、`rejected_face_too_small`：未进入识别的人脸检测失败画面。

为避免持续占用磁盘，每次最多保存 30 组有效人脸和 10 张检测失败画面。确认问题后，把 `save_debug_frames` 改为 `false` 即可停止保存。照片只写入本机 `data/face_debug`，不会保存到身份特征表。

模型文件及 SHA-256：

- `models/face_detection_yunet_2023mar.onnx`: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- `models/face_recognition_sface_2021dec.onnx`: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`

对应许可证保存在 `models/LICENSE-YuNet.txt` 和 `models/LICENSE-SFace.txt`。

## 正式运行

完成家长账号和正式人脸录入后，使用一个命令同时启动 FastAPI Web 与提醒服务：

```bash
funwithcam serve \
  --config config/config.yaml \
  --database data/funwithcam.db
```

运行规则：

- 达到 Web 配置的首次简报时间后，人物需在书桌区域稳定出现；
- 多帧确认是孩子才播放今日简报；
- 识别为家长时保持静默，不消耗当天简报；
- `unknown` 或多人画面不触发简报；
- 第二次提醒只在首次简报已经完成后执行；
- 第二次提醒晚于计划超过 30 分钟，或计划时间早于实际首次简报时，不补播旧提醒；
- 未启用本地语音识别时仍可使用 Web 按钮控制学习记录。

## Docker 部署到另一台 Mac

冷启动只需要 Docker Desktop 和 Git。新 Mac 不安装 Python、OpenCV、
MediaPipe、FFmpeg 或语音模型，也不执行 `docker save`：

```bash
git clone git@github.com:easonzs/funWithCam.git
cd funWithCam
./deploy.sh
```

脚本默认从 `ghcr.io/easonzs/funwithcam:latest` 拉取正式镜像，生成 `.env`，
保留宿主机 `runtime/` 家庭数据，引导通过 go2rtc WebUI 配置小米摄像头，
创建 `parent` 家长账号，并实际检查 `study_room` 的视频和音频轨道。

开发者需要从当前源码构建时才使用：

```bash
./deploy.sh --build
```

发布新代码后，新 Mac 更新使用：

```bash
./update.sh
```

不要进入运行中的容器覆盖代码。完整的镜像来源、GHCR 首次公开设置、配置保存
位置和故障处理见 `docs/DOCKER_COLD_START.md`。

常用诊断命令：

```bash
docker compose ps
docker compose logs -f
./scripts/diagnose.sh
```

迁移模式暂未并入一键脚本，当前新 Mac 按冷启动重新配置。

## 文档

- `PRODUCT.md`
- `docs/PRODUCT_LOGIC.md`
- `docs/TECHNICAL_DESIGN.md`
- `docs/PRODUCT_DECISIONS_2026-10-10_TO_12.md`：最近两天讨论形成的产品决策、
  已确认边界和待开发项；
- `docs/INTERACTION_TECHNICAL_DESIGN.md`：受控语义匹配、持久化对话、结束检查
  和视频打卡实现。
- `docs/DOCKER_COLD_START.md`：新 Mac 冷启动、镜像发布、更新和数据位置。

