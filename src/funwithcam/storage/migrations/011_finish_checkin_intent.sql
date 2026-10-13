INSERT OR IGNORE INTO speech_intents (
    command, label, description, phrases_json, custom_phrases_json,
    keyword_groups_json, confirmation_mode, confirmation_prompt,
    confirm_phrases_json, cancel_phrases_json, enabled, updated_at
) VALUES (
    'finish_checkin',
    '结束打卡',
    '仅在视频打卡录制中停止录制并保存视频。',
    '["打卡结束","结束录制"]',
    '[]',
    '[["打卡","录制"],["结束","完成","停止"]]',
    'never',
    '',
    '[]',
    '[]',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);
