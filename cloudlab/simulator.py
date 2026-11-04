"""Seeded discrete-time resource simulator. No model is used to generate jobs."""
from dataclasses import asdict, dataclass
import math
import random
from .recovery import RecoveryQueue, POLICY_VERSION, MAX_RETRIES, WAIT_TICKS, START_UTILISATION

FEATURES = ["cpu_utilisation", "memory_utilisation", "cpu_request_ratio",
            "memory_request_ratio", "active_task_count", "task_age", "shortfall_streak"]
GENERATOR_VERSION = "resource-shortfall-v1"
HORIZON = 5
RATES = {"low": 0.65, "medium": 1.45, "high": 2.7, "reduced": 1.45}


@dataclass(frozen=True)
class Config:
    scenario: str = "medium"
    duration: int = 120
    seed: int = 42
    cpu_capacity: int = 100
    memory_capacity: int = 100
    server_count: int = 3
    capacity_reduction: bool = False
    auto_retry: bool = False

    def __post_init__(self):
        if self.scenario not in RATES:
            raise ValueError("Choose normal, busy, very busy, or reduced capacity.")
        for key, lo, hi in (("duration", 30, 300), ("seed", 0, 2**31-1),
                            ("cpu_capacity", 50, 200), ("memory_capacity", 50, 200),
                            ("server_count", 1, 6)):
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key.replace('_', ' ')} must be a whole number from {lo} to {hi}.")
        if type(self.capacity_reduction) is not bool:
            raise ValueError("Capacity reduction must be true or false.")
        if type(self.auto_retry) is not bool:
            raise ValueError("Automatic retry must be true or false.")


