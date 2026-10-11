CREATE TABLE admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE web_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(admin_user_id) REFERENCES admin_users(id)
);

CREATE INDEX idx_web_sessions_expiry
ON web_sessions(expires_at);

CREATE TABLE agenda_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_date TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK(item_type IN ('homework', 'course', 'temporary')),
    title TEXT NOT NULL,
    subject TEXT,
    scheduled_time TEXT,
    estimated_minutes INTEGER,
    preparation_minutes INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'done')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_agenda_items_date
ON agenda_items(item_date, status, scheduled_time);

CREATE TABLE weekly_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    weekday INTEGER NOT NULL CHECK(weekday BETWEEN 1 AND 7),
    start_time TEXT NOT NULL,
    delivery_type TEXT NOT NULL CHECK(delivery_type IN ('online', 'offline')),
    preparation_minutes INTEGER NOT NULL DEFAULT 0,
    travel_minutes INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_weekly_courses_day
ON weekly_courses(weekday, enabled, start_time);

CREATE TABLE daily_preferences (
    preference_date TEXT PRIMARY KEY,
    day_type TEXT NOT NULL DEFAULT 'auto'
        CHECK(day_type IN ('auto', 'school_day', 'rest_day')),
    second_reminder_mode TEXT NOT NULL DEFAULT 'default'
        CHECK(second_reminder_mode IN ('default', 'custom', 'disabled')),
    second_reminder_time TEXT,
    homework_reminders_enabled INTEGER NOT NULL DEFAULT 1,
    posture_reminders_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE family_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO family_settings(setting_key, setting_value, updated_at) VALUES
    ('first_briefing_weekday_time', '17:00', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('first_briefing_rest_day_time', '09:00', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('second_reminder_weekday_time', '18:10', strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ('second_reminder_rest_day_time', '09:30', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

CREATE UNIQUE INDEX idx_reminders_once
ON reminders(session_date, reminder_type, scheduled_at);
