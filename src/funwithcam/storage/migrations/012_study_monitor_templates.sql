INSERT OR IGNORE INTO speech_templates (
    event_key,
    label,
    trigger_description,
    template_text,
    enabled,
    updated_at
) VALUES
    (
        'posture_abnormal',
        '监测到姿势异常',
        '学习中姿势偏离孩子已确认的坐姿、写字和阅读基准，并达到累计次数与冷却规则。',
        '{孩子称呼}，头稍微抬高一点，保持舒服的坐姿。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    (
        'seat_leave',
        '监测到频繁离座',
        '学习中连续采样确认离开书桌，且在配置时间窗内达到累计次数。',
        '{孩子称呼}，如果没有需要离开，可以先回来坐好。',
        1,
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    );
