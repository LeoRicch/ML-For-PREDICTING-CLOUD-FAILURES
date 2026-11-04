"""SQLite persistence for experiments, raw observations, predictions and alerts."""
import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS simulation_runs(id TEXT PRIMARY KEY, created TEXT NOT NULL,
                config TEXT NOT NULL, manifest TEXT NOT NULL, model_version TEXT, tick INTEGER NOT NULL,
                duration INTEGER NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS virtual_servers(run_id TEXT, server_id INTEGER, payload TEXT,
                PRIMARY KEY(run_id,server_id));
            CREATE TABLE IF NOT EXISTS tasks(run_id TEXT, task_id INTEGER, payload TEXT,
                PRIMARY KEY(run_id,task_id));
            CREATE TABLE IF NOT EXISTS observations(run_id TEXT, task_id INTEGER, tick INTEGER,
                features TEXT, target INTEGER, PRIMARY KEY(run_id,task_id,tick));
            CREATE TABLE IF NOT EXISTS predictions(run_id TEXT, task_id INTEGER, tick INTEGER,
                model_version TEXT, risk REAL, predicted INTEGER, target INTEGER,
                PRIMARY KEY(run_id,task_id,tick));
            CREATE TABLE IF NOT EXISTS alerts(run_id TEXT, episode_id INTEGER, payload TEXT,
                PRIMARY KEY(run_id,episode_id));
            CREATE TABLE IF NOT EXISTS models(version TEXT PRIMARY KEY, payload TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(run_id TEXT, tick INTEGER, payload TEXT,
                PRIMARY KEY(run_id,tick));
            CREATE INDEX IF NOT EXISTS obs_run ON observations(run_id,tick);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def create_run(self, run_id, engine, created, model_version):
        with self.connect() as db:
            db.execute("INSERT INTO simulation_runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, created, json.dumps(engine.manifest()["config"]), json.dumps(engine.manifest()),
                 model_version, 0, engine.limit, "paused"))

    def save(self, session, snapshot, new_rows, refreshed):
        run_id, engine = session.id, session.engine
        with self.connect() as db:
            db.execute("UPDATE simulation_runs SET tick=?,status=? WHERE id=?",
                (engine.tick, "finished" if engine.finished else "paused", run_id))
            db.executemany("INSERT OR REPLACE INTO virtual_servers VALUES(?,?,?)",
                [(run_id,s["id"],json.dumps(s)) for s in engine.servers])
            db.executemany("INSERT OR REPLACE INTO tasks VALUES(?,?,?)",
                [(run_id,t["id"],json.dumps(t)) for t in engine.tasks])
            db.executemany("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)",
                [(run_id,r["task_id"],r["tick"],json.dumps(r),None) for r in new_rows])
            db.executemany("UPDATE observations SET target=? WHERE run_id=? AND task_id=? AND tick=?",
                [(r["target"],run_id,r["task_id"],r["tick"]) for r in refreshed])
            db.executemany("INSERT OR REPLACE INTO predictions VALUES(?,?,?,?,?,?,?)",
                [(run_id,p["task_id"],p["tick"],session.version,p["risk"],p["predicted"],p["target"])
                 for p in session.predictions if p["tick"] == engine.tick])
            db.executemany("UPDATE predictions SET target=? WHERE run_id=? AND task_id=? AND tick=?",
                [(r["target"],run_id,r["task_id"],r["tick"]) for r in refreshed])
            db.executemany("INSERT OR REPLACE INTO alerts VALUES(?,?,?)",
                [(run_id,a["id"],json.dumps(a)) for a in session.alerts])
            db.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?)",(run_id,engine.tick,json.dumps(snapshot)))

    def history(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM simulation_runs ORDER BY created DESC LIMIT 100").fetchall()
        return [{**dict(r), "config":json.loads(r["config"]), "manifest":json.loads(r["manifest"])} for r in rows]

    def get_run(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM simulation_runs WHERE id=?",(run_id,)).fetchone()
        if row is None:
            raise KeyError("This experiment was not found.")
        return {**dict(row), "config":json.loads(row["config"]), "manifest":json.loads(row["manifest"])}

    def snapshot(self, run_id, tick=None):
        with self.connect() as db:
            if tick is None:
                row = db.execute("SELECT payload FROM snapshots WHERE run_id=? ORDER BY tick DESC LIMIT 1",(run_id,)).fetchone()
            else:
                row = db.execute("SELECT payload FROM snapshots WHERE run_id=? AND tick=?",(run_id,tick)).fetchone()
        if row is None:
            raise KeyError("This saved minute was not found.")
        return json.loads(row["payload"])

    def export_rows(self, run_id):
        self.get_run(run_id)
        with self.connect() as db:
            rows = db.execute('''SELECT o.features,o.target,p.model_version,p.risk,p.predicted
                FROM observations o LEFT JOIN predictions p
                ON o.run_id=p.run_id AND o.task_id=p.task_id AND o.tick=p.tick
                WHERE o.run_id=? ORDER BY o.tick,o.task_id''',(run_id,)).fetchall()
        return [{**json.loads(r["features"]), "target":r["target"], "model_version":r["model_version"],
                 "risk_score":r["risk"], "predicted_failure":r["predicted"]} for r in rows]
