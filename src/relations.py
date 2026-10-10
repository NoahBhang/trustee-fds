"""
1단계: 특수관계인 분류 (related_party.yaml).

관계 유형별 판정과 관계 종료일 전후의 시간 창 처리를 담당한다.
"""
from __future__ import annotations

if __package__:
    from .dataset import _date, _num
else:  # 직접 실행(`python src/cli.py`)·테스트의 src 경로 import 호환
    from dataset import _date, _num


# ------------------------------------------------------- 1단계: 관계·시간

def _boundary_window_days(rp_rules):
    """관계 종료일 전후 relation_boundary 플래그를 붙일 폭(일). related_party.yaml."""
    return rp_rules["temporal_scope"]["boundary_window_days"]


def _apply_temporal_window(verdict, flags, party, tx_date, rp_rules):
    """
    관계 유효기간(relation_valid_from/to) 밖의 거래는 not_related 로 되돌린다.
    former_spouse 를 제외한 모든 관계 유형이 공유하는 로직 — former_spouse 는
    혼인기간을 related 로 취급해야 해서 자체 함수에서 이 로직을 직접 수행한다.
    """
    frm, to = _date(party.get("relation_valid_from")), _date(party.get("relation_valid_to"))
    if verdict != "not_related":
        if frm and tx_date < frm:
            verdict, flags = "not_related", flags + ["relation_not_yet"]
        elif to and tx_date > to:
            verdict, flags = "not_related", flags + ["relation_ended"]
    if to and abs((tx_date - to).days) <= _boundary_window_days(rp_rules):
        flags = flags + ["relation_boundary"]
    if not frm and not to and verdict != "not_related":
        flags = flags + ["relation_period_unknown"]
    if verdict == "verify":
        flags = flags + [rp_rules["verify_behavior"]["flag"]]
    return verdict, flags


def _fact_yes_no(party, field_name):
    """Y/N 구조화 사실을 True/False/None으로 읽는다."""
    raw = (party.get(field_name) or "").strip().upper()
    if raw == "Y":
        return True
    if raw == "N":
        return False
    return None


def _classify_kinship(party, limit):
    """촌수 한계를 데이터로 확인한다. 값이 없으면 related로 추정하지 않는다."""
    degree = _num(party.get("kinship_degree"))
    if degree is None:
        return "verify", ["kinship_degree_unknown"]
    if degree <= limit:
        return "related", []
    return "not_related", ["kinship_degree_outside_statutory_limit"]


def _classify_employee(party):
    """직원 신분이 아니라 생계 의존·공동생계 사실로 시행령 요건을 판정한다."""
    dependent = _fact_yes_no(party, "financial_dependency")
    shared = _fact_yes_no(party, "shared_livelihood")
    if dependent is True or shared is True:
        return "related", ["livelihood_relationship_confirmed"]
    if dependent is False and shared is False:
        return "not_related", []
    return "verify", ["livelihood_relationship_unknown"]


def _classify_affiliate(party, rp_rules, debtor_type=None):
    """
    지분율 기준 동적 판정. ownership_test.threshold_percent 이상이면 related.
    미상이면 verify(사실 데이터 부재), 미달이면 verify(사실상 영향력 배제 못 함) —
    임계값 미달을 not_related 로 자동 확정하지 않는다.
    """
    # 법인 채무자의 '계열회사'는 공정거래법상 계열회사 해당 사실로 확인한다.
    # 단순 지분 30%를 정의의 대용물로 쓰지 않는다.
    if debtor_type == "corporation":
        verified = _fact_yes_no(party, "affiliate_status_verified")
        if verified is True:
            return "related", ["affiliate_status_confirmed"]
        if verified is False:
            return "not_related", []
        return "verify", ["affiliate_status_unknown"]

    ot = rp_rules["ownership_test"]
    threshold = ot["threshold_percent"]
    direct = _num(party.get("ownership_percentage"))
    aggregated = _num(party.get("aggregated_ownership_percentage"))
    control = _fact_yes_no(party, "de_facto_control")
    pct = aggregated if aggregated is not None else direct
    if pct is None and control is not True:
        return "verify", ["ownership_and_control_unknown"]
    if control is True:
        return "related", ["de_facto_control_confirmed"]
    if pct >= threshold:
        return "related", []
    return "verify", ["ownership_below_threshold_de_facto_control_unassessed"]


