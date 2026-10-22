-- DeepSeek API Key 存入 family_settings，web 设置页可配置。
-- 默认空值 = 只用内置离线诗库；填写后古诗解读优先走 DeepSeek。
INSERT INTO family_settings (setting_key, setting_value, updated_at) VALUES
    ('deepseek_api_key', '', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
