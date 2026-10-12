import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest
from src.ui_service import ROOT


def test_sample_analysis_rerun_and_clear():
    app = AppTest.from_file(str(ROOT / 'streamlit_app.py'), default_timeout=15).run()
    assert not app.exception
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert [m.value for m in app.metric][:3] == ['34', '20', '3']
    app.run()
    assert not app.exception
    assert len(app.session_state['analysis']['results']) == 34
    # 거래 상세는 JSON이 아니라 문장·표로 보이고, 원본 JSON은 접힌 메뉴 안에만 있다.
    assert [e.label for e in app.expander] == ["원본 데이터 보기 (감사·검증용)"]
    assert "지급정지일(2025-08-20) 기준 23일 이후에 이루어진 매각입니다." in [m.value for m in app.markdown]
    app.sidebar.button[1].click().run()
    assert not app.metric


def test_changed_input_clears_previous_results():
    app = AppTest.from_file(str(ROOT / 'streamlit_app.py'), default_timeout=15).run()
    app.sidebar.button[0].click().run()
    app.sidebar.radio[0].set_value('CSV 업로드').run()
    assert not app.exception
    assert not app.metric
    assert app.sidebar.button[0].disabled
