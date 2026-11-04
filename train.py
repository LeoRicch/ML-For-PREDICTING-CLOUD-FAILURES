"""Generate independent runs, select RF on validation only, evaluate final test once."""
import os
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import random
import threading
import time
import csv
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score, f1_score
from cloudlab.simulator import Config, Simulator, FEATURES, GENERATOR_VERSION
from cloudlab.training_lock import acquire, release

ROOT = Path(__file__).resolve().parent
TRAIN_LOCK = threading.Lock()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def classification(y, predicted):
    cm = confusion_matrix(y, predicted, labels=[0, 1]).tolist()
    tn, fp = cm[0]
    fn, tp = cm[1]
    return dict(observations=len(y), accuracy=float(accuracy_score(y,predicted)),
        precision=float(precision_score(y,predicted,zero_division=0)) if tp+fp else None,
        recall=float(recall_score(y,predicted,zero_division=0)) if tp+fn else None,
        f1=float(f1_score(y,predicted,zero_division=0)) if 2*tp+fp+fn else None,
        confusion_matrix=cm, true_negatives=tn, false_positives=fp, false_negatives=fn, true_positives=tp)


def warning_metrics(frame, risks, threshold, task_outcomes):
    work = frame[["run_id","task_id","tick","target"]].copy()
    work["warn"] = np.asarray(risks) >= threshold
    warned = work[work.warn].sort_values(["run_id","task_id","tick"])
    by_task = {(rid,int(tid)): g for (rid,tid), g in warned.groupby(["run_id","task_id"])}
    leads = []
    failed = [t for t in task_outcomes if t["failure_tick"] is not None]
    for task in failed:
        group = by_task.get((task["run_id"],task["id"]))
        if group is None:
            continue
        eligible = group[(group.tick < task["failure_tick"]) & (group.tick >= task["failure_tick"]-5)]
        if not eligible.empty:
            leads.append(int(task["failure_tick"]-eligible.tick.min()))
    episodes = false_episodes = 0
    for _, group in by_task.items():
        episode = []
        for row in group.itertuples():
            if episode and row.tick != episode[-1].tick+1:
                episodes += 1
                false_episodes += not any(r.target == 1 for r in episode)
                episode = []
            episode.append(row)
        if episode:
            episodes += 1
            false_episodes += not any(r.target == 1 for r in episode)
    run_count = int(frame.run_id.nunique())
    return dict(failed_tasks=len(failed), warned_failed_tasks=len(leads), missed_failed_tasks=len(failed)-len(leads),
        task_failure_recall=len(leads)/len(failed) if failed else None,
        median_warning_minutes=float(np.median(leads)) if leads else None,
        mean_warning_minutes=float(np.mean(leads)) if leads else None,
        alert_episodes=episodes, false_alert_episodes=int(false_episodes),
        false_alert_episodes_per_run=false_episodes/run_count if run_count else None,
        note="Alert episodes here use evaluable observation windows only; right-censored windows are excluded.")


