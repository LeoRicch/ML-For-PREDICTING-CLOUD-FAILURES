"""Audit the generated artifacts when a saved model exists."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from cloudlab.ml import latest_model
from cloudlab.simulator import FEATURES,Config,Simulator
from cloudlab.session import Session
from cloudlab.storage import Store

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT/"models"/"current.json").exists(),reason="Prepare a model before auditing trained artifacts")


def test_independent_partitions_and_future_window_labels_are_auditable():
    model = latest_model(ROOT)
    directory = ROOT/"models"/model.version
    manifests = json.loads((directory/"run_manifest.json").read_text())
    partitions = {p:{m["run_id"] for m in manifests if m["partition"]==p} for p in ("train","validation","test")}
    assert not partitions["train"] & partitions["validation"]
    assert not partitions["train"] & partitions["test"]
    assert not partitions["validation"] & partitions["test"]
    frame = pd.read_csv(directory/"dataset.csv.gz")
    tasks = pd.read_csv(directory/"tasks.csv.gz")
    assert len(frame) == model.metadata["dataset_rows"]
    assert not frame.duplicated(["run_id","task_id","tick"]).any()
    merged = frame.merge(tasks[["run_id","id","finish_tick","failure_tick"]],left_on=["run_id","task_id"],right_on=["run_id","id"],validate="many_to_one")
    positive = (merged.failure_tick>merged.tick)&(merged.failure_tick<=merged.tick+5)
    assert np.array_equal(positive.to_numpy().astype(int),merged.target.to_numpy())
    resolved_early = (merged.finish_tick>merged.tick)&(merged.finish_tick<=merged.tick+5)
    assert ((merged.tick+5<=120)|positive|resolved_early).all()
    assert model.metadata["features"] == FEATURES


def test_saved_final_scores_match_recorded_predictions():
    model = latest_model(ROOT)
    frame = pd.read_csv(ROOT/"models"/model.version/"test_predictions.csv.gz")
    threshold = model.threshold
    assert np.array_equal((frame.risk_score.to_numpy()>=threshold).astype(int),frame.predicted_failure.to_numpy())
    test = model.metadata["test"]
    assert int(((frame.target==1)&(frame.predicted_failure==0)).sum()) == test["false_negatives"]
    assert int(((frame.target==0)&(frame.predicted_failure==1)).sum()) == test["false_positives"]
    sample = frame.sample(min(50,len(frame)),random_state=47)
    risks = model.predict(sample.to_dict("records"))
    assert np.allclose(risks,sample.risk_score,atol=1e-6)


def test_real_model_scores_live_tasks_and_persists_outcomes(tmp_path):
    model = latest_model(ROOT)
    session = Session(Simulator(Config(scenario="high",duration=60,seed=77)),model,Store(tmp_path/"live.sqlite3"))
    for _ in range(60):
        snapshot = session.step()
    assert snapshot["model_version"] == model.version
    assert session.predictions and session.alerts
    assert any(p["target"] is not None for p in session.predictions)
    assert any(a["status"]=="confirmed" for a in session.alerts)
    rows = session.store.export_rows(session.id)
    assert any(r["risk_score"] is not None and r["target"]==1 for r in rows)
