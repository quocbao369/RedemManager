import os
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).parent.resolve()

from config import settings, validate_paths, apply_settings_update
from jinja2 import Environment, FileSystemLoader
from database import Database, log_activity, dict_from_row
from services import (
    ldplayer_service,
    adb_service,
    installer_service,
    bing_service,
    rewards_service,
    oxyproxy_service,
    bing_login_service,
    bing_redeem_service,
    proxy_rotation,
    ip_blacklist,
    job_progress,
    setup_logging,
    get_logger,
    logger_service,
)
from services.ldplayer_service import InstanceInfo
from services.adb_service import ADBDevice


logger = get_logger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


async def startup_event():
    setup_logging()
    logger_service.instrument_services()
    logger.info("Startup: begin")
    errors = validate_paths()
    if errors:
        for err in errors:
            logger.error(f"Config error: {err}")
    ldplayer_service.sync_instances_to_db()
    logger.info("RedemManager started")

async def shutdown_event():
    logger.info("RedemManager shutting down")
    Database.close_all()


# NOTE: `lifespan` used to be passed "off" here, but that value belongs to
# the uvicorn CLI, not FastAPI. FastAPI stored the raw string and crashed
# with "'str' object is not callable" the moment anything walked the
# lifespan protocol (uvicorn default, TestClient, ...), so run.bat had to
# disable lifespans and on_startup silently never ran in production.
app = FastAPI(title="RedemManager", on_startup=[startup_event], on_shutdown=[shutdown_event])
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Every HTTP request is logged with its status and duration. HTTPExceptions
# (expected 4xx from the endpoints) are a warning, real crashes are an ERROR
# with the full traceback.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    method, path = request.method, request.url.path
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except HTTPException as e:
        logger.warning("%s %s -> HTTP %s: %s (%.0fms)",
                       method, path, e.status_code, e.detail,
                       (time.perf_counter() - started) * 1000)
        raise
    except Exception:
        logger.exception("%s %s -> LOI 500 sau %.0fms",
                         method, path, (time.perf_counter() - started) * 1000)
        raise
    duration = (time.perf_counter() - started) * 1000
    if response.status_code >= 500:
        logger.error("%s %s -> %s (%.0fms)", method, path, response.status_code, duration)
    else:
        logger.info("%s %s -> %s (%.0fms)", method, path, response.status_code, duration)
    return response

# Custom Jinja2 environment to avoid cache issues
jinja_env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    auto_reload=True,
    cache_size=0,  # Disable cache to avoid "unhashable type: dict" error
)

def render_template(template_name: str, context: dict):
    template = jinja_env.get_template(template_name)
    return template.render(context)


class SettingsUpdate(BaseModel):
    ldplayer_path: Optional[str] = None
    ldplayer14_path: Optional[str] = None
    adb_path: Optional[str] = None
    bing_apk_path: Optional[str] = None
    oxyproxy_xapk_path: Optional[str] = None
    default_timeout: Optional[int] = None
    boot_timeout: Optional[int] = None
    server_port: Optional[int] = None
    server_host: Optional[str] = None
    bing_package_name: Optional[str] = None
    oxyproxy_package_name: Optional[str] = None
    bing_order_history_url: Optional[str] = None
    connectivity_test_url: Optional[str] = None
    database_path: Optional[str] = None
    rewards_db_path: Optional[str] = None
    log_level: Optional[str] = None
    workflow_lock_timeout: Optional[int] = None


class AssignInstanceRequest(BaseModel):
    instance_id: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return HTMLResponse(render_template("dashboard.html", {"request": request}))


@app.get("/instances", response_class=HTMLResponse)
async def instances_page(request: Request):
    return HTMLResponse(render_template("instances.html", {"request": request}))


@app.get("/apps", response_class=HTMLResponse)
async def apps_page(request: Request):
    return HTMLResponse(render_template("apps.html", {"request": request}))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return HTMLResponse(render_template("logs.html", {"request": request}))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return HTMLResponse(render_template("settings.html", {"request": request}))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/instances")
async def api_instances():
    instances = ldplayer_service.list_instances()
    # The optional LDPlayer 14 installation is merged in; its instances use
    # their own 0-based indices, so rows are keyed by (ld_version, ld_index).
    if settings.ldplayer14.is_configured():
        instances = instances + ldplayer_service.list_instances(ld_version=14)
    db_instances = {
        (i.get("ld_version") or 9, i["ld_index"]): i
        for i in ldplayer_service.get_db_instances()
    }
    # Instance names are shortened (the "@domain" part is cut), so the mapped
    # account email is shipped too - the search box and the table need it.
    emails = {}
    with Database.transaction() as conn:
        cursor = conn.execute(
            "SELECT instance_id, account_email FROM account_mapping WHERE instance_id IS NOT NULL")
        for row in cursor.fetchall():
            emails[row["instance_id"]] = row["account_email"]
    result = []
    for inst in instances:
        db = db_instances.get((inst.ld_version, inst.index), {})
        result.append({
            "id": db.get("id"),
            "index": inst.index,
            "ld_version": inst.ld_version,
            "name": inst.name,
            "email": emails.get(db.get("id")),
            "status": inst.status,
            "serial": inst.serial or db.get("serial"),
            "android_version": db.get("android_version"),
            "resolution": db.get("resolution"),
            "cpu_cores": db.get("cpu_cores"),
            "memory_mb": db.get("memory_mb"),
        })
    return result


