from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from api_migration_checker.app.config import load_config, validate_config
from api_migration_checker.app.mapper import (
    build_config,
    dataclass_dict,
    execute_curl_specs,
    flatten_api_responses,
    from_json,
    parse_api_specs,
    parse_curl_specs,
    parse_json_response_mode,
    parse_test_cases,
    suggestion_to_mapping,
    suggest_mappings,
    to_json,
)
from api_migration_checker.app.runner import run_check
from api_migration_checker.app.storage import SQLiteRepository


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = Path("./web_runs")
UPLOAD_DIR = WORK_DIR / "uploads"
DEFAULT_DB = Path("./results/migration_check.db")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.post("/validate")
def validate_upload() -> str:
    try:
        path = _get_config_path()
        report = validate_config(load_config(path))
    except Exception as exc:
        path = request.form.get("config_path", "")
        report = type("Report", (), {"valid": False, "errors": [str(exc)], "warnings": []})()
    return render_template("validate.html", path=str(path), report=report)


@app.post("/run")
def run_upload() -> tuple[str, int] | str:
    try:
        path = _get_config_path()
        case_id = request.form.get("case_id") or None
        result = run_check(path, case_id=case_id, db_path=DEFAULT_DB)
    except Exception as exc:
        return render_template("error.html", message=str(exc)), 400
    return render_template("run.html", result=result)


@app.get("/runs")
def runs() -> str:
    repo = SQLiteRepository(DEFAULT_DB)
    rows = repo.list_runs()
    repo.close()
    return render_template("runs.html", runs=rows)


@app.get("/internal-api")
def internal_api() -> str:
    return render_template("internal_api.html")


@app.get("/config/new")
def new_config() -> str:
    return render_template("config_form.html")


@app.get("/mapper")
def mapper() -> str:
    return render_template(
        "mapper.html",
        old_apis=DEFAULT_OLD_APIS,
        new_apis=DEFAULT_NEW_APIS,
        old_metadata=DEFAULT_OLD_METADATA,
        new_metadata=DEFAULT_NEW_METADATA,
        old_json_responses=DEFAULT_OLD_JSON_RESPONSES,
        new_json_responses=DEFAULT_NEW_JSON_RESPONSES,
        old_curl_specs=DEFAULT_OLD_CURL_SPECS,
        new_curl_specs=DEFAULT_NEW_CURL_SPECS,
        test_cases=DEFAULT_TEST_CASES,
    )


@app.post("/mapper/analyze")
def mapper_analyze() -> tuple[str, int] | str:
    try:
        input_mode = request.form.get("input_mode", "yaml")
        execution_statuses: list[dict[str, Any]] = []
        if input_mode == "json":
            source_apis = parse_json_response_mode(request.form.get("old_metadata", ""), request.form.get("old_json_responses", ""))
            target_apis = parse_json_response_mode(request.form.get("new_metadata", ""), request.form.get("new_json_responses", ""))
        elif input_mode == "curl":
            if request.form.get("allow_curl_execute") != "yes":
                raise ValueError("Enable the curl execution checkbox before analyzing curl commands")
            source_apis, source_statuses = execute_curl_specs(parse_curl_specs(request.form.get("old_curl_specs", "")))
            target_apis, target_statuses = execute_curl_specs(parse_curl_specs(request.form.get("new_curl_specs", "")))
            execution_statuses = [*source_statuses, *target_statuses]
        else:
            source_apis = parse_api_specs(request.form.get("old_apis", ""))
            target_apis = parse_api_specs(request.form.get("new_apis", ""))
        test_cases = parse_test_cases(request.form.get("test_cases", ""))
        suggestions, unmatched_source, unmatched_target = suggest_mappings(flatten_api_responses(source_apis), flatten_api_responses(target_apis))
    except Exception as exc:
        return render_template("error.html", message=str(exc)), 400
    return render_template(
        "mapper_result.html",
        suggestions=suggestions,
        unmatched_source=unmatched_source,
        unmatched_target=unmatched_target,
        source_apis_json=to_json(source_apis),
        target_apis_json=to_json(target_apis),
        test_cases_json=to_json(test_cases),
        suggestion_dicts=[dataclass_dict(item) for item in suggestions],
        execution_statuses=execution_statuses,
        to_json=to_json,
    )


