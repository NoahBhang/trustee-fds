"""Local review workspace for the trustee-fds engine."""
import csv
from dataclasses import asdict

import streamlit as st

from src import ui_view as view
from src.engine import partition
from src.ui_service import REQUIRED, OPTIONAL, analyze, export_json, fingerprint, sample_files

st.set_page_config(page_title="trustee-fds | 무상부인 검토", layout="wide")
st.title("무상부인 검토 작업실")
st.caption("trustee-fds · 제391조 제4호 · 조사 대상과 확인할 근거를 정리합니다.")
st.warning("연구·검증용입니다. 실제 사건에 사용하지 마세요. 법률 전문가 검토와 실사건 검증은 완료되지 않았습니다.")

with st.sidebar:
    st.header("분석 자료")
    mode = st.radio("입력 방식", ["합성 샘플", "CSV 업로드"])
    files = {}
    if mode == "합성 샘플":
        files = sample_files()
        st.caption("저장소의 합성 샘플 34건으로 실행합니다.")
    else:
        st.caption("UTF-8 또는 Excel 기본(CP949) CSV를 사용하세요. 열 이름은 샘플과 같아야 합니다.")
        for name in (*REQUIRED, OPTIONAL):
            item = st.file_uploader(name + (" (선택)" if name == OPTIONAL else " (필수)"), type=["csv"], key=name)
            if item is not None:
                files[name] = item.getvalue()
    ready = set(REQUIRED).issubset(files)
    clicked = st.button("분석 실행", type="primary", disabled=not ready)
    if st.button("결과 지우기"):
        st.session_state.pop("analysis", None)

current = fingerprint(files)
if st.session_state.get("analysis", {}).get("key") != (mode, current):
    st.session_state.pop("analysis", None)
if clicked:
    st.session_state.pop("analysis", None)
    try:
        with st.spinner("입력 검증 및 분석 중입니다…"):
            ds, rules, results, blocked = analyze(files)
        st.session_state.analysis = dict(key=(mode, current), ds=ds, rules=rules, results=results, blocked=blocked)
    except (ValueError, KeyError, OSError, UnicodeError, csv.Error) as exc:
        st.error(f"분석을 중단했습니다. 입력 자료를 확인해 주세요: {exc}")

analysis = st.session_state.get("analysis")
if not analysis:
    st.info("왼쪽에서 자료를 선택하고 ‘분석 실행’을 눌러 주세요.")
    st.stop()
ds, rules, results = analysis["ds"], analysis["rules"], analysis["results"]
st.caption("룰 상태: " + " · ".join(f"{r['rule_id']} v{r['version']} ({r['status']})" for r in rules.values()))
for case in ds.cases.values():
    if case.get("suspension_of_payment_date") and case.get("suspension_date_confirmed") != "Y":
        st.warning(f"{case['case_id']}: 미확정 지급정지일 {case['suspension_of_payment_date']}을 잠정 기준점으로 사용합니다. 지급정지일을 제외·변경한 결과도 확인해야 합니다.")
for issue in ds.integrity_issues:
    with st.expander(f"{issue.severity.value}: {issue.message}", expanded=True):
        st.json(issue.details)
if analysis["blocked"]:
    st.error("CRITICAL 입력 오류가 있어 분석을 중단했습니다. 자료를 수정해 다시 실행해 주세요.")
    st.stop()

case_id = st.selectbox("사건", ["전체"] + list(ds.cases))
txs = {t["transaction_id"]: t for t in ds.transactions}
visible = [r for r in results if case_id == "전체" or txs[r.tx_id]["case_id"] == case_id]
unvalued, valued = partition(visible, rules["art391-4-gratuitous"])
cols = st.columns(4)
for col, label, value in zip(cols, ["검토 거래", "조사 후보", "평가 필요", "순출연 추정액 (평가 완료분)"],
                            [len(visible), len(unvalued) + len(valued), len(unvalued), f"{sum(r.value_out-r.value_in for r in valued):,.0f}원"]):
    col.metric(label, value)
st.caption("평가 필요 항목은 합계에서 제외됩니다. 순출연 추정액은 회수확률을 반영한 기대값이 아닙니다.")

rule = rules["art391-4-gratuitous"]


def rows(items):
    return [{"거래": r.tx_id, "사건": txs[r.tx_id]["case_id"],
             "행위 유형": view.action_label(txs[r.tx_id]["action_type"]),
             "우선순위": view.priority_label(r),
             "순출연 추정액": "평가 필요" if r.value_out is None else view.won(r.value_out-r.value_in),
             "대가비율": view.percent(r.ratio), "조사 이유": " · ".join(view.signal_labels(r.signals, rule)),
             "참고 사항": " · ".join(view.flag_labels(r.flags)),
             "제외 사유": view.drop_reason_label(r)} for r in items]


