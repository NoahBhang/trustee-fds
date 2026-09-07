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
    ds = SimpleNamespace(cases={}, integrity_issues=[issue])
    monkeypatch.setattr(cli, "load_rules", lambda root: rules)
    monkeypatch.setattr(cli.Dataset, "load", lambda root, unilateral_acts: ds)

    def should_not_run(*args):
        raise AssertionError("CRITICAL 입력에서는 run()을 호출하면 안 된다")

    monkeypatch.setattr(cli, "run", should_not_run)
    assert cli.main() == 2
    assert "판정 실행을 중단" in capsys.readouterr().out


def test_report_warns_when_using_unconfirmed_suspension_anchor(capsys):
    ds = SimpleNamespace(cases={"C1": {
        "case_id": "C1",
        "suspension_of_payment_date": "2025-08-20",
        "suspension_date_confirmed": "N",
    }})
    cli.report_provisional_anchors(ds)
    output = capsys.readouterr().out
    assert "미확정 지급정지일을 잠정 기준점으로 사용" in output
    assert "C1: 2025-08-20" in output
    assert "post_anchor" in output


def test_report_discloses_debt_repayment_normal_and_excess_parts(capsys):
    ds = SimpleNamespace(transactions=[{
        "transaction_id": "T1", "transaction_date": "2025-09-05",
        "action_type": "debt_repayment", "consideration_paid": "130",
        "liability_reduction": "100", "note": "",
    }])
    rules = {"art391-4-gratuitous": {
        "ranking": {"partitions": [
            {"id": "unvalued", "label": "평가 필요"},
            {"id": "valued", "label": "순출연 추정액 순"},
        ]},
        "output": {"debt_repayment_excess_disclosure": {
            "label": "채무변제 초과분 산식",
            "template": (
                "{tx_id}: 지급 {paid:,.0f}, 채무 감소 {reduced:,.0f}, "
                "초과 {excess:,.0f}; Value In=0은 정상 상환분이 없다는 뜻이 아닙니다."
            ),
        }},
    }}
    result = Result(
        tx_id="T1", reached="ranking", candidate=True, lookback_months=6,
        priority="high", value_out=30.0, value_in=0.0, ratio=0.0,
        flags=["debt_repayment_excess_isolated"],
    )
    cli.report(ds, rules, [result])
    output = capsys.readouterr().out
    assert "채무변제 초과분 산식" in output
    assert "지급 130, 채무 감소 100, 초과 30" in output
    assert "Value In=0은 정상 상환분이 없다는 뜻이 아닙니다" in output


def test_report_skips_excess_disclosure_when_no_positive_excess(capsys):
    """과소변제(excess == 0)는 '초과 0원' 공시로 혼란을 주지 않는다."""
    ds = SimpleNamespace(transactions=[{
        "transaction_id": "T1", "transaction_date": "2025-09-05",
        "action_type": "debt_repayment", "consideration_paid": "80",
        "liability_reduction": "100", "note": "",
    }])
    rules = {"art391-4-gratuitous": {
        "ranking": {"partitions": [
            {"id": "unvalued", "label": "평가 필요"},
            {"id": "valued", "label": "순출연 추정액 순"},
        ]},
        "output": {"debt_repayment_excess_disclosure": {
            "label": "채무변제 초과분 산식",
            "template": "{tx_id}: 초과 {excess:,.0f}",
        }},
    }}
    result = Result(
        tx_id="T1", reached="ranking", candidate=True, lookback_months=6,
        priority="unresolved", value_out=0.0, value_in=0.0, ratio=None,
        flags=["debt_repayment_excess_isolated"],
    )
    cli.report(ds, rules, [result])
    assert "채무변제 초과분 산식" not in capsys.readouterr().out