@app.post("/mapper/download")
def mapper_download() -> Response:
    source_apis = from_json(request.form["source_apis_json"])
    target_apis = from_json(request.form["target_apis_json"])
    test_cases = from_json(request.form["test_cases_json"])
    suggestions = from_json(request.form["suggestions_json"])
    selected = set(request.form.getlist("selected_mapping"))
    mappings: list[dict[str, Any]] = []
    for index, suggestion in enumerate(suggestions):
        if str(index) not in selected:
            continue
        mapping = suggestion_to_mapping(type("Suggestion", (), suggestion), f"mapping_{index + 1:03d}")
        for field_name in ("source_api", "source_field", "target_api", "target_field", "compare_type"):
            override = request.form.get(f"{field_name}_{index}")
            if override:
                mapping[field_name] = override
        if request.form.get(f"tolerance_{index}"):
            mapping["tolerance"] = float(request.form[f"tolerance_{index}"])
        if request.form.get(f"source_format_{index}"):
            mapping["source_format"] = request.form[f"source_format_{index}"]
        if request.form.get(f"target_format_{index}"):
            mapping["target_format"] = request.form[f"target_format_{index}"]
        mappings.append(mapping)
    config = build_config(source_apis, target_apis, test_cases, mappings)
    content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    return Response(content, mimetype="application/x-yaml", headers={"Content-Disposition": "attachment; filename=mapped-api-config.yaml"})


@app.post("/config/download")
def download_config() -> Response:
    config = build_config_from_form(request.form)
    content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    return Response(
        content,
        mimetype="application/x-yaml",
        headers={"Content-Disposition": "attachment; filename=api-migration-config.yaml"},
    )


@app.get("/internal-api/old/customers/<customer_id>")
def internal_old_customer(customer_id: str):
    require_bearer("old-demo-token")
    return jsonify(
        {
            "data": {
                "customer_id": customer_id,
                "customer_name": "张伟",
                "english_name": "Zhang Wei",
                "birth_date": "28/05/1990",
                "vip": "yes",
                "contact": {
                    "email": "zhang.wei@example.com",
                    "phone": "+86-138-0000-0000",
                    "address": {"city": "上海", "district": "浦东新区"},
                },
            }
        }
    )


@app.post("/internal-api/old/orders/<order_id>")
def internal_old_order(order_id: str):
    require_header("X-API-Key", "old-demo-api-key")
    payload = request.get_json(silent=True) or {}
    return jsonify(
        {
            "order": {
                "id": order_id,
                "requested_customer_id": payload.get("customer_id"),
                "created_at": "2026/05/28",
                "total_amount": "199.991",
                "currency": "CNY",
                "items": [
                    {"sku": "SKU-CN-001", "title": "高级茶杯", "price": "88.80"},
                    {"sku": "SKU-CN-002", "title": "龙井茶", "price": "111.19"},
                ],
            }
        }
    )


@app.get("/internal-api/old/loyalty/<customer_id>")
def internal_old_loyalty(customer_id: str):
    require_cookie("legacy_session", "old-session-123")
    return jsonify(
        {
            "loyalty": {
                "customer_id": customer_id,
                "member_level": "金牌会员",
                "points": "1500",
                "expiry_date": "31/12/2026",
                "referral_code": "CN-REF-2026-ZW",
            }
        }
    )


@app.get("/internal-api/new/profiles/<customer_id>")
def internal_new_profile(customer_id: str):
    require_bearer("new-demo-token")
    return jsonify(
        {
            "profile": {
                "identity": {
                    "id": customer_id,
                    "fullName": "张伟",
                    "displayName": "ZHANG WEI",
                    "birthDate": "1990-05-28",
                },
                "flags": {"isVip": True},
                "contact": {
                    "emailAddress": "zhang.wei@example.com",
                    "primaryAddress": {"city": "上海市", "district": "浦东新区"},
                },
            }
        }
    )


