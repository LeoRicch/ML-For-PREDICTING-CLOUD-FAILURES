import csv
import io
import json
import pytest
from app import create_app
from cloudlab.session import Session
from cloudlab.storage import Store
from cloudlab.simulator import Config, Simulator, FEATURES


class WarnEverything:
    version = "test-model"
    threshold = 0.5
    def predict(self, rows):
        assert all("target" not in r and "failure_tick" not in r for r in rows)
        return [.9]*len(rows)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path)
    app.testing = True
    return app.test_client()


def test_offline_page_assets_and_no_model_path(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/cloud.png").status_code == 200
    assert client.get("/api/status").json["model"] is None
    run = client.post("/api/runs",json={"duration":30}).json
    assert run["model_version"] is None
    snapshot = client.post(f'/api/runs/{run["id"]}/step',json={"expected_tick":0}).json
    assert snapshot["tick"] == 1
    assert all(t["risk"] is None for t in snapshot["tasks"])


def test_invalid_input_and_duplicate_step_do_not_advance(client):
    assert client.post("/api/runs",json={"duration":1}).status_code == 400
    assert client.post("/api/runs",json={"unexpected":42}).status_code == 400
    run = client.post("/api/runs",json={}).json
    url = f'/api/runs/{run["id"]}/step'
    assert client.post(url,json={"expected_tick":0}).status_code == 200
    assert client.post(url,json={"expected_tick":0}).status_code == 409
    assert client.get(f'/api/runs/{run["id"]}').json["tick"] == 1


def test_persistence_export_and_replay_do_not_reveal_future(client,tmp_path):
    run = client.post("/api/runs",json={"scenario":"high","duration":30}).json
    rid = run["id"]
    for tick in range(30):
        assert client.post(f"/api/runs/{rid}/step",json={"expected_tick":tick}).status_code == 200
    old = client.get(f"/api/runs/{rid}/snapshot/1").json
    assert old["tick"] == 1
    assert not any(t["failure_tick"] for t in old["tasks"])
    assert old["replay"] is True
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{rid}/export.csv").text)))
    assert rows and all(r["data_source"]=="synthetic" for r in rows)
    assert any(r["target"]=="1" for r in rows)
    assert any(r["target"]=="" for r in rows)  # Right-censored windows are not fake successes.
    assert client.get(f"/api/runs/{rid}/report.json").json["run"]["config"]["seed"]==42
    restarted = create_app(tmp_path).test_client()
    saved = restarted.get(f"/api/runs/{rid}").json
    assert saved["tick"] == 30 and saved["session_live"] is False


def test_warning_before_completion_is_saved_as_false_alarm(tmp_path):
    store = Store(tmp_path/"test.sqlite3")
    engine = Simulator(Config(scenario="low",duration=30,seed=71))
    session = Session(engine,WarnEverything(),store)
    for _ in range(30):
        session.step()
    completed_ids = {t["id"] for t in engine.tasks if t["status"]=="completed"}
    assert completed_ids
    assert any(a["task_id"] in completed_ids and a["status"]=="false_alarm" for a in session.alerts)
    assert any(p["target"] == 0 and p["predicted"]==1 for p in session.predictions)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM predictions WHERE target=0 AND predicted=1").fetchone()[0]>0
    assert len(session.alerts) <= len(engine.tasks)  # Continuous warnings are grouped.


def test_local_only_and_cross_origin_write_protection(client):
    assert client.get("/api/status",headers={"Host":"evil.example"}).status_code == 403
    assert client.post("/api/runs",json={},headers={"Origin":"https://evil.example"}).status_code == 403
    assert client.post("/api/runs",data="{}").status_code == 415
