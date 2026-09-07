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
    IntegrityIssue,
    IssueCategory,
    IssueSeverity,
    _trace_inbound_chains,
    evaluate_related_party_fund_flow,
    _classify_affiliate,
    _classify_kinship,
    _classify_employee,
    _classify_officer,
    _classify_former_spouse,
    _debt_repayment_within_tolerance,
    classify_relation,
    economics,
    PREDICATES,
    run,
    triage_priority,
)


# --------------------------------------------------------------------------- 픽스처

FFA = {
    "min_confidence": "probable",
    "proximity_window_days": 90,
    "max_hops": 3,
    "eligible_link_types": ["funds_flow"],
    "outside_window_flag": "funds_flow_delayed",
    "multi_hop_flag": "related_party_fund_flow_multihop",
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


# -------------------------------------------------- evaluate_related_party_fund_flow
# 자금흐름 연결 신호는 상대방이 실제 특수관계인일 때만 성립해야 한다.
# funds_flow 링크 하나만으로 무관한 제3자 거래에 관계인 신호를 붙이면 안 된다.

def test_related_party_fund_flow_true_when_counterparty_related():
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-10"))
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    rule = {"fund_flow_analysis": FFA}
    circled, flags = evaluate_related_party_fund_flow(
        ds, "C1", txs[1], rule, related_counterparty=True)
    assert circled is True
    assert flags == []


def test_related_party_fund_flow_false_when_counterparty_unrelated():
    # A -> B 로 funds_flow 링크가 있어도, B 의 legal_counterparty 가 무관한
    # 제3자면 특수관계인 상대 자금흐름 신호를 세우지 않는다.
    txs, links = _linear_chain(("A", "2025-01-01"), ("B", "2025-01-10"))
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    rule = {"fund_flow_analysis": FFA}
    circled, flags = evaluate_related_party_fund_flow(
        ds, "C1", txs[1], rule, related_counterparty=False)
    assert circled is False
    assert flags == []


def test_related_party_fund_flow_multihop_flag_still_gated_by_relatedness():
    txs, links = _linear_chain(
        ("A", "2025-01-01"), ("B", "2025-01-05"), ("C", "2025-01-10"))
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    rule = {"fund_flow_analysis": FFA}
    circled, flags = evaluate_related_party_fund_flow(
        ds, "C1", txs[2], rule, related_counterparty=True)
    assert circled is True
    assert "related_party_fund_flow_multihop" in flags

    circled, flags = evaluate_related_party_fund_flow(
        ds, "C1", txs[2], rule, related_counterparty=False)
    assert circled is False
    assert flags == []


# ----------------------------------------------------------- _check_duplicate_keys
# transactions.csv 의 transaction_id 중복은 조회부에 따라 다른 행이 조용히
# 선택된다(딕셔너리 컴프리헨션은 마지막 행, next()는 첫 행) — cases/parties 와
# 같은 이유로 CRITICAL 이어야 한다 (코덱스 리뷰에서 지적된 무결성 공백).

def test_duplicate_transaction_id_is_critical():
    txs = [
        {"transaction_id": "T1", "case_id": "C1", "transaction_date": "2025-01-01"},
        {"transaction_id": "T1", "case_id": "C1", "transaction_date": "2025-02-01"},
    ]
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[])
    issues = ds._check_duplicate_keys()
    hits = [i for i in issues if "transaction_id" in i.message]
    assert len(hits) == 1
    assert hits[0].severity == IssueSeverity.CRITICAL


def test_no_duplicate_transaction_id_raises_nothing():
    txs = [
        {"transaction_id": "T1", "case_id": "C1", "transaction_date": "2025-01-01"},
        {"transaction_id": "T2", "case_id": "C1", "transaction_date": "2025-02-01"},
    ]
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[])
    issues = ds._check_duplicate_keys()
    assert not any("transaction_id" in i.message for i in issues)


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
    assert "ownership_and_control_unknown" in flags


def test_affiliate_requires_configured_threshold():
    try:
        _classify_affiliate({"ownership_percentage": "30"}, {})
    except KeyError as exc:
        assert exc.args == ("ownership_test",)
    else:
        raise AssertionError("법정 임계값을 코드 기본값으로 대체하면 안 된다")


