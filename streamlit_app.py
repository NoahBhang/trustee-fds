"""Local review workspace for the trustee-fds engine."""
import csv
from dataclasses import asdict

import streamlit as st

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
        st.caption("UTF-8 CSV를 사용하세요. 열 이름은 샘플과 같아야 합니다.")
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

def rows(items):
    return [{"거래": r.tx_id, "사건": txs[r.tx_id]["case_id"], "우선순위": r.priority,
             "순출연 추정액": None if r.value_out is None else r.value_out-r.value_in,
             "대가비율": r.ratio, "신호": ", ".join(r.signals), "플래그": ", ".join(r.flags),
             "제외 사유": r.drop_reason} for r in items]

for title, items in [("평가 필요", unvalued), ("평가 완료 — 순출연 추정액 순", valued),
                     ("후보 제외 거래", [r for r in visible if not r.candidate])]:
    st.subheader(title)
    if items:
        st.dataframe(rows(items), hide_index=True, use_container_width=True)
    else:
        st.caption("해당 항목이 없습니다.")

if visible:
    selected = st.selectbox("거래 상세", [r.tx_id for r in visible])
    result = next(r for r in visible if r.tx_id == selected)
    tx = txs[selected]
    st.write("입력 사실 및 출처")
    st.json(tx)
    if "debt_repayment_excess_isolated" in result.flags and result.value_out is not None and result.value_out-result.value_in > 0:
        st.info(f"과다변제 산식: 실제 지급액 {tx.get('consideration_paid')}원 − 실제 채무 감소액 {tx.get('liability_reduction')}원 → 초과분 {result.value_out-result.value_in:,.0f}원. 분석 결과의 Value In=0은 채무 감소가 없었다는 뜻이 아닙니다.")
    st.write("엔진 결과 — 신호·미확인 사항·제외 사유")
    st.json(asdict(result))
    with st.expander("연결 당사자와 거래 링크"):
        st.json({"당사자": [dict(link, facts=ds.parties.get((tx['case_id'], link['party_id']))) for link in ds.links if link['transaction_id'] == selected],
                 "거래 링크": [link for link in ds.tx_links if selected in (link['from_transaction_id'], link['to_transaction_id'])]})
st.download_button("전체 사건 결과·입력 근거 저장 (JSON)", export_json(ds, rules, results, current),
                   file_name="trustee-fds-review.json", mime="application/json")
