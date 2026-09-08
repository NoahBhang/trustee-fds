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
    app.sidebar.button[1].click().run()
    assert not app.metric


def test_changed_input_clears_previous_results():
    app = AppTest.from_file(str(ROOT / 'streamlit_app.py'), default_timeout=15).run()
    app.sidebar.button[0].click().run()
    app.sidebar.radio[0].set_value('CSV 업로드').run()
    assert not app.exception
    assert not app.metric
    assert app.sidebar.button[0].disabled
