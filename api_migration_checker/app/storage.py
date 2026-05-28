from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  config_path TEXT,
  summary_json TEXT
);
CREATE TABLE IF NOT EXISTS test_case_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  migration_run_id INTEGER NOT NULL,
  case_id TEXT NOT NULL,
  status TEXT NOT NULL,
  params_json TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY (migration_run_id) REFERENCES migration_runs(id)
);
CREATE TABLE IF NOT EXISTS api_execution_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  test_case_run_id INTEGER NOT NULL,
  api_name TEXT NOT NULL,
  api_role TEXT NOT NULL,
  method TEXT NOT NULL,
  url TEXT NOT NULL,
  request_headers_json TEXT,
  request_body_json TEXT,
  status_code INTEGER,
  response_body_json TEXT,
  error TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY (test_case_run_id) REFERENCES test_case_runs(id)
);
CREATE TABLE IF NOT EXISTS comparison_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  test_case_run_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  compared_fields INTEGER NOT NULL,
  mismatched_fields INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (test_case_run_id) REFERENCES test_case_runs(id)
);
CREATE TABLE IF NOT EXISTS comparison_details (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_result_id INTEGER NOT NULL,
  mapping_name TEXT NOT NULL,
  source_api TEXT NOT NULL,
  target_api TEXT NOT NULL,
  source_field TEXT NOT NULL,
  target_field TEXT NOT NULL,
  compare_type TEXT NOT NULL,
  source_value_json TEXT,
  target_value_json TEXT,
  status TEXT NOT NULL,
  message TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (comparison_result_id) REFERENCES comparison_results(id)
);
CREATE TABLE IF NOT EXISTS cross_check_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_result_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  notes_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (comparison_result_id) REFERENCES comparison_results(id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_run(self, migration_name: str, config_path: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO migration_runs (migration_name, status, started_at, config_path) VALUES (?, ?, ?, ?)",
            (migration_name, "RUNNING", now(), config_path),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE migration_runs SET status = ?, finished_at = ?, summary_json = ? WHERE id = ?",
            (status, now(), jdumps(summary), run_id),
        )
        self.connection.commit()

    def insert_test_case(self, run_id: int, case_id: str, status: str, params: dict[str, Any]) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO test_case_runs (migration_run_id, case_id, status, params_json, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, case_id, status, jdumps(params), now(), now()),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_api_result(self, test_case_run_id: int, result: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO api_execution_results
            (test_case_run_id, api_name, api_role, method, url, request_headers_json, request_body_json,
             status_code, response_body_json, error, duration_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                test_case_run_id,
                result["api_name"],
                result["api_role"],
                result["method"],
                result["url"],
                jdumps(result.get("request_headers")),
                jdumps(result.get("request_body")),
                result.get("status_code"),
                jdumps(result.get("response_body")),
                result.get("error"),
                result.get("duration_ms"),
                now(),
            ),
        )
        self.connection.commit()

    def insert_comparison(self, test_case_run_id: int, status: str, details: list[dict[str, Any]]) -> int:
        compared = len([item for item in details if item["status"] != "SKIPPED"])
        mismatched = len([item for item in details if item["status"] in {"FAIL", "ERROR"}])
        cursor = self.connection.execute(
            """
            INSERT INTO comparison_results (test_case_run_id, status, compared_fields, mismatched_fields, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (test_case_run_id, status, compared, mismatched, now()),
        )
        comparison_id = int(cursor.lastrowid)
        for item in details:
            self.connection.execute(
                """
                INSERT INTO comparison_details
                (comparison_result_id, mapping_name, source_api, target_api, source_field, target_field,
                 compare_type, source_value_json, target_value_json, status, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comparison_id,
                    item["mapping_name"],
                    item["source_api"],
                    item["target_api"],
                    item["source_field"],
                    item["target_field"],
                    item["compare_type"],
                    jdumps(item.get("source_value")),
                    jdumps(item.get("target_value")),
                    item["status"],
                    item.get("message"),
                    now(),
                ),
            )
        self.connection.commit()
        return comparison_id

    def insert_cross_check(self, comparison_id: int, status: str, notes: list[str]) -> None:
        self.connection.execute(
            "INSERT INTO cross_check_results (comparison_result_id, status, notes_json, created_at) VALUES (?, ?, ?, ?)",
            (comparison_id, status, jdumps(notes), now()),
        )
        self.connection.commit()

    def list_runs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM migration_runs ORDER BY id DESC").fetchall()
        return [_dict(row) for row in rows]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM migration_runs WHERE id = ?", (run_id,)).fetchone()
        return _dict(row) if row else None

    def get_mismatches(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT t.case_id, t.params_json, d.*
            FROM comparison_details d
            JOIN comparison_results c ON c.id = d.comparison_result_id
            JOIN test_case_runs t ON t.id = c.test_case_run_id
            WHERE t.migration_run_id = ? AND d.status IN ('FAIL', 'ERROR')
            ORDER BY t.case_id, d.mapping_name
            """,
            (run_id,),
        ).fetchall()
        return [_dict(row) for row in rows]

    def get_details(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT t.case_id, t.params_json, d.*, x.status AS cross_check_status, x.notes_json
            FROM comparison_details d
            JOIN comparison_results c ON c.id = d.comparison_result_id
            JOIN test_case_runs t ON t.id = c.test_case_run_id
            LEFT JOIN cross_check_results x ON x.comparison_result_id = c.id
            WHERE t.migration_run_id = ?
            ORDER BY t.case_id, d.mapping_name
            """,
            (run_id,),
        ).fetchall()
        return [_dict(row) for row in rows]

    def get_api_results(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT t.case_id, t.params_json, a.*
            FROM api_execution_results a
            JOIN test_case_runs t ON t.id = a.test_case_run_id
            WHERE t.migration_run_id = ?
            ORDER BY t.case_id, a.api_role, a.api_name
            """,
            (run_id,),
        ).fetchall()
        return [_dict(row) for row in rows]


def _dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def jdumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
