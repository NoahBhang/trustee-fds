"""trustee-fds CLI — 리포트 출력 및 기대값 대조."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

if __package__:
    from .engine import Dataset, Result, STAGE_ORDER, load_rules, partition, run, IssueCategory, IssueSeverity
else:  # 직접 실행(`python src/cli.py`) 호환
    from engine import Dataset, Result, STAGE_ORDER, load_rules, partition, run, IssueCategory, IssueSeverity

ROOT = Path(__file__).resolve().parent.parent

# 윈도우 한국어 콘솔(cp949)에는 이 리포트가 쓰는 '—'(U+2014) 와 '⚠'(U+26A0) 이
# 없어, 그대로 두면 리포트 첫 줄에서 UnicodeEncodeError 로 죽는다.
# 파일 입출력은 각 open() 에서 이미 encoding 을 명시하므로 출력만 맞추면 된다.
if hasattr(sys.stdout, "reconfigure"):        # Python 3.7+
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def won(v):
    return "산출 불가" if v is None else f"{v:,.0f}원"


def report_rule_status(rules):
    """법률 검토·운영 승인 전 룰이 조용히 실사건용처럼 보이지 않게 한다."""
    pending = [r for r in rules.values() if r.get("status") != "production"]
    if not pending:
        return
    print("=" * 74)
    print("⚠ 룰 승인 상태 — 연구·검증용 실행")
    print("=" * 74)
    for rule in sorted(pending, key=lambda r: r["rule_id"]):
        print(f"  • {rule['rule_id']}: {rule['status']}")
    print("  production 승인 전에는 실제 사건의 법률판단에 사용하지 말 것.\n")


def report_provisional_anchors(ds):
    """미확정 지급정지일을 잠정 기준점으로 쓰는 사실을 결과보다 먼저 알린다."""
    provisional = [
        case for case in ds.cases.values()
        if (case.get("suspension_of_payment_date") or "").strip()
        and (case.get("suspension_date_confirmed") or "").strip() != "Y"
    ]
    if not provisional:
        return
    print("=" * 74)
    print("⚠ 미확정 지급정지일을 잠정 기준점으로 사용")
    print("=" * 74)
    for case in sorted(provisional, key=lambda row: row["case_id"]):
        print(f"  • {case['case_id']}: {case['suspension_of_payment_date']} "
              f"(confirmed={case.get('suspension_date_confirmed') or 'blank'})")
    print("  후보 누락을 줄이기 위해 시간 창과 post_anchor 신호에 포함한 잠정값이다.")
    print("  지급정지일을 제외·변경한 민감도 시나리오를 함께 확인할 것.\n")


# ------------------------------------------------------------------ 무결성 검사 리포트

def report_integrity(ds):
    """데이터 무결성 검사 결과를 출력한다."""
    if not ds.integrity_issues:
        return
    
    print("=" * 74)
    print("⚠ 데이터 무결성 검사")
    print("=" * 74)
    
    # 심각도별로 분류
    by_severity = {}
    for issue in ds.integrity_issues:
        if issue.severity not in by_severity:
            by_severity[issue.severity] = []
        by_severity[issue.severity].append(issue)
    
    # CRITICAL부터 출력
    severity_order = [IssueSeverity.CRITICAL, IssueSeverity.WARNING, IssueSeverity.INFO]
    for severity in severity_order:
        if severity not in by_severity:
            continue
        
        issues = by_severity[severity]
        if severity == IssueSeverity.CRITICAL:
            print(f"\n【심각 — 조용히 판정 오류】  {len(issues)}건")
        elif severity == IssueSeverity.WARNING:
            print(f"\n【경고 — 데이터 소실】  {len(issues)}건")
        else:
            print(f"\n【정보 — 진단 개선】  {len(issues)}건")
        
        # 카테고리별로 다시 분류
        by_category = {}
        for issue in issues:
            if issue.category not in by_category:
                by_category[issue.category] = []
            by_category[issue.category].append(issue)
        
        for category in [IssueCategory.REFERENTIAL, IssueCategory.VALUE_CONSTRAINT]:
            if category not in by_category:
                continue
            
            print(f"  [{category.value}]")
            for issue in by_category[category]:
                print(f"    • {issue.message}")
                for detail in issue.details[:3]:  # 처음 3개만 표시
                    if isinstance(detail, dict):
                        detail_str = ", ".join(f"{k}={v}" for k, v in detail.items())
                        print(f"      - {detail_str}")
                    else:
                        print(f"      - {detail}")
                if len(issue.details) > 3:
                    print(f"      ... 외 {len(issue.details) - 3}건")
        
        print()


# ------------------------------------------------------------------ 리포트

def report(ds, rules, results):
    rule = rules["art391-4-gratuitous"]
    unvalued, valued = partition(results, rule)
    labels = {p["id"]: p["label"] for p in rule["ranking"]["partitions"]}

    print("=" * 74)
    print("무상부인 검토 리포트 — 채무자회생법 제391조 제4호")
    print(f"검토 거래 {len(results)}건 → 후보 {len(unvalued) + len(valued)}건")
    print("=" * 74)

    print(f"\n【{labels['unvalued']}】  {len(unvalued)}건")
    print("  금액으로 줄을 세울 수 없을 뿐, 순출연 추정액이 0인 것이 아니다.\n")
    for r in unvalued:
        tx = next(t for t in ds.transactions if t["transaction_id"] == r.tx_id)
        print(f"  {r.tx_id}  [{r.priority}]  {tx['transaction_date']}  {tx['action_type']}")
        print(f"        {tx['note'][:56]}")
        print(f"        신호: {', '.join(r.signals) or '-'}")

    print(f"\n【{labels['valued']}】  {len(valued)}건")
    print(f"\n  {'거래':7}{'우선':11}{'순출연 추정액':>18}{'대가비율':>10}  소급  신호")
    print("  " + "-" * 70)
    for r in valued:
        ratio = "     -" if r.ratio is None else f"{r.ratio:6.4f}"
        print(f"  {r.tx_id:7}{r.priority:11}{r.value_out - r.value_in:>18,.0f}"
              f"{ratio:>10}  {r.lookback_months:>2}월  {len(r.signals)}")

    repayment_results = [
        (r, next(t for t in ds.transactions if t["transaction_id"] == r.tx_id))
        for r in valued if "debt_repayment_excess_isolated" in r.flags
    ]
    if repayment_results:
        disclosure = rule["output"]["debt_repayment_excess_disclosure"]
        print(f"\n【{disclosure['label']}】")
        for r, tx in repayment_results:
            paid = float(tx["consideration_paid"])
            reduced = float(tx["liability_reduction"])
            print("  " + disclosure["template"].format(
                tx_id=r.tx_id, paid=paid, reduced=reduced,
                excess=r.value_out - r.value_in,
            ))

    total = sum(r.value_out - r.value_in for r in valued)
    print(f"\n  순출연 추정액 합계(평가 완료분만): {total:,.0f}원")
    print(f"  ※ 평가 필요 {len(unvalued)}건은 합계에 포함되지 않음\n")


# ------------------------------------------------------------------ 대조

def _duplicates(values):
    seen, duplicates = set(), []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _optional(raw, cast=str):
    raw = (raw or "").strip()
    return None if raw == "" else cast(raw)


def _list_field(raw):
    return [item.strip() for item in (raw or "").split(";") if item.strip()]


def validate(results, expected_path=None):
    expected_path = expected_path or ROOT / "data/sample/expected_results.csv"
    with open(expected_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    mismatches, unreachable = [], []
    for tid in _duplicates(r["transaction_id"] for r in rows):
        mismatches.append((tid, "기대값 ID 중복", "유일", "중복"))
    for tid in _duplicates(r.tx_id for r in results):
        mismatches.append((tid, "실제 결과 ID 중복", "유일", "중복"))

    exp = {r["transaction_id"]: r for r in rows}
    got = {r.tx_id: r for r in results}
    for tid in sorted(set(got) - set(exp)):
        mismatches.append((tid, "예상 밖 결과", "없음", "결과 생성"))
    for tid in sorted(set(exp) - set(got)):
        mismatches.append((tid, "결과 없음", "결과 생성", "없음"))

    for tid, e in exp.items():
        r: Result | None = got.get(tid)
        if r is None:
            continue

        raw_candidate = e["expected_candidate"].strip()
        if raw_candidate not in {"Y", "N"}:
            mismatches.append((tid, "기대 후보값 형식", "Y/N", raw_candidate or "빈 값"))
            continue
        want = raw_candidate == "Y"
        if want != r.candidate:
            mismatches.append((tid, "후보 여부", "Y" if want else "N",
                               "Y" if r.candidate else f"N ({r.drop_reason})"))

        comparisons = [
            ("도달 단계", _optional(e.get("expected_reached")), r.reached),
            ("소급기간", _optional(e.get("expected_lookback_months"), int), r.lookback_months),
            ("우선순위", _optional(e.get("expected_priority")), r.priority),
            ("탈락 사유", _optional(e.get("expected_drop_reason")), r.drop_reason),
        ]
        for what, want_value, got_value in comparisons:
            if want_value != got_value:
                mismatches.append((tid, what, str(want_value), str(got_value)))

        for column, what, got_value in (
            ("expected_value_out", "Value Out", r.value_out),
            ("expected_value_in", "Value In", r.value_in),
            ("expected_ratio", "대가비율", r.ratio),
        ):
            want_value = _optional(e.get(column), float)
            equal = (want_value is None and got_value is None) or (
                want_value is not None and got_value is not None
                and math.isclose(want_value, got_value, rel_tol=1e-9, abs_tol=1e-9)
            )
            if not equal:
                mismatches.append((tid, what, str(want_value), str(got_value)))

        want_signals = _list_field(e.get("expected_signals"))
        if want_signals != r.signals:
            mismatches.append((tid, "신호", ";".join(want_signals) or "-",
                               ";".join(r.signals) or "-"))
        want_flags = _list_field(e.get("expected_flags"))
        if want_flags != r.flags:
            mismatches.append((tid, "플래그", ";".join(want_flags) or "-",
                               ";".join(r.flags) or "-"))

        # 이 케이스가 검증하려던 단계에 실제로 도달했는가
        ts = e.get("target_stage", "").strip()
        if ts not in STAGE_ORDER:
            mismatches.append((tid, "target_stage", "알려진 단계", ts or "빈 값"))
        elif STAGE_ORDER[ts] > STAGE_ORDER.get(r.reached, 0):
            unreachable.append((tid, ts, r.reached, r.drop_reason or "-"))

    print("=" * 74)
    print("기대값 대조")
    print("=" * 74)
    print(f"\n케이스 {len(exp)}건 중 불일치 {len(mismatches)}건\n")
    if mismatches:
        print(f"  {'거래':7}{'항목':12}{'기대':22}실제")
        print("  " + "-" * 68)
        for tid, what, want, gotv in mismatches:
            print(f"  {tid:7}{what:12}{want:22}{gotv}")

    print(f"\n\n무력한 테스트 (target_stage 미도달) {len(unreachable)}건")
    print("  앞 단계에서 걸러진 케이스는 뒷 단계를 검증하지 못한다.\n")
    if unreachable:
        print(f"  {'거래':7}{'검증 대상':12}{'실제 도달':12}탈락 사유")
        print("  " + "-" * 68)
        for tid, ts, reached, why in unreachable:
            print(f"  {tid:7}{ts:12}{reached:12}{why}")
    else:
        print("  없음 — 모든 케이스가 자기 검증 단계에 도달했다.\n")

    return len(mismatches), len(unreachable)


def main():
    # 룰을 먼저 로드한다 — 무결성 검사가 unilateral_acts(상대방 없는 단독행위)를
    # 알아야 legal_counterparty 커버리지 검사에서 그것들을 제외할 수 있다.
    rules = load_rules(ROOT)
    report_rule_status(rules)
    unilateral = rules["art391-4-gratuitous"]["action_filter"].get("unilateral_acts", [])
    ds = Dataset.load(ROOT, unilateral_acts=unilateral)
    report_provisional_anchors(ds)

    # 무결성 검사 결과 출력 (리포트 앞에)
    report_integrity(ds)
    critical = [i for i in ds.integrity_issues if i.severity == IssueSeverity.CRITICAL]

    if critical:
        print("=" * 74)
        print(f"⚠ CRITICAL 무결성 문제 {len(critical)}건 — 판정 실행을 중단한다.")
        print("  입력을 수정한 뒤 다시 실행할 것.")
        print("=" * 74)
        return 2

    # 본 리포트
    results = run(ds, rules)
    report(ds, rules, results)

    # 기대값 대조
    m, u = validate(results)

    # 종료 코드는 상호 배타적이다. CRITICAL 입력은 위에서 validate() 전에 2로
    # 끝나며, 여기까지 온 실행에서 골든 불일치·target_stage 미도달은 1이다.
    # 0=정상, 1=회귀 대조 실패, 2=입력 무결성 실패.
    return 1 if (m or u) else 0


if __name__ == "__main__":
    sys.exit(main())
