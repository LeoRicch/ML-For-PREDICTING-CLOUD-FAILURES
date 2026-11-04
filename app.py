"""Local-only Flask application. Start with START_APP.bat or python app.py."""
from datetime import datetime, timezone
import csv
import io
import json
import os
from pathlib import Path
import threading
import webbrowser
from flask import Flask, jsonify, request, render_template, Response
from cloudlab.simulator import Config, Simulator, FEATURES
from cloudlab.session import Session
from cloudlab.storage import Store
from cloudlab.ml import latest_model
from cloudlab.training_lock import active as training_active

ROOT = Path(__file__).resolve().parent


def create_app(root=ROOT):
    root = Path(root)
    app = Flask(__name__,template_folder=str(ROOT/"templates"),static_folder=str(ROOT/"static"))
    app.config["MAX_CONTENT_LENGTH"] = 16384
    store = Store(root/"data"/"cloudlab.sqlite3")
    sessions, lock = {}, threading.RLock()
    cached = {"model":None, "version":None, "error":None}
    training_thread = {"thread":None}

    def load_model():
        pointer = root/"models"/"current.json"
        if not pointer.exists():
            return None
        try:
            version = json.loads(pointer.read_text(encoding="utf-8"))["version"]
            if version != cached["version"]:
                cached["model"] = latest_model(root)
                cached["version"] = version
                with store.connect() as db:
                    db.execute("INSERT OR REPLACE INTO models VALUES(?,?)",(version,json.dumps(cached["model"].metadata)))
            cached["error"] = None
            return cached["model"]
        except (OSError,ValueError,KeyError,EOFError) as exc:
            cached["error"] = f"The saved model could not be loaded: {exc}"
            return None

    @app.before_request
    def local_only():
        if request.host.split(":")[0] not in ("127.0.0.1","localhost","[::1]"):
            return jsonify(error="This application accepts localhost requests only."),403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and origin not in (f"http://{request.host}",):
                return jsonify(error="Open this application directly on localhost."),403
            if not request.is_json:
                return jsonify(error="A JSON request is required."),415

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.errorhandler(ValueError)
    def bad_input(exc):
        return jsonify(error=str(exc)),400

    @app.errorhandler(KeyError)
    def not_found(exc):
        return jsonify(error=str(exc).strip("'")),404

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        model = load_model()
        status_path = root/"data"/"training_status.json"
        progress = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else None
        thread = training_thread["thread"]
        # A stopped process must not leave a permanent 'training' indicator.
        active = bool(thread and thread.is_alive()) or training_active(root)
        interrupted = progress and progress["stage"] not in ("complete","failed") and not active
        return jsonify(app_id="sarah-cloudlab-v1",model=model.metadata if model else None,error=cached["error"],
            training=progress,training_active=active,training_interrupted=bool(interrupted))

    @app.post("/api/runs")
    def start():
        body = request.get_json()
        if not isinstance(body,dict) or set(body)-set(Config.__dataclass_fields__):
            raise ValueError("Invalid simulation settings.")
        body.setdefault("auto_retry",True)
        config = Config(**body)
        with lock:
            if len(sessions) >= 12:
                # Old experiments remain safely stored and replayable.
                oldest = next(iter(sessions))
                del sessions[oldest]
            session = Session(Simulator(config),load_model(),store)
            sessions[session.id] = session
            return jsonify(session.snapshot())

    @app.post("/api/runs/<run_id>/step")
    def step(run_id):
        body = request.get_json()
        if not isinstance(body,dict) or type(body.get("expected_tick")) is not int:
            raise ValueError("The current simulated minute is required.")
        with lock:
            if run_id not in sessions:
                raise ValueError("This is a saved experiment. Choose Repeat settings to start it again.")
            session = sessions[run_id]
            if body["expected_tick"] != session.engine.tick:
                return jsonify(error="The run has already advanced. Refresh its state before stepping again.",snapshot=session.snapshot()),409
            return jsonify(session.step())

    @app.get("/api/runs/<run_id>")
    def current(run_id):
        with lock:
            if run_id in sessions:
                return jsonify(sessions[run_id].snapshot())
            snapshot = store.snapshot(run_id)
            snapshot["session_live"] = False
            return jsonify(snapshot)

    @app.get("/api/runs/<run_id>/snapshot/<int:tick>")
    def replay(run_id,tick):
        snapshot = store.snapshot(run_id,tick)
        snapshot["session_live"] = False
        snapshot["replay"] = True
        return jsonify(snapshot)

    @app.post("/api/runs/<run_id>/alerts/<int:episode_id>/acknowledge")
    def acknowledge(run_id,episode_id):
        with lock:
            if run_id not in sessions:
                raise ValueError("Saved warnings can be reviewed in the experiment history.")
            session = sessions[run_id]
            alert = next((a for a in session.alerts if a["id"]==episode_id),None)
            if alert is None:
                raise KeyError("Warning not found.")
            alert["acknowledged"] = True
            snapshot = session.snapshot()
            store.save(session,snapshot,[],[])
            return jsonify(snapshot)

    @app.get("/api/history")
    def history():
        return jsonify(runs=store.history())

    @app.get("/api/runs/<run_id>/export.csv")
    def export(run_id):
        run = store.get_run(run_id)
        rows = store.export_rows(run_id)
        out = io.StringIO(newline="")
        fields = ["data_source","generator_version","run_id","seed","scenario","model_version","task_id","server_id","tick",*FEATURES,"risk_score","predicted_failure","target"]
        writer = csv.DictWriter(out,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(data_source="synthetic",generator_version=run["manifest"]["generator_version"],run_id=run_id,
                seed=run["config"]["seed"],scenario=run["config"]["scenario"],**row))
        return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":f'attachment; filename="cloud-run-{run_id}.csv"'})

    @app.get("/api/runs/<run_id>/report.json")
    def report(run_id):
        run = store.get_run(run_id)
        snapshot = store.snapshot(run_id)
        return Response(json.dumps(dict(run=run,snapshot=snapshot,
            scope="Synthetic local experiment; not evidence of real-cloud predictive accuracy."),indent=2),
            mimetype="application/json",headers={"Content-Disposition":f'attachment; filename="cloud-report-{run_id}.json"'})

    @app.post("/api/model/train")
    def prepare_model():
        with lock:
            thread = training_thread["thread"]
            if (thread and thread.is_alive()) or training_active(root):
                raise ValueError("Model preparation is already running.")
            def work():
                from train import train
                try:
                    train(root=root,runs_per_load=200)
                except Exception:
                    app.logger.exception("Model preparation failed")
            thread = threading.Thread(target=work,daemon=True,name="cloudlab-training")
            training_thread["thread"] = thread
            thread.start()
        return jsonify(message="Generating 600 independent simulation runs and training the model."),202

    app.store = store
    app.sessions = sessions
    return app


if __name__ == "__main__":
    port = int(os.environ.get("CLOUDLAB_PORT","5057"))
    # Reopening the launcher should bring up the existing lab rather than fail on its port.
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",timeout=2) as response:
            existing = json.load(response)
        if existing.get("app_id") == "sarah-cloudlab-v1":
            if "--no-browser" not in __import__("sys").argv:
                webbrowser.open(f"http://127.0.0.1:{port}")
            print(f"Cloud Operations Lab is already running at http://127.0.0.1:{port}")
            raise SystemExit(0)
    except (OSError,ValueError):
        pass
    app = create_app()
    if "--no-browser" not in __import__("sys").argv:
        threading.Timer(1.5,lambda:webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1",port=port,debug=False,threaded=True,use_reloader=False)
