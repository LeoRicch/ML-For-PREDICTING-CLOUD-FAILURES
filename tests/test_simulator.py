from dataclasses import asdict
import pytest
from cloudlab.simulator import Config, Simulator, FEATURES


def task(task_id=1, cpu=10, memory=10, duration=10):
    return dict(id=task_id,kind="Test",server_id=1,cpu_request=cpu,memory_request=memory,
        duration=duration,arrival=0,progress=0,shortfall_streak=0,status="running",finish_tick=None,failure_tick=None)


def quiet_engine():
    engine = Simulator(Config(duration=30,server_count=1,cpu_capacity=50,memory_capacity=50))
    engine._poisson = lambda rate:0
    return engine


def test_same_seed_reproduces_jobs_allocations_and_outcomes():
    a = Simulator(Config(scenario="high",seed=301,capacity_reduction=True)).run()
    b = Simulator(Config(scenario="high",seed=301,capacity_reduction=True)).run()
    assert a.tasks == b.tasks
    assert a.observations == b.observations
    assert a.events == b.events
    assert any(t["status"]=="failed" for t in a.tasks)
    assert any(t["status"]=="completed" for t in a.tasks)


def test_failure_requires_three_consecutive_shortfalls():
    engine = quiet_engine()
    engine.tasks = [task(cpu=60)]
    first = engine.step()[0]
    second = engine.step()[0]
    assert (first["shortfall_streak"],second["shortfall_streak"]) == (1,2)
    assert engine.target(first) is None  # No peeking into a not-yet-observed future.
    assert engine.step() == []
    assert engine.tasks[0]["failure_tick"] == 3
    assert engine.target(first) == 1


def test_recovery_resets_shortfall_and_task_completes():
    engine = quiet_engine()
    engine.tasks = [task(cpu=60,duration=2)]
    engine.step()
    engine.tasks[0]["cpu_request"] = 10
    engine.step()
    assert engine.tasks[0]["shortfall_streak"] == 0
    engine.step()
    assert engine.tasks[0]["status"] == "completed"
    assert engine.target(engine.observations[0]) == 0


def test_allocation_never_exceeds_current_capacity():
    engine = Simulator(Config(scenario="high",seed=444,capacity_reduction=True))
    while engine.tick < engine.config.duration:
        rows = engine.step()
        for s in engine.servers:
            assert 0 <= s["cpu_used"] <= s["cpu_capacity"]
            assert 0 <= s["memory_used"] <= s["memory_capacity"]
        for row in rows:
            assert set(row) == set(FEATURES)|{"task_id","server_id","tick"}
            assert engine.tasks[row["task_id"]-1]["status"] == "running"


def test_five_minute_boundary_and_censoring():
    engine = quiet_engine()
    engine.tasks = [task()]
    engine.tick = 6
    engine.tasks[0].update(status="failed",failure_tick=6)
    assert engine.target({"task_id":1,"tick":1}) == 1
    assert engine.target({"task_id":1,"tick":0}) == 0
    engine.tasks[0].update(status="running",failure_tick=None)
    assert engine.target({"task_id":1,"tick":2}) is None
    assert engine.target({"task_id":1,"tick":1}) == 0


@pytest.mark.parametrize("values",[{"duration":0},{"duration":30.0},{"seed":-1},{"scenario":"unknown"},{"server_count":True},{"cpu_capacity":0},{"capacity_reduction":"yes"}])
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValueError):
        Config(**values)