class Simulator:
    def __init__(self, config: Config):
        self.config = config
        self.rng = random.Random(config.seed)
        self.tick = 0
        self.tasks = []
        self.observations = []
        self.servers = []
        self.events = []
        self.recovery = RecoveryQueue()
        # A hidden future schedule belongs to the generator, never to ML inputs.
        self.drop_start = self.rng.randint(max(5, config.duration//4), config.duration//2)
        self.drop_length = self.rng.randint(6, 18)
        self.drop_server = self.rng.randrange(config.server_count)
        self.drop_factor = self.rng.uniform(0.45, 0.7)
        self._server_states()

    @property
    def limit(self):
        return self.config.duration+(120 if self.config.auto_retry else 0)

    @property
    def finished(self):
        if self.tick < self.config.duration:
            return False
        if not self.config.auto_retry or self.tick >= self.limit:
            return True
        return not any(t["status"]=="running" or t.get("recovery_status") in ("queued","retrying") for t in self.tasks)

    def _server_states(self):
        old = {s["id"]: s for s in self.servers}
        self.servers = []
        c = self.config
        for i in range(c.server_count):
            reduced = ((c.capacity_reduction or c.scenario == "reduced") and
                       i == self.drop_server and self.drop_start <= self.tick < self.drop_start+self.drop_length)
            factor = self.drop_factor if reduced else 1.0
            previous = old.get(i+1, {})
            self.servers.append(dict(id=i+1, name=f"Server {i+1:02}",
                                     cpu_capacity=round(c.cpu_capacity*factor, 3),
                                     memory_capacity=round(c.memory_capacity*factor, 3),
                                     cpu_used=0, memory_used=0, active_tasks=0,
                                     reduced=reduced))
            if reduced != previous.get("reduced", False):
                self.events.append(dict(tick=self.tick, message=f"Server {i+1:02} capacity {'reduced' if reduced else 'restored'}."))

    def _poisson(self, rate):
        product, k = 1.0, 0
        while product > math.exp(-rate):
            k += 1
            product *= self.rng.random()
        return k-1

    def step(self):
        if self.finished:
            return []
        self.tick += 1
        self._server_states()
        # Bursty arrivals, independent of any predictions or future labels.
        rate = RATES[self.config.scenario] * (self.config.server_count/3)
        rate *= 1.35 if (self.tick//12) % 3 == 1 else 0.85
        kinds = ["Report generation", "Data processing", "Image processing", "File conversion"]
        # After arrivals stop, give pending jobs a bounded recovery period.
        arrivals = self._poisson(rate) if self.tick <= self.config.duration else 0
        for _ in range(arrivals):
            self.tasks.append(dict(id=len(self.tasks)+1, kind=self.rng.choice(kinds),
                server_id=self.rng.randrange(self.config.server_count)+1,
                cpu_request=self.rng.randint(6, 24), memory_request=self.rng.randint(6, 26),
                duration=self.rng.randint(10, 30), arrival=self.tick, progress=0,
                shortfall_streak=0, status="running", finish_tick=None, failure_tick=None))
        # FIFO with backfilling: each job receives all requested resources or none.
        # Later jobs may fit when a larger earlier job does not.
        for task in self.tasks:
            if task["status"] != "running":
                continue
            server = self.servers[task["server_id"]-1]
            fits = (server["cpu_used"]+task["cpu_request"] <= server["cpu_capacity"] and
                    server["memory_used"]+task["memory_request"] <= server["memory_capacity"])
            if fits:
                server["cpu_used"] += task["cpu_request"]
                server["memory_used"] += task["memory_request"]
                task["progress"] += 1
                task["shortfall_streak"] = 0
            else:
                task["shortfall_streak"] += 1
            if task["shortfall_streak"] >= 3:
                task.update(status="failed", failure_tick=self.tick)
                if self.config.auto_retry:
                    self.recovery.enqueue(task,self.tick)
            elif task["progress"] >= task["duration"]:
                task.update(status="completed", finish_tick=self.tick)
        for server in self.servers:
            server["active_tasks"] = sum(t["status"] == "running" and t["server_id"] == server["id"] for t in self.tasks)
        rows = []
        for task in self.tasks:
            if task["status"] != "running":
                continue
            server = self.servers[task["server_id"]-1]
            row = dict(task_id=task["id"], server_id=task["server_id"], tick=self.tick,
                cpu_utilisation=server["cpu_used"]/server["cpu_capacity"],
                memory_utilisation=server["memory_used"]/server["memory_capacity"],
                cpu_request_ratio=task["cpu_request"]/server["cpu_capacity"],
                memory_request_ratio=task["memory_request"]/server["memory_capacity"],
                active_task_count=server["active_tasks"], task_age=self.tick-task["arrival"],
                shortfall_streak=task["shortfall_streak"])
            rows.append(row)
        self.observations.extend(rows)
        if self.config.auto_retry:
            self.recovery.step(self.tasks,self.servers,self.tick)
        return rows

    def target(self, row):
        """Return a label only once the relevant follow-up has actually occurred."""
        task = self.tasks[row["task_id"]-1]
        t = row["tick"]
        if task["failure_tick"] is not None and t < task["failure_tick"] <= t+HORIZON:
            return 1
        if task["finish_tick"] is not None and t < task["finish_tick"] <= t+HORIZON:
            return 0
        if self.tick >= t+HORIZON:
            return 0
        return None

    def manifest(self):
        return dict(config=asdict(self.config), generator_version=GENERATOR_VERSION,
            recovery_policy=dict(version=POLICY_VERSION,enabled=self.config.auto_retry,max_retries=MAX_RETRIES,
                minimum_wait_ticks=WAIT_TICKS,start_utilisation_ceiling=START_UTILISATION,
                maximum_drain_ticks=120,
                priority="First attempts always allocated before background retries",ml_scope="First attempts only"),
            generator_parameters=dict(base_arrivals_per_tick=RATES[self.config.scenario]*(self.config.server_count/3),
                burst_block_ticks=12,burst_multipliers=[0.85,1.35,0.85],
                cpu_request_range=[6,24],memory_request_range=[6,26],task_duration_range=[10,30],
                failure_shortfall_ticks=3,capacity_drop_factor_range=[0.45,0.70],capacity_drop_length_range=[6,18]),
            capacity_schedule=dict(start=self.drop_start, length=self.drop_length,
                                   server=self.drop_server+1, factor=self.drop_factor),
            scheduler="FIFO with backfilling; all-or-none allocation", horizon=HORIZON)

    def run(self):
        while not self.finished:
            self.step()
        return self