# ---------------------------------------------------------------- _classify_officer

def test_officer_of_debtor_corporation_is_related():
    parties = {("C1", "D"): {"relation_type": "self"}}
    assert _classify_officer({"officer_of": "D"}, RP, "C1", parties, "corporation")[0] == "related"


def test_officer_of_debtor_individual_flags_data_inconsistency():
    parties = {("C1", "D"): {"relation_type": "self"}}
    v, flags = _classify_officer({"officer_of": "D"}, RP, "C1", parties, "individual")
    assert v == "verify"
    assert "officer_of_individual_debtor_data_inconsistency" in flags


def test_officer_of_affiliate_inherits_verified_affiliate_status():
    parties = {("C1", "AFF"): {
        "relation_type": "affiliate", "affiliate_status_verified": "Y"}}
    assert _classify_officer({"officer_of": "AFF"}, RP, "C1", parties, "corporation")[0] == "related"
    parties["C1", "AFF"]["affiliate_status_verified"] = ""
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


def test_debt_repayment_requires_configured_tolerance():
    tx = {"consideration_paid": "100", "liability_reduction": "100"}
    try:
        _debt_repayment_within_tolerance(tx, {})
    except KeyError as exc:
        assert exc.args == ("tolerance",)
    else:
        raise AssertionError("법률·운영 임계값을 코드 기본값으로 대체하면 안 된다")


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


# ----------------------------------------------------------- 법정 관계 사실 필드

def test_kinship_requires_degree_and_applies_statutory_limit():
    assert _classify_kinship({"kinship_degree": ""}, 4)[0] == "verify"
    assert _classify_kinship({"kinship_degree": "4"}, 4)[0] == "related"
    assert _classify_kinship({"kinship_degree": "5"}, 4)[0] == "not_related"
    verdict, _ = classify_relation(
        {"relation_type": "lineal_ascendant", "kinship_degree": "9"},
        date(2025, 1, 1), RP,
    )
    assert verdict == "not_related"


def test_employee_uses_livelihood_facts_not_job_title():
    assert _classify_employee({})[0] == "verify"
    assert _classify_employee({"financial_dependency": "Y"})[0] == "related"
    assert _classify_employee({
        "financial_dependency": "N", "shared_livelihood": "N"})[0] == "not_related"


def test_corporate_affiliate_requires_verified_affiliate_status():
    assert _classify_affiliate({}, RP, "corporation")[0] == "verify"
    assert _classify_affiliate(
        {"affiliate_status_verified": "Y"}, RP, "corporation")[0] == "related"
    assert _classify_affiliate(
        {"affiliate_status_verified": "N"}, RP, "corporation")[0] == "not_related"


def test_individual_affiliate_uses_aggregated_ownership_or_control():
    party = {"ownership_percentage": "10", "aggregated_ownership_percentage": "35"}
    assert _classify_affiliate(party, RP, "individual")[0] == "related"
    assert _classify_affiliate({"de_facto_control": "Y"}, RP, "individual")[0] == "related"


# --------------------------------------------------------------- 경제 실질

def _economic_tx(**overrides):
    tx = {
        "action_type": "sale",
        "asset_fair_value": "100",
        "liability_increase_value": "",
        "liability_reduction": "",
        "waived_right_value": "",
        "consideration_contractual": "0",
        "consideration_paid": "0",
        "payment_verified": "N",
        "debtor_direct_benefit_value": "",
        "benefit_realizability": "",
    }
    tx.update(overrides)
    return tx


def test_verified_debt_reduction_is_value_in():
    tx = _economic_tx(liability_reduction="100")
    assert economics(tx, {})[:3] == (100.0, 100.0, 1.0)


def test_debt_repayment_isolates_only_excess():
    tx = _economic_tx(
        action_type="debt_repayment", asset_fair_value="",
        consideration_paid="130", payment_verified="Y", liability_reduction="100")
    assert economics(tx, {})[:3] == (30.0, 0.0, 0.0)


