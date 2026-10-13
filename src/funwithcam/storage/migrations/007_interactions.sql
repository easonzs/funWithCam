CREATE TABLE speech_intents (
    command TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    phrases_json TEXT NOT NULL,
    keyword_groups_json TEXT NOT NULL DEFAULT '[]',
    confirmation_mode TEXT NOT NULL DEFAULT 'ambiguous'
        CHECK(confirmation_mode IN ('never', 'ambiguous', 'always')),
    confirmation_prompt TEXT NOT NULL,
    confirm_phrases_json TEXT NOT NULL,
    cancel_phrases_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

INSERT INTO speech_intents (
    command, label, description, phrases_json, keyword_groups_json,
    confirmation_mode, confirmation_prompt,
    confirm_phrases_json, cancel_phrases_json, enabled, updated_at
) VALUES
    (
        'start', '开始学习',
        '完成开始前检查并建立学习记录。',
        '["开始写作业","开始学习","开始记录","开始作业","我要做作业","我准备学习了"]',
        '[["开始","准备","要"],["学习","作业","记录"]]',
        'ambiguous',
        '你是要开始学习吗？请回答“确认开始”或者“取消”。',
        '["确认开始","是的","开始学习"]',
        '["取消","不是","先不开始"]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'pause', '暂停学习',
        '暂停当前学习记录。',
        '["我要休息","休息一下","暂停学习"]',
        '[["休息","暂停"],["学习","作业","一下"]]',
        'ambiguous',
        '你是要暂停学习吗？请回答“确认暂停”或者“继续学习”。',
        '["确认暂停","是的","暂停学习"]',
        '["继续学习","取消","不是"]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'resume', '继续学习',
        '重新检查书桌状态后继续学习。',
        '["继续学习","继续写作业","继续记录"]',
        '[["继续","接着"],["学习","作业","记录"]]',
        'ambiguous',
        '你是要继续学习吗？请回答“确认继续”或者“结束学习”。',
        '["确认继续","继续学习","是的"]',
        '["结束学习","取消","不是"]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'finish', '结束学习',
        '进入今日任务完成情况检查，确认后结束学习。',
        '["结束学习","结束记录","作业写完了","我写完了","写完作业了"]',
        '[["结束","完成","写完"],["学习","作业","记录"]]',
        'always',
        '准备结束本次学习了吗？请回答“检查完成情况”或者“继续学习”。',
        '["检查完成情况","确认结束","是的"]',
        '["继续学习","取消结束","不是"]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'query_homework', '查询今日作业',
        '播报日程中尚未完成的学校作业。',
        '["今天有什么作业","今天作业是什么","今天的作业是什么"]',
        '[["今天"],["作业"]]',
        'never', '',
        '[]', '[]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'query_courses', '查询今日课程',
        '播报今天录入的课程和每周固定课程。',
        '["今天有什么课","今天的课是什么","今天几点上课"]',
        '[["今天"],["课","课程","上课"]]',
        'never', '',
        '[]', '[]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'start_checkin', '开始打卡',
        '选择今天的视频打卡任务并进入准备流程。',
        '["英语打卡","英语单词打卡","英语读书打卡","古诗打卡","古诗词背诵"]',
        '[["打卡","背诵"]]',
        'ambiguous',
        '你是要开始视频打卡吗？请回答“确认打卡”或者“取消”。',
        '["确认打卡","开始打卡","是的"]',
        '["取消","不是","先不打卡"]',
        1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );

CREATE TABLE task_types (
    task_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK(category IN ('school_homework', 'checkin', 'other')),
    completion_mode TEXT NOT NULL
        CHECK(completion_mode IN ('verbal', 'video', 'parent')),
    finish_prompt TEXT NOT NULL,
    include_in_finish_check INTEGER NOT NULL DEFAULT 1,
    max_recording_seconds INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    built_in INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO task_types (
    task_key, label, category, completion_mode, finish_prompt,
    include_in_finish_check, max_recording_seconds, enabled, built_in,
    created_at, updated_at
) VALUES
    ('school_homework', '学校作业', 'school_homework', 'verbal',
     '“{title}”完成了吗？请回答“完成了”“还没有”或者“稍后完成”。',
     1, NULL, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('english_words', '英语单词打卡', 'checkin', 'video',
     '英语单词打卡还没有视频，要现在打卡吗？请回答“现在打卡”“稍后完成”或者“跳过”。',
     1, 180, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('english_reading', '英语读书打卡', 'checkin', 'video',
     '英语读书打卡还没有视频，要现在打卡吗？请回答“现在打卡”“稍后完成”或者“跳过”。',
     1, 300, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('poem_recitation', '古诗词背诵', 'checkin', 'video',
     '古诗词背诵还没有视频，要现在打卡吗？请回答“现在打卡”“稍后完成”或者“跳过”。',
     1, 180, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

ALTER TABLE agenda_items ADD COLUMN task_type_key TEXT;
ALTER TABLE agenda_items ADD COLUMN completion_evidence TEXT NOT NULL DEFAULT 'none'
    CHECK(completion_evidence IN (
        'none', 'child_confirmed', 'video', 'parent_confirmed',
        'later', 'skipped', 'unanswered'
    ));
ALTER TABLE agenda_items ADD COLUMN confirmed_at TEXT;

UPDATE agenda_items
SET task_type_key = 'school_homework'
WHERE item_type = 'homework' AND task_type_key IS NULL;

CREATE TABLE dialogue_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    dialogue_type TEXT NOT NULL
        CHECK(dialogue_type IN ('command_confirmation', 'finish_review', 'checkin')),
    state TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    prompt TEXT NOT NULL,
    allowed_replies_json TEXT NOT NULL DEFAULT '[]',
    ask_count INTEGER NOT NULL DEFAULT 1,
    next_prompt_at TEXT,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'completed', 'cancelled', 'expired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_dialogue_sessions_active
ON dialogue_sessions(session_date, status, updated_at);

CREATE TABLE checkin_recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agenda_item_id INTEGER NOT NULL,
    session_date TEXT NOT NULL,
    task_type_key TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK(status IN ('preparing', 'recording', 'saved', 'cancelled', 'failed')),
    video_path TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_seconds REAL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(agenda_item_id) REFERENCES agenda_items(id),
    FOREIGN KEY(task_type_key) REFERENCES task_types(task_key)
);

CREATE INDEX idx_checkin_recordings_item
ON checkin_recordings(agenda_item_id, created_at DESC);

INSERT INTO family_settings(setting_key, setting_value, updated_at) VALUES
    ('dialogue_first_retry_seconds', '20', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('dialogue_next_retry_seconds', '60', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('dialogue_max_prompts', '3', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('away_auto_pause_minutes', '2', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('away_interrupted_minutes', '60', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
