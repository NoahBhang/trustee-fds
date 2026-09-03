"""engine.py 단위 테스트.

골든파일(expected_results.csv) 대조는 통합 테스트라, 함수 단위 로직이 깨져도
다른 단계에서 상쇄되면 안 잡힌다. 여기서는 실제 CSV 를 읽지 않고 최소 딕셔너리
픽스처로 각 함수를 직접 검증한다 — 샘플 데이터가 바뀌어도 이 테스트는 안 깨진다.

실행: pip install -r requirements-dev.txt && pytest
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from engine import (                                        # noqa: E402
    Dataset,
    _trace_inbound_chains,
    _classify_affiliate,
    _classify_officer,
    _classify_former_spouse,
    _debt_repayment_within_tolerance,
    triage_priority,
)


# --------------------------------------------------------------------------- 픽스처

FFA = {
    "min_confidence": "probable",
    "proximity_window_days": 90,
    "max_hops": 3,
    "eligible_link_types": ["funds_flow"],
    "outside_window_flag": "funds_flow_delayed",
    "multi_hop_flag": "funds_circled_back_multihop",
    "confidence_order": ["alleged", "probable", "verified"],
}

RP = {
    "ownership_test": {"threshold_percent": 30},
    "verify_behavior": {"flag": "relation_unverified"},
    "temporal_scope": {"boundary_window_days": 30},
}

RULE = {"economic_substance": {"ratio": {
    "applicable_action_types": ["gift", "sale"],
    "triage_bands": [
        {"when": "ratio is undefined", "priority": "unresolved"},
        {"max": 0.10, "priority": "high"},
        {"max": 0.50, "priority": "medium"},
        {"max": None, "priority": "low"},
    ],
    "signal_count_bands": [
        {"max": 0, "priority": "low"},
        {"max": 2, "priority": "medium"},
        {"max": None, "priority": "high"},
    ],
}}}


def _rank(ffa):
    return {c: i for i, c in enumerate(ffa["confidence_order"])}


def _linear_chain(*id_dates, confidence="probable"):
    """id_dates: ('A','2025-01-01') ... 오래된 것부터. 각 다리를 다음 거래로 funds_flow 링크."""
    txs = [{"transaction_id": i, "transaction_date": d} for i, d in id_dates]
    links = []
    for (fid, _), (tid, _) in zip(id_dates, id_dates[1:]):
        links.append({
            "link_id": f"L-{fid}{tid}", "case_id": "C1",
            "from_transaction_id": fid, "to_transaction_id": tid,
            "link_type": "funds_flow", "confidence": confidence,
            "evidence": "x", "note": "",
        })
    return txs, links


def _trace(txs, links, start_id, start_date, ffa=FFA):
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    return _trace_inbound_chains(
        ds, "C1", start_id, date.fromisoformat(start_date),
        ffa, _rank(ffa), depth=1, visited={start_id},
    )


# ----------------------------------------------------- _trace_inbound_chains (재귀)

def test_trace_single_hop_in_window():
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-10"))
    in_w, delayed = _trace(txs, links, "B", "2025-01-10")
    assert in_w == [1]
    assert delayed == []


def test_trace_two_hops_counts_depth():
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-05"), ("C", "2025-01-10"))
    in_w, delayed = _trace(txs, links, "C", "2025-01-10")
    assert sorted(in_w) == [1, 2]
    assert delayed == []


def test_trace_three_hops_within_max():
    txs, links = _linear_chain(
        ("A", "2025-01-01"), ("B", "2025-01-03"), ("C", "2025-01-06"), ("D", "2025-01-10"))
    in_w, _ = _trace(txs, links, "D", "2025-01-10")
    assert sorted(in_w) == [1, 2, 3]


def test_trace_max_hops_caps_depth():
    txs, links = _linear_chain(
        ("A", "2025-01-01"), ("B", "2025-01-03"), ("C", "2025-01-06"), ("D", "2025-01-10"))
    in_w, _ = _trace(txs, links, "D", "2025-01-10", {**FFA, "max_hops": 2})
    assert sorted(in_w) == [1, 2]        # 3번째 홉(A)은 추적 안 됨


def test_trace_cycle_terminates_without_infinite_loop():
    txs = [{"transaction_id": "A", "transaction_date": "2025-01-01"},
           {"transaction_id": "B", "transaction_date": "2025-01-05"}]
    links = [
        {"link_id": "L1", "case_id": "C1", "from_transaction_id": "A",
         "to_transaction_id": "B", "link_type": "funds_flow", "confidence": "probable"},
        {"link_id": "L2", "case_id": "C1", "from_transaction_id": "B",
         "to_transaction_id": "A", "link_type": "funds_flow", "confidence": "probable"},
    ]
    in_w, delayed = _trace(txs, links, "B", "2025-01-05")
    assert in_w == [1]                   # B<-A 만. A<-B 는 visited 로 차단


def test_trace_weak_link_stops_that_path():
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-05"), ("C", "2025-01-10"))
    links[0]["confidence"] = "alleged"   # A->B 는 진술뿐
    in_w, delayed = _trace(txs, links, "C", "2025-01-10")
    assert in_w == [1]                   # C<-B 만, B<-A 는 confidence 미달로 끊김
    assert delayed == []


def test_trace_delayed_hop_propagates_upstream():
    # A->B 는 5개월(창 밖), B->C 는 4일(창 안)
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-06-01"), ("C", "2025-06-05"))
    in_w, delayed = _trace(txs, links, "C", "2025-06-05")
    assert 1 in in_w                     # C<-B 는 창 안
    assert 2 in delayed                  # B<-A 는 창 밖 → 지연
    assert 2 not in in_w


def test_trace_link_type_filter():
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-10"))
    links[0]["link_type"] = "same_asset"          # funds_flow 아님
    in_w, delayed = _trace(txs, links, "B", "2025-01-10")
    assert in_w == [] and delayed == []


# --------------------------------------------------------------- _classify_affiliate

def test_affiliate_at_threshold_exactly_is_related():
    assert _classify_affiliate({"ownership_percentage": "30"}, RP)[0] == "related"


def test_affiliate_below_threshold_is_verify_not_not_related():
    v, flags = _classify_affiliate({"ownership_percentage": "29.99"}, RP)
    assert v == "verify"
    assert "ownership_below_threshold_de_facto_control_unassessed" in flags


def test_affiliate_unknown_ownership_is_verify():
    v, flags = _classify_affiliate({"ownership_percentage": ""}, RP)
    assert v == "verify"
    assert "ownership_percentage_unknown" in flags


def test_affiliate_default_threshold_when_rule_missing():
    assert _classify_affiliate({"ownership_percentage": "30"}, {})[0] == "related"
    assert _classify_affiliate({"ownership_percentage": "29"}, {})[0] == "verify"


# ---------------------------------------------------------------- _classify_officer

def test_officer_of_debtor_corporation_is_related():
    parties = {("C1", "D"): {"relation_type": "self"}}
    assert _classify_officer({"officer_of": "D"}, RP, "C1", parties, "corporation")[0] == "related"


def test_officer_of_debtor_individual_flags_data_inconsistency():
    parties = {("C1", "D"): {"relation_type": "self"}}
    v, flags = _classify_officer({"officer_of": "D"}, RP, "C1", parties, "individual")
    assert v == "verify"
    assert "officer_of_individual_debtor_data_inconsistency" in flags


def test_officer_of_affiliate_inherits_ownership_verdict():
    parties = {("C1", "AFF"): {"relation_type": "affiliate", "ownership_percentage": "62"}}
    assert _classify_officer({"officer_of": "AFF"}, RP, "C1", parties, "corporation")[0] == "related"
    parties["C1", "AFF"]["ownership_percentage"] = "10"
    assert _classify_officer({"officer_of": "AFF"}, RP, "C1", parties, "corporation")[0] == "verify"


def test_officer_of_unrelated_company_is_not_related():
    parties = {("C1", "X"): {"relation_type": "none"}}
    assert _classify_officer({"officer_of": "X"}, RP, "C1", parties, "corporation")[0] == "not_related"


def test_officer_of_empty_or_unresolved_is_verify():
    assert _classify_officer({"officer_of": ""}, RP, "C1", {}, "corporation")[0] == "verify"
    assert _classify_officer({"officer_of": "GHOST"}, RP, "C1", {}, "corporation")[0] == "verify"


# ---------------------------------------------------------- _classify_former_spouse

FS = {"verify_behavior": {"flag": "relation_unverified"},
      "temporal_scope": {"boundary_window_days": 30}}
_MARRIAGE = {"relation_valid_from": "2010-01-01", "relation_valid_to": "2025-03-01"}


def test_former_spouse_during_marriage_is_related():
    assert _classify_former_spouse(_MARRIAGE, date(2024, 1, 1), FS)[0] == "related"


def test_former_spouse_exactly_on_end_date_is_related():
    p = {"relation_valid_from": "2010-01-01", "relation_valid_to": "2025-03-08"}
    assert _classify_former_spouse(p, date(2025, 3, 8), FS)[0] == "related"


def test_former_spouse_before_marriage_is_not_related():
    assert _classify_former_spouse(_MARRIAGE, date(2009, 1, 1), FS)[0] == "not_related"


def test_former_spouse_after_divorce_dependency_unknown_is_verify():
    p = {**_MARRIAGE, "post_divorce_dependency": ""}
    v, flags = _classify_former_spouse(p, date(2025, 6, 1), FS)
    assert v == "verify"
    assert "post_divorce_dependency_unknown" in flags


def test_former_spouse_after_divorce_dependency_yes_is_related():
    p = {**_MARRIAGE, "post_divorce_dependency": "Y"}
    assert _classify_former_spouse(p, date(2025, 6, 1), FS)[0] == "related"


def test_former_spouse_after_divorce_dependency_no_is_not_related():
    p = {**_MARRIAGE, "post_divorce_dependency": "N"}
    assert _classify_former_spouse(p, date(2025, 6, 1), FS)[0] == "not_related"


# ------------------------------------------------ _debt_repayment_within_tolerance

def test_debt_repayment_exactly_matching_is_within():
    tx = {"consideration_paid": "100000000", "liability_reduction": "100000000"}
    assert _debt_repayment_within_tolerance(tx, {"tolerance": 0.02}) is True


def test_debt_repayment_over_tolerance_is_false():
    tx = {"consideration_paid": "130000000", "liability_reduction": "100000000"}
    assert _debt_repayment_within_tolerance(tx, {"tolerance": 0.02}) is False


def test_debt_repayment_just_inside_tolerance():
    tx = {"consideration_paid": "100", "liability_reduction": "101"}   # 1% 차이
    assert _debt_repayment_within_tolerance(tx, {"tolerance": 0.02}) is True


def test_debt_repayment_missing_data_does_not_route():
    assert _debt_repayment_within_tolerance(
        {"consideration_paid": "", "liability_reduction": "100"}, {}) is False
    assert _debt_repayment_within_tolerance(
        {"consideration_paid": "0", "liability_reduction": "0"}, {}) is False


def test_debt_repayment_default_tolerance():
    tx = {"consideration_paid": "100", "liability_reduction": "100"}
    assert _debt_repayment_within_tolerance(tx, {}) is True          # 기본 0.02


# ------------------------------------------------------------------ triage_priority

def test_triage_priority_ratio_none_always_unresolved():
    assert triage_priority("gift", None, 5, RULE) == "unresolved"
    assert triage_priority("third_party_guarantee", None, 5, RULE) == "unresolved"


def test_triage_priority_in_scope_uses_ratio_bands():
    assert triage_priority("sale", 0.05, 0, RULE) == "high"
    assert triage_priority("sale", 0.30, 0, RULE) == "medium"
    assert triage_priority("sale", 0.90, 9, RULE) == "low"          # 신호 개수 무시


def test_triage_priority_out_of_scope_uses_signal_count():
    assert triage_priority("third_party_guarantee", 0.04, 0, RULE) == "low"
    assert triage_priority("third_party_guarantee", 0.04, 1, RULE) == "medium"
    assert triage_priority("third_party_guarantee", 0.001, 4, RULE) == "high"


def test_triage_priority_boundary_at_band_edge():
    assert triage_priority("sale", 0.10, 0, RULE) == "high"         # max: 0.10 포함
    assert triage_priority("sale", 0.50, 0, RULE) == "medium"       # max: 0.50 포함
