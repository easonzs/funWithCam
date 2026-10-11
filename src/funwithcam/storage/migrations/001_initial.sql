CREATE TABLE daily_status (
    session_date TEXT PRIMARY KEY,
    workflow_state TEXT NOT NULL,
    return_state TEXT,
    first_seen_at TEXT,
    briefed_at TEXT,
    second_reminder_at TEXT,
    disabled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    start_source TEXT,
    completion_status TEXT,
    config_version_id INTEGER,
    calibration_version_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_study_sessions_date
ON study_sessions(session_date);

CREATE TABLE session_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_session_id INTEGER NOT NULL,
    interval_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(study_session_id) REFERENCES study_sessions(id)
);

CREATE INDEX idx_session_intervals_session
ON session_intervals(study_session_id, started_at);

CREATE TABLE observation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_session_id INTEGER,
    event_group_id TEXT,
    event_type TEXT NOT NULL,
    candidate_started_at TEXT NOT NULL,
    confirmed_at TEXT,
    ended_at TEXT,
    average_confidence REAL,
    average_deviation REAL,
    maximum_deviation REAL,
    reminded INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(study_session_id) REFERENCES study_sessions(id)
);

CREATE INDEX idx_observation_events_session
ON observation_events(study_session_id, candidate_started_at);

CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    study_session_id INTEGER,
    reminder_type TEXT NOT NULL,
    scheduled_at TEXT,
    played_at TEXT,
    status TEXT NOT NULL,
    cancellation_reason TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(study_session_id) REFERENCES study_sessions(id)
);

CREATE INDEX idx_reminders_date
ON reminders(session_date, scheduled_at);

CREATE TABLE config_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE calibration_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calibration_json TEXT NOT NULL,
    quality_json TEXT NOT NULL,
    camera_fingerprint TEXT,
    created_at TEXT NOT NULL
);