@app.get("/internal-api/new/order-summary/<order_id>")
def internal_new_order_summary(order_id: str):
    require_cookie("new_session", "new-session-456")
    return jsonify(
        {
            "summary": {
                "orderId": order_id,
                "placedDate": "28-05-2026",
                "payment": {"total": 200.00, "wrongTotal": 250.00, "currencyCode": "CNY"},
                "lines": [
                    {"productCode": "SKU-CN-001", "productName": "高级茶杯", "unitPrice": 88.8},
                    {"productCode": "SKU-CN-002", "productName": "西湖龙井茶叶", "unitPrice": 120.0},
                ],
            }
        }
    )


@app.get("/internal-api/new/rewards/<customer_id>")
def internal_new_rewards(customer_id: str):
    require_header("X-API-Key", "new-demo-api-key")
    return jsonify(
        {
            "rewards": {
                "customerId": customer_id,
                "tierName": "银牌会员",
                "pointsBalance": 1500,
                "validUntil": "2026-12-31",
                "referral": {"code": "BAD-REF-2026"},
            }
        }
    )


@app.get("/runs/<int:run_id>")
def run_detail(run_id: int) -> str:
    repo = SQLiteRepository(DEFAULT_DB)
    row = repo.get_run(run_id)
    details = repo.get_details(run_id)
    api_results = repo.get_api_results(run_id)
    repo.close()
    if not row:
        abort(404, "Run not found")
    summary = json.loads(row["summary_json"] or "{}")
    return render_template("detail.html", run=row, summary=summary, details=details, api_results=api_results)


@app.get("/runs/<int:run_id>/responses")
def run_responses(run_id: int) -> str:
    repo = SQLiteRepository(DEFAULT_DB)
    row = repo.get_run(run_id)
    api_results = repo.get_api_results(run_id)
    repo.close()
    if not row:
        abort(404, "Run not found")

    cases: dict[str, dict[str, Any]] = {}
    for result in api_results:
        case = cases.setdefault(
            result["case_id"],
            {"case_id": result["case_id"], "params_json": result.get("params_json"), "source": [], "target": []},
        )
        role = "source" if result.get("api_role") == "source" else "target"
        item = dict(result)
        item["response_pretty"] = pretty_json_text(result.get("response_body_json"))
        item["request_body_pretty"] = pretty_json_text(result.get("request_body_json"))
        item["request_headers_pretty"] = pretty_json_text(result.get("request_headers_json"))
        case[role].append(item)
    return render_template("responses.html", run=row, cases=list(cases.values()))


@app.get("/runs/<int:run_id>/mismatches")
def mismatches(run_id: int) -> str:
    repo = SQLiteRepository(DEFAULT_DB)
    rows = repo.get_mismatches(run_id)
    repo.close()
    return render_template("mismatches.html", run_id=run_id, mismatches=rows)


@app.get("/runs/<int:run_id>/download/<kind>")
def download(run_id: int, kind: str):
    names = {"json": "full_result.json", "csv": "mismatch_report.csv", "txt": "summary.txt"}
    if kind not in names:
        abort(404, "Unknown export kind")
    path = Path("./results") / f"run_{run_id}" / names[kind]
    if not path.exists():
        abort(404, "Export not found")
    return send_file(path, as_attachment=True, download_name=path.name)


def _get_config_path() -> Path:
    config_file = request.files.get("config_file")
    config_path = request.form.get("config_path", "").strip()

    if config_file and config_file.filename:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = Path(config_file.filename).name
        target = UPLOAD_DIR / safe_name
        with target.open("wb") as handle:
            shutil.copyfileobj(config_file.stream, handle)
        return target

    if config_path:
        path = Path(config_path)
        if path.exists():
            return path

    raise ValueError("Upload a config file or provide a valid config path")


