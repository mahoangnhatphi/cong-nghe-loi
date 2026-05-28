# API Migration Checker Tutorial

This tutorial shows how to use the Python web UI and CLI with a deliberately problematic many-to-many mock API migration config.

The sample file is:

```text
examples/config.problematic-many-to-many.yaml
```

It uses mock responses, so you do not need real API servers.

## 1. What This Sample Demonstrates

The sample verifies a migration from multiple old APIs to multiple new APIs.

Old APIs:

- `old_customer_api`
- `old_order_api`
- `old_loyalty_api`

New APIs:

- `new_profile_api`
- `new_order_summary_api`
- `new_rewards_api`

The mappings intentionally include both passing and failing checks so you can learn how reports look.

It includes:

- Many-to-many API verification
- Different field names
- Nested response fields
- Array paths like `order.items[0].price`
- Date format conversion
- Chinese Unicode values such as `张伟`, `上海`, and `金牌会员`
- Numeric tolerance
- Boolean normalization
- Contains comparison
- Regex comparison
- Missing source fields
- Missing target fields
- Ignored fields
- Suspicious empty values detected by cross-check notes

## 2. Install Dependencies

From the project root, install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

If you want the CLI command installed as `api-migration-checker`, install the project in editable mode:

```bash
python3 -m pip install -e .
```

## 3. Start The Web UI

Start the simple Python web UI:

```bash
python3 -m api_migration_checker.app.web.main
```

If you installed the project in editable mode, you can also start it through the CLI:

```bash
api-migration-checker web --host 127.0.0.1 --port 8000 --debug
```

Open this URL:

```text
http://127.0.0.1:8000
```

## 4. Run The Problematic Sample In The Web UI

On the home page, use the config path input:

```text
examples/config.problematic-many-to-many.yaml
```

Click `Validate` first.

Expected result:

```text
Config is valid.
```

## 4a. Run The Internal Real HTTP Sample

The Flask app also exposes internal sample APIs that behave like real HTTP APIs. They require authentication headers and cookies.

Start the app:

```bash
python3 -m api_migration_checker.app.web.main
```

In another terminal, set demo auth values:

```bash
export OLD_API_TOKEN="old-demo-token"
export OLD_API_KEY="old-demo-api-key"
export OLD_LEGACY_SESSION="old-session-123"
export NEW_API_TOKEN="new-demo-token"
export NEW_API_KEY="new-demo-api-key"
export NEW_SESSION_ID="new-session-456"
```

Use this config path on the Run page:

```text
examples/config.internal-api.yaml
```

This config calls local endpoints under:

```text
http://127.0.0.1:8000/internal-api/...
```

It verifies bearer token auth, API key auth, cookie header auth, and structured `cookies:` auth.

## 4b. Create And Download A Config

Open:

```text
http://127.0.0.1:8000/config/new
```

Fill in source API, target API, auth, test params, and the first field mapping. Click `Download YAML Config`.

The generated file uses environment variable placeholders for secrets, for example:

```yaml
headers:
  Authorization: "Bearer ${OLD_API_TOKEN}"
cookies:
  sessionid: "${NEW_SESSION_ID}"
```

## 4c. Use The Response Mapper

Open:

```text
http://127.0.0.1:8000/mapper
```

You can choose one of three modes:

- YAML API specs with pasted responses
- Plain JSON responses plus API metadata
- curl commands that the app executes to fetch responses

For curl mode, check the explicit execution checkbox before clicking `Analyze Responses`. The app parses a safe subset of curl and executes HTTP requests with Python. It rejects shell features such as pipes, redirects, command chaining, subshells, output files, and unsupported options.

After analysis, review old and new values side by side, keep good mappings checked, edit compare rules if needed, then download the generated YAML config.

Optional AI-assisted mapping:

1. Enable `Use AI-assisted mapping`.
2. Choose `Auto`, `Claude CLI`, or `OpenCode CLI`.
3. Check the consent box.
4. Leave `Include sample values` checked for better matches, or uncheck it for sensitive data.
5. Click `Analyze Responses`.

