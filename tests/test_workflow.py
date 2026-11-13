import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from workflow import WorkflowEngine, WorkflowStatus, StepStatus, WORKFLOW_STEPS
from database import Database


@pytest.fixture(autouse=True)
def cleanup_db():
    yield
    with Database.transaction() as conn:
        conn.execute("DELETE FROM workflow_steps")
        conn.execute("DELETE FROM workflows")
        conn.execute("DELETE FROM instances")
        conn.execute("DELETE FROM proxy_profiles")
        conn.execute("DELETE FROM activity_logs")


@pytest.fixture
def test_instance():
    with Database.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO instances (ld_index, name, status, serial) VALUES (1, 'Test Instance', 'running', 'emulator-5554')"
        )
        instance_id = cursor.lastrowid
    return instance_id


@pytest.fixture
def test_proxy_profile():
    with Database.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO proxy_profiles (name, host, port, protocol) VALUES ('Test Proxy', '1.2.3.4', 8080, 'http')"
        )
        profile_id = cursor.lastrowid
    return profile_id


def test_workflow_steps_order():
    assert WORKFLOW_STEPS[0] == "preflight"
    assert WORKFLOW_STEPS[1] == "create_instance"
    assert WORKFLOW_STEPS[2] == "start_instance"
    assert WORKFLOW_STEPS[3] == "wait_boot"
    assert WORKFLOW_STEPS[4] == "install_bing"
    assert WORKFLOW_STEPS[5] == "install_oxyproxy"
    assert WORKFLOW_STEPS[6] == "configure_proxy"
    assert WORKFLOW_STEPS[7] == "verify_connection"
    assert WORKFLOW_STEPS[8] == "launch_bing"
    assert WORKFLOW_STEPS[9] == "wait_manual_login"
    assert WORKFLOW_STEPS[10] == "generate_report"
    assert len(WORKFLOW_STEPS) == 11


def test_workflow_creation(test_instance):
    engine = WorkflowEngine()
    wf = engine.create_workflow(instance_id=test_instance, proxy_profile_id=None)

    assert wf.id is not None
    assert wf.instance_id == test_instance
    assert wf.proxy_profile_id is None
    assert wf.status == WorkflowStatus.PENDING
    assert wf.current_step == "preflight"
    assert len(wf.steps) == 11

    for i, step in enumerate(wf.steps):
        assert step.name == WORKFLOW_STEPS[i]
        assert step.status == StepStatus.PENDING


def test_workflow_with_proxy(test_instance, test_proxy_profile):
    engine = WorkflowEngine()
    wf = engine.create_workflow(instance_id=test_instance, proxy_profile_id=test_proxy_profile)

    assert wf.proxy_profile_id == test_proxy_profile


def test_step_status_enum():
    assert StepStatus.PENDING.value == "pending"
    assert StepStatus.RUNNING.value == "running"
    assert StepStatus.SUCCESS.value == "success"
    assert StepStatus.FAILED.value == "failed"
    assert StepStatus.SKIPPED.value == "skipped"
    assert StepStatus.WAITING_FOR_USER.value == "waiting_for_user"


def test_workflow_status_enum():
    assert WorkflowStatus.PENDING.value == "pending"
    assert WorkflowStatus.RUNNING.value == "running"
    assert WorkflowStatus.SUCCESS.value == "success"
    assert WorkflowStatus.FAILED.value == "failed"
    assert WorkflowStatus.CANCELLED.value == "cancelled"


def test_get_workflow(test_instance):
    engine = WorkflowEngine()
    wf = engine.create_workflow(instance_id=test_instance)
    retrieved = engine.get_workflow(wf.id)

    assert retrieved is not None
    assert retrieved.id == wf.id
    assert retrieved.instance_id == test_instance


def test_list_workflows(test_instance):
    engine = WorkflowEngine()
    engine.create_workflow(instance_id=test_instance)
    engine.create_workflow(instance_id=test_instance)

    workflows = engine.list_workflows()
    assert len(workflows) >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])