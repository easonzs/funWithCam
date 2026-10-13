CREATE TABLE speech_templates (
    event_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    trigger_description TEXT NOT NULL,
    template_text TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

INSERT INTO speech_templates (
    event_key,
    label,
    trigger_description,
    template_text,
    enabled,
    updated_at
) VALUES
    (
        'first_briefing',
        '首次检测到孩子',
        '超过当天简报时间，并连续确认孩子身份后，每天最多触发一次。',
        '{孩子称呼}，欢迎回家。{今天安排简报}你可以先安排吃饭和休息，{下一次提醒时间}我再提醒你。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'second_reminder',
        '第二次提醒',
        '首次简报播放后仍未开始学习，到达第二次提醒时间且没有超过宽限期。',
        '{孩子称呼}，现在是{当前时间}。今天还有{待完成任务列表}，可以开始准备了。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'start_precheck_passed',
        '开始学习检查通过',
        '孩子发出开始学习口令，画面、身份、光线和姿势检查通过。',
        '{孩子称呼}，检查通过，现在开始记录今天的学习。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'light_abnormal',
        '监测到光线异常',
        '脸部或桌面亮度持续偏暗达到配置的异常时长。',
        '{孩子称呼}，书桌有点暗，请把灯光调亮一些。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
