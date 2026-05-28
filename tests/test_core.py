from api_migration_checker.app.compare import compare_values
from api_migration_checker.app.config import load_config, validate_config
from api_migration_checker.app.extract import extract_value
from api_migration_checker.app.runner import run_check


def test_sample_config_valid():
    report = validate_config(load_config("examples/config.sample.yaml"))
    assert report.valid, report.errors


def test_extract_nested_and_list():
    data = {"items": [{"price": 12.5}], "profile": {"address": {"city": "NYC"}}}
    assert extract_value(data, "items[0].price").value == 12.5
    assert extract_value(data, "profile.address.city").value == "NYC"
    assert not extract_value(data, "items[1].price").found


def test_comparison_rules():
    assert compare_values("Ada", "ada", {"compare_type": "ignore_case"})[0] == "PASS"
    assert compare_values("10.01", 10.0, {"compare_type": "number", "tolerance": 0.02})[0] == "PASS"
    assert compare_values("yes", True, {"compare_type": "boolean"})[0] == "PASS"
    assert compare_values("10/12/1815", "1815-12-10", {"compare_type": "date_format", "source_format": "dd/MM/yyyy", "target_format": "yyyy-MM-dd"})[0] == "PASS"


def test_run_check_sample(tmp_path):
    result = run_check("examples/config.sample.yaml", db_path=tmp_path / "migration_check.db")
    assert result["summary"]["passed_cases"] == 1
    assert result["summary"]["total_mismatched_fields"] == 0