@app.post("/api/instances/sync")
async def api_sync_instances():
    try:
        ldplayer_service.sync_instances_to_db()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


class InstanceCreate(BaseModel):
    name: Optional[str] = None
    phone_mode: bool = True
    # When set the new instance is cloned from this LDPlayer index instead of
    # being created empty (LDPlayer "copy --from"). The source lives in the
    # same installation as the new instance.
    clone_from: Optional[int] = None
    # Which LDPlayer installation owns the new instance (9 default, 14 opt-in).
    ld_version: int = 9


def _validate_ld_version(ld_version: int) -> int:
    """Only drivable installations may be targeted."""
    if ld_version not in (9, 14):
        raise HTTPException(400, f"Loai gia lap khong ho tro: {ld_version} (chi 9 hoac 14)")
    if ld_version == 14 and not settings.ldplayer14.is_configured():
        raise HTTPException(400,
                            "LDPlayer 14 chua duoc cau hinh - dat thu muc cai dat "
                            "trong Settings > Paths (LDPlayer 14 Directory)")
    return ld_version


@app.post("/api/instances")
async def api_create_instance(payload: InstanceCreate):
    # The UI posts JSON; this used to be declared with Form(), which silently
    # dropped every field (the name never reached LDPlayer).
    name, phone_mode = payload.name, payload.phone_mode
    ld_version = _validate_ld_version(payload.ld_version)
    try:
        # Cloning copies the whole disk image and takes minutes. Running it
        # inline would block the event loop and freeze the whole dashboard,
        # so it goes to a worker thread.
        if payload.clone_from is not None:
            index = await run_in_threadpool(
                ldplayer_service.clone_instance, payload.clone_from, name,
                phone_mode, ld_version)
        else:
            index = await run_in_threadpool(
                ldplayer_service.create_instance, name, phone_mode, ld_version)
        ldplayer_service.sync_instances_to_db()
        return {"success": True, "index": index, "ld_version": ld_version}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/instances/{db_id}/rename")
async def api_rename_instance(db_id: int, name: str = Form(...)):
    try:
        identity = ldplayer_service.get_instance_identity(db_id)
        if identity is None:
            raise HTTPException(404, "Instance not found")
        ld_index, ld_version = identity
        ldplayer_service.rename_instance(ld_index, name, ld_version=ld_version)
        ldplayer_service.sync_instances_to_db()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/ldplayer/runtimes")
async def api_ldplayer_runtimes():
    """Which LDPlayer installations exist and are drivable.

    The Create Instance dialog uses this to offer only usable versions.
    """
    return {"versions": [
        {
            "version": 9,
            "name": "LDPlayer 9",
            "path": str(settings.ldplayer.ldplayer_path),
            "available": settings.ldplayer.ldconsole_path.exists(),
        },
        {
            "version": 14,
            "name": "LDPlayer 14",
            "path": str(settings.ldplayer14.ldplayer_path or ""),
            "available": settings.ldplayer14.is_configured(),
        },
    ]}


def _setup_oxyproxy_for_account(db_id: int, ld_index: int, email: Optional[str],
                                say=lambda m, l="info": None,
                                ld_version: int = 9) -> str:
    """Make sure OxyProxy has a connected "DF" entry for this account.

    V6: rotate the global proxy once, then (re)connect the existing DF entry.
    V4: no rotation, just (re)connect.
    A missing DF entry is created first, then activated.
    """
    if not email:
        return "OxyProxy: skipped (account khong xac dinh)"

    accounts = rewards_service.get_accounts()
    account = rewards_service.find_account(email)
    if not account:
        return "OxyProxy: skipped (khong tim thay account)"

    is_v6 = account.proxy_type == "v6"

    # V6 accounts rotate every time, whether or not DF already exists.
    if is_v6:
        current_ip = proxy_rotation.get_instance_ip(db_id) or None
        rot = proxy_rotation.rotate_proxy(previous_ip=current_ip)
        say(f"Xoay proxy V6: {rot.message}")
        if rot.success and rot.ip:
            proxy_rotation.remember_instance_ip(db_id, rot.ip)
            if rot.ip == current_ip:
                say(f"IP moi: {rot.ip} (trung IP cu, da ghi nho)", "warning")
            else:
                say(f"IP moi: {rot.ip}", "success")
        elif rot.blacklisted:
            say(f"Canh bao: {rot.message}", "warning")
        else:
            say(f"Xoay proxy that bai: {rot.message}", "error")

    proxy = proxy_rotation.get_global_proxy() if is_v6 else account.proxy_parsed
    if not proxy or not proxy.get("host") or not proxy.get("port"):
        return "OxyProxy: khong co proxy hop le"

    say("Dang cau hinh proxy trong app OxyProxy...")
    result = oxyproxy_service.configure_oxyproxy(
        ld_index, "DF",
        str(proxy["host"]), str(proxy["port"]),
        str(proxy.get("username") or ""), str(proxy.get("password") or ""),
        ld_version=ld_version,
    )
    msg = f"OxyProxy: {result.message}"
    say(msg, "success" if result.success else "error")
    return msg


