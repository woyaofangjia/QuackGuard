import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import PROTOCOL_VERSION

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    title TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (rule_id, rule_version)
);
CREATE TABLE IF NOT EXISTS builds (
    build_id TEXT PRIMARY KEY,
    artifact_version TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    rules_sha256 TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    command TEXT NOT NULL,
    input_path TEXT,
    output_path TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    build_id TEXT NOT NULL REFERENCES builds(build_id),
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    artifact_version TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (build_id, rule_id, rule_version)
);
CREATE TABLE IF NOT EXISTS plugins (
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    entrypoint TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (plugin_id, plugin_version)
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def add_rules(self, rules, source_hashes):
        statement = """
            INSERT INTO rules (
                rule_id, rule_version, title, source_sha256,
                source_json, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id, rule_version) DO UPDATE SET
                title = excluded.title,
                source_sha256 = excluded.source_sha256,
                source_json = excluded.source_json,
                imported_at = excluded.imported_at
        """
        imported_at = now()
        records = []
        for rule in rules:
            key = rule["rule_id"] + "@" + rule["version"]
            records.append((
                rule["rule_id"],
                rule["version"],
                rule.get("title", ""),
                source_hashes[key],
                json.dumps(rule, ensure_ascii=False),
                imported_at,
            ))
        self.db.executemany(statement, records)
        self.db.commit()

    def add_build(self, artifact_version, root_path, rules_sha256, config_sha256):
        build_id = uuid.uuid4().hex
        self.db.execute(
            """
            INSERT INTO builds (
                build_id, artifact_version, root_path, rules_sha256,
                config_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_id,
                artifact_version,
                str(root_path),
                rules_sha256,
                config_sha256,
                "built",
                now(),
            ),
        )
        self.db.commit()
        return build_id

    def add_versions(self, build_id, entries):
        records = [
            (
                build_id,
                entry["rule_id"],
                entry["rule_version"],
                entry["artifact_version"],
                entry["path"],
                entry["sha256"],
            )
            for entry in entries
        ]
        self.db.executemany(
            """
            INSERT INTO versions (
                build_id, rule_id, rule_version, artifact_version,
                artifact_path, sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        self.db.commit()

    def start_task(self, command):
        task_id = uuid.uuid4().hex
        self.db.execute(
            """
            INSERT INTO tasks (
                task_id, command, status, error_code, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, command, "running", None, now(), None),
        )
        self.db.commit()
        return task_id

    def start_run(self, task_id, command, input_path, output_path):
        run_id = uuid.uuid4().hex
        self.db.execute(
            """
            INSERT INTO runs (
                run_id, task_id, command, input_path, output_path,
                status, error_code, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                command,
                str(input_path),
                str(output_path),
                "running",
                None,
                now(),
                None,
            ),
        )
        self.db.commit()
        return run_id

    def finish(self, task_id, run_id, status, error_code=None):
        finished_at = now()
        self.db.execute(
            """
            UPDATE tasks
            SET status = ?, error_code = ?, finished_at = ?
            WHERE task_id = ?
            """,
            (status, error_code, finished_at, task_id),
        )
        self.db.execute(
            """
            UPDATE runs
            SET status = ?, error_code = ?, finished_at = ?
            WHERE run_id = ?
            """,
            (status, error_code, finished_at, run_id),
        )
        self.db.commit()

    def register_plugin(self, plugin_id, plugin_version, entrypoint):
        self.db.execute(
            """
            INSERT INTO plugins (
                plugin_id, plugin_version, schema_version, entrypoint
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(plugin_id, plugin_version) DO UPDATE SET
                entrypoint = excluded.entrypoint
            """,
            (plugin_id, plugin_version, PROTOCOL_VERSION, entrypoint),
        )
        self.db.commit()
