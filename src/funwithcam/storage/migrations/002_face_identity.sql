CREATE TABLE face_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('child', 'parent')),
    name TEXT NOT NULL,
    profile_type TEXT NOT NULL CHECK(profile_type IN ('test', 'formal')),
    embedding_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(role, name, profile_type)
);

CREATE INDEX idx_face_profiles_type_role
ON face_profiles(profile_type, role, active);