def test_unverified_contractual_payment_is_not_double_counted_as_no_consideration():
    tx = _economic_tx(consideration_contractual="30", consideration_paid="30")
    ctx = {"tx": tx, "value_in": economics(tx, {})[1]}
    assert PREDICATES["payment_unverified"](ctx) is True
    assert PREDICATES["no_verified_consideration"](ctx) is False


# ------------------------------------------------------------ 입력·그래프 무결성

def test_malformed_or_non_finite_number_is_critical_not_missing_value():
    for raw in ("not-a-number", "NaN", "inf", "-inf"):
        ds = Dataset(
            cases={}, parties={}, links=[],
            transactions=[{
                "transaction_id": "T1", "transaction_date": "2025-01-01",
                "asset_fair_value": raw, "payment_verified": "",
                "benefit_realizability": "",
            }],
        )
        issues = ds._check_value_formats()
        assert len(issues) == 1
        assert issues[0].severity == IssueSeverity.CRITICAL
        assert issues[0].details[0]["field"] == "asset_fair_value"


def test_same_day_multi_node_cycle_is_critical():
    txs = [
        {"transaction_id": "A", "case_id": "C", "transaction_date": "2025-01-01"},
        {"transaction_id": "B", "case_id": "C", "transaction_date": "2025-01-01"},
    ]
    links = [
        {"link_id": "L1", "case_id": "C", "from_transaction_id": "A",
         "to_transaction_id": "B", "link_type": "funds_flow", "confidence": "verified"},
        {"link_id": "L2", "case_id": "C", "from_transaction_id": "B",
         "to_transaction_id": "A", "link_type": "funds_flow", "confidence": "verified"},
    ]
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    issues = ds._check_tx_link_cycles()
    assert len(issues) == 1
    assert issues[0].severity == IssueSeverity.CRITICAL


def test_all_disjoint_cycle_components_are_reported():
    txs = [
        {"transaction_id": tx_id, "case_id": "C", "transaction_date": "2025-01-01"}
        for tx_id in ("A", "B", "C", "D")
    ]
    links = [
        {"link_id": "L1", "case_id": "C", "from_transaction_id": "A",
         "to_transaction_id": "B", "link_type": "funds_flow"},
        {"link_id": "L2", "case_id": "C", "from_transaction_id": "B",
         "to_transaction_id": "A", "link_type": "funds_flow"},
        {"link_id": "L3", "case_id": "C", "from_transaction_id": "C",
         "to_transaction_id": "D", "link_type": "funds_flow"},
        {"link_id": "L4", "case_id": "C", "from_transaction_id": "D",
         "to_transaction_id": "C", "link_type": "funds_flow"},
    ]
    ds = Dataset(cases={}, transactions=txs, parties={}, links=[], tx_links=links)
    issue = ds._check_tx_link_cycles()[0]
    assert len(issue.details) == 2
    assert {tuple(item["transactions"]) for item in issue.details} == {
        ("A", "B"), ("C", "D")}


def test_duplicate_transaction_link_id_and_edge_are_reported():
    links = [
        {"link_id": "L1", "case_id": "C", "from_transaction_id": "A",
         "to_transaction_id": "B", "link_type": "funds_flow"},
        {"link_id": "L1", "case_id": "C", "from_transaction_id": "A",
         "to_transaction_id": "B", "link_type": "funds_flow"},
    ]
    ds = Dataset(cases={}, transactions=[], parties={}, links=[], tx_links=links)
    issues = ds._check_duplicate_keys()
    assert any(i.severity == IssueSeverity.CRITICAL and "link_id" in i.message for i in issues)
    assert any(i.severity == IssueSeverity.WARNING and "거래 간 링크" in i.message for i in issues)


def test_engine_refuses_to_run_with_critical_integrity_issue():
    issue = IntegrityIssue(
        IssueCategory.VALUE_CONSTRAINT, IssueSeverity.CRITICAL, "invalid input")
    ds = Dataset(cases={}, transactions=[], parties={}, links=[], integrity_issues=[issue])
    try:
        run(ds, {})
    except ValueError as exc:
        assert "판정 실행 거부" in str(exc)
    else:
        raise AssertionError("CRITICAL 입력으로 엔진을 직접 호출해도 중단해야 한다")
