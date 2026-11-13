import threading
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List
from datetime import datetime
from config import settings
from database import Database, dict_from_row, log_activity
from services import ldplayer_service, adb_service, installer_service, proxy_service, bing_service


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    WAITING_FOR_USER = "waiting_for_user"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


WORKFLOW_STEPS = [
    "preflight",
    "create_instance",
    "start_instance",
    "wait_boot",
    "install_bing",
    "install_oxyproxy",
    "configure_proxy",
    "verify_connection",
    "launch_bing",
    "wait_manual_login",
    "generate_report",
]


@dataclass
class WorkflowStep:
    name: str
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    output: Optional[Dict] = None


@dataclass
class Workflow:
    id: int
    instance_id: Optional[int]
    proxy_profile_id: Optional[int]
    status: WorkflowStatus
    current_step: Optional[str]
    steps: List[WorkflowStep]
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    lock_token: Optional[str] = None


class WorkflowEngine:
    def __init__(self):
        self._locks: Dict[int, str] = {}
        self._running: Dict[int, threading.Thread] = {}
        self._callbacks: List[Callable] = []
        self._stop_flags: Dict[int, bool] = {}

    def register_callback(self, callback: Callable):
        self._callbacks.append(callback)

    def _notify(self, workflow_id: int, event: str, data: Any = None):
        for cb in self._callbacks:
            try:
                cb(workflow_id, event, data)
            except Exception:
                pass

    def _acquire_lock(self, instance_id: int) -> Optional[str]:
        token = str(uuid.uuid4())
        with Database.transaction() as conn:
            cursor = conn.execute(
                "SELECT id FROM workflows WHERE instance_id = ? AND status IN ('pending', 'running')",
                (instance_id,),
            )
            if cursor.fetchone():
                return None
            self._locks[instance_id] = token
        return token

    def _release_lock(self, instance_id: int, token: str):
        if self._locks.get(instance_id) == token:
            self._locks.pop(instance_id, None)

    def create_workflow(self, instance_id: int, proxy_profile_id: Optional[int] = None) -> Workflow:
        with Database.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO workflows (instance_id, proxy_profile_id, status, current_step, started_at)
                VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP)
                """,
                (instance_id, proxy_profile_id, WORKFLOW_STEPS[0]),
            )
            workflow_id = cursor.lastrowid

            for i, step_name in enumerate(WORKFLOW_STEPS):
                conn.execute(
                    "INSERT INTO workflow_steps (workflow_id, step_name, step_order, status) VALUES (?, ?, ?, 'pending')",
                    (workflow_id, step_name, i),
                )

        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: int) -> Optional[Workflow]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
            row = cursor.fetchone()
            if not row:
                return None

            cursor = conn.execute("SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY step_order", (workflow_id,))
            steps = [WorkflowStep(
                name=row["step_name"],
                status=StepStatus(row["status"]),
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                error=row["error_message"],
                output=eval(row["output_data"]) if row["output_data"] else None,
            ) for row in cursor.fetchall()]

            return Workflow(
                id=row["id"],
                instance_id=row["instance_id"],
                proxy_profile_id=row["proxy_profile_id"],
                status=WorkflowStatus(row["status"]),
                current_step=row["current_step"],
                steps=steps,
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                error=row["error_message"],
            )

    def list_workflows(self, limit: int = 50) -> List[Workflow]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?", (limit,))
            workflows = []
            for row in cursor.fetchall():
                wf = self.get_workflow(row["id"])
                if wf:
                    workflows.append(wf)
            return workflows

    def run_workflow(self, workflow_id: int):
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            return

        self._stop_flags[workflow_id] = False
        token = self._acquire_lock(workflow.instance_id)
        if not token:
            self._update_status(workflow_id, WorkflowStatus.FAILED, error="Instance already has active workflow")
            self._notify(workflow_id, "failed", {"error": "Instance already has active workflow"})
            return

        workflow.lock_token = token
        thread = threading.Thread(target=self._execute_workflow, args=(workflow_id, token), daemon=True)
        self._running[workflow_id] = thread
        thread.start()

    def _execute_workflow(self, workflow_id: int, token: str):
        workflow = self.get_workflow(workflow_id)
        if not workflow:
            return

        self._update_status(workflow_id, WorkflowStatus.RUNNING)
        self._notify(workflow_id, "started", {})

        instance = self._get_instance_info(workflow.instance_id)
        if not instance:
            self._fail_workflow(workflow_id, "Instance not found", token)
            return

        serial = instance.get("serial")
        if not serial:
            self._fail_workflow(workflow_id, "No ADB serial for instance", token)
            return

        try:
            self._run_step(workflow_id, "preflight", lambda: self._step_preflight(serial))
            if self._stop_flags.get(workflow_id):
                self._cancel_workflow(workflow_id, token)
                return

            if workflow.instance_id is None:
                self._run_step(workflow_id, "create_instance", lambda: self._step_create_instance(workflow))
                if self._stop_flags.get(workflow_id):
                    self._cancel_workflow(workflow_id, token)
                    return
                serial = self._get_instance_info(workflow.instance_id).get("serial")

            self._run_step(workflow_id, "start_instance", lambda: self._step_start_instance(workflow.instance_id, serial))
            self._run_step(workflow_id, "wait_boot", lambda: self._step_wait_boot(workflow.instance_id, serial))
            self._run_step(workflow_id, "install_bing", lambda: self._step_install_bing(serial))
            self._run_step(workflow_id, "install_oxyproxy", lambda: self._step_install_oxyproxy(serial))
            self._run_step(workflow_id, "configure_proxy", lambda: self._step_configure_proxy(serial, workflow.proxy_profile_id))
            self._run_step(workflow_id, "verify_connection", lambda: self._step_verify_connection(serial))
            self._run_step(workflow_id, "launch_bing", lambda: self._step_launch_bing(serial))
            self._run_step(workflow_id, "wait_manual_login", lambda: self._step_wait_manual_login(serial))
            self._run_step(workflow_id, "generate_report", lambda: self._step_generate_report(workflow_id))

            self._update_status(workflow_id, WorkflowStatus.SUCCESS, completed_at=datetime.now().isoformat())
            self._notify(workflow_id, "completed", {})

        except Exception as e:
            self._fail_workflow(workflow_id, str(e), token)
        finally:
            self._release_lock(workflow.instance_id, token)
            self._running.pop(workflow_id, None)
            self._stop_flags.pop(workflow_id, None)

    def _run_step(self, workflow_id: int, step_name: str, func: Callable):
        self._update_step(workflow_id, step_name, StepStatus.RUNNING, started_at=datetime.now().isoformat())
        self._update_status(workflow_id, WorkflowStatus.RUNNING, current_step=step_name)
        self._notify(workflow_id, "step_started", {"step": step_name})

        try:
            result = func()
            self._update_step(workflow_id, step_name, StepStatus.SUCCESS, completed_at=datetime.now().isoformat(), output=result)
            self._notify(workflow_id, "step_completed", {"step": step_name, "output": result})
        except Exception as e:
            self._update_step(workflow_id, step_name, StepStatus.FAILED, completed_at=datetime.now().isoformat(), error=str(e))
            self._notify(workflow_id, "step_failed", {"step": step_name, "error": str(e)})
            raise

    def _update_status(self, workflow_id: int, status: WorkflowStatus, current_step: Optional[str] = None,
                       completed_at: Optional[str] = None, error: Optional[str] = None):
        with Database.transaction() as conn:
            updates = ["status = ?"]
            params = [status.value]
            if current_step:
                updates.append("current_step = ?")
                params.append(current_step)
            if completed_at:
                updates.append("completed_at = ?")
                params.append(completed_at)
            if error:
                updates.append("error_message = ?")
                params.append(error)
            params.append(workflow_id)
            conn.execute(f"UPDATE workflows SET {', '.join(updates)} WHERE id = ?", params)

    def _update_step(self, workflow_id: int, step_name: str, status: StepStatus,
                     started_at: Optional[str] = None, completed_at: Optional[str] = None,
                     error: Optional[str] = None, output: Optional[Dict] = None):
        with Database.transaction() as conn:
            updates = ["status = ?"]
            params = [status.value]
            if started_at:
                updates.append("started_at = ?")
                params.append(started_at)
            if completed_at:
                updates.append("completed_at = ?")
                params.append(completed_at)
            if error:
                updates.append("error_message = ?")
                params.append(error)
            if output is not None:
                updates.append("output_data = ?")
                params.append(str(output))
            params.extend([workflow_id, step_name])
            conn.execute(f"UPDATE workflow_steps SET {', '.join(updates)} WHERE workflow_id = ? AND step_name = ?", params)

    def _fail_workflow(self, workflow_id: int, error: str, token: str):
        self._update_status(workflow_id, WorkflowStatus.FAILED, error=error, completed_at=datetime.now().isoformat())
        self._notify(workflow_id, "failed", {"error": error})
        log_activity("ERROR", f"Workflow {workflow_id} failed: {error}", workflow_id=workflow_id)

    def _cancel_workflow(self, workflow_id: int, token: str):
        self._update_status(workflow_id, WorkflowStatus.CANCELLED, completed_at=datetime.now().isoformat())
        self._notify(workflow_id, "cancelled", {})

    def stop_workflow(self, workflow_id: int):
        self._stop_flags[workflow_id] = True
        if workflow_id in self._running:
            self._running[workflow_id].join(timeout=5)

    def _get_instance_info(self, instance_id: int) -> Optional[Dict]:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT * FROM instances WHERE id = ?", (instance_id,))
            row = cursor.fetchone()
            return dict_from_row(row) if row else None

    def _step_preflight(self, serial: str) -> Dict:
        errors = []
        if not settings.ldplayer.ldconsole_path.exists():
            errors.append("ldconsole not found")
        if not settings.ldplayer.adb_path.exists():
            errors.append("adb not found")
        if not settings.apps.bing_apk_path.exists():
            errors.append("Bing APK not found")
        if not settings.apps.oxyproxy_xapk_path.exists():
            errors.append("OxyProxy XAPK not found")

        devices = adb_service.list_devices()
        device = next((d for d in devices if d.serial == serial), None)
        if not device:
            errors.append(f"Device {serial} not connected via ADB")

        if errors:
            raise RuntimeError("; ".join(errors))
        return {"checks_passed": True, "device": serial}

    def _step_create_instance(self, workflow: Workflow) -> Dict:
        index = ldplayer_service.create_instance()
        with Database.transaction() as conn:
            conn.execute("UPDATE workflows SET instance_id = ? WHERE id = ?", (index, workflow.id))
        ldplayer_service.sync_instances_to_db()
        return {"instance_index": index}

    def _step_start_instance(self, instance_id: int, serial: str) -> Dict:
        ldplayer_service.launch_instance(instance_id, wait_for_boot=False)
        return {"launched": True}

    def _step_wait_boot(self, instance_id: int, serial: str) -> Dict:
        ldplayer_service.wait_for_boot(instance_id)
        return {"booted": True}

    def _step_install_bing(self, serial: str) -> Dict:
        success, msg = installer_service.install_bing(serial)
        if not success:
            raise RuntimeError(msg)
        return {"installed": True, "message": msg}

    def _step_install_oxyproxy(self, serial: str) -> Dict:
        success, msg = installer_service.install_oxyproxy(serial)
        if not success:
            raise RuntimeError(msg)
        return {"installed": True, "message": msg}

    def _step_configure_proxy(self, serial: str, proxy_profile_id: Optional[int]) -> Dict:
        if not proxy_profile_id:
            return {"skipped": True, "reason": "No proxy profile"}
        profile = proxy_service.get_profile(proxy_profile_id)
        if not profile:
            raise RuntimeError("Proxy profile not found")
        if not proxy_service.configure_device_proxy(serial, profile):
            raise RuntimeError("Failed to configure proxy on device")
        return {"configured": True, "proxy": f"{profile.host}:{profile.port}"}

    def _step_verify_connection(self, serial: str) -> Dict:
        ok = adb_service.check_connectivity(serial)
        if not ok:
            raise RuntimeError("Connectivity test failed")
        return {"connected": True}

    def _step_launch_bing(self, serial: str) -> Dict:
        result = bing_service.launch_bing(serial)
        if not result.success:
            raise RuntimeError(result.message)
        return {"launched": True}

    def _step_wait_manual_login(self, serial: str) -> Dict:
        result = bing_service.wait_for_manual_login(serial, timeout=300)
        if not result.success:
            raise RuntimeError(result.message)
        return {"login_detected": True}

    def _step_generate_report(self, workflow_id: int) -> Dict:
        workflow = self.get_workflow(workflow_id)
        steps_summary = {s.name: s.status.value for s in workflow.steps}
        return {"workflow_id": workflow_id, "steps": steps_summary}


workflow_engine = WorkflowEngine()