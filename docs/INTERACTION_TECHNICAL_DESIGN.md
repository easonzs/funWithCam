# 交互、任务检查与视频打卡技术设计

## 1. 模块边界

- `SpeechIntentRepository`：保存可配置意图和匹配规则；
- `match_speech_intent`：无副作用地执行明确短语和关键词组合匹配；
- `DialogueRepository`：持久化待确认、结束检查和打卡对话；
- `SpeechCommandHandler`：结合当前对话状态处理语音；
- `TaskTypeRepository`：管理学校作业和打卡模板；
- `CheckinRepository`：记录打卡视频生命周期；
- `CheckinRecorder`：通过 FFmpeg 从 go2rtc RTSP 录制 MP4；
- `FamilyRepository`：关联当天日程、任务类型和完成证据。

## 2. 为什么对话状态要持久化

确认流程可能跨越数十秒，服务也可能重启。只在 Python 内存保存会导致：

- 服务重启后不知道上一句问了什么；
- 执行历史无法解释为什么某条回复没有执行；
- Web 无法展示正在等待的回答。

因此 `dialogue_sessions` 保存：

- 对话类型；
- 当前步骤；
- 上下文 JSON；
- 提示话术和允许回复；
- 已询问次数；
- 下次重问时间；
- 完成、取消或过期状态。

## 3. 匹配算法

输入先移除常见标点、空格并转为小写。

### 明确匹配

任一配置短语完整出现在识别文本中。只命中一个意图时返回 `exact`。

### 组合匹配

每一组关键词至少命中一个。例如：

```json
[
  ["开始", "准备", "要"],
  ["学习", "作业", "记录"]
]
```

“我要做作业”可以命中两个组并返回 `composite`。

### 冲突

同时命中多个意图时返回 `conflict`，不执行动作。

确认策略：

- `never`：直接执行；
- `ambiguous`：明确匹配直接执行，组合匹配二次确认；
- `always`：始终二次确认。

## 4. 结束检查

结束检查读取当天：

- 状态为 `pending`；
- 不是课程；
- 对应任务类型启用了 `include_in_finish_check`。

语音回复更新 `agenda_items.completion_evidence`。只有
`child_confirmed`、`video` 或 `parent_confirmed` 会同步把任务状态设为 `done`。

视频任务不能通过普通“完成了”直接完成，只接受“现在打卡”“稍后完成”或“跳过”。
家长确认型任务同样不能通过孩子口头回复完成；家长在 Web 中标记完成时写入
`parent_confirmed`。

## 5. 重复询问

后台对话提示线程每秒检查一次到期对话：

1. 未达到最大询问次数：重复原提示并安排下一次；
2. 达到最大次数：标记 `expired`，写入执行历史；
3. 不改变学习完成状态。

录制中的打卡对话不设置重问时间，避免系统播报进入视频。

## 6. 视频录制

第一版 FFmpeg 参数：

```text
-rtsp_transport tcp
-i <go2rtc RTSP>
-map 0:v:0
-map 0:a:0?
-c:v copy
-c:a aac
-movflags +faststart
```

视频保持摄像头原编码，音频转 AAC 以提高 MP4 和浏览器兼容性。
停止录制时向 FFmpeg 发送 `SIGINT`，让 FFmpeg 有机会写完 MP4 尾部。后台提示线程
同时检查最长录制时长，到期后自动保存，并记录准备、开始、保存、取消或失败事件。

需要继续验证：

- 摄像头 H265 视频在目标浏览器中的播放能力；
- FFmpeg 被终止时 MP4 尾部是否完整；
- 录制期间与视觉检测的资源竞争；
- 服务异常退出后的 `recording` 状态恢复。

## 7. 数据迁移

迁移 `007_interactions.sql` 新增：

- `speech_intents`；
- `task_types`；
- `dialogue_sessions`；
- `checkin_recordings`；
- `agenda_items.task_type_key`；
- `agenda_items.completion_evidence`；
- `agenda_items.confirmed_at`；
- 交互重问和离座阈值默认设置。

迁移只新增表和列，不删除已有日程、学习记录、人脸、图片或执行历史。

## 8. 下一阶段

下一阶段应新增独立的 `StudyMonitor`：

1. 仅在学习会话为 `STUDYING/AWAY_GRACE/PAUSED` 时运行；
2. 复用最新画面，避免重复打开 RTSP；
3. 连续离座达到配置阈值后调用状态机暂停；
4. 孩子回来并确认身份后创建“继续/结束”对话；
5. 持续记录坐姿、头部、光线和离座异常；
6. 与基准采集和视频打卡共享摄像头占用协调器。
