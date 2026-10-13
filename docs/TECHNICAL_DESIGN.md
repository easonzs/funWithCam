# FunWithCam 技术设计

> 可配置语义匹配、持久化对话、任务完成证据和视频打卡的增量设计见
> `INTERACTION_TECHNICAL_DESIGN.md`。

## 1. 设计目标

新实现以单家庭、单儿童、单摄像头和单台 MacBook 为边界，优先保证：

1. 状态转换确定且可以恢复；
2. 视频、语音、Web 和计时器不能并发修改业务状态；
3. 默认本地处理和本地保存；
4. 每项功能可以独立测试；
5. 不引入当前规模不需要的分布式组件。

## 2. 进程模型

第一版保留两个操作系统进程：

```text
go2rtc
FunWithCam Python 应用
```

FunWithCam 内部由多个服务组成，但仍然是一个应用进程：

```text
视频采集线程 ─┐
视觉推理线程 ─┤
音频采集线程 ─┼→ 统一事件队列 → 串行业务状态机 → 数据库/语音输出
Web 请求      ─┤
日程计时器    ─┘
```

阻塞的视频、音频和模型推理运行在线程或受控子进程中。所有业务事件最终进入同一条串行处理路径。

## 3. 技术选择

### 3.1 后端

- Python 3.11；
- Web 使用 FastAPI、Uvicorn 和 Jinja2；
- 第一版页面使用服务端渲染和少量原生 JavaScript；
- 不引入 Redis、Celery、React 或 Vue。

### 3.2 视频

- go2rtc 统一连接摄像头；
- 应用只读取本机 go2rtc 输出；
- OpenCV 负责第一阶段解码；
- 独立采集线程只保留最新帧，避免旧帧堆积；
- 视觉推理按 3 至 5 FPS 读取最新帧。

### 3.3 视觉

- MediaPipe Pose Landmarker：姿态关键点和多人冲突检测；
- OpenCV YuNet：人脸检测；
- OpenCV SFace：本地一对一或小规模家庭身份验证；
- OpenCV 图像统计：亮度和画面变化；
- 第一版使用可解释几何特征，不训练坐姿分类器。

### 3.4 语音

- `sounddevice`：MacBook 麦克风采集；
- sherpa-onnx：本地中文流式识别和 VAD；
- 规则和模糊匹配：有限意图识别；
- macOS `say`：语音输出；
- 播报期间暂停意图提交，使用半双工避免回声误触发。

### 3.5 存储

- SQLite WAL；
- 原生 `sqlite3`；
- 编号 SQL 迁移；
- Repository 隔离 SQL 和业务状态机；
- 短事务；
- 视觉原始帧不写入数据库。

## 4. 事件模型

所有输入统一转换为事件：

```python
Event(
    type,
    occurred_at,
    source,
    payload,
)
```

事件来源包括：

- `vision`
- `identity`
- `speech`
- `schedule`
- `web`
- `system`

视觉、语音和 Web 只发布事件，不直接播放语音或修改数据库状态。

## 5. 状态机

业务工作流以不可变快照表示：

```text
WAITING_FOR_FIRST_APPEARANCE
IDENTITY_CONFIRMING
BRIEFED
WAITING_TO_START
PRECHECK
STUDYING
AWAY_GRACE
PAUSED
INTERRUPTED
FINISH_CONFIRMING
COMPLETED
DISABLED
```

需要取消确认或取消快速检查时，快照保存 `return_state`，避免在状态机之外猜测应返回哪个状态。

非法事件不会被静默忽略，而是抛出明确错误。调用方可以记录该事件，但不能改变状态。

## 6. 时间

- 日历时间使用带时区的 `datetime`；
- 默认业务时区为 `Asia/Shanghai`；
- 持续时间在运行期使用单调时钟；
- 持久化保存 ISO 8601 时间；
- 每次状态变化都保存更新时间。

## 7. SQLite 数据

初始迁移建立：

- `schema_migrations`
- `daily_status`
- `study_sessions`
- `session_intervals`
- `observation_events`
- `reminders`
- `config_versions`
- `calibration_versions`

`daily_status` 保存当天主工作流。一次学习的详细时间和事件由其他表保存。

状态机先计算新快照，再由 Repository 在短事务内持久化。后续涉及“每日只执行一次”的操作使用条件更新或 `BEGIN IMMEDIATE` 抢占执行权。

## 8. 配置

部署配置与家庭配置分离：

```text
YAML：视频地址、数据库路径、监听端口、日志
SQLite：日程、提醒、阈值、模式、今日覆盖
```

每次学习会话引用不可变的配置版本和基准版本。

## 9. 测试策略

### 单元测试

- 状态转换；
- 非法转换；
- 确认取消后的返回状态；
- 提醒规则和冷却；
- 会话时间计算；
- 意图匹配。

### 数据库测试

- 迁移；
- 状态恢复；
- 每日幂等；
- 并发抢占；
- 配置版本。

### 感知测试

视觉模块输出结构化观测。业务测试使用保存的观测 JSON，不依赖每次运行摄像头和模型。

语音模块使用儿童真实语句的离线测试集评估识别率和意图匹配。

## 10. 分阶段实施

1. 事件、状态机、数据库迁移；
2. go2rtc 视频采集、人物出现检测和 `say` 输出；
3. 家庭 Web 和日程录入；
4. 首次简报和第二次提醒；
5. 学习会话和时间区间；
6. 孩子和家长身份确认；
7. 麦克风、本地 ASR 和意图识别；
8. 长期基准；
9. 姿势、光线和离座事件；
10. 观察模式、统计和打卡视频。

摄像头与 `say` 先通过独立测试命令完成端到端验证。身份确认完成前，该命令不写入正式每日提醒状态，避免家长经过书桌时误消耗提醒权。

## 11. 当前实现状态（2026-10-11）

已完成：

- YuNet 人脸检测和 SFace 本地特征；
- `child / parent / unknown` 多帧身份验证；
- 测试身份与正式身份隔离；
- FastAPI 家庭 Web、家长登录和 CSRF 防护；
- 今日安排、固定课程、默认提醒和今日覆盖的 SQLite 存储；
- 今日简报和第二次提醒文案生成；
- 摄像头稳定出现、正式身份、日程和 `say` 的提醒闭环；
- `serve` 在一个 Python 进程内运行 Web，后台线程运行视频与提醒。
- sherpa-onnx 本地流式语音口令与半双工播报；
- 学习会话开始、暂停、继续和结束；
- 开始与继续前的视频、区域、单人、孩子身份和光线检查。

尚未进入当前版本：

- 儿童真实发音的现场识别率调优；
- 长期坐姿与灯光基准；
- 姿势、光线和离座事件统计；
- 打卡视频截取。


