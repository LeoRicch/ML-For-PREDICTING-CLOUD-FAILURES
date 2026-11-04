"""Background retries consume only spare capacity after first-attempt allocation.

Original task states and ML observations are never rewritten. Retry work is
preemptible, uses no generator randomness and cannot displace first attempts.
"""
POLICY_VERSION = "spare-capacity-retry-v1"
MAX_RETRIES = 2
WAIT_TICKS = 3
START_UTILISATION = 0.65


class RecoveryQueue:
    def enqueue(self, task, tick):
        task.update(recovery_status="queued",retry_count=0,retry_ready_tick=tick+WAIT_TICKS,
            retry_progress=0,retry_shortfall=0,retry_server_id=None,recovered_tick=None,
            attempts=[dict(attempt=0,server_id=task["server_id"],ended=tick,outcome="failed")])

    def step(self, tasks, servers, tick):
        for server in servers:
            server.update(recovery_cpu_used=0,recovery_memory_used=0,recovery_active=0)
        started = set()
        # Continue admitted retry attempts first; then FIFO by initial failure time.
        work = sorted((t for t in tasks if t.get("recovery_status") in ("queued","retrying")),
            key=lambda t:(t["recovery_status"]!="retrying",t["failure_tick"],t["id"]))
        for task in work:
            if task["recovery_status"] == "queued":
                if tick < task["retry_ready_tick"]:
                    continue
                candidates = []
                for s in servers:
                    cpu = s["cpu_used"]+s["recovery_cpu_used"]
                    memory = s["memory_used"]+s["recovery_memory_used"]
                    utilisation = max(cpu/s["cpu_capacity"],memory/s["memory_capacity"])
                    if (s["id"] not in started and utilisation <= START_UTILISATION and
                        cpu+task["cpu_request"] <= s["cpu_capacity"] and
                        memory+task["memory_request"] <= s["memory_capacity"]):
                        candidates.append((utilisation,s["id"],s))
                if not candidates:
                    continue
                server = min(candidates,key=lambda c:(c[0],c[1]))[2]
                task.update(recovery_status="retrying",retry_count=task["retry_count"]+1,
                            retry_server_id=server["id"],retry_progress=0,retry_shortfall=0)
                task["attempts"].append(dict(attempt=task["retry_count"],server_id=server["id"],
                    started=tick,ended=None,outcome="running"))
                started.add(server["id"])
            else:
                server = servers[task["retry_server_id"]-1]
            fits = (server["cpu_used"]+server["recovery_cpu_used"]+task["cpu_request"] <= server["cpu_capacity"] and
                    server["memory_used"]+server["recovery_memory_used"]+task["memory_request"] <= server["memory_capacity"])
            if fits:
                server["recovery_cpu_used"] += task["cpu_request"]
                server["recovery_memory_used"] += task["memory_request"]
                task["retry_progress"] += 1
                task["retry_shortfall"] = 0
            else:
                task["retry_shortfall"] += 1
            if task["retry_progress"] >= task["duration"]:
                task.update(recovery_status="recovered",recovered_tick=tick)
                task["attempts"][-1].update(ended=tick,outcome="completed")
            elif task["retry_shortfall"] >= 3:
                task["attempts"][-1].update(ended=tick,outcome="failed")
                task.update(recovery_status="exhausted" if task["retry_count"] >= MAX_RETRIES else "queued",
                            retry_ready_tick=tick+WAIT_TICKS*task["retry_count"],retry_progress=0)
        for server in servers:
            server["recovery_active"] = sum(t.get("recovery_status")=="retrying" and
                t["retry_server_id"]==server["id"] for t in work)
