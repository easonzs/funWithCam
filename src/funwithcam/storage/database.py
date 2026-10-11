import sqlite3
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator


class Database:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            migration_root = resources.files("funwithcam.storage.migrations")
            for migration in sorted(migration_root.iterdir(), key=lambda item: item.name):
                if migration.suffix != ".sql":
                    continue
                version = int(migration.name.split("_", 1)[0])
                if version in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    """
                    INSERT INTO schema_migrations (version, applied_at)
                    VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (version,),
                )
                connection.commit()