for title, items, drop in [("평가 필요", unvalued, ["제외 사유"]),
                           ("평가 완료 — 순출연 추정액 순", valued, ["제외 사유"]),
                           ("후보 제외 거래", [r for r in visible if not r.candidate],
                            ["우선순위", "순출연 추정액", "대가비율", "조사 이유"])]:
    st.subheader(title)
    if items:
        st.dataframe([{k: v for k, v in row.items() if k not in drop} for row in rows(items)],
                     hide_index=True, width="stretch")
    else:
        st.caption("해당 항목이 없습니다.")

if visible:
    st.subheader("거래 상세")
    selected = st.selectbox("거래 선택", [r.tx_id for r in visible],
                            format_func=lambda t: f"{t} · {view.action_label(txs[t]['action_type'])} · {txs[t]['transaction_date']}")
    result = next(r for r in visible if r.tx_id == selected)
    tx = txs[selected]
    case = ds.cases.get(tx["case_id"], {})
    with st.container(border=True):
        st.markdown(f"#### {selected} · {view.action_label(tx['action_type'])} · {tx['transaction_date']}")
        st.write(view.anchor_sentence(tx, case, rule))
        st.write(f"상대방: {view.counterparty_text(ds, tx, result)}")
        if result.candidate:
            st.markdown(f"**조사 후보 · 우선순위 {view.priority_label(result)}**")
        else:
            st.markdown(f"**후보 제외 — {view.drop_reason_label(result)}**")

        if result.candidate:
            st.markdown("##### 왜 조사 대상인가")
            st.markdown("\n".join(f"- {s}" for s in view.signal_labels(result.signals, rule)) or "- 관측된 신호 없음")
            st.markdown("##### 금액")
            c1, c2, c3 = st.columns(3)
            c1.metric("나간 가치 (Value Out)", "산출 불가" if result.value_out is None else view.won(result.value_out))
            c2.metric("확인된 대가 (Value In)", view.won(result.value_in))
            c3.metric("순출연 추정액", "평가 필요" if result.value_out is None else view.won(result.value_out-result.value_in))
            st.caption(f"대가비율 {view.percent(result.ratio)} — 조사 우선순위 지표이며 법정 기준이 아닙니다. "
                       "계약서상 대가는 실제 지급이 확인된 금액만 Value In에 들어갑니다.")
            if "debt_repayment_excess_isolated" in result.flags and result.value_out is not None and result.value_out-result.value_in > 0:
                st.info(f"과다변제 산식: 실제 지급액 {view.won(tx.get('consideration_paid'))} − 실제 채무 감소액 {view.won(tx.get('liability_reduction'))} → 초과분 {result.value_out-result.value_in:,.0f}원. 분석 결과의 Value In=0은 채무 감소가 없었다는 뜻이 아닙니다.")
            if result.lookback_months:
                st.write(f"적용 검토 조문: 채무자회생법 제391조 제4호 · 소급기간 {result.lookback_months}개월 ({view.lookback_basis(result, rule)})")
            st.markdown("##### ⚠ 확인 필요")
            st.markdown("\n".join(f"- {t}" for t in view.verification_items(tx, rule)))
        if result.flags:
            st.markdown("##### 참고 사항")
            st.markdown("\n".join(f"- {f}" for f in view.flag_labels(result.flags)))

        st.markdown("##### 입력 사실과 출처")
        facts = view.facts_table(tx)
        # 기본 높이는 10행까지라 비고·출처가 표 안 스크롤에 숨는다. 행 수만큼 펼친다(행 35px).
        st.dataframe(facts, hide_index=True, width="stretch", height=(len(facts) + 1) * 35 + 3)
        st.markdown("##### 관련 당사자")
        parties = view.party_rows(ds, tx)
        if parties:
            st.dataframe(parties, hide_index=True, width="stretch")
        else:
            st.caption("등재된 당사자가 없습니다.")
        links = view.tx_link_rows(ds, selected)
        if links:
            st.markdown("##### 연결된 거래")
            st.dataframe(links, hide_index=True, width="stretch")
        st.caption("본 화면은 조사 대상 선별 결과이며, 부인권 성립 여부에 관한 법률적 판단이 아닙니다. 최종 판단은 파산관재인에게 있습니다.")
    with st.expander("원본 데이터 보기 (감사·검증용)"):
        st.json({"입력 행": tx, "엔진 결과": asdict(result)})
st.download_button("전체 사건 결과·입력 근거 저장 (JSON)", export_json(ds, rules, results, current),
                   file_name="trustee-fds-review.json", mime="application/json")