def require_bearer(expected_token: str) -> None:
    expected = f"Bearer {expected_token}"
    if request.headers.get("Authorization") != expected:
        abort(401, "Missing or invalid bearer token")


def require_header(name: str, expected_value: str) -> None:
    if request.headers.get(name) != expected_value:
        abort(401, f"Missing or invalid {name}")


def require_cookie(name: str, expected_value: str) -> None:
    if request.cookies.get(name) != expected_value:
        abort(401, f"Missing or invalid cookie: {name}")


def build_config_from_form(form: Any) -> dict[str, Any]:
    source_name = form.get("source_api_name", "old_customer_api") or "old_customer_api"
    target_name = form.get("target_api_name", "new_customer_api") or "new_customer_api"
    source_api = build_api_from_form(form, "source")
    target_api = build_api_from_form(form, "target")
    source_api["name"] = source_name
    target_api["name"] = target_name
    source_field = form.get("source_field", "data.customer_name") or "data.customer_name"
    target_field = form.get("target_field", "profile.identity.fullName") or "profile.identity.fullName"
    compare_type = form.get("compare_type", "exact") or "exact"

    mapping: dict[str, Any] = {
        "name": form.get("mapping_name", "first_mapping") or "first_mapping",
        "source_api": source_name,
        "source_field": source_field,
        "target_api": target_name,
        "target_field": target_field,
        "compare_type": compare_type,
    }
    if form.get("tolerance"):
        mapping["tolerance"] = float(form["tolerance"])
    if form.get("source_format"):
        mapping["source_format"] = form["source_format"]
    if form.get("target_format"):
        mapping["target_format"] = form["target_format"]

    return {
        "migration_name": form.get("migration_name", "real-api-migration") or "real-api-migration",
        "source_apis": [source_api],
        "target_apis": [target_api],
        "test_cases": [
            {
                "id": form.get("case_id", "case_001") or "case_001",
                "params": parse_json_object(form.get("params_json")) or {"customerId": "C1001", "orderId": "O9001"},
            }
        ],
        "field_mapping": [mapping],
        "output": {"directory": "./results", "sqlite_path": "./results/migration_check.db"},
    }


def build_api_from_form(form: Any, prefix: str) -> dict[str, Any]:
    api: dict[str, Any] = {
        "method": form.get(f"{prefix}_method", "GET") or "GET",
        "url": form.get(f"{prefix}_url", "") or "http://127.0.0.1:8000/internal-api/old/customers/{customerId}",
        "headers": parse_key_value_lines(form.get(f"{prefix}_headers", "")),
        "query_params": parse_key_value_lines(form.get(f"{prefix}_query", "")),
        "timeout_seconds": int(form.get(f"{prefix}_timeout", 10) or 10),
        "retry_count": int(form.get(f"{prefix}_retry", 1) or 1),
    }
    body = parse_json_object(form.get(f"{prefix}_body"))
    if body:
        api["body"] = body
    cookies = parse_key_value_lines(form.get(f"{prefix}_cookies", ""))
    if cookies:
        api["cookies"] = cookies
    apply_auth(api, form, prefix)
    if not api["headers"]:
        api.pop("headers")
    if not api["query_params"]:
        api.pop("query_params")
    return api


