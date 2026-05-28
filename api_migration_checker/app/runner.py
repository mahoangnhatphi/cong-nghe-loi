from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from api_migration_checker.app.compare import compare_values
from api_migration_checker.app.config import load_config, validate_config
from api_migration_checker.app.extract import extract_value
from api_migration_checker.app.storage import SQLiteRepository
from api_migration_checker.app.template_resolver import resolve_templates


def run_check(config_path: str | Path, case_id: str | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    report = validate_config(config)
    if not report.valid:
        raise ValueError("Config validation failed: " + "; ".join(report.errors))

    output = config.get("output", {})
    result_dir = Path(output.get("directory", "./results"))
    db = Path(db_path or output.get("sqlite_path", result_dir / "migration_check.db"))
    repo = SQLiteRepository(db)

    run_id = repo.create_run(config["migration_name"], str(config_path))
    selected_cases = [case for case in config["test_cases"] if case_id is None or case["id"] == case_id]
    if not selected_cases:
        raise ValueError(f"No matching test case: {case_id}")

    source_apis = {api["name"]: api for api in config["source_apis"]}
    target_apis = {api["name"]: api for api in config["target_apis"]}
    all_apis = {**source_apis, **target_apis}
    run_payload: dict[str, Any] = {"run_id": run_id, "migration_name": config["migration_name"], "test_cases": []}

    for test_case in selected_cases:
        params = {**config.get("variables", {}), **test_case.get("params", {})}
        responses: dict[str, dict[str, Any]] = {}
        api_results: list[dict[str, Any]] = []

        needed_source = {mapping["source_api"] for mapping in config["field_mapping"]}
        needed_target = {mapping["target_api"] for mapping in config["field_mapping"]}
        for api_name in [*sorted(needed_source), *sorted(needed_target)]:
            api = all_apis[api_name]
            role = "source" if api_name in source_apis else "target"
            result = execute_api(api, params, role)
            responses[api_name] = result
            api_results.append(result)

        details: list[dict[str, Any]] = []
        for mapping in config["field_mapping"]:
            source_response = responses[mapping["source_api"]].get("response_body")
            target_response = responses[mapping["target_api"]].get("response_body")
            source_extracted = extract_value(source_response, mapping["source_field"])
            target_extracted = extract_value(target_response, mapping["target_field"])
            if not source_extracted.found:
                status, message = "FAIL", source_extracted.error
            elif not target_extracted.found:
                status, message = "FAIL", target_extracted.error
            else:
                status, message = compare_values(source_extracted.value, target_extracted.value, mapping)
            details.append(
                {
                    "mapping_name": mapping["name"],
                    "source_api": mapping["source_api"],
                    "target_api": mapping["target_api"],
                    "source_field": mapping["source_field"],
                    "target_field": mapping["target_field"],
                    "compare_type": mapping.get("compare_type", "exact"),
                    "source_value": source_extracted.value if source_extracted.found else None,
                    "target_value": target_extracted.value if target_extracted.found else None,
                    "status": status,
                    "message": message,
                }
            )

        cross_status, notes = cross_check(details, api_results)
        case_status = "PASS" if cross_status == "CONFIRMED_PASS" else "FAIL" if cross_status == "CONFIRMED_FAIL" else cross_status
        test_case_run_id = repo.insert_test_case(run_id, test_case["id"], case_status, test_case.get("params", {}))
        for api_result in api_results:
            repo.insert_api_result(test_case_run_id, api_result)
        comparison_id = repo.insert_comparison(test_case_run_id, "PASS" if all(d["status"] in {"PASS", "SKIPPED"} for d in details) else "FAIL", details)
        repo.insert_cross_check(comparison_id, cross_status, notes)
        run_payload["test_cases"].append(
            {
                "case_id": test_case["id"],
                "params": test_case.get("params", {}),
                "resolved_params": params,
                "status": case_status,
                "api_results": api_results,
                "comparison_details": details,
                "cross_check": {"status": cross_status, "notes": notes},
            }
        )

    summary = summarize(run_payload)
    repo.finish_run(run_id, "PASS" if summary["failed_cases"] == 0 and summary["error_cases"] == 0 else "FAIL", summary)
    export_files = export_run(result_dir / f"run_{run_id}", run_payload, summary, repo.get_mismatches(run_id))
    repo.close()
    return {"run_id": run_id, "db_path": str(db), "summary": summary, "files": export_files}


def execute_api(api: dict[str, Any], params: dict[str, Any], role: str) -> dict[str, Any]:
    resolved = resolve_templates(api, params)
    method = resolved["method"].upper()
    url = resolved["url"].format(**params)
    headers = resolved.get("headers") or {}
    query = resolved.get("query_params") or {}
    cookies = resolved.get("cookies") or {}
    body = resolved.get("body")
    started = time.perf_counter()

    if "mock_response" in resolved:
        return {
            "api_name": resolved["name"],
            "api_role": role,
            "method": method,
            "url": url,
            "request_headers": headers,
            "request_cookies": mask_secret_values(cookies),
            "request_body": body,
            "status_code": int(resolved.get("mock_status_code", 200)),
            "response_body": resolved["mock_response"],
            "error": None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    retry_count = int(resolved.get("retry_count", 0))
    timeout = float(resolved.get("timeout_seconds", 10))
    error: str | None = None
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise RuntimeError("httpx is required for real HTTP API execution. Install the project dependencies first.") from exc

    for attempt in range(retry_count + 1):
        try:
            response = httpx.request(method, url, headers=headers, params=query, json=body, cookies=cookies, timeout=timeout)
            try:
                response_body: Any = response.json()
            except ValueError:
                response_body = response.text
            return {
                "api_name": resolved["name"],
                "api_role": role,
                "method": method,
                "url": str(response.url),
                "request_headers": headers,
                "request_cookies": mask_secret_values(cookies),
                "request_body": body,
                "status_code": response.status_code,
                "response_body": response_body,
                "error": None if response.status_code < 500 or attempt == retry_count else f"HTTP {response.status_code}",
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            error = str(exc)
            if attempt == retry_count:
                break
    return {
        "api_name": resolved["name"],
        "api_role": role,
        "method": method,
        "url": url,
        "request_headers": headers,
        "request_cookies": mask_secret_values(cookies),
        "request_body": body,
        "status_code": None,
        "response_body": None,
        "error": error,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def mask_secret_values(values: dict[str, Any]) -> dict[str, str]:
    return {str(key): "***" for key in values}


def cross_check(details: list[dict[str, Any]], api_results: list[dict[str, Any]]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if any(result.get("error") for result in api_results):
        notes.append("One or more API executions failed")
        return "INVALID_TEST", notes
    if any(not result.get("status_code") or not 200 <= int(result["status_code"]) < 300 for result in api_results):
        notes.append("One or more API responses are not 2xx")
        return "INVALID_TEST", notes
    ignored = len([detail for detail in details if detail["status"] == "SKIPPED"])
    if details and ignored / len(details) > 0.5:
        notes.append("More than 50% of fields were ignored")
    for detail in details:
        if detail["status"] == "PASS" and detail.get("source_value") in (None, "") and detail.get("target_value") in (None, ""):
            notes.append(f"{detail['mapping_name']} passed with both values empty")
    if any(detail["status"] in {"FAIL", "ERROR"} for detail in details):
        return "CONFIRMED_FAIL", notes
    if notes:
        return "NEEDS_REVIEW", notes
    return "CONFIRMED_PASS", notes


def summarize(run_payload: dict[str, Any]) -> dict[str, int]:
    cases = run_payload["test_cases"]
    details = [detail for case in cases for detail in case["comparison_details"]]
    return {
        "total_test_cases": len(cases),
        "passed_cases": len([case for case in cases if case["status"] == "PASS"]),
        "failed_cases": len([case for case in cases if case["status"] == "FAIL"]),
        "error_cases": len([case for case in cases if case["status"] == "INVALID_TEST"]),
        "needs_review_cases": len([case for case in cases if case["status"] == "NEEDS_REVIEW"]),
        "total_compared_fields": len([detail for detail in details if detail["status"] != "SKIPPED"]),
        "total_mismatched_fields": len([detail for detail in details if detail["status"] in {"FAIL", "ERROR"}]),
    }


def export_run(output_dir: Path, run_payload: dict[str, Any], summary: dict[str, int], mismatches: list[dict[str, Any]]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    full_json = output_dir / "full_result.json"
    mismatch_csv = output_dir / "mismatch_report.csv"
    summary_txt = output_dir / "summary.txt"
    full_json.write_text(json.dumps({"summary": summary, **run_payload}, indent=2, ensure_ascii=False), encoding="utf-8")
    with mismatch_csv.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "case_id",
            "params_json",
            "mapping_name",
            "source_api",
            "target_api",
            "source_field",
            "target_field",
            "source_value_json",
            "target_value_json",
            "status",
            "message",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in mismatches:
            writer.writerow({field: row.get(field) for field in fields})
    summary_txt.write_text("\n".join(f"{key}: {value}" for key, value in summary.items()) + "\n", encoding="utf-8")
    return {"json": str(full_json), "csv": str(mismatch_csv), "txt": str(summary_txt)}
