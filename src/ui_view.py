"""화면 표시용 변환 — 엔진 결과와 입력 행을 관재인이 읽는 한국어 문장으로 바꾼다.

판정은 하지 않는다. 엔진이 낸 값을 그대로 옮기되 코드값·빈 값·금액 표기만 바꾼다.
신호·확인 필요 항목·소급기간 근거의 문구는 룰 YAML의 label/text/basis를 그대로 쓴다.
"""
from __future__ import annotations

import re
from datetime import date

ACTION_LABELS = {
    "gift": "증여", "debt_forgiveness": "채무면제", "right_waiver": "권리포기",
    "prescription_benefit_waiver": "시효이익의 포기", "gratuitous_loan_for_use": "사용대차",
    "third_party_guarantee": "제3자를 위한 보증", "third_party_collateral": "제3자를 위한 물상보증",
    "sale": "매각", "collateral_provision": "담보 설정",
    "litigation_concession": "소송상 양보(청구 포기·인낙·화해·소취하)",
    "inheritance_partition_waiver": "상속재산분할협의상 지분 포기", "debt_repayment": "채무 변제",
    "inheritance_renunciation": "상속포기", "divorce_property_division": "이혼 재산분할",
    "ordinary_expense": "일상 경비",
}
PRIORITY_LABELS = {"high": "높음", "medium": "보통", "low": "낮음",
                   "unresolved": "판단 보류 — 평가 필요"}
RELATION_LABELS = {
    "self": "채무자 본인", "spouse": "배우자", "lineal_ascendant": "직계존속",
    "lineal_descendant": "직계비속", "sibling": "형제자매", "affinity": "인척",
    "collateral_relative": "방계혈족", "employee": "피용자", "former_spouse": "전 배우자",
    "affiliate": "계열회사", "officer": "임원", "none": "관계 없음",
}
ROLE_LABELS = {"legal_counterparty": "법률행위 상대방", "principal_debtor": "주채무자",
               "ultimate_beneficiary": "최종 수익자"}
FLAG_LABELS = {
    "boundary_case": "기준일 또는 소급기간 시작일 ±3일 이내의 경계 거래",
    "post_adjudication_soft": "파산선고일 이후의 행위 — 상한이 미확정이라 제외하지 않고 표시",
    "relation_period_unknown": "특수관계 기간 정보 없음 — 거래일에도 유효한 것으로 간주",
    "relation_unverified": "특수관계 판단 보류 — 누락 방지를 위해 넓은 소급기간 적용",
    "affiliate_status_confirmed": "계열회사 여부 확인됨",
    "officer_of_unresolved": "임원 관계 확인 필요",
    "related_party_fund_flow_multihop": "자금흐름이 여러 단계를 거쳐 연결됨",
    "funds_flow_delayed": "자금흐름 간격이 기준 기간을 넘음 — 참고만",
    "debt_repayment_excess_isolated": "정상 상환분을 빼고 초과 변제분만 검토",
    "direct_benefit == consideration_paid — 이중계상 회피":
        "채무자 직접 이익이 지급 대가와 같은 항목이라 한 번만 반영",
}
ROUTE_LABELS = {"art391-1/2/3": "다른 부인 유형(제391조 제1~3호)에서 검토",
                "manual_review": "사람이 직접 검토할 대상으로 분류",
                "excluded": "검토 대상에서 제외"}
SOURCE_LABELS = {"verified": "확인된 가액", "estimate": "추정치", "market": "시세",
                 "appraisal": "감정가"}
GUARANTEE_TYPES = {"third_party_guarantee", "third_party_collateral", "collateral_provision"}

# (열 이름, 화면 이름, 금액 여부). 여기 없는 열도 숨기지 않고 뒤에 붙인다.
FACT_FIELDS = [
    ("transaction_date", "거래일", False),
    ("action_type", "행위 유형", False),
    ("asset_fair_value", "출연 재산 가치", True),
    ("fair_value_source", "가치 산정 근거", False),
    ("liability_increase_value", "보증·담보로 늘어난 채무", True),
    ("liability_reduction", "실제로 줄어든 채무", True),
    ("waived_right_value", "포기한 권리의 가치", True),
    ("consideration_contractual", "계약서상 대가", True),
    ("consideration_paid", "실제 지급된 대가", True),
    ("payment_verified", "대가 지급 확인", False),
    ("debtor_direct_benefit_value", "채무자가 직접 얻은 이익", True),
    ("benefit_realizability", "이익 실현 여부", False),
    ("necessity_evidence", "필요성·상당성 자료", False),
    ("contrary_evidence", "반대 증거", False),
    ("source", "출처", False),
    ("note", "비고", False),
]
HIDDEN_FIELDS = {"transaction_id", "case_id"}
EMPTY = "기재 없음"


def blank(value):
    return value is None or str(value).strip() == ""


def won(value):
    if blank(value):
        return EMPTY
    try:
        return f"{float(value):,.0f}원"
    except (TypeError, ValueError):
        return str(value)


def percent(ratio):
    return "-" if ratio is None else f"{ratio:.1%}"


def action_label(code):
    return ACTION_LABELS.get(code, code)


def priority_label(result):
    return PRIORITY_LABELS.get(result.priority, result.priority) if result.candidate else "후보 아님"


def signal_labels(signals, rule):
    labels = {s["id"]: s["label"] for s in rule["priority_signals"]}
    return [labels.get(s, s) for s in signals]


