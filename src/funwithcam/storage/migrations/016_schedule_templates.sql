-- 日程模型：模板 + 任意日期集合生效 + 单日例外/覆盖。
-- 配合 agenda_items（当天实例）使用；模板生成的实例标记 source='template'，
-- 取消生效日期时回收未完成的模板实例，保留已完成记录。

ALTER TABLE agenda_items ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';

-- 模板在某天的生效集合（任意日期集合打勾）。
CREATE TABLE template_schedule_dates (
    task_type_key TEXT NOT NULL
        REFERENCES task_types(task_key) ON DELETE CASCADE,
    target_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_type_key, target_date)
);

CREATE INDEX idx_template_schedule_dates_date
ON template_schedule_dates(target_date);

-- 模板在某天的例外：disabled（该天不生效）或 override（当天补充具体内容）。
CREATE TABLE template_schedule_overrides (
    task_type_key TEXT NOT NULL
        REFERENCES task_types(task_key) ON DELETE CASCADE,
    target_date TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    override_title TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_type_key, target_date)
);

CREATE INDEX idx_template_schedule_overrides_date
ON template_schedule_overrides(target_date);