def apply_auth(api: dict[str, Any], form: Any, prefix: str) -> None:
    auth_type = form.get(f"{prefix}_auth_type", "none")
    headers = api.setdefault("headers", {})
    if auth_type == "bearer":
        env_name = form.get(f"{prefix}_auth_env", "API_TOKEN") or "API_TOKEN"
        headers["Authorization"] = f"Bearer ${{{env_name}}}"
    elif auth_type == "api_key":
        header_name = form.get(f"{prefix}_api_key_header", "X-API-Key") or "X-API-Key"
        env_name = form.get(f"{prefix}_auth_env", "API_KEY") or "API_KEY"
        headers[header_name] = f"${{{env_name}}}"
    elif auth_type == "basic":
        env_name = form.get(f"{prefix}_auth_env", "BASIC_AUTH") or "BASIC_AUTH"
        headers["Authorization"] = f"Basic ${{{env_name}}}"
    elif auth_type == "cookie_header":
        headers["Cookie"] = form.get(f"{prefix}_cookie_header", "sessionid=${SESSION_ID}") or "sessionid=${SESSION_ID}"
    elif auth_type == "cookie_object_simple":
        env_prefix = form.get(f"{prefix}_cookie_env_prefix", prefix.upper()) or prefix.upper()
        names = [name.strip() for name in form.get(f"{prefix}_cookie_names", "sessionid").split(",") if name.strip()]
        api["cookies"] = {name: f"${{{env_prefix}_{normalize_env_name(name)}}}" for name in names}
    elif auth_type == "cookie_object_advanced":
        api["cookies"] = parse_cookie_advanced(form.get(f"{prefix}_cookies_advanced", ""))