def flag_labels(flags):
    return [FLAG_LABELS.get(f, f) for f in flags]


def drop_reason_label(result):
    reason = result.drop_reason
    if not reason:
        return ""
    if m := re.fullmatch(r"(\S+) 로 라우팅", reason):
        return ROUTE_LABELS.get(m[1], f"{m[1]}(으)로 분류")
    if m := re.fullmatch(r"후보 행위 유형 아님 \((\S+)\)", reason):
        return f"무상부인 검토 대상 행위가 아님 ({action_label(m[1])})"
    if m := re.fullmatch(r"시간 창 밖 \(창 시작 (\S+)\)", reason):
        return f"소급기간 밖의 거래 (기간 시작 {m[1]})"
    if result.ratio is not None and reason.startswith("대가비율"):
        return f"대가비율 {percent(result.ratio)} — 정상 대가 범위"
    return reason


def fact_value(field, value):
    if blank(value):
        return EMPTY
    if field == "action_type":
        return action_label(value)
    if field == "fair_value_source":
        return SOURCE_LABELS.get(value, value)
    if field == "payment_verified":
        return {"Y": "확인됨", "N": "확인되지 않음"}.get(str(value).strip().upper(), value)
    if field == "benefit_realizability":
        return {"realized": "실현됨"}.get(value, value)
    return value


def facts_table(tx):
    known = {f for f, _, _ in FACT_FIELDS}
    rows = [{"항목": label, "내용": won(tx.get(f)) if money else fact_value(f, tx.get(f))}
            for f, label, money in FACT_FIELDS if f in tx]
    rows += [{"항목": f, "내용": EMPTY if blank(v) else v}
             for f, v in tx.items() if f not in known | HIDDEN_FIELDS]
    return rows


def _date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def anchor_sentence(tx, case, rule):
    """룰 output.template 첫 문장: '{기준일}({날짜}) 기준 N일 이전/이후에 이루어진 {행위}'."""
    td = _date(tx.get("transaction_date"))
    anchors = [(a["label"], _date(case.get(a["field"].split(".", 1)[1])))
               for a in rule["temporal_filter"]["anchor_events"]]
    anchors = [(label, d) for label, d in anchors if d]
    if not td or not anchors:
        return f"{tx.get('transaction_date') or '거래일 미상'}에 이루어진 {action_label(tx.get('action_type'))}입니다."
    label, anchor = min(anchors, key=lambda a: abs((a[1] - td).days))
    days = (anchor - td).days
    when = "당일" if days == 0 else f"{abs(days)}일 {'이전' if days > 0 else '이후'}"
    return f"{label}({anchor}) 기준 {when}에 이루어진 {action_label(tx.get('action_type'))}입니다."


def lookback_basis(result, rule):
    case = rule["temporal_filter"]["lookback"]["cases"].get(result.lookback_key)
    return case["basis"] if case else ""


def verification_items(tx, rule):
    """룰 requires_human_verification 의 trigger 를 입력 열로 옮긴 것. 모르는 trigger 는 항상 표시."""
    unpaid = str(tx.get("payment_verified") or "").strip().upper() != "Y"
    triggers = {
        "fair_value_basis": blank(tx.get("asset_fair_value")) or tx.get("fair_value_source") == "estimate",
        "actual_payment": unpaid,
        "direct_benefit": tx.get("action_type") in GUARANTEE_TYPES,
    }
    return [item["text"] for item in rule["requires_human_verification"]
            if triggers.get(item["id"], True)]


def party_rows(ds, tx):
    rows = []
    for link in ds.links:
        if link["transaction_id"] != tx["transaction_id"]:
            continue
        party = ds.parties.get((tx["case_id"], link["party_id"])) or {}
        rows.append({
            "역할": ROLE_LABELS.get(link["role"], link["role"]),
            "당사자": party.get("display_name") or link["party_id"],
            "채무자와의 관계": RELATION_LABELS.get(party.get("relation_type"), party.get("relation_type") or EMPTY),
            "관계 근거·출처": " · ".join(v for v in (party.get("relation_basis"), party.get("relation_source")) if not blank(v)) or EMPTY,
            "비고": party.get("note") or "",
        })
    return rows


def counterparty_text(ds, tx, result):
    names = [r["당사자"] + (f"({r['채무자와의 관계']})" if r["채무자와의 관계"] not in ("관계 없음", EMPTY, r["당사자"]) else "")
             for r in party_rows(ds, tx) if r["역할"] == ROLE_LABELS["legal_counterparty"]]
    suffix = {"related": " — 특수관계인", "verify": " — 특수관계 판단 보류"}.get(result.lookback_key, "")
    return (", ".join(names) or "없음 (단독행위)") + suffix


def tx_link_rows(ds, tx_id):
    confidence = {"confirmed": "확인됨", "probable": "개연성 있음", "possible": "가능성 있음"}
    rows = []
    for link in ds.tx_links:
        if tx_id not in (link["from_transaction_id"], link["to_transaction_id"]):
            continue
        outgoing = link["from_transaction_id"] == tx_id
        rows.append({
            "방향": "이 거래 → " + link["to_transaction_id"] if outgoing else link["from_transaction_id"] + " → 이 거래",
            "연결 유형": {"funds_flow": "자금흐름"}.get(link["link_type"], link["link_type"]),
            "근거": link.get("evidence") or EMPTY,
            "신뢰도": confidence.get(link.get("confidence"), link.get("confidence") or EMPTY),
            "메모": link.get("note") or "",
        })
    return rows