def _ensure_apps(ld_index: int, db_id: int, say=lambda m, l="info": None,
                 ld_version: int = 9) -> Tuple[bool, bool]:
    """Install the required apps only when they are missing.

    Instances are created fresh rather than cloned, so each one starts empty
    and the apps have to be put in place on first launch.

    Returns (bing_ready, oxyproxy_ready).
    """
    bing_pkg = settings.apps.bing_package_name
    oxy_pkg = "io.oxylabs.proxymanager"

    bing_ok = installer_service.is_installed_to_index(ld_index, bing_pkg, ld_version=ld_version)
    if bing_ok:
        say("Bing da duoc cai dat")
    else:
        say("Dang cai dat Bing...")
        result = installer_service.install_bing_to_index(ld_index, ld_version=ld_version)
        bing_ok = bool(result[0])
        say(f"Bing: {result[1]}", "success" if bing_ok else "error")

    oxy_ok = installer_service.is_installed_to_index(ld_index, oxy_pkg, ld_version=ld_version)
    if oxy_ok:
        say("OxyProxy da duoc cai dat")
    else:
        say("Dang cai dat OxyProxy...")
        result = installer_service.install_oxyproxy_to_index(ld_index, ld_version=ld_version)
        oxy_ok = bool(result[0])
        say(f"OxyProxy: {result[1]}", "success" if oxy_ok else "error")

    return bing_ok, oxy_ok


def _bing_login_for_account(db_id: int, ld_index: int, email: Optional[str],
                            say=lambda m, l="info": None,
                            ld_version: int = 9) -> str:
    """Sign the Bing app in with the account's Microsoft credentials."""
    accounts = rewards_service.get_accounts()
    account = rewards_service.find_account(email)
    if not account or not account.password:
        return "Bing login: skipped (khong co mat khau)"

    say("Dang dang nhap Bing...")
    result = bing_login_service.bing_login(ld_index, email, account.password,
                                           ld_version=ld_version)
    msg = f"Bing login: {result.message}"
    say(msg, "success" if result.success else "error")
    return msg


def _launch_and_install(db_id: int, ld_index: int, install_apps: bool,
                        account_email: Optional[str] = None,
                        sign_in_bing: bool = False,
                        ld_version: int = 9) -> None:
    """Background worker.

    Order: boot -> set Vietnamese locale -> install the apps if missing ->
    configure the OxyProxy entry -> sign in to Bing. Every step is reported
    so the UI can show progress.
    """
    def say(msg: str, level: str = "info"):
        job_progress.step(db_id, msg, level)
        log_activity(level.upper() if level != "info" else "INFO",
                     msg, instance_id=db_id)

    ok = True
    try:
        say("Dang khoi dong LDPlayer...")
        ldplayer_service.launch_instance(ld_index, wait_for_boot=True,
                                         ld_version=ld_version)
        ldplayer_service.sync_instances_to_db()
        say("LDPlayer da len")

        # Vietnamese guest locale; done right after boot while ADB is fresh.
        try:
            if ldplayer_service.set_vietnamese_language(ld_index, ld_version=ld_version):
                say("Da chuyen ngon ngu he thong sang tieng Viet")
        except Exception as e:
            say(f"Khong chuyen duoc ngon ngu: {e}", "warn")

        if not install_apps:
            return

        bing_ok, oxy_ok = _ensure_apps(ld_index, db_id, say, ld_version=ld_version)

        # The proxy entry is only meaningful once OxyProxy is actually there.
        if oxy_ok:
            _setup_oxyproxy_for_account(db_id, ld_index, account_email, say,
                                        ld_version=ld_version)

        if sign_in_bing and account_email and bing_ok:
            _bing_login_for_account(db_id, ld_index, account_email, say,
                                    ld_version=ld_version)
    except Exception as e:
        ok = False
        say(f"Launch/install failed: {e}", "error")
        log_activity("ERROR", f"Launch/install failed: {e}", instance_id=db_id)
    finally:
        ldplayer_service.sync_instances_to_db()
        job_progress.finish_job(db_id, ok)


