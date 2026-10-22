-- DeepSeek 模型存入 family_settings，web 设置页可配置（与 API Key 同区块）。
-- 默认 deepseek-flash（V4.1-Flash，通用对话）；deepseek-v4-pro 更强但更慢，解读任务一般用不到。
INSERT INTO family_settings (setting_key, setting_value, updated_at) VALUES
    ('deepseek_model', 'deepseek-flash', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