def _classify_officer(party, rp_rules, case_id, all_parties, debtor_type):
    """
    officer_of(party_id 참조)로 동적 판정.
    - 채무자 법인 자신(relation_type=self)을 가리키면: 법인 채무자에 한해 related.
    - affiliate 를 가리키면: 그 법인의 ownership_test 판정을 물려받는다.
    - 'none'(무관한 법인)을 가리키면: not_related.
    - 대상을 특정할 수 없으면: verify.
    """
    target_id = (party.get("officer_of") or "").strip()
    if not target_id or all_parties is None or case_id is None:
        return "verify", ["officer_of_unknown"]

    target = all_parties.get((case_id, target_id))
    if target is None:
        return "verify", ["officer_of_unresolved"]

    target_rt = (target.get("relation_type") or "").strip()

    if target_rt == "self":
        if debtor_type == "corporation":
            return "related", ["officer_of_debtor_itself"]
        # 개인 채무자는 '임원' 개념 자체가 성립하지 않는다 — 데이터 불일치 신호.
        return "verify", ["officer_of_individual_debtor_data_inconsistency"]

    if target_rt == "affiliate":
        verdict, _ = _classify_affiliate(target, rp_rules, debtor_type)
        return verdict, [f"officer_of_affiliate_{verdict}"]

    if target_rt == "none":
        return "not_related", []

    return "verify", ["officer_of_relation_unclassified"]


def _classify_former_spouse(party, tx_date, rp_rules):
    """
    혼인 기간(relation_valid_from~relation_valid_to) 중 거래는 배우자와 동일하게
    related. 이혼 후 거래는 원칙 not_related 이나, post_divorce_dependency = Y
    (다목 — 본인의 금전·재산으로 생계 유지)이면 related. 그 필드가 비어 있으면 verify.

    동일인 여부(예: 샘플 P-007이 P-001과 동일인인지)는 사실관계 조사 문제이므로
    이 함수는 판정하지 않는다 — parties.csv 에 별도 party 로 등재된 그대로 다룬다.
    """
    frm = _date(party.get("relation_valid_from"))
    to = _date(party.get("relation_valid_to"))
    dep = (party.get("post_divorce_dependency") or "").strip().upper()
    flags = []

    if frm and tx_date < frm:
        return "not_related", ["relation_not_yet"]

    if to is None or tx_date <= to:
        # 혼인 기간 중(또는 종료일 미상) — 배우자와 동일하게 취급
        return "related", flags

    if abs((tx_date - to).days) <= _boundary_window_days(rp_rules):
        flags.append("relation_boundary")

    if dep == "Y":
        return "related", flags + ["post_divorce_dependency_confirmed"]
    if dep == "N":
        return "not_related", flags + ["relation_ended"]
    return "verify", flags + [rp_rules["verify_behavior"]["flag"], "post_divorce_dependency_unknown"]


def classify_relation(party, tx_date, rp_rules, case_id=None, all_parties=None, debtor_type=None):
    """
    관계라는 '사실'을 특수관계인 해당 여부라는 '판단'으로 옮긴다.

    former_spouse / affiliate / officer / 혈족·인척 / 생계 관계는 parties.csv 의
    구조화 필드로 동적 판정한다. 그 외 유형은 classification 딕셔너리의 정적
    verdict 를 시간 창(_apply_temporal_window)에 통과시킨다.
    """
    rt = (party.get("relation_type") or "").strip()

    if rt == "former_spouse":
        return _classify_former_spouse(party, tx_date, rp_rules)
    if rt == "affiliate":
        verdict, flags = _classify_affiliate(party, rp_rules, debtor_type)
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)
    if rt == "officer":
        verdict, flags = _classify_officer(party, rp_rules, case_id, all_parties, debtor_type)
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)
    kinship_limits = {
        "lineal_ascendant": 8,
        "lineal_descendant": 8,
        "sibling": 8,
        "affinity": 4,
        "collateral_relative": 8,
    }
    if rt in kinship_limits:
        verdict, flags = _classify_kinship(party, kinship_limits[rt])
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)
    if rt == "employee":
        verdict, flags = _classify_employee(party)
        return _apply_temporal_window(verdict, flags, party, tx_date, rp_rules)

    entry = rp_rules["classification"].get(rt)
    verdict = entry["verdict"] if entry else rp_rules["unknown_relation_type"]
    return _apply_temporal_window(verdict, [], party, tx_date, rp_rules)
