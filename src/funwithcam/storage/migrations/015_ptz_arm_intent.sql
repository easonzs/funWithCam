INSERT OR IGNORE INTO speech_intents (
    command, label, description, phrases_json, custom_phrases_json,
    keyword_groups_json, confirmation_mode, confirmation_prompt,
    confirm_phrases_json, cancel_phrases_json, enabled, updated_at
) VALUES (
    'ptz_arm', '摄像头调整',
    '开启摄像头调整模式，60 秒内转向口令直接生效。',
    '["摄像头调整","调整摄像头","开始调整摄像头"]',
    '[]',
    '[["摄像头"],["调整","调节"]]',
    'never',
    '',
    '[]',
    '[]',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);
