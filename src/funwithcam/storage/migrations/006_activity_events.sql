CREATE TABLE activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_activity_events_date_time
ON activity_events(event_date, occurred_at DESC, id DESC);
