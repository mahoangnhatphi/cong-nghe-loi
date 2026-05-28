# API Migration Checker

Simple Python implementation for API migration result verification with a server-rendered Flask web UI and a small CLI.

## What It Does

- Loads YAML or JSON migration configs.
- Validates API definitions, test cases, and field mappings.
- Executes source and target APIs, including mock responses for local testing.
- Extracts response values using paths like `data.customer.name` and `items[0].price`.
- Compares mapped fields with `exact`, `ignore_case`, `number`, `boolean`, `date_format`, `contains`, `regex`, and `custom_transform`.
- Cross-checks suspicious results.
- Saves runs to SQLite.
- Exports JSON, CSV, and TXT reports.
- Provides a simple Python web UI for validate/run/report flows.

## Install

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

## Start Web UI

```bash
api-migration-checker web --host 127.0.0.1 --port 8000 --debug
```

Or directly:

```bash
python3 -m api_migration_checker.app.web.main
```

Open `http://127.0.0.1:8000`.

## Internal Sample API

The Flask app includes local JSON endpoints for real HTTP verification. Start the app, then set demo auth values in another terminal:

```bash
export OLD_API_TOKEN="old-demo-token"
export OLD_API_KEY="old-demo-api-key"
export OLD_LEGACY_SESSION="old-session-123"
export NEW_API_TOKEN="new-demo-token"
export NEW_API_KEY="new-demo-api-key"
export NEW_SESSION_ID="new-session-456"
```

Use this config path in the web UI:

```text
examples/config.internal-api.yaml
```

The sample exercises bearer auth, API key auth, cookie header auth, and structured `cookies:` auth.

## Create Config Screen

Open `/config/new` to generate and download a YAML config for a real source API and target API. The form writes secrets as environment variable placeholders such as `${OLD_API_TOKEN}` instead of raw secret values.

## Response Mapper

Open `/mapper` to generate mappings from examples. It supports three input modes:

- YAML API specs with pasted responses.
- Plain JSON responses plus API metadata.
- Safe curl commands that are parsed and executed with Python HTTP requests, not shell execution.

curl mode requires checking the execution confirmation box. Unsafe shell syntax such as pipes, redirects, command chaining, subshells, output files, and unsupported options is rejected.

The mapper also supports optional AI-assisted matching. If `claude` or `opencode` is available on the machine, enable `Use AI-assisted mapping`, choose a provider, and confirm consent. The app sends only field paths and optional sample values, not headers/cookies/tokens. If CLI execution fails, the result page shows a generated prompt you can copy into an AI tool manually, then paste the returned JSON back into the mapper.

## Try The Sample

Use `examples/config.sample.yaml` in the web form as a path, or upload it.

CLI usage:

```bash
api-migration-checker validate --config examples/config.sample.yaml
api-migration-checker run --config examples/config.sample.yaml
api-migration-checker report --db ./results/migration_check.db --run-id 1
```

## Web UI Features

- Upload YAML/JSON config.
- Provide local config path.
- Validate config.
- Run all cases or one case ID.
- View run summary.
- View field-by-field comparisons.
- View mismatches.
- Download JSON, CSV, and TXT reports.

## Notes

This is a practical first milestone. The web UI is intentionally Python-only: Flask, Jinja2 templates, and plain CSS. It can later be extended with background jobs, progress updates, auth, and a richer dashboard.