@app.get("/api/instances/{db_id}/progress")
async def api_instance_progress(db_id: int, since: float = 0.0):
    job_progress.cleanup()
    return job_progress.get_job(db_id, since=since) or {"running": False,
                                                         "done": True,
                                                         "steps": []}


@app.get("/api/instances/{db_id}/health")
async def api_instance_health(db_id: int):
    identity = ldplayer_service.get_instance_identity(db_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity
    serial = ldplayer_service.get_serial(ld_index, ld_version=ld_version)
    return {
        "instance_id": db_id,
        "ld_index": ld_index,
        "ld_version": ld_version,
        "serial": serial,
        "responsive": ldplayer_service.is_responsive(ld_index, ld_version=ld_version),
        "pids": ldplayer_service.get_pids(ld_index, ld_version=ld_version),
        "adb_online": adb_service.ensure_device(serial, timeout=10),
    }


@app.post("/api/instances/{db_id}/restart")
async def api_restart_instance(db_id: int, wait_boot: bool = True,
                               background_tasks: BackgroundTasks = None):
    identity = ldplayer_service.get_instance_identity(db_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity

    def _work():
        ok = ldplayer_service.restart_instance(ld_index, wait_boot=wait_boot,
                                               ld_version=ld_version)
        ldplayer_service.sync_instances_to_db()
        log_activity("INFO" if ok else "ERROR",
                     f"Restart instance {ld_index}: {'OK' if ok else 'FAILED'}",
                     instance_id=db_id)

    background_tasks.add_task(_work)
    return {"success": True, "status": "restarting", "instance_id": db_id}


@app.get("/api/redeemed-ips")
async def api_redeemed_ips(limit: int = 50):
    return {
        "stat": ip_blacklist.stat(),
        "recent": ip_blacklist.get_blacklisted()[-limit:],
    }


@app.post("/api/redeemed-ips/cleanup")
async def api_cleanup_redeemed_ips():
    removed = ip_blacklist.cleanup()
    return {"success": True, "removed": removed, "stat": ip_blacklist.stat()}


@app.post("/api/accounts/{email}/collect-redeem")
async def api_collect_redeem(email: str, save: bool = True):
    """Read the account's current points and newest order from the Bing app,
    then mirror the VIP dashboard's redeem bookkeeping."""
    instance_id = rewards_service.get_mapping(email)
    if not instance_id:
        raise HTTPException(400, "Account chua gan instance")
    identity = ldplayer_service.get_instance_identity(instance_id)
    if identity is None:
        raise HTTPException(400, "Khong tim thay instance")
    ld_index, ld_version = identity
    if not ldplayer_service.is_running(ld_index, ld_version=ld_version):
        raise HTTPException(400, "Instance dang tat, hay mo instance truoc")

    result = bing_redeem_service.collect_redeem_info(ld_index, expected_email=email,
                                                     ld_version=ld_version)
    if not result.success:
        raise HTTPException(400, result.message)

    info = result.message

    # Points bookkeeping.
    #   - points read straight off the live order history header are already
    #     the post-redemption balance, so they are used as-is.
    #   - otherwise the balance is derived from the account totals:
    #     (initial + collected) - order points.
    account = rewards_service.find_account(email)
    final_points = info.points
    if not info.points_from_dom and info.order_points is not None and account:
        base = (account.initial_points or 0) + (account.collected_points or 0)
        if base > 0:
            final_points = max(base - info.order_points, 0)

    payload = {
        "email": info.email,
        "points": info.points,
        "final_points": final_points,
        "points_from_dom": info.points_from_dom,
        "order_date": info.order_date,
        "product": info.product,
        "order_points": info.order_points,
        "order_no": info.order_no,
        "voucher_url": info.voucher_url,
        "reward_type": info.reward_type,
        "saved": False,
    }

    if save and info.order_date:
        try:
            saved = rewards_service.save_redeem_result(
                email, final_points, info.voucher_url, info.reward_type)
            payload["saved"] = True
            payload.update(saved)
            log_activity("INFO",
                         f"Collect redeem {email}: {info.reward_type} "
                         f"points={final_points} url={'co' if info.voucher_url else 'khong'}",
                         instance_id=instance_id)

            # Remember the exit IP so it is never spent on another redemption.
            if account and account.proxy_type == "v6":
                ip = proxy_rotation.get_instance_ip(instance_id)
                if ip:
                    count = ip_blacklist.mark_redeemed(ip, email)
                    payload["redeemed_ip"] = ip
                    payload["ip_success_count"] = count
                    log_activity("INFO",
                                 f"Da ghi nho IP {ip} (lan {count})", instance_id=instance_id)
                else:
                    payload["redeemed_ip"] = None
        except Exception as e:
            payload["error"] = str(e)

    return payload


@app.post("/api/instances/{db_id}/launch")
async def api_launch_instance(db_id: int, install_apps: bool = True,
                              account_email: Optional[str] = None,
                              sign_in_bing: bool = False,
                              background_tasks: BackgroundTasks = None):
    identity = ldplayer_service.get_instance_identity(db_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity

    # Booting + installing can take minutes; keep it off the event loop so the
    # rest of the dashboard stays responsive.
    job_progress.start_job(db_id)
    background_tasks.add_task(_launch_and_install, db_id, ld_index, install_apps,
                              account_email, sign_in_bing, ld_version)
    return {"success": True, "status": "launching", "instance_id": db_id}


@app.post("/api/instances/{db_id}/quit")
async def api_quit_instance(db_id: int):
    try:
        identity = ldplayer_service.get_instance_identity(db_id)
        if identity is None:
            raise HTTPException(404, "Instance not found")
        ld_index, ld_version = identity
        success = ldplayer_service.quit_instance(ld_index, ld_version=ld_version)
        ldplayer_service.sync_instances_to_db()
        return {"success": success}
    except Exception as e:
        raise HTTPException(500, str(e))


def _remove_worker(db_id: int, ld_index: int, ld_version: int = 9) -> None:
    """Delete an instance off the request path.

    `ldconsole remove` wipes the whole disk image and can run for minutes;
    calling it inline froze the entire server because the endpoint is async.
    """
    def say(msg: str, level: str = "info"):
        job_progress.step(db_id, msg, level)
        log_activity(level.upper() if level != "info" else "INFO",
                     msg, instance_id=db_id)

    try:
        if ldplayer_service.is_running(ld_index, ld_version=ld_version):
            say("Dang tat LDPlayer truoc khi xoa...")
            ldplayer_service.quit_instance(ld_index, ld_version=ld_version)
            say("Dang cho may tat han...")
            ldplayer_service._wait_stopped(ld_index, timeout=180, ld_version=ld_version)
        say("Dang xoa LDPlayer, co the lai vai phut...")
        ldplayer_service.remove_instance(ld_index, ld_version=ld_version)
        say("Da xoa LDPlayer", "success")
    except Exception as e:
        say(f"Xoa LDPlayer that bai: {e}", "error")
    finally:
        try:
            ldplayer_service.sync_instances_to_db()
        except Exception:
            pass


@app.post("/api/instances/{db_id}/remove")
async def api_remove_instance(db_id: int, background_tasks: BackgroundTasks = None):
    identity = ldplayer_service.get_instance_identity(db_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity

    job_progress.start_job(db_id)
    background_tasks.add_task(_remove_worker, db_id, ld_index, ld_version)
    return {"success": True, "status": "removing", "instance_id": db_id}


@app.get("/api/instances/{db_id}/serial")
async def api_instance_serial(db_id: int):
    try:
        identity = ldplayer_service.get_instance_identity(db_id)
        if identity is None:
            raise HTTPException(404, "Instance not found")
        ld_index, ld_version = identity
        serial = ldplayer_service.get_serial(ld_index, ld_version=ld_version)
        return {"serial": serial}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/instances/{db_id}/log")
async def api_instance_log(db_id: int):
    with Database.transaction() as conn:
        cursor = conn.execute(
            "SELECT * FROM activity_logs WHERE instance_id = ? ORDER BY created_at DESC LIMIT 100",
            (db_id,),
        )
        logs = [dict_from_row(row) for row in cursor.fetchall()]
    return logs


@app.get("/api/diagnostics/adb")
async def api_adb_diagnostics():
    status = adb_service.get_adb_status()
    try:
        instances = [{"index": i.index, "name": i.name, "status": i.status} for i in ldplayer_service.list_instances()]
    except Exception as e:
        instances = []
        status["ldconsole_error"] = str(e)
    return {"adb": status, "instances": instances}


@app.post("/api/diagnostics/adb/restart")
async def api_adb_restart():
    adb_service.restart_server()
    return {"success": True, "adb": adb_service.get_adb_status()}


@app.get("/api/apps/status")
async def api_apps_status():
    bing = installer_service.verify_apk(settings.apps.bing_apk_path)
    oxy = installer_service.analyze_xapk(settings.apps.oxyproxy_xapk_path)
    return {
        "bing": {
            "path": str(settings.apps.bing_apk_path),
            "exists": settings.apps.bing_apk_path.exists(),
            "valid": bing["valid"],
            "package": bing.get("package_name"),
            "version": bing.get("version_name"),
            "error": bing.get("error"),
        },
        "oxyproxy": {
            "path": str(settings.apps.oxyproxy_xapk_path),
            "exists": settings.apps.oxyproxy_xapk_path.exists(),
            "apk_count": len(oxy.apks),
            "base_apk": oxy.base_apk.name if oxy.base_apk else None,
            "split_count": len(oxy.split_apks),
        },
    }


@app.post("/api/apps/install/bing")
async def api_install_bing(instance_id: int = Form(...)):
    identity = ldplayer_service.get_instance_identity(instance_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity
    success, msg = installer_service.install_bing_to_index(ld_index, ld_version=ld_version)
    return {"success": success, "message": msg}


@app.post("/api/apps/install/oxyproxy")
async def api_install_oxyproxy(instance_id: int = Form(...)):
    identity = ldplayer_service.get_instance_identity(instance_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity
    success, msg = installer_service.install_oxyproxy_to_index(ld_index, ld_version=ld_version)
    return {"success": success, "message": msg}


@app.post("/api/apps/launch/bing")
async def api_launch_bing(instance_id: int = Form(...)):
    identity = ldplayer_service.get_instance_identity(instance_id)
    if identity is None:
        raise HTTPException(404, "Instance not found")
    ld_index, ld_version = identity
    ok = ldplayer_service.launch_app(ld_index, settings.apps.bing_package_name,
                                     ld_version=ld_version)
    return {"success": ok, "message": "Bing launched" if ok else "Failed to launch Bing"}


@app.get("/api/accounts")
async def api_accounts(group: Optional[str] = None, sort: Optional[str] = None,
                       ready: Optional[str] = None):
    accounts = rewards_service.get_accounts()

    # Filter by group
    if group:
        accounts = [a for a in accounts if a.account_group == group]

    # Filter: only accounts with enough points to redeem
    if ready == "true":
        accounts = [a for a in accounts if a.ready_to_redeem]

    # Sort: points (asc/desc) or created date (newest/oldest)
    if sort == "asc":
        accounts = sorted(accounts, key=lambda a: a.points or 0)
    elif sort == "desc":
        accounts = sorted(accounts, key=lambda a: a.points or 0, reverse=True)
    elif sort == "created_asc":
        accounts = sorted(accounts, key=lambda a: a.created_at or 0)
    elif sort == "created_desc":
        accounts = sorted(accounts, key=lambda a: a.created_at or 0, reverse=True)
    
    return [
        {
            "email": a.email,
            "password": a.password,
            "totp_secret": a.totp_secret,
            "recovery_email": a.recovery_email,
            "geo_locale": a.geo_locale,
            "lang_code": a.lang_code,
            "proxy": a.proxy,
            "proxy_parsed": a.proxy_parsed,
            "proxy_display": a.proxy_display,
            "proxy_type": a.proxy_type,
            "tag": a.tag,
            "account_group": a.account_group,
            "created_at": a.created_at,
            "tag": a.tag,
            "coupon_discount": a.coupon_discount,
            "ready_to_redeem": a.ready_to_redeem,
            "points_needed": a.points_needed,
            "points": a.points,
            "suspended": a.suspended,
            "rank": a.rank,
            "instance_id": a.instance_id,
            "instance_name": a.instance_name,
            "instance_status": a.instance_status,
            "has_instance": a.instance_id is not None,
        }
        for a in accounts
    ]


class AssignInstanceRequest(BaseModel):
    instance_id: Optional[int] = None


@app.post("/api/accounts/{email}/assign")
async def api_assign_instance(email: str, request: AssignInstanceRequest):
    try:
        rewards_service.assign_instance(email, request.instance_id)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/accounts/{email}/mapping")
async def api_get_mapping(email: str):
    instance_id = rewards_service.get_mapping(email)
    instance = None
    if instance_id:
        with Database.transaction() as conn:
            cursor = conn.execute("SELECT id, ld_index, name, status FROM instances WHERE id = ?", (instance_id,))
            row = cursor.fetchone()
            if row:
                instance = dict_from_row(row)
    return {"email": email, "instance_id": instance_id, "instance": instance}


@app.get("/api/workflows")
async def api_workflows(limit: int = 50):
    """Recent workflows - feeds the filter dropdown on the Logs page."""
    with Database.transaction() as conn:
        cursor = conn.execute(
            "SELECT id, instance_id, status, current_step, started_at, created_at "
            "FROM workflows ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict_from_row(row) for row in cursor.fetchall()]


@app.get("/api/logs")
async def api_logs(workflow_id: Optional[int] = None, instance_id: Optional[int] = None,
                   level: Optional[str] = None, limit: int = 200):
    with Database.transaction() as conn:
        query = "SELECT * FROM activity_logs WHERE 1=1"
        params = []
        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if instance_id:
            query += " AND instance_id = ?"
            params.append(instance_id)
        if level:
            query += " AND level = ?"
            params.append(level)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cursor = conn.execute(query, params)
        return [dict_from_row(row) for row in cursor.fetchall()]


@app.get("/api/settings")
async def api_get_settings():
    return {
        "ldplayer_path": str(settings.ldplayer.ldplayer_path),
        "ldplayer14_path": str(settings.ldplayer14.ldplayer_path) if settings.ldplayer14.ldplayer_path else "",
        "adb_path": str(settings.ldplayer.adb_path),
        "bing_apk_path": str(settings.apps.bing_apk_path),
        "oxyproxy_xapk_path": str(settings.apps.oxyproxy_xapk_path),
        "default_timeout": settings.ldplayer.default_timeout,
        "boot_timeout": settings.ldplayer.boot_timeout,
        "server_port": settings.server.port,
        "server_host": settings.server.host,
        "bing_package_name": settings.apps.bing_package_name,
        "oxyproxy_package_name": settings.apps.oxyproxy_package_name,
        "bing_order_history_url": settings.apps.bing_order_history_url,
        "connectivity_test_url": settings.apps.connectivity_test_url,
        "database_path": str(settings.database.path),
        "rewards_db_path": str(settings.rewards.db_path),
        "log_level": settings.logging.level,
        "workflow_lock_timeout": settings.workflow_lock_timeout,
        "proxy_rotation_url": proxy_rotation.get_setting(proxy_rotation.SETTING_ROTATION_URL),
        "proxy_global": proxy_rotation.get_setting(proxy_rotation.SETTING_GLOBAL_PROXY),
    }


class ProxySettingsUpdate(BaseModel):
    proxy_rotation_url: Optional[str] = None
    proxy_global: Optional[str] = None


@app.post("/api/settings/proxy")
async def api_update_proxy_settings(update: ProxySettingsUpdate):
    if update.proxy_rotation_url is not None:
        proxy_rotation.set_setting(proxy_rotation.SETTING_ROTATION_URL, update.proxy_rotation_url.strip())
    if update.proxy_global is not None:
        raw = update.proxy_global.strip()
        if raw and not proxy_rotation.parse_proxy_string(raw):
            raise HTTPException(400, "Proxy global khong hop le (canh dang ip:port:user:pass)")
        proxy_rotation.set_setting(proxy_rotation.SETTING_GLOBAL_PROXY, raw)
    return {"success": True, "message": "Proxy settings saved"}


@app.post("/api/settings/proxy/rotate")
async def api_rotate_proxy():
    result = proxy_rotation.rotate_proxy()
    return {
        "success": result.success,
        "proxy": result.proxy,
        "ip": result.ip,
        "message": result.message,
    }


@app.post("/api/settings")
async def api_update_settings(update: SettingsUpdate):
    values = {
        "ldplayer_path": update.ldplayer_path,
        "ldplayer14_path": update.ldplayer14_path,
        "adb_path": update.adb_path,
        "bing_apk_path": update.bing_apk_path,
        "oxyproxy_xapk_path": update.oxyproxy_xapk_path,
        "default_timeout": update.default_timeout,
        "boot_timeout": update.boot_timeout,
        "server_port": update.server_port,
        "server_host": update.server_host,
        "bing_package_name": update.bing_package_name,
        "oxyproxy_package_name": update.oxyproxy_package_name,
        "bing_order_history_url": update.bing_order_history_url,
        "connectivity_test_url": update.connectivity_test_url,
        "database_path": update.database_path,
        "rewards_db_path": update.rewards_db_path,
        "log_level": update.log_level,
        "workflow_lock_timeout": update.workflow_lock_timeout,
    }
    # Must go through apply_settings_update(): the config object is built at
    # import time, so writing only to os.environ never changed what the app
    # (or the Settings form) actually reads.
    warn_list = apply_settings_update(values)
    changes = {k: v for k, v in values.items() if v is not None}

    errors = validate_paths()
    return {"success": len(errors) == 0, "changes": changes, "errors": errors,
            "warnings": warn_list}


class SettingsLoadRequest(BaseModel):
    database_path: Optional[str] = None
    rewards_db_path: Optional[str] = None


@app.post("/api/settings/load")
async def api_load_databases(update: SettingsLoadRequest):
    """The Settings > Database & Logging "Load" button.

    The user picks new *.db files in the form; this endpoint applies those
    paths to the running app WITHOUT a restart and reports what each file
    actually contains. `Save` alone is not enough: `rewards_service` keeps
    its own db_path reference and `Database` keeps one connection per
    worker thread, so the dashboard keeps serving data from the previously
    loaded file until those are re-pointed here.
    """
    values = {}
    if update.database_path and update.database_path.strip():
        values["database_path"] = update.database_path.strip()
    if update.rewards_db_path and update.rewards_db_path.strip():
        values["rewards_db_path"] = update.rewards_db_path.strip()

    errors = []
    rewards_stats = {"path": str(settings.rewards.db_path), "accounts": None, "changed": False}
    app_stats = {"path": str(settings.database.path), "instances": None, "changed": False}

    if values:
        # Applies to the in-memory config AND persists into .env.
        apply_settings_update(values)

    if "rewards_db_path" in values:
        rewards_stats["path"] = str(settings.rewards.db_path)
        rewards_stats["changed"] = True
        rewards_service.set_db_path(settings.rewards.db_path)
        if settings.rewards.db_path.exists():
            try:
                with sqlite3.connect(settings.rewards.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    row = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()
                    rewards_stats["accounts"] = int(row["n"]) if row else 0
            except sqlite3.Error as e:
                errors.append(f"Rewards DB khong doc duoc: {e}")
        else:
            errors.append(f"Rewards DB khong ton tai (se dung khi file xuat hien): {settings.rewards.db_path}")

    if "database_path" in values:
        app_stats["path"] = str(settings.database.path)
        app_stats["changed"] = True
        # sqlite3 cannot create a database inside a missing folder.
        try:
            settings.database.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            errors.append(f"Khong tao duoc thu muc cho app DB: {e}")
        else:
            # Connections cached per worker thread point at the OLD file;
            # drop them and re-create the schema on the new one.
            Database.reopen()
            try:
                Database.initialize()
                with Database.transaction() as conn:
                    row = conn.execute("SELECT COUNT(*) AS n FROM instances").fetchone()
                    app_stats["instances"] = int(row["n"])
            except sqlite3.Error as e:
                errors.append(f"App DB khong doc duoc: {e}")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "rewards_db": rewards_stats,
        "app_db": app_stats,
    }


# ---------------------------------------------------------------------------
# File picker (Settings > Database & Logging)
# ---------------------------------------------------------------------------

def _browse_directory(raw_path: str, raw_exts: str) -> dict:
    """Read-only directory listing that powers the Settings file picker.

    Browsers never expose the full path of a file chosen through
    <input type="file">, so the picker calls the local backend to walk the
    real filesystem instead. Empty/invalid paths fall back to the drive
    list so the user can start browsing from the roots.
    """
    exts = {e.strip().lower() for e in raw_exts.split(",") if e.strip()}

    base = None
    raw = (raw_path or "").strip().strip('"')
    if raw:
        candidate = Path(raw)
        try:
            # A path pointing at a file (e.g. pasted in the box) lists the
            # folder holding it.
            if candidate.is_file():
                candidate = candidate.parent
            if candidate.is_dir():
                base = candidate
        except OSError:
            base = None

    if base is None:
        import string
        drives = [
            {"name": f"{letter}:", "path": f"{letter}:\\", "kind": "drive"}
            for letter in string.ascii_uppercase
            if Path(f"{letter}:\\").exists()
        ]
        if not drives:
            raise HTTPException(500, "Khong tim thay o dia nao tren may")
        return {"path": "", "parent": "", "drives": True, "entries": drives}

    entries = []
    try:
        for item in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            try:
                if item.is_dir():
                    entries.append({"name": item.name, "path": str(item), "kind": "dir"})
                elif item.is_file():
                    if exts and item.suffix.lower() not in exts:
                        continue
                    entries.append({"name": item.name, "path": str(item), "kind": "file"})
            except OSError:
                continue  # entry locked or unreadable (system files)
    except OSError as e:
        raise HTTPException(400, f"Khong doc duoc thu muc: {e}")

    parent = str(base.parent) if base.parent != base else ""
    return {"path": str(base), "parent": parent, "drives": False, "entries": entries}


@app.get("/api/system/browse")
async def api_system_browse(path: str = "", exts: str = ""):
    """Directory listing for the file picker.

    The scan can block on slow/network drives, so it runs off the event
    loop in a thread.
    """
    return await run_in_threadpool(_browse_directory, path, exts)


@app.get("/api/overview")
async def api_overview():
    instances = ldplayer_service.list_instances()
    running = sum(1 for i in instances if i.status == "running")
    stopped = sum(1 for i in instances if i.status == "stopped")

    accounts = rewards_service.get_accounts()
    total_accounts = len(accounts)

    with Database.transaction() as conn:
        cursor = conn.execute(
            "SELECT * FROM activity_logs ORDER BY created_at DESC LIMIT 10"
        )
        recent_logs = [dict_from_row(row) for row in cursor.fetchall()]

    return {
        "total_instances": len(instances),
        "running_instances": running,
        "stopped_instances": stopped,
        "error_instances": 0,
        "running_workflows": 0,
        "recent_logs": recent_logs,
        "total_accounts": total_accounts,
    }


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.server.host, port=settings.server.port, reload=settings.server.reload)