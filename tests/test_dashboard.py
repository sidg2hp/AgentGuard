from streamlit.testing.v1 import AppTest


def test_offline_research_and_paired_dashboard():
    app = AppTest.from_file("../app.py", default_timeout=30).run()
    app.text_input[0].set_value("What do research agents do?")
    app.button[0].click().run()
    assert not app.exception
    assert any("[S1]" in t.value for t in app.text)
    app.sidebar.radio[0].set_value("Evaluation").run()
    app.button[0].click().run()
    assert not app.exception
    report = app.session_state["benchmark"]
    assert len(report["runs"]) == 60
    assert any(t.label == "Expected status" for t in app.metric)