If automatic AI execution fails, open the generated prompt on the result page, copy it into your AI tool, then paste the JSON response into `Manual AI JSON response fallback` and analyze again.

Then go back and click `Run Check`.

The run should complete and show a summary with failures because the sample intentionally contains bad mappings and bad target values.

## 5. Run The Same Sample With CLI

Validate:

```bash
api-migration-checker validate --config examples/config.problematic-many-to-many.yaml
```

Run:

```bash
api-migration-checker run --config examples/config.problematic-many-to-many.yaml
```

If the script entry point is not installed, you can run the module directly:

```bash
python3 -m api_migration_checker.app.cli.commands validate --config examples/config.problematic-many-to-many.yaml
python3 -m api_migration_checker.app.cli.commands run --config examples/config.problematic-many-to-many.yaml
```

## 6. How Many-To-Many Mapping Works

Each field mapping names a source API and a target API.

Example:

```yaml
- name: order_total_tolerance_pass
  source_api: old_order_api
  source_field: order.total_amount
  target_api: new_order_summary_api
  target_field: summary.payment.total
  compare_type: number
  tolerance: 0.01
```

This means:

- Extract `order.total_amount` from `old_order_api`
- Extract `summary.payment.total` from `new_order_summary_api`
- Compare both values as numbers
- Allow a difference of `0.01`

Because each mapping chooses its own source and target API, one config can compare:

- One old API to one new API
- One old API to many new APIs
- Many old APIs to one new API
- Many old APIs to many new APIs

## 7. Nested Field Paths

The extractor supports object paths:

```text
data.contact.address.city
```

It also supports array indexes:

```text
order.items[0].price
summary.lines[1].unitPrice
```

Example mapping:

```yaml
- name: first_item_nested_price_pass
  source_api: old_order_api
  source_field: order.items[0].price
  target_api: new_order_summary_api
  target_field: summary.lines[0].unitPrice
  compare_type: number
  tolerance: 0.01
```

## 8. Date Format Examples

The sample includes old and new APIs that represent the same date differently.

Example 1:

```yaml
- name: birth_date_format_pass
  source_api: old_customer_api
  source_field: data.birth_date
  target_api: new_profile_api
  target_field: profile.identity.birthDate
  compare_type: date_format
  source_format: dd/MM/yyyy
  target_format: yyyy-MM-dd
```

This compares:

```text
28/05/1990
1990-05-28
```

Example 2:

```yaml
- name: signup_date_format_pass
  source_api: old_customer_api
  source_field: data.registered_at
  target_api: new_profile_api
  target_field: profile.identity.signupDate
  compare_type: date_format
  source_format: yyyy/MM/dd
  target_format: dd-MM-yyyy
```

This compares:

```text
2026/05/28
28-05-2026
```

Supported format tokens in this implementation include:

- `yyyy`
- `MM`
- `dd`
- `HH`
- `mm`
- `ss`

## 9. Chinese Unicode Examples

YAML supports Unicode text directly. The sample includes Chinese customer names, cities, addresses, product names, and loyalty tiers.

Exact Chinese match:

```yaml
- name: chinese_name_exact_pass
  source_api: old_customer_api
  source_field: data.customer_name
  target_api: new_profile_api
  target_field: profile.identity.fullName
  compare_type: exact
```

This compares:

```text
张伟
张伟
```

Chinese contains match:

```yaml
- name: chinese_city_contains_pass
  source_api: old_customer_api
  source_field: data.contact.address.city
  target_api: new_profile_api
  target_field: profile.contact.primaryAddress.city
  compare_type: contains
  contains_direction: target_contains_source
```

This passes because:

```text
上海市 contains 上海
```

Chinese mismatch example:

```yaml
- name: loyalty_tier_chinese_exact_fail
  source_api: old_loyalty_api
  source_field: loyalty.member_level
  target_api: new_rewards_api
  target_field: rewards.tierName
  compare_type: exact
```

This fails because:

```text
金牌会员 != 银牌会员
```

## 10. Expected Passing Checks

