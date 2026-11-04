from datetime import datetime, timezone
import uuid
from .simulator import FEATURES, HORIZON


class Session:
    def __init__(self, engine, model, store):
        self.id = uuid.uuid4().hex[:16]
        self.engine, self.model, self.store = engine, model, store
        self.version = model.version if model else None
        self.threshold = model.threshold if model else None
        self.predictions, self.alerts, self.pending = [], [], []
        self.last_warning = {}
        self.current = {}
        self.created = datetime.now(timezone.utc).isoformat()
        store.create_run(self.id,engine,self.created,self.version)
        store.save(self,self.snapshot(),[],[])

    def step(self):
        if self.engine.finished:
            return self.snapshot()
        rows = self.engine.step()
        risks = self.model.predict(rows) if self.model else [None]*len(rows)
        self.current = {}
        for row,risk in zip(rows,risks):
            observation = dict(row, target=None)
            self.pending.append(observation)
            if risk is None:
                continue
            p = dict(row, risk=float(risk), predicted=int(risk >= self.threshold),target=None)
            self.predictions.append(p)
            observation["prediction"] = p
            self.current[row["task_id"]] = p
            if p["predicted"]:
                previous = self.last_warning.get(row["task_id"])
                if previous and previous["last_tick"] == row["tick"]-1:
                    previous["last_tick"] = row["tick"]
                    previous["risk"] = p["risk"]
                    previous["warning_ticks"].append(row["tick"])
                else:
                    previous = dict(id=len(self.alerts)+1,task_id=row["task_id"],server_id=row["server_id"],
                        first_tick=row["tick"],last_tick=row["tick"],risk=p["risk"],status="pending",
                        warning_ticks=[row["tick"]],acknowledged=False)
                    self.alerts.append(previous)
                    self.last_warning[row["task_id"]] = previous
        refreshed, still_pending = [], []
        for row in self.pending:
            target = self.engine.target(row)
            if target is None:
                still_pending.append(row)
            else:
                row["target"] = target
                if "prediction" in row:
                    row["prediction"]["target"] = target
                refreshed.append(row)
        self.pending = still_pending
        for a in self.alerts:
            task = self.engine.tasks[a["task_id"]-1]
            failure = task["failure_tick"]
            eligible = failure is not None and any(0 < failure-t <= HORIZON for t in a["warning_ticks"])
            if eligible:
                a["status"] = "confirmed"
            elif task["finish_tick"] is not None or self.engine.tick >= a["last_tick"]+HORIZON:
                a["status"] = "false_alarm"
            elif failure is not None:
                a["status"] = "false_alarm"
            elif self.engine.finished:
                a["status"] = "incomplete"
        snapshot = self.snapshot()
        self.store.save(self,snapshot,rows,refreshed)
        return snapshot

    def snapshot(self):
        e = self.engine
        tasks = []
        for task in e.tasks:
            p = self.current.get(task["id"])
            recovery_status = task.get("recovery_status")
            display_progress = task.get("retry_progress",task["progress"]) if recovery_status else task["progress"]
            tasks.append(dict(task,risk=p["risk"] if p else None,
                flagged=bool(p and p["predicted"]),
                display_progress=display_progress,
                progress_percent=round(100*display_progress/task["duration"])))
        finished = e.finished
        return dict(id=self.id,tick=e.tick,duration=e.limit,workload_duration=e.config.duration,
            phase="recovery" if e.tick>e.config.duration else "workload",config=e.manifest()["config"],
            model_version=self.version,threshold=self.threshold,finished=finished,session_live=True,
            tasks=tasks,servers=e.servers,alerts=self.alerts,
            counts=dict(active=sum(t["status"]=="running" for t in tasks),
                completed=sum(t["status"]=="completed" for t in tasks),
                failed=sum(t["status"]=="failed" for t in tasks),
                recovered=sum(t.get("recovery_status")=="recovered" for t in tasks),
                queued=sum(t.get("recovery_status")=="queued" for t in tasks),
                retrying=sum(t.get("recovery_status")=="retrying" for t in tasks),
                successful=sum(t["status"]=="completed" or t.get("recovery_status")=="recovered" for t in tasks),
                permanent_failed=sum(t["status"]=="failed" and (not e.config.auto_retry or
                    t.get("recovery_status")=="exhausted") for t in tasks),
                flagged=sum(t["flagged"] for t in tasks)),
            events=e.events[-10:],incomplete_windows=len(self.pending),
            recovery_enabled=e.config.auto_retry,
            feature_ranges_warning=e.config.cpu_capacity!=100 or e.config.memory_capacity!=100 or e.config.server_count!=3,
            data_source="Synthetic simulation",generator_version=e.manifest()["generator_version"])
