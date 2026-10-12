from src import ui_view as view
from src.ui_service import analyze, sample_files

DS, RULES, RESULTS, _ = analyze(sample_files())
RULE = RULES["art391-4-gratuitous"]
TXS = {t["transaction_id"]: t for t in DS.transactions}
BY_ID = {r.tx_id: r for r in RESULTS}


def test_every_sample_code_has_a_korean_label():
    """샘플에 나오는 행위·신호·플래그·제외 사유가 코드 그대로 화면에 나가지 않는다."""
    for tx in DS.transactions:
        assert tx["action_type"] in view.ACTION_LABELS
    signal_ids = {s["id"] for s in RULE["priority_signals"]}
    for r in RESULTS:
        assert set(r.signals) <= signal_ids
        assert set(r.flags) <= set(view.FLAG_LABELS), r.flags
        label = view.drop_reason_label(r)
        assert "라우팅" not in label and "_" not in label, label


def test_t001_detail_reads_as_sentences():
    tx, r = TXS["T001"], BY_ID["T001"]
    case = DS.cases[tx["case_id"]]
    assert view.anchor_sentence(tx, case, RULE) == "지급정지일(2025-08-20) 기준 23일 이후에 이루어진 매각입니다."
    assert view.priority_label(r) == "높음"
    assert "계약서상 대가는 있으나 실제 지급 미확인" in view.signal_labels(r.signals, RULE)
    assert view.counterparty_text(DS, tx, r).endswith("— 특수관계인")
    facts = {row["항목"]: row["내용"] for row in view.facts_table(tx)}
    assert facts["출연 재산 가치"] == "480,000,000원"
    assert facts["가치 산정 근거"] == "추정치"
    assert facts["대가 지급 확인"] == "확인되지 않음"
    assert facts["필요성·상당성 자료"] == view.EMPTY
    assert "transaction_id" not in facts
    items = view.verification_items(tx, RULE)
    assert any("공정가치" in t for t in items) and any("실제 지급" in t for t in items)
    assert not any("직접적·현실적 이익" in t for t in items)   # 보증·담보가 아니다


def test_unresolved_is_not_shown_as_low():
    r = next(r for r in RESULTS if r.priority == "unresolved")
    assert view.priority_label(r) == "판단 보류 — 평가 필요"


def test_amount_and_ratio_formatting():
    assert view.won("") == view.EMPTY and view.won("1500") == "1,500원"
    assert view.percent(None) == "-" and view.percent(0.9583) == "95.8%"
