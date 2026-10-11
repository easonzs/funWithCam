INSERT OR IGNORE INTO family_settings(setting_key, setting_value, updated_at)
VALUES (
    'child_name',
    '小朋友',
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);