def parse_key_value_lines(raw: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        separator = ":" if ":" in line else "="
        if separator not in line:
            continue
        key, value = line.split(separator, 1)
        result[key.strip()] = value.strip().strip('"')
    return result


def parse_cookie_advanced(raw: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        cookie_name, env_name = line.split("=", 1)
        result[cookie_name.strip()] = f"${{{env_name.strip()}}}"
    return result


def parse_json_object(raw: str | None) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    data = yaml.safe_load(raw)
    return data if isinstance(data, dict) else {}


def normalize_env_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in value.upper())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def pretty_json_text(raw: Any) -> str:
    if raw in (None, ""):
        return "null"
    if not isinstance(raw, str):
        return json.dumps(raw, ensure_ascii=False, indent=2)
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return raw


DEFAULT_OLD_APIS = """- name: old_customer_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/old/customers/{customerId}"
  auth_type: bearer
  auth_env: OLD_API_TOKEN
  response:
    data:
      customer_id: C1001
      customer_name: "张伟"
      english_name: "Zhang Wei"
      birth_date: "28/05/1990"
      vip: "yes"
      contact:
        email: "zhang.wei@example.com"
        address:
          city: "上海"
          district: "浦东新区"

- name: old_order_api
  method: POST
  url: "http://127.0.0.1:8000/internal-api/old/orders/{orderId}"
  auth_type: api_key
  api_key_header: X-API-Key
  auth_env: OLD_API_KEY
  body:
    customer_id: "${customerId}"
  response:
    order:
      id: O9001
      created_at: "2026/05/28"
      total_amount: "199.991"
      items:
        - sku: SKU-CN-001
          title: "高级茶杯"
          price: "88.80"
        - sku: SKU-CN-002
          title: "龙井茶"
          price: "111.19"

- name: old_loyalty_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/old/loyalty/{customerId}"
  auth_type: cookie_header
  cookie_header: "legacy_session=${OLD_LEGACY_SESSION}"
  response:
    loyalty:
      member_level: "金牌会员"
      points: "1500"
      expiry_date: "31/12/2026"
"""


DEFAULT_NEW_APIS = """- name: new_profile_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/new/profiles/{customerId}"
  auth_type: bearer
  auth_env: NEW_API_TOKEN
  response:
    profile:
      identity:
        id: C1001
        fullName: "张伟"
        displayName: "ZHANG WEI"
        birthDate: "1990-05-28"
      flags:
        isVip: true
      contact:
        emailAddress: "zhang.wei@example.com"
        primaryAddress:
          city: "上海市"
          district: "浦东新区"

- name: new_order_summary_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/new/order-summary/{orderId}"
  cookies:
    new_session: "${NEW_SESSION_ID}"
  response:
    summary:
      orderId: O9001
      placedDate: "28-05-2026"
      payment:
        total: 200.00
        wrongTotal: 250.00
      lines:
        - productCode: SKU-CN-001
          productName: "高级茶杯"
          unitPrice: 88.8
        - productCode: SKU-CN-002
          productName: "西湖龙井茶叶"
          unitPrice: 120.0

- name: new_rewards_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/new/rewards/{customerId}"
  auth_type: api_key
  api_key_header: X-API-Key
  auth_env: NEW_API_KEY
  response:
    rewards:
      tierName: "银牌会员"
      pointsBalance: 1500
      validUntil: "2026-12-31"
"""


DEFAULT_TEST_CASES = """customerId,orderId,loyaltyId
C1001,O9001,L1001
C1002,O9002,L1002
"""


DEFAULT_OLD_METADATA = """- name: old_customer_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/old/customers/{customerId}"
  auth_type: bearer
  auth_env: OLD_API_TOKEN

- name: old_order_api
  method: POST
  url: "http://127.0.0.1:8000/internal-api/old/orders/{orderId}"
  auth_type: api_key
  api_key_header: X-API-Key
  auth_env: OLD_API_KEY
  body:
    customer_id: "${customerId}"
"""


DEFAULT_NEW_METADATA = """- name: new_profile_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/new/profiles/{customerId}"
  auth_type: bearer
  auth_env: NEW_API_TOKEN

- name: new_order_summary_api
  method: GET
  url: "http://127.0.0.1:8000/internal-api/new/order-summary/{orderId}"
  cookies:
    new_session: "${NEW_SESSION_ID}"
"""


DEFAULT_OLD_JSON_RESPONSES = """{
  "old_customer_api": {
    "data": {
      "customer_name": "张伟",
      "birth_date": "28/05/1990",
      "vip": "yes",
      "contact": {"address": {"city": "上海"}}
    }
  },
  "old_order_api": {
    "order": {
      "created_at": "2026/05/28",
      "total_amount": "199.991",
      "items": [{"title": "高级茶杯", "price": "88.80"}]
    }
  }
}
"""


DEFAULT_NEW_JSON_RESPONSES = """{
  "new_profile_api": {
    "profile": {
      "identity": {"fullName": "张伟", "birthDate": "1990-05-28"},
      "flags": {"isVip": true},
      "contact": {"primaryAddress": {"city": "上海市"}}
    }
  },
  "new_order_summary_api": {
    "summary": {
      "placedDate": "28-05-2026",
      "payment": {"total": 200.0},
      "lines": [{"productName": "高级茶杯", "unitPrice": 88.8}]
    }
  }
}
"""


DEFAULT_OLD_CURL_SPECS = """- name: old_customer_api
  url_template: "http://127.0.0.1:8000/internal-api/old/customers/{customerId}"
  curl: |
    curl -X GET "http://127.0.0.1:8000/internal-api/old/customers/C1001" \
      -H "Authorization: Bearer old-demo-token" \
      -H "Accept: application/json"

- name: old_order_api
  url_template: "http://127.0.0.1:8000/internal-api/old/orders/{orderId}"
  curl: |
    curl -X POST "http://127.0.0.1:8000/internal-api/old/orders/O9001" \
      -H "X-API-Key: old-demo-api-key" \
      -H "Content-Type: application/json" \
      --data '{"customer_id":"C1001"}'
"""


DEFAULT_NEW_CURL_SPECS = """- name: new_profile_api
  url_template: "http://127.0.0.1:8000/internal-api/new/profiles/{customerId}"
  curl: |
    curl -X GET "http://127.0.0.1:8000/internal-api/new/profiles/C1001" \
      -H "Authorization: Bearer new-demo-token"

- name: new_order_summary_api
  url_template: "http://127.0.0.1:8000/internal-api/new/order-summary/{orderId}"
  curl: |
    curl -X GET "http://127.0.0.1:8000/internal-api/new/order-summary/O9001" \
      --cookie "new_session=new-session-456"
"""


def run_server(host: str = "127.0.0.1", port: int = 8000, debug: bool = False) -> None:
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server(debug=True)
