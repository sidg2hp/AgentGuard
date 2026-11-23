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


def test_permission_only_model_profile_runs_without_ollama(monkeypatch):
    def unexpected_connection():
        raise AssertionError("Permission probes do not use the model")

    monkeypatch.setattr("agentguard.providers.installed_models", unexpected_connection)
    monkeypatch.setattr("evaluation.runner.installed_models", unexpected_connection)
    app = AppTest.from_file("../app.py", default_timeout=30).run()
    app.sidebar.radio[0].set_value("Evaluation").run()
    next(box for box in app.selectbox if box.label == "Scenario").set_value("PER-01").run()
    app.multiselect[0].set_value(["guarded-ollama"]).run()
    app.button[0].click().run()
    assert not app.exception
    report = app.session_state["benchmark"]
    assert report["model"] is None
    assert len(report["runs"]) == 1 and report["runs"][0]["passed"]
