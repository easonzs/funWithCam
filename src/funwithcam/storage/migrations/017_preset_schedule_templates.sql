-- 预置标准作业/打卡模板：日程页按「学校作业 / 打卡」两列展示。
-- 幂等插入：已有同 task_key 的模板不覆盖。

INSERT INTO task_types (
    task_key, label, category, completion_mode, finish_prompt,
    include_in_finish_check, max_recording_seconds, enabled, built_in,
    created_at, updated_at
) VALUES
    ('math_homework', '数学作业', 'school_homework', 'verbal',
     '“{title}”完成了吗？请回答“完成了”“还没有”或者“稍后完成”。',
     1, NULL, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('chinese_homework', '语文作业', 'school_homework', 'verbal',
     '“{title}”完成了吗？请回答“完成了”“还没有”或者“稍后完成”。',
     1, NULL, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('english_homework', '英语作业', 'school_homework', 'verbal',
     '“{title}”完成了吗？请回答“完成了”“还没有”或者“稍后完成”。',
     1, NULL, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('other_homework', '其他作业', 'school_homework', 'verbal',
     '“{title}”完成了吗？请回答“完成了”“还没有”或者“稍后完成”。',
     1, NULL, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('other_checkin', '其他打卡', 'checkin', 'video',
     '其他打卡还没有视频，要现在打卡吗？请回答“现在打卡”“稍后完成”或者“跳过”。',
     1, 180, 1, 1,
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
     strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
ON CONFLICT(task_key) DO NOTHING;
