"""골든파일 검증기 자체의 회귀 테스트."""
import csv
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cli  # noqa: E402
from cli import validate  # noqa: E402
from engine import IntegrityIssue, IssueCategory, IssueSeverity, Result  # noqa: E402


FIELDS = [
    "transaction_id", "target_stage", "expected_reached", "expected_candidate",
    "expected_lookback_months", "expected_priority", "expected_value_out",
    "expected_value_in", "expected_ratio", "expected_signals", "expected_flags",
    "expected_drop_reason", "rationale",
]


def _write_expected(path, **overrides):
    row = {
        "transaction_id": "T1", "target_stage": "ranking",
        "expected_reached": "ranking", "expected_candidate": "Y",
        "expected_lookback_months": "12", "expected_priority": "high",
        "expected_value_out": "100", "expected_value_in": "0",
        "expected_ratio": "0", "expected_signals": "s1;s2",
        "expected_flags": "f1", "expected_drop_reason": "", "rationale": "test",
    }
    row.update(overrides)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(row)


def _matching_result():
    return Result(
        tx_id="T1", reached="ranking", candidate=True, lookback_months=12,
        priority="high", value_out=100.0, value_in=0.0, ratio=0.0,
        signals=["s1", "s2"], flags=["f1"],
    )


def test_validate_accepts_exact_match(tmp_path, capsys):
    expected = tmp_path / "expected.csv"
    _write_expected(expected)
    assert validate([_matching_result()], expected) == (0, 0)
    capsys.readouterr()


def test_validate_rejects_missing_expected_scalar_values(tmp_path, capsys):
    expected = tmp_path / "expected.csv"
    _write_expected(expected)
    result = _matching_result()
    result.lookback_months = None
    result.priority = None
    mismatches, _ = validate([result], expected)
    assert mismatches == 2
    capsys.readouterr()


def test_validate_rejects_unexpected_result_and_duplicate_id(tmp_path, capsys):
    expected = tmp_path / "expected.csv"
    _write_expected(expected)
    extra = Result(tx_id="EXTRA")
    mismatches, _ = validate([_matching_result(), extra, extra], expected)
    assert mismatches == 2
    capsys.readouterr()


def test_validate_compares_signal_and_flag_lists_exactly(tmp_path, capsys):
    expected = tmp_path / "expected.csv"
    _write_expected(expected)
    result = _matching_result()
    result.signals.append("unexpected")
    result.flags = []
    mismatches, _ = validate([result], expected)
    assert mismatches == 2
    capsys.readouterr()


def test_validate_reports_target_stage_not_reached(tmp_path, capsys):
    expected = tmp_path / "expected.csv"
    _write_expected(expected, target_stage="signal", expected_reached="temporal")
    result = _matching_result()
    result.reached = "temporal"
    mismatches, unreachable = validate([result], expected)
    assert mismatches == 0
    assert unreachable == 1
    capsys.readouterr()


def test_cli_stops_before_run_on_critical_integrity_issue(monkeypatch, capsys):
    rules = {"art391-4-gratuitous": {
        "rule_id": "art391-4-gratuitous", "status": "draft",
        "action_filter": {"unilateral_acts": []},
    }}
    issue = IntegrityIssue(
        IssueCategory.VALUE_CONSTRAINT, IssueSeverity.CRITICAL, "invalid input")
    ds = SimpleNamespace(integrity_issues=[issue])
    monkeypatch.setattr(cli, "load_rules", lambda root: rules)
    monkeypatch.setattr(cli.Dataset, "load", lambda root, unilateral_acts: ds)

    def should_not_run(*args):
        raise AssertionError("CRITICAL 입력에서는 run()을 호출하면 안 된다")

    monkeypatch.setattr(cli, "run", should_not_run)
    assert cli.main() == 2
    assert "판정 실행을 중단" in capsys.readouterr().out
