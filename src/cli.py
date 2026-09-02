"""trustee-fds CLI — 리포트 출력 및 기대값 대조."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from engine import Dataset, Result, STAGE_ORDER, load_rules, partition, run

ROOT = Path(__file__).resolve().parent.parent


def won(v):
    return "산출 불가" if v is None else f"{v:,.0f}원"


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
    print("  금액으로 줄을 세울 수 없을 뿐, 회수 기대액이 0인 것이 아니다.\n")
    for r in unvalued:
        tx = next(t for t in ds.transactions if t["transaction_id"] == r.tx_id)
        print(f"  {r.tx_id}  [{r.priority}]  {tx['transaction_date']}  {tx['action_type']}")
        print(f"        {tx['note'][:56]}")
        print(f"        신호: {', '.join(r.signals) or '-'}")

    print(f"\n【{labels['valued']}】  {len(valued)}건")
    print(f"\n  {'거래':7}{'우선':11}{'회수 기대액':>18}{'대가비율':>10}  소급  신호")
    print("  " + "-" * 70)
    for r in valued:
        ratio = "     -" if r.ratio is None else f"{r.ratio:6.4f}"
        print(f"  {r.tx_id:7}{r.priority:11}{r.value_out - r.value_in:>18,.0f}"
              f"{ratio:>10}  {r.lookback_months:>2}월  {len(r.signals)}")

    total = sum(r.value_out - r.value_in for r in valued)
    print(f"\n  회수 기대액 합계(평가 완료분만): {total:,.0f}원")
    print(f"  ※ 평가 필요 {len(unvalued)}건은 합계에 포함되지 않음\n")


# ------------------------------------------------------------------ 대조

def validate(results):
    exp = {r["transaction_id"]: r for r in
           csv.DictReader(open(ROOT / "data/sample/expected_results.csv", encoding="utf-8"))}
    got = {r.tx_id: r for r in results}

    mismatches, unreachable = [], []
    for tid, e in exp.items():
        r: Result | None = got.get(tid)
        if r is None:
            mismatches.append((tid, "결과 없음", "-", "-"))
            continue

        want = e["expected_candidate"] == "Y"
        if want != r.candidate:
            mismatches.append((tid, "후보 여부", "Y" if want else "N",
                               "Y" if r.candidate else f"N ({r.drop_reason})"))

        wm = e["expected_lookback_months"].strip()
        if wm and r.lookback_months and int(wm) != r.lookback_months:
            mismatches.append((tid, "소급기간", f"{wm}월", f"{r.lookback_months}월"))

        wp = e["expected_priority"].strip()
        if wp and r.priority and wp != r.priority:
            mismatches.append((tid, "우선순위", wp, r.priority))

        # 이 케이스가 검증하려던 단계에 실제로 도달했는가
        ts = e.get("target_stage", "").strip()
        if ts and STAGE_ORDER.get(ts, 0) > STAGE_ORDER.get(r.reached, 0):
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
    ds = Dataset.load(ROOT)
    rules = load_rules(ROOT)
    results = run(ds, rules)
    report(ds, rules, results)
    m, u = validate(results)
    return 1 if m else 0


if __name__ == "__main__":
    sys.exit(main())