These mappings should pass:

- `chinese_name_exact_pass`
- `english_display_name_ignore_case_pass`
- `birth_date_format_pass`
- `signup_date_format_pass`
- `chinese_city_contains_pass`
- `chinese_notes_contains_pass`
- `vip_boolean_pass`
- `order_total_tolerance_pass`
- `order_created_date_format_pass`
- `first_item_nested_price_pass`
- `second_item_chinese_name_contains_pass`
- `loyalty_points_number_pass`
- `loyalty_expiry_date_format_pass`

## 11. Expected Failing Checks

These mappings are intentionally wrong:

- `order_total_wrong_amount_fail`: `199.991` compared with `250.00`
- `second_item_nested_price_fail`: `111.19` compared with `120.0`
- `loyalty_tier_chinese_exact_fail`: `金牌会员` compared with `银牌会员`
- `referral_code_regex_fail`: target referral code does not match the required pattern
- `missing_target_field_fail`: target path `profile.contact.phoneNumber` does not exist
- `missing_source_field_fail`: source path `data.tax_identifier` does not exist

## 12. Cross-Check Notes

This mapping is intentionally suspicious:

```yaml
- name: both_empty_suspicious_review_note
  source_api: old_customer_api
  source_field: data.empty_legacy_code
  target_api: new_profile_api
  target_field: profile.emptyNewCode
  compare_type: exact
  nullable: true
```

Both values are empty strings. The field comparison marks it as `PASS`, but the cross-check agent adds a note because both sides are empty.

If all fields passed except suspicious empty fields, the case could become `NEEDS_REVIEW`. In this sample, there are also real failures, so the case becomes `FAIL` / `CONFIRMED_FAIL` with notes.

## 13. Output Files

After a run, files are written under:

```text
./results/run_<run_id>/
```

Important files:

```text
full_result.json
mismatch_report.csv
summary.txt
```

SQLite is written to:

```text
./results/migration_check.db
```

## 14. Inspect Results In The Web UI

After running the sample:

1. Click `View Details` to see every field comparison.
2. Click `Mismatches` to show only failed/error mappings.
3. Use the `JSON`, `CSV`, and `TXT` buttons to download reports.

## 15. How To Fix The Intentional Problems

Fix wrong amount:

```yaml
wrongTotal: 199.99
```

Fix second item price:

```yaml
unitPrice: 111.19
```

Fix loyalty tier:

```yaml
tierName: "金牌会员"
```

Fix referral code:

```yaml
code: "CN-REF-2026-ZW"
```

Fix missing target phone field:

```yaml
phoneNumber: "+86-138-0000-0000"
```

Fix missing source tax field:

```yaml
tax_identifier: C1001
```

Or remove the mapping if the field is not part of the migration contract.

## 16. Create Your Own Config

Start by copying the sample:

```bash
cp examples/config.problematic-many-to-many.yaml examples/my-migration.yaml
```

Then change:

- `migration_name`
- `source_apis`
- `target_apis`
- `test_cases`
- `field_mapping`

For real APIs, remove `mock_response` and provide real `url`, `headers`, `query_params`, and `body` values.

Example real API shape:

```yaml
source_apis:
  - name: old_customer_api
    method: GET
    url: "https://old.example.com/customers/{customerId}"
    headers:
      Authorization: "Bearer ${OLD_API_TOKEN}"
    timeout_seconds: 10
    retry_count: 2
```

Variables like `${OLD_API_TOKEN}` can come from environment variables.

## 17. Current Limitations

- The web UI runs checks synchronously.
- Mock responses are static per API.
- Real HTTP execution requires `httpx`.
- Custom transforms are limited to a safe built-in registry.
- The current UI is intentionally simple and server-rendered with Flask/Jinja.

## 18. Recommended Next Improvements

- Add background jobs for long runs.
- Add progress polling or WebSocket updates.
- Add per-test-case mock responses.
- Add authentication for the web UI.
- Add richer filters for test case, API pair, status, and field name.
- Add side-by-side raw response viewers.