def train(root=ROOT, runs_per_load=200, progress=None):
    if not TRAIN_LOCK.acquire(blocking=False):
        raise ValueError("Model preparation is already running.")
    root = Path(root)
    process_lock = acquire(root)
    if process_lock is None:
        TRAIN_LOCK.release()
        raise ValueError("Another process is already preparing a model.")
    start = time.monotonic()
    version = datetime.now(timezone.utc).strftime("rf-%Y%m%dT%H%M%S%fZ")
    directory = root / "models" / version
    directory.mkdir(parents=True, exist_ok=False)
    def report(stage, percent, message):
        value = dict(stage=stage, percent=percent, message=message, version=version)
        atomic_json(root/"data"/"training_status.json", value)
        if progress:
            progress(value)
    try:
        if runs_per_load < 10 or runs_per_load % 5:
            raise ValueError("Runs per load must be a multiple of 5 and at least 10.")
        report("generating",0,"Generating independent synthetic experiments")
        manifests, records, outcomes = [], [], []
        rng = random.Random(20261104)
        # Stratified complete-run partitions, assigned before generation.
        for level, scenario in enumerate(("low","medium","high")):
            seeds = list(range(100000+level*100000,100000+level*100000+runs_per_load))
            rng.shuffle(seeds)
            for i, seed in enumerate(seeds):
                partition = "train" if i < runs_per_load*.6 else "validation" if i < runs_per_load*.8 else "test"
                run_id = f"{scenario}-{seed}"
                config = Config(scenario=scenario, seed=seed, capacity_reduction=(i%3==0))
                engine = Simulator(config).run()
                manifests.append(dict(run_id=run_id, partition=partition, **engine.manifest()))
                for row in engine.observations:
                    target = engine.target(row)
                    if target is not None:
                        records.append(dict(run_id=run_id,scenario=scenario,partition=partition,**row,target=target))
                outcomes.extend(dict(run_id=run_id, partition=partition, **t) for t in engine.tasks)
                done = len(manifests)
                if done%20 == 0:
                    report("generating",round(25*done/(3*runs_per_load)),f"Generated {done}/{3*runs_per_load} runs")
        frame = pd.DataFrame.from_records(records)
        del records
        if frame.duplicated(["run_id","task_id","tick"]).any():
            raise ValueError("Duplicate observations were generated.")
        if not np.isfinite(frame[FEATURES].to_numpy()).all():
            raise ValueError("Invalid resource observations were generated.")
        frame.to_csv(directory/"dataset.csv.gz",index=False,compression="gzip")
        pd.DataFrame(outcomes).to_csv(directory/"tasks.csv.gz",index=False,compression="gzip")
        atomic_json(directory/"run_manifest.json",manifests)
        subsets = {p:frame[frame.partition == p] for p in ("train","validation","test")}
        run_sets = {p:set(f.run_id) for p,f in subsets.items()}
        assert not (run_sets["train"] & run_sets["validation"] or run_sets["train"] & run_sets["test"] or run_sets["validation"] & run_sets["test"])
        xs = {p:f[FEATURES].to_numpy(dtype=np.float32) for p,f in subsets.items()}
        ys = {p:f.target.to_numpy(dtype=np.int8) for p,f in subsets.items()}
        if any(len(set(y)) < 2 for y in ys.values()):
            raise ValueError("Both outcomes must appear in every data partition.")
        best, best_key, trials = None, (-1,-1), []
        for trees in (100,200):
            for depth in (10,None):
                for leaf in (1,5):
                    params = dict(n_estimators=trees,max_depth=depth,min_samples_leaf=leaf,
                                  class_weight="balanced_subsample",random_state=42,n_jobs=min(3,os.cpu_count() or 1))
                    report("training",30+len(trials)*5,f"Training candidate {len(trials)+1}/8")
                    estimator = RandomForestClassifier(**params).fit(xs["train"],ys["train"])
                    risks = estimator.predict_proba(xs["validation"])[:,list(estimator.classes_).index(1)]
                    for threshold in (.3,.4,.5,.6,.7):
                        metrics = classification(ys["validation"],risks >= threshold)
                        key = (metrics["f1"] or 0, metrics["recall"] or 0)
                        if key > best_key:
                            best_key = key
                            best = (estimator,threshold,params,metrics)
                    trials.append(dict(parameters=params,validation_f1_at_05=classification(ys["validation"],risks>=.5)["f1"]))
        estimator, threshold, params, validation_metrics = best
        # Checkpoint before report generation; no final-test information is used here.
        joblib.dump(estimator,directory/"selected_candidate.joblib",compress=3)
        atomic_json(directory/"selection.json",dict(parameters=params,threshold=threshold,
            validation=validation_metrics,candidate_trials=trials))
        report("learning_curve",73,"Checking whether more independent training runs improve validation")
        curves = []
        ordered_train = sorted(run_sets["train"])
        random.Random(2026).shuffle(ordered_train)
        for fraction in (.25,.5,1.0):
            if fraction == 1:
                model = estimator
                selected = subsets["train"]
            else:
                selected_ids = set()
                for scenario in ("low","medium","high"):
                    ids = [rid for rid in ordered_train if rid.startswith(scenario+"-")]
                    selected_ids.update(ids[:max(1,int(len(ids)*fraction))])
                selected = subsets["train"][subsets["train"].run_id.isin(selected_ids)]
                model = RandomForestClassifier(**params).fit(selected[FEATURES].to_numpy(dtype=np.float32),selected.target)
            risk = model.predict_proba(xs["validation"])[:,list(model.classes_).index(1)]
            curves.append(dict(training_runs=int(selected.run_id.nunique()), training_observations=len(selected),
                               **classification(ys["validation"],risk>=threshold)))
        # The test partition is scored only now, after parameters and threshold are frozen.
        report("evaluating",85,"Evaluating the frozen model on untouched test runs")
        test_risk = estimator.predict_proba(xs["test"])[:,list(estimator.classes_).index(1)]
        test_pred = test_risk >= threshold
        test = classification(ys["test"],test_pred)
        test.update(warning_metrics(subsets["test"],test_risk,threshold,[t for t in outcomes if t["partition"]=="test"]))
        majority_class = Counter(ys["train"].tolist()).most_common(1)[0][0]
        baseline = classification(ys["test"],np.full(len(ys["test"]),majority_class))
        shortfall_baseline = classification(ys["test"],subsets["test"].shortfall_streak.to_numpy()>=2)
        per_scenario = {}
        for scenario in ("low","medium","high"):
            mask = subsets["test"].scenario.to_numpy()==scenario
            per_scenario[scenario] = classification(ys["test"][mask],test_pred[mask])
        # Separate out-of-range stress experiments; never blended into final test results.
        stress_records, stress_tasks = [], []
        for i in range(30):
            eng = Simulator(Config(scenario="high",seed=900000+i,cpu_capacity=50,memory_capacity=50,capacity_reduction=True)).run()
            for row in eng.observations:
                target = eng.target(row)
                if target is not None:
                    stress_records.append(dict(run_id=f"stress-{i}",**row,target=target))
            stress_tasks.extend(dict(run_id=f"stress-{i}",**t) for t in eng.tasks)
        stress = pd.DataFrame(stress_records)
        stress_risk = estimator.predict_proba(stress[FEATURES].to_numpy(dtype=np.float32))[:,list(estimator.classes_).index(1)]
        stress_report = classification(stress.target,stress_risk>=threshold)
        stress_report.update(warning_metrics(stress,stress_risk,threshold,stress_tasks))
        metadata = dict(version=version,created=datetime.now(timezone.utc).isoformat(),generator_version=GENERATOR_VERSION,
            scope="Synthetic resource-related task failures only; real-cloud accuracy unverified.",
            dataset_runs=len(manifests),dataset_rows=len(frame),horizon=5,features=FEATURES,
            partition_counts={p:dict(runs=len(run_sets[p]),rows=len(subsets[p]),
                nonfailure=int((ys[p]==0).sum()),failure=int((ys[p]==1).sum())) for p in subsets},
            parameters=params,threshold=threshold,validation=validation_metrics,test=test,
            majority_baseline=baseline,shortfall_baseline=shortfall_baseline,per_scenario=per_scenario,
            stress=stress_report,stress_runs=30,learning_curve=curves,candidate_trials=trials,
            feature_importance=dict(zip(FEATURES,[float(x) for x in estimator.feature_importances_])),
            training_seconds=round(time.monotonic()-start,2),
            methodology_change="600 default independent runs replace the chapter's planned 150; same 60:20:20 grouping.")
        joblib.dump(estimator,directory/"model.joblib",compress=3)
        atomic_json(directory/"metrics.json",metadata)
        # Export measured final predictions for an auditable report.
        scored = subsets["test"].copy()
        scored["risk_score"],scored["predicted_failure"] = test_risk,test_pred.astype(int)
        scored.to_csv(directory/"test_predictions.csv.gz",index=False,compression="gzip")
        os.environ.setdefault("MPLCONFIGDIR",str(root/"work"/"matplotlib"))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import ConfusionMatrixDisplay
        fig, ax = plt.subplots(figsize=(5,4))
        ConfusionMatrixDisplay(np.asarray(test["confusion_matrix"]),display_labels=["No failure","Failure"]).plot(ax=ax,cmap="Purples",colorbar=False)
        ax.set_title("Held-out simulated runs")
        fig.tight_layout(); fig.savefig(directory/"confusion_matrix.png",dpi=160); plt.close(fig)
        atomic_json(root/"models"/"current.json",dict(version=version))
        report("complete",100,f"Model ready: {len(manifests)} runs and {len(frame):,} labelled observations")
        return metadata
    except Exception as exc:
        report("failed",0,str(exc))
        raise
    finally:
        release(process_lock)
        TRAIN_LOCK.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-per-load",type=int,default=200)
    args = parser.parse_args()
    result = train(runs_per_load=args.runs_per_load,progress=lambda p:print(p["message"],flush=True))
    print(json.dumps({"version":result["version"],"rows":result["dataset_rows"],"test":result["test"]},indent=2))
