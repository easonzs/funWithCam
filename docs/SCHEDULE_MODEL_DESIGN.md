# 日程模型重构：模板 + 生效规则（设计文档）

> 状态：**待确认**（数据模型、页面交互、迁移路径）
> 对应需求：
> 1. 打卡是常规动作，勾选即当日生效，可配置到日历/批量标日期；
> 2. 学校作业参照打卡类型，按科目（语文/数学/英语…）配置，某日可特殊，无特殊诉求按"xx作业"默认循环生效，可日历批量生效；
> 3. 临时安排单独配置、可指定任意一天，不只【今日】。

---

## 0. 一句话目标

把「打卡 / 作业 / 临时安排」的配置从 **每天手动建一行**，变成 **一次定义模板，按周几循环 + 单日例外 + 临时安排选日期**。

勾选即当日生效；批量标日期；临时安排可指定任意一天。

---

## 1. 现状（代码事实）

| 载体 | 现状 | 问题 |
|---|---|---|
| `agenda_items` | "某天实例"：每天建一行，`item_date` 写死那一天 | 周一~周五每天打卡要建 5 行 |
| `task_types` | 已是独立模板表（school_homework / english_words / english_reading / poem_recitation），有 label / category / completion_mode / finish_prompt / include_in_finish_check / max_recording_seconds | 差"怎么挂到日子上"这一层 |
| `weekly_courses` | 已是"周几循环"模板，独立表 | 和打卡/作业不是一套体系 |
| `temporary` | 只是 `agenda_items` 里一个 item_type，Web 入口 `/today/items` 写死今天 | 无法选日期 |

---

## 2. 数据模型

新增 3 张表，改 1 张表；保留 `agenda_items` 作为"当天合并后的实例"。

### 2.1 task_types —— 保留，加 3 个字段

| 新增字段 | 说明 |
|---|---|
| `default_schedule_days` | 默认生效周几，如 `1,2,3,4,5`（工作日） |
| `subject` | 仅作业用：科目名 |
| `default_title_pattern` | 作业默认名称模板，如 `数学作业`；某日可被覆盖 |

现有内置模板（school_homework / english_words / english_reading / poem_recitation）全部复用，不新增重复内置。

### 2.2 task_type_schedules —— 新增（模板 × 日期的生效/例外）

日历标注表。

| 字段 | 说明 |
|---|---|
| `task_type_key` | 关联模板 |
| `target_date` | 具体日期 |
| `effect` | `enable` / `disable` / `override` |
| `override_title` | effect=override 时的当天名称 |
| `notes` | 当天备注 |

用途：
- 日历上勾某天 = `effect=enable`
- 某天不打卡 = `effect=disable`
- 某天内容特殊 = `effect=override` + `override_title`

> 临时安排不在此表——它有独立表。

### 2.3 temporary_items —— 新增（临时安排，指定任意日期）

| 字段 | 说明 |
|---|---|
| `id` | 主键 |
| `target_date` | 生效日期（任意） |
| `title` | 事项名称 |
| `scheduled_time / estimated_minutes / preparation_minutes / notes` | 同现有语义 |

### 2.4 agenda_items —— 保留，改语义为"当天合并后的实例"

不删除。由模板 + 生效规则 **自动生成 / 合并**，完成状态、结束询问标记仍存在这里。人不再逐天手建。

---

## 3. 某天的日程如何算出（合并逻辑）

给定日期 D：

1. 遍历启用中的打卡/作业模板；
2. 默认：D 的星期 ∈ 模板 `default_schedule_days` 则生效；
3. 叠加 `task_type_schedules`：enable → 强制生效；disable → 强制不生效；override → 生效且标题/备注用覆盖值；
4. 加上 `temporary_items` 中 `target_date = D` 的行；
5. 加上 `weekly_courses`（D 的星期匹配）——**保留现有，不并入模板表**；
6. 写入/刷新 `agenda_items`（该天实例），完成状态保留。

新增/停用模板或标注例外后，当天实例按需增量更新。

---

## 4. Web 页面草图

### 4.1 新增页：打卡类型管理（/config/checkin-types）
- 每个模板：名称 / 确认方式 / 默认周几生效网格（周一~周日开关）
- 操作：添加打卡类型、进入"日历例外"

### 4.2 新增页：作业科目管理（/config/subjects）
- 每个科目：数学作业 / 语文作业 / 英语作业，默认周几生效
- 无特殊诉求 → 按默认循环自动生效

### 4.3 改造页：日程页 /schedule（改为可切日期）
- 顶部：周历（周几循环模板默认状态一目了然）+ 日期选择器
- 点某天 → 右侧列当天生效模板 + 开关（勾选即当日生效）
- 临时安排：右上角"添加临时安排"可指定任意日期

### 4.4 迁移后首页/今日视图
- 当天日程 = 合并结果，展示自动生成来源（模板/例外/临时）

---

## 5. 语音简报与结束询问联动

改动在 `reminders.py`（简报文案）和 `speech.py`（结束询问/打卡），核心是"模板自动生成当天实例"发生在读日程之前。

| 环节 | 改前 | 改后 |
|---|---|---|
| 首次简报 | 读当天 agenda_items | 先合并出当天实例再读；文案不变，仍分课程/作业/临时 |
| 结束询问 | `list_finish_check_items` 读当天实例 | 同一合并结果，实例带 include_in_finish_check |
| 视频打卡 | 手建"英语单词打卡"行 | 模板自动生成；"开始打卡"走现有 checkin 流程 |
| 作业特殊配置 | 无 | 日历 override 时当天标题/备注取覆盖值 |

---

## 6. 现有数据迁移

一次性脚本，保留今天已见到的日程不变：

1. 已有 homework 行 → `school_homework` 模板 + 对应日期 `task_type_schedules` enable；
2. 已有 checkin 类行（task_type_key 非空、category=checkin）→ 对应打卡模板 + 当天 enable；
3. 已有 temporary 行 → 复制到 `temporary_items`，保留原日期；
4. `weekly_courses` 不动；
5. 迁移后 `agenda_items` 保留完成状态。

---

## 7. 待拍板

- [ ] 批量标注的粒度：默认「周几循环 + 单日例外」；是否需要「任意日期集合精确打勾」入口？
- [ ] 作业"特殊配置"：是否需要 `override_title`（内容不同）？还是只要 enable/disable（多一项/少一项）？
- [ ] "勾选即当日生效"放在日历某天右侧模板开关，是否直观？
- [ ] 存量数据：接受迁移脚本（推荐）还是保留手动建行？
- [ ] 课程：保持 `weekly_courses` 独立不并入（我推荐），还是统一？
