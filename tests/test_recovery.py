import json
import pytest
from cloudlab.simulator import Config,Simulator
from cloudlab.session import Session
from cloudlab.storage import Store
from cloudlab.recovery import RecoveryQueue
from app import create_app


def failed_task(task_id=1,duration=2):
    return dict(id=task_id,kind="Test",server_id=1,cpu_request=10,memory_request=10,duration=duration,
        arrival=0,progress=0,shortfall_streak=3,status="failed",finish_tick=None,failure_tick=3)


def server(server_id=1,used=0):
    return dict(id=server_id,cpu_used=used,memory_used=used,cpu_capacity=100,memory_capacity=100)


def test_retry_waits_then_recovers_without_rewriting_initial_failure():
    q=RecoveryQueue(); task=failed_task(); q.enqueue(task,3)
    for tick in (3,4,5):
        q.step([task],[server()],tick)
        assert task["recovery_status"]=="queued" and task["retry_count"]==0
    q.step([task],[server()],6)
    assert task["recovery_status"]=="retrying" and task["retry_progress"]==1
    q.step([task],[server()],7)
    assert task["recovery_status"]=="recovered" and task["recovered_tick"]==7
    assert task["status"]=="failed" and task["failure_tick"]==3 and task["finish_tick"] is None
    assert task["attempts"][-1]["outcome"]=="completed"


def test_busy_servers_do_not_start_retries_and_quieter_server_is_selected():
    q=RecoveryQueue(); task=failed_task();q.enqueue(task,3)
    q.step([task],[server(1,90),server(2,80)],6)
    assert task["retry_count"]==0
    resources=[server(1,90),server(2,10)]
    q.step([task],resources,7)
    assert task["retry_server_id"]==2
    assert resources[1]["recovery_cpu_used"]==10
    assert resources[1]["cpu_used"]==10  # First-attempt allocation stays distinguishable.


def test_retry_limit_and_backoff_prevent_endless_restarts():
    q=RecoveryQueue(); task=failed_task(duration=20);q.enqueue(task,3)
    q.step([task],[server()],6)
    for tick in (7,8,9):q.step([task],[server(used=100)],tick)
    assert task["recovery_status"]=="queued" and task["retry_ready_tick"]==12
    q.step([task],[server()],11)
    assert task["retry_count"]==1
    q.step([task],[server()],12)
    for tick in (13,14,15):q.step([task],[server(used=100)],tick)
    assert task["recovery_status"]=="exhausted" and task["retry_count"]==2
    q.step([task],[server()],30)
    assert task["retry_count"]==2


def test_background_recovery_preserves_all_primary_observations_and_labels():
    a=Simulator(Config(scenario="medium",seed=42,auto_retry=False)).run()
    b=Simulator(Config(scenario="medium",seed=42,auto_retry=True))
    for _ in range(120):b.step()
    assert a.observations==b.observations
    assert [a.target(r) for r in a.observations]==[b.target(r) for r in b.observations]
    original_keys=list(a.tasks[0])
    assert [{k:t[k] for k in original_keys} for t in b.tasks]==a.tasks
    assert any(t.get("recovery_status")=="recovered" for t in b.tasks)
    assert any(t.get("recovery_status")=="queued" for t in b.tasks)


def test_combined_allocation_never_exceeds_capacity():
    engine=Simulator(Config(scenario="high",auto_retry=True,capacity_reduction=True,seed=123))
    for _ in range(120):
        engine.step()
        for s in engine.servers:
            assert s["cpu_used"]+s["recovery_cpu_used"]<=s["cpu_capacity"]
            assert s["memory_used"]+s["recovery_memory_used"]<=s["memory_capacity"]


def test_recovery_history_and_honest_counts_survive_restart(tmp_path):
    app=create_app(tmp_path);client=app.test_client()
    response=client.post("/api/runs",json={"duration":120,"scenario":"low","seed":42})
    run=response.json
    assert run["config"]["auto_retry"] is True
    for tick in range(120):
        final=client.post(f'/api/runs/{run["id"]}/step',json={"expected_tick":tick}).json
    assert final["counts"]["recovered"]>0
    assert final["counts"]["successful"]==final["counts"]["completed"]+final["counts"]["recovered"]
    assert final["counts"]["failed"]==sum(final["counts"][k] for k in ("queued","retrying","recovered","permanent_failed"))
    restored=create_app(tmp_path).test_client().get(f'/api/runs/{run["id"]}').json
    assert restored["tasks"]==final["tasks"]
    report=client.get(f'/api/runs/{run["id"]}/report.json').json
    assert any(t.get("attempts") for t in report["snapshot"]["tasks"])


def test_invalid_retry_flag_is_rejected():
    with pytest.raises(ValueError):Config(auto_retry="yes")


def test_arrivals_stop_and_recovery_period_is_bounded():
    engine=Simulator(Config(scenario="medium",seed=42,auto_retry=True))
    for _ in range(120):engine.step()
    total_jobs=len(engine.tasks)
    resolved={ (r["task_id"],r["tick"]):engine.target(r) for r in engine.observations if engine.target(r) is not None }
    while not engine.finished:
        engine.step()
        assert len(engine.tasks)==total_jobs
    assert 120<=engine.tick<=240
    assert sum(t.get("recovery_status")=="recovered" for t in engine.tasks)>30
    assert all(engine.target({"task_id":key[0],"tick":key[1]})==value for key,value in resolved.items())
    assert any(t.get("recovery_status") in ("queued","retrying") for t in engine.tasks)


def test_recovery_does_not_turn_a_correct_warning_into_false_alarm(tmp_path):
    class AlwaysWarn:
        version="warning-test"
        threshold=0.5
        def predict(self,rows):return [0.9]*len(rows)
    session=Session(Simulator(Config(scenario="low",seed=42,duration=60,auto_retry=True)),
        AlwaysWarn(),Store(tmp_path/"warnings.sqlite3"))
    while not session.engine.finished:session.step()
    recovered={t["id"] for t in session.engine.tasks if t.get("recovery_status")=="recovered"}
    assert recovered
    confirmed=[a for a in session.alerts if a["task_id"] in recovered and a["status"]=="confirmed"]
    assert confirmed
    assert any(p["task_id"] in recovered and p["target"]==1 for p in session.predictions)
