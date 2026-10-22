INSERT INTO speech_intents (
    command, label, description, phrases_json, custom_phrases_json,
    keyword_groups_json, confirmation_mode, confirmation_prompt,
    confirm_phrases_json, cancel_phrases_json, enabled, updated_at
) VALUES (
    'interpret_poem', '解读古诗',
    '说出诗名，播报全诗和逐句白话解读。',
    '["解读古诗","讲古诗","讲解古诗","这首诗是什么意思","背一遍古诗"]',
    '[]',
    '[["解读","讲解","讲","意思"],["古诗","诗"]]',
    'never',
    '',
    '[]',
    '[]',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
);

-- dialogue_type 增加 poem_interpret：SQLite 无法 ALTER CHECK，重建表。
CREATE TABLE dialogue_sessions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    dialogue_type TEXT NOT NULL
        CHECK(dialogue_type IN (
            'command_confirmation', 'finish_review', 'checkin', 'poem_interpret'
        )),
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

INSERT INTO dialogue_sessions_new (
    id, session_date, dialogue_type, state, context_json, prompt,
    allowed_replies_json, ask_count, next_prompt_at, expires_at, status,
    created_at, updated_at
)
SELECT
    id, session_date, dialogue_type, state, context_json, prompt,
    allowed_replies_json, ask_count, next_prompt_at, expires_at, status,
    created_at, updated_at
FROM dialogue_sessions;

DROP TABLE dialogue_sessions;
ALTER TABLE dialogue_sessions_new RENAME TO dialogue_sessions;

CREATE INDEX idx_dialogue_sessions_active
ON dialogue_sessions(session_date, status, updated_at);
