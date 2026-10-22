-- DeepSeek 长诗解读的 token 上限与超时存入 family_settings，web 设置页可配置。
-- deepseek-flash 是推理模型，长诗推理会占大量 token；默认 4000 足够《将进酒》。
INSERT INTO family_settings (setting_key, setting_value, updated_at) VALUES
    ('deepseek_max_tokens', '4000', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('deepseek_timeout_seconds', '30', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
