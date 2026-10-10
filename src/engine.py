"""
trustee-fds 판정 실행기

설계 원칙
---------
1. 조문에서 나오는 값(소급기간, 기준 사건, 행위 유형, 임계값, 정렬 구획)은
   전부 YAML에서 읽는다. 필수 설정이 없으면 코드 기본값으로 대체하지 않고 실패한다.

2. 술어(predicate)는 id 로 등록한다. YAML 의 `when` 문자열은 사람이 읽는
   명세이고, 실행은 같은 id 로 등록된 파이썬 함수가 한다.
   YAML 에 있는 id 가 코드에 없으면 즉시 실패한다 (UnimplementedPredicate).
   → 완전한 표현식 언어를 만들지 않은 대신, 룰과 코드의 어긋남이
     조용히 넘어가지 않도록 강제한다.

3. 각 거래가 '몇 단계까지 갔는지'를 기록한다. 테스트 케이스가 자기가
   검증하려는 단계에 도달했는지 확인하기 위한 것이다.

4. 무결성 검사는 Dataset 로드 시점에 수행한다. 데이터의 일관성은 데이터가
   만들어지는 순간 보장되어야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta

# 로딩·무결성 검사는 dataset.py, 1단계 관계 분류는 relations.py 에 있다.
# cli·ui·테스트가 쓰는 이름은 이전처럼 engine 에서 가져갈 수 있게 재노출한다.
if __package__:
    from .dataset import Dataset, IntegrityIssue, IssueCategory, IssueSeverity, _date, _num  # noqa: F401
    from .relations import (  # noqa: F401
        _classify_affiliate, _classify_employee, _classify_former_spouse,
        _classify_kinship, _classify_officer, classify_relation,
    )
else:  # 직접 실행(`python src/cli.py`)·테스트의 src 경로 import 호환
    from dataset import Dataset, IntegrityIssue, IssueCategory, IssueSeverity, _date, _num  # noqa: F401
    from relations import (  # noqa: F401
        _classify_affiliate, _classify_employee, _classify_former_spouse,
        _classify_kinship, _classify_officer, classify_relation,
    )

# 단계 순서. target_stage 검사에 쓴다.
STAGE_ORDER = {"temporal": 1, "party": 1, "action": 2, "economic": 3,
               "ranking": 4, "signal": 4}


class UnimplementedPredicate(Exception):
    """YAML 에 선언된 술어가 코드에 없을 때."""


VALID_RULE_STATUSES = {"draft", "spec_verified", "legal_reviewed", "production"}


def load_rules(root: Path):
    rules = {}
    for p in (root / "rules").glob("*.yaml"):
        r = yaml.safe_load(p.read_text(encoding="utf-8"))
        for required in ("rule_id", "version", "status"):
            if required not in r:
                raise ValueError(f"{p.name}: 필수 룰 메타데이터 누락 — {required}")
        if r["status"] not in VALID_RULE_STATUSES:
            raise ValueError(f"{p.name}: 알 수 없는 status — {r['status']}")
        if r["rule_id"] in rules:
            raise ValueError(f"중복 rule_id — {r['rule_id']}")
        rules[r["rule_id"]] = r
    main = rules["art391-4-gratuitous"]
    for dep in main.get("depends_on", []):
        if dep["rule_id"] not in rules:
            raise KeyError(f"의존 룰 누락: {dep['rule_id']}")
    _check_route_out(main["action_filter"]["route_out"])
    return rules


def _check_route_out(route_out):
    """route_out 항목은 action_type 키로 고른다. when 문구와 어긋나면 로드를 거부한다."""
    seen = set()
    for r in route_out:
        at = r.get("action_type")
        if not at:
            raise ValueError(f"route_out 항목에 action_type 누락 — {r.get('when')}")
        if at in seen:
            raise ValueError(f"route_out action_type 중복 — {at}")
        if f'"{at}"' not in str(r.get("when", "")):
            raise ValueError(f"route_out action_type({at})과 when 문구가 다름 — {r.get('when')}")
        seen.add(at)



def resolve_lookback(verdicts, rule):
    """소급기간. legal_counterparty 의 판정만 본다. (2008다48117)"""
    lb = rule["temporal_filter"]["lookback"]
    key = "related" if any(v == "related" for v in verdicts) else (
        "verify" if any(v == "verify" for v in verdicts) else "not_related")
    case = lb["cases"][key]
    return case["months"], case["basis"], key


def temporal_window(case_row, months, rule):
    anchors = []
    for a in rule["temporal_filter"]["anchor_events"]:
        fld = a["field"].split(".", 1)[1]
        dt = _date(case_row.get(fld))
        if dt:
            anchors.append((a["label"], dt))
    if not anchors:
        raise ValueError("기준 사건이 하나도 없다")
    start = min(dt - relativedelta(months=months) for _, dt in anchors)
    upper = _date(case_row.get(rule["temporal_filter"]["upper_bound"]["field"].split(".", 1)[1]))
    return start, anchors, upper


def _debt_repayment_within_tolerance(tx, route_rule):
    """
    자기 채무 변제가 허용오차 이내인지 확인한다. '실제로 지급한 금액'
    (consideration_paid)과 '실제로 채무가 줄어든 금액'(liability_reduction)을
    비교한다. 허용오차를 벗어나면(과다변제) route_out 하지 않는다 —
    route_out 은 '확실히 무상이 아닌' 거래만 배제해야 하기 때문이다.

    비교할 데이터가 없으면 정상 변제로 단정하지 않는다(False 반환 — 3단계로
    보내 결측을 드러낸다. route_out 은 조용히 배제하는 통로이므로, 근거 없이
    통과시키지 않는다).
    """
    verified = str(tx.get("payment_verified","")).strip().upper()
    if verified != "Y":
        return False

    paid = _num(tx.get("consideration_paid"))
    reduced = _num(tx.get("liability_reduction"))
    if paid is None or reduced is None or paid == 0:
        return False
    tolerance = route_rule["tolerance"]
    return abs(paid - reduced) / paid <= tolerance


# ------------------------------------------------------- 3단계: 경제 실질

def economics(tx, rule):
    """Value Out / Value In. 채무자 기준으로만 계산한다."""
    if tx.get("action_type") == "debt_repayment":
        paid = _num(tx.get("consideration_paid"))
        reduced = _num(tx.get("liability_reduction"))
        verified = str(tx.get("payment_verified") or "").strip().upper()

        if verified != "Y" or paid is None or reduced is None:
            return None, 0.0, None, ["debt_repayment_amount_unverified"]
        if paid <= reduced:
            # 지급액이 채무 감소액 이하 → 재산 감소 없음. 값이 있으므로 평가 가능(ratio 1.0)
            return 0.0, 0.0, 1.0, ["debt_repayment_no_excess"]
        excess = paid - reduced
        return excess, 0.0, 0.0, ["debt_repayment_excess_isolated"]

    outs = [_num(tx.get("asset_fair_value")),
            _num(tx.get("liability_increase_value")),
            _num(tx.get("waived_right_value"))]
    have = [v for v in outs if v is not None]
    value_out = sum(have) if have else None

    cash = _num(tx.get("consideration_paid")) or 0.0
    if tx.get("payment_verified") != "Y":
        cash = 0.0                                   # 검증되지 않은 대가는 산입하지 않는다
    debt_reduction = _num(tx.get("liability_reduction")) or 0.0
    benefit = _num(tx.get("debtor_direct_benefit_value")) or 0.0
    if tx.get("benefit_realizability") != "realized":
        benefit = 0.0                                # 추상적 기대는 Value In 이 아니다

    notes = []
    if benefit and cash and abs(benefit - cash) < 1e-6:
        benefit = 0.0                                # 동일 항목의 이중계상 방지
        notes.append("direct_benefit == consideration_paid — 이중계상 회피")
    value_in = cash + debt_reduction + benefit

    ratio = (value_in / value_out) if value_out else None
    return value_out, value_in, ratio, notes


def triage(ratio, rule):
    bands = rule["economic_substance"]["ratio"]["triage_bands"]
    if ratio is None:
        for b in bands:
            if "when" in b:
                return b["priority"]
        return "unresolved"
    # ratio 가 있으면 'when' 밴드(undefined 전용)는 건너뛴다.
    # 이 조건을 빠뜨리면 max 키가 없는 when 밴드가 catch-all 로 오인되어
    # 모든 항목이 첫 밴드로 떨어진다.
    for b in (x for x in bands if "when" not in x):
        if b.get("max") is None or ratio <= b["max"]:
            return b["priority"]
    return "low"


def triage_by_signal_count(signal_count, rule):
    """
    ratio 기반 triage_bands 가 척도로서 성립하지 않는 행위 유형(예: 보증)에 쓴다.
    '보증료율이 몇 %면 정상'이라는 근거 없는 숫자 대신, 이미 있는 다른 신호의
    중첩 정도로 우선순위를 매긴다.
    """
    bands = rule["economic_substance"]["ratio"]["signal_count_bands"]
    for b in bands:
        if b.get("max") is None or signal_count <= b["max"]:
            return b["priority"]
    return "low"


def triage_priority(action_type, ratio, signal_count, rule):
    """
    action_type 이 applicable_action_types 안이면 비율 기반(triage), 밖이면
    신호 개수 기반(triage_by_signal_count)으로 우선순위를 매긴다.
    T103(보증료율 0.04)이 매각과 같은 비율 밴드로 잘못 high 처리되던 문제를 고친다.

    ratio 가 None(value_out 산출 불가 — "평가 필요")인 경우는 행위 유형과 무관하게
    항상 unresolved 다. 이건 대가비율이 무의미한 행위인지의 문제가 아니라, 애초에
    비교할 값 자체가 없다는 뜻이기 때문이다.
    """
    if ratio is None:
        return "unresolved"
    scope = rule["economic_substance"]["ratio"].get("applicable_action_types")
    if scope is None or action_type in scope:
        return triage(ratio, rule)
    return triage_by_signal_count(signal_count, rule)


def undervalue_verdict(tx, ratio, rule):
    ua = rule["economic_substance"].get("undervalue_assessment")
    if not ua or tx["action_type"] not in ua["applies_to"]:
        return "candidate"
    if ratio is None:
        return "unresolved"
    threshold = ua["threshold"]     # YAML 이 실행값을 명시한다. when 문자열은 명세.
    return "candidate" if ratio <= threshold else "not_candidate"


def _trace_inbound_chains(ds, case_id, tx_id, tx_date, ffa, confidence_rank,
                           depth, visited):
    """
    tx_id 로 자금이 흘러들어온 경로를 max_hops 까지 역방향으로 추적한다.

    약한 고리 원칙: 각 홉마다 min_confidence 와 proximity_window_days 를 독립
    적용한다. confidence 미달인 홉에서는 그 경로 추적을 중단하고(신호로 세우지
    않음), 시간창을 넘는 홉이 하나라도 있으면 그 경로 전체를 지연으로 표시한다.

    반환: (in_window_hits: list[int], delayed_hits: list[int])
          각 리스트는 신호가 성립한 홉 수(1=직접 연결, 2 이상=다단계)를 담는다.
    """
    min_rank = confidence_rank[ffa["min_confidence"]]
    window = ffa["proximity_window_days"]
    max_hops = ffa["max_hops"]
    link_types = ffa["eligible_link_types"]

    in_window, delayed = [], []
    if depth > max_hops:
        return in_window, delayed

    for link in ds.inbound_fund_flows(case_id, tx_id, link_types):
        if confidence_rank.get(link["confidence"], -1) < min_rank:
            continue  # 약한 고리 — 이 경로는 여기서 끊는다

        from_id = link["from_transaction_id"]
        if from_id in visited:
            continue  # cycle_policy: skip_visited

        from_tx = next((t for t in ds.transactions
                         if t["transaction_id"] == from_id), None)
        if from_tx is None:
            continue  # 무결성 검사가 별도로 잡는다

        fd = _date(from_tx["transaction_date"])
        if fd is None or tx_date is None:
            continue

        within = (tx_date - fd).days <= window
        if within:
            in_window.append(depth)
        else:
            delayed.append(depth)

        # 이 홉의 출발 거래에서 다시 거슬러 올라간다
        sub_in, sub_delayed = _trace_inbound_chains(
            ds, case_id, from_id, fd, ffa, confidence_rank,
            depth + 1, visited | {from_id})
        # 상류 홉이 시간창을 넘었으면 경로 전체가 지연이다 (약한 고리)
        if within:
            in_window += sub_in
        else:
            delayed += sub_in
        delayed += sub_delayed

    return in_window, delayed


def evaluate_related_party_fund_flow(ds, case_id, tx, rule, related_counterparty):
    """
    이 거래로 자금이 흘러들어온(inbound funds_flow) 경로가 있는지, 그리고
    그것이 신호로 인정되는지 판정한다. 링크는 사실, 이 함수는 판단.

    max_hops 까지 다단계 연쇄(layering)를 추적한다. 이는 자금흐름 연결의 단서이며,
    추적 금액·계좌 명의가 없는 상태에서 동일 자금의 환류를 확정하지 않는다.

    related_counterparty: 이 거래(tx)의 legal_counterparty 가 특수관계인으로
    판정됐는지. "특수관계인 상대 거래로 이어지는 자금흐름" 신호이므로 실제로
    이 거래의 상대방이 관계인일 때만 성립시킨다 — 단순히 funds_flow
    링크 하나가 있다는 사실만으로는(상대방이 무관한 제3자여도) 성립시키지
    않는다.
    """
    ffa = rule.get("fund_flow_analysis")
    if not ffa:
        return False, []

    # confidence 서열은 YAML 이 정한다 (README §4 confidence 어휘와 일치해야 함).
    confidence_rank = {c: i for i, c in enumerate(ffa["confidence_order"])}
    tx_id = tx["transaction_id"]
    td = _date(tx["transaction_date"])

    in_window, delayed = _trace_inbound_chains(
        ds, case_id, tx_id, td, ffa, confidence_rank, depth=1, visited={tx_id})

    if not related_counterparty:
        return False, []

    flags = []
    if delayed:
        flags.append(ffa["outside_window_flag"])  # 배제하지 않고 표시만 (soft)
    # 2홉 이상으로 잡힌 경우 직접 연결과 증거 강도가 다르므로 구별해 표시한다
    if any(hops >= 2 for hops in in_window) and "multi_hop_flag" in ffa:
        flags.append(ffa["multi_hop_flag"])

    return bool(in_window), flags


# ------------------------------------------------------------ 신호 술어

PREDICATES = {}


def predicate(pid):
    def deco(fn):
        PREDICATES[pid] = fn
        return fn
    return deco


@predicate("no_verified_consideration")
def _p1(c):
    contractual = _num(c["tx"].get("consideration_contractual")) or 0
    return c["value_in"] == 0 and contractual == 0


@predicate("payment_unverified")
def _p2(c):
    return (_num(c["tx"].get("consideration_contractual")) or 0) > 0 \
        and c["tx"].get("payment_verified") != "Y"


@predicate("debt_repayment_excess")
def _p2b(c):
    if c["tx"].get("action_type") != "debt_repayment":
        return False
    paid = _num(c["tx"].get("consideration_paid"))
    reduced = _num(c["tx"].get("liability_reduction"))
    return paid is not None and reduced is not None and paid > reduced


@predicate("related_counterparty")
def _p3(c): return "related" in c["cp_verdicts"]


@predicate("role_split_guarantee")
def _p4(c):
    pd = {p["party_id"] for p in c["principal_debtors"]}
    cp = {p["party_id"] for p in c["counterparties"]}
    return bool(pd) and not pd <= cp


@predicate("related_principal_debtor")
def _p5(c): return "related" in c["pd_verdicts"]


@predicate("related_party_fund_flow")
def _p6(c):
    return c["related_party_fund_flow"]


@predicate("non_cash_waiver")
def _p7(c): return _num(c["tx"].get("waived_right_value")) is not None


@predicate("proximity_before")
def _p8(c): return c["days_to_anchor"] is not None and 0 < c["days_to_anchor"] <= 30


@predicate("post_anchor")
def _p9(c): return c["days_to_anchor"] is not None and c["days_to_anchor"] <= 0


@predicate("no_valuation")
def _p10(c): return c["value_out"] is None


@predicate("no_necessity_evidence")
def _p11(c): return not (c["tx"].get("necessity_evidence") or "").strip()


def check_predicate_coverage(rule):
    missing = [s["id"] for s in rule["priority_signals"] if s["id"] not in PREDICATES]
    if missing:
        raise UnimplementedPredicate(
            "YAML 에 선언됐으나 구현이 없는 술어: " + ", ".join(missing))


# ---------------------------------------------------------------- 파이프라인

@dataclass
class Result:
    tx_id: str
    reached: str = "temporal"      # 도달한 최종 단계
    candidate: bool = False
    lookback_months: int | None = None
    lookback_key: str | None = None
    priority: str | None = None
    value_out: float | None = None
    value_in: float | None = None
    ratio: float | None = None
    signals: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    drop_reason: str | None = None


def run(ds: Dataset, rules: dict) -> list[Result]:
    critical = [i for i in ds.integrity_issues if i.severity == IssueSeverity.CRITICAL]
    if critical:
        raise ValueError(f"CRITICAL 데이터 무결성 문제 {len(critical)}건 — 판정 실행 거부")

    rule = rules["art391-4-gratuitous"]
    rp = rules["related-party-classification"]
    check_predicate_coverage(rule)

    af = rule["action_filter"]
    boundary_days = rule["temporal_filter"]["boundary_days"]
    results = []

    for tx in ds.transactions:
        res = Result(tx_id=tx["transaction_id"])
        case = ds.cases[tx["case_id"]]
        case_id = tx["case_id"]
        td = _date(tx["transaction_date"])

        # --- 1단계: 관계 → 소급기간 → 시간 창
        cps = ds.parties_of(case_id, tx["transaction_id"], "legal_counterparty")
        pds = ds.parties_of(case_id, tx["transaction_id"], "principal_debtor")
        debtor_type = case.get("debtor_type")
        cp_v, pd_v = [], []
        for p in cps:
            v, fl = classify_relation(p, td, rp, case_id=case_id, all_parties=ds.parties,
                                       debtor_type=debtor_type)
            cp_v.append(v)
            res.flags += fl
        for p in pds:
            v, _ = classify_relation(p, td, rp, case_id=case_id, all_parties=ds.parties,
                                      debtor_type=debtor_type)
            pd_v.append(v)

        months, basis, key = resolve_lookback(cp_v, rule)
        res.lookback_months, res.lookback_key = months, key
        start, anchors, upper = temporal_window(case, months, rule)

        nearest = min(anchors, key=lambda a: abs((a[1] - td).days))
        days_to_anchor = (nearest[1] - td).days
        if abs(days_to_anchor) <= boundary_days or abs((td - start).days) <= boundary_days:
            res.flags.append("boundary_case")
        if td < start:
            res.drop_reason = f"시간 창 밖 (창 시작 {start})"
            results.append(res)
            continue
        if upper and td > upper:
            res.flags.append("post_adjudication_soft")   # 배제하지 않고 표시만

        # --- 2단계: 행위 유형
        res.reached = "action"
        at = tx["action_type"]
        # route_out 항목은 action_type 키로 고른다(load_rules 가 when 문구와 대조).
        # debt_repayment 는 숫자 조건(허용오차)이 있어 별도 함수로 판정하고,
        # 그 외는 action_type 만으로 결정된다.
        routed = next((r for r in af["route_out"] if r["action_type"] == at), None)
        if routed and at == "debt_repayment" and not _debt_repayment_within_tolerance(tx, routed):
            # 허용오차를 벗어나면(과다변제) route_out 하지 않는다 —
            # 아래로 흘려보내 후보 행위 유형 검사를 받는다.
            routed = None
        if routed:
            res.drop_reason = f"{routed['to']} 로 라우팅"
            results.append(res)
            continue
        if at not in af["candidate_types"]:
            res.drop_reason = f"후보 행위 유형 아님 ({at})"
            results.append(res)
            continue

        # --- 3단계: 경제적 실질
        res.reached = "economic"
        vo, vi, ratio, notes = economics(tx, rule)
        res.value_out, res.value_in, res.ratio = vo, vi, ratio
        res.flags += notes
        verdict = undervalue_verdict(tx, ratio, rule)
        if verdict == "not_candidate":
            res.drop_reason = f"대가비율 {ratio:.4f} — 정상 대가 범위"
            results.append(res)
            continue

        # --- 4단계: 정렬 구획 + 신호
        res.reached = "ranking"
        res.candidate = True
        related_cp = any(v == "related" for v in cp_v)
        fund_flow_link, ff_flags = evaluate_related_party_fund_flow(
            ds, case_id, tx, rule, related_cp)
        res.flags += ff_flags
        ctx = dict(tx=tx, value_in=vi, value_out=vo, cp_verdicts=cp_v, pd_verdicts=pd_v,
                   counterparties=cps, principal_debtors=pds, days_to_anchor=days_to_anchor,
                   related_party_fund_flow=fund_flow_link)
        res.signals = [s["id"] for s in rule["priority_signals"] if PREDICATES[s["id"]](ctx)]
        # 신호를 먼저 계산해야 triage_priority 가 signal_count_bands 를 쓸 수 있다
        # (applicable_action_types 밖의 행위, 예: 보증).
        res.priority = ("unresolved" if verdict == "unresolved"
                         else triage_priority(at, ratio, len(res.signals), rule))
        results.append(res)

    for result in results:
        result.flags = list(dict.fromkeys(result.flags))
    return results


def partition(results, rule):
    """value_out 결측 항목을 목록 아래로 밀지 않는다."""
    cands = [r for r in results if r.candidate]
    unvalued = [r for r in cands if r.value_out is None]
    valued = [r for r in cands if r.value_out is not None]
    unvalued.sort(key=lambda r: (-len(r.signals), r.tx_id))
    valued.sort(key=lambda r: -(r.value_out - r.value_in))
    return unvalued, valued
