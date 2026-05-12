"""FastAPI admin app for Telegram crawler operations and review workflows."""

import json
import logging
import os
import platform
import re
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import psycopg2
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from psycopg2.extras import RealDictCursor

from auth import LoginRedirect, create_token, get_current_user, hash_password, is_admin, verify_password
from db_util import db_execute

app = FastAPI(title='TG Crawler Admin')
templates = Jinja2Templates(directory='templates')

DB_URL = os.getenv('DATABASE_URL', 'postgresql://tguser:tgpwd@localhost:5432/tg_crawler')
APP_ROOT = os.path.abspath(os.path.dirname(__file__))
REPO_ROOT = os.path.abspath(os.path.join(APP_ROOT, '..'))
SCRIPTS_LOCAL_DIR = os.path.join(REPO_ROOT, 'scripts', 'local')
SYSTEM_LOG_DIR = os.path.join(REPO_ROOT, '.local', 'runtime-logs')
MINIO_API_PORT = 9000
MINIO_CONSOLE_PORT = 9001
MINIO_DATA_DIR = os.path.join(REPO_ROOT, '.local', 'minio', 'data')
SERVICE_START_TIMEOUT_SEC = 12.0
SERVICE_STOP_TIMEOUT_SEC = 8.0
SYSTEM_ACTION_LOCK_TIMEOUT_SEC = 5.0
PLATFORM_IS_WINDOWS = os.name == 'nt'
_SCRIPT_EXT = '.ps1' if PLATFORM_IS_WINDOWS else '.sh'
SERVICE_SCRIPT_NAME = {
    'crawler': f'run-crawler{_SCRIPT_EXT}',
    'minio': f'run-minio{_SCRIPT_EXT}',
}

LOGGER = logging.getLogger(__name__)
SYSTEM_ACTION_LOCK = threading.Lock()


def _parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    if value in {'1', 'true', 'yes', 'y', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'n', 'off'}:
        return False
    return None


def _parse_tags(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    tags = [t.strip() for t in raw.replace('，', ',').split(',') if t.strip()]
    deduped = list(dict.fromkeys(tags))
    return deduped or None


def _normalize_code(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r'[`\s]+', '', text)
    text = re.sub(r'[^A-Za-z0-9_-]', '', text)
    return text or None


def _normalize_code_key(value: Optional[str]) -> Optional[str]:
    text = _normalize_code(value)
    if not text:
        return None
    text = re.sub(r'[^A-Za-z0-9]+', '', text)
    text = text.lower()
    return text or None


def _query_string(params: Dict[str, Any]) -> str:
    filtered = {}
    for key, value in params.items():
        if value in (None, ''):
            continue
        filtered[key] = value
    return urlencode(filtered, doseq=True)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _require_positive_page_size(value: int) -> int:
    return 20 if value < 1 else (100 if value > 100 else value)


def _require_admin_user(user: Dict[str, Any]):
    if not is_admin(user):
        raise HTTPException(403, '仅管理员可执行该操作')


def _parse_channel_lines(raw: Optional[str]) -> List[str]:
    if raw is None:
        return []
    text = raw.replace('\r', '\n').replace(',', '\n')
    values = []
    for part in text.split('\n'):
        channel = part.strip().lstrip('@')
        if not channel:
            continue
        values.append(channel)
    return list(dict.fromkeys(values))


def _append_message_scope(user: Dict[str, Any], conditions: List[str], params: Dict[str, Any], alias: str = 'm'):
    if is_admin(user):
        return
    conditions.append(f'{alias}.owner_user_id = %(viewer_user_id)s')
    params['viewer_user_id'] = int(user['id'])


def _ensure_message_access(db, user: Dict[str, Any], msg_id: int):
    if is_admin(user):
        return
    row = db_execute(
        db,
        'SELECT id FROM messages WHERE id = %s AND owner_user_id = %s LIMIT 1',
        (msg_id, user['id']),
    ).fetchone()
    if not row:
        raise HTTPException(404, '消息不存在或无权限访问')


def _ps_quote(text: str) -> str:
    """Escapes a string as a single-quoted PowerShell literal."""
    return "'" + text.replace("'", "''") + "'"


def _shell_quote(text: str) -> str:
    """Escapes a string for safe use in POSIX shell commands."""
    return "'" + text.replace("'", "'\\''") + "'"


def _run_powershell(ps_command: str, timeout_sec: float = 30.0) -> subprocess.CompletedProcess:
    """Executes a PowerShell command and captures both stdout and stderr."""
    return subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps_command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )


def _run_shell(shell_command: str, timeout_sec: float = 30.0) -> subprocess.CompletedProcess:
    """Executes a POSIX shell command and captures both stdout and stderr."""
    return subprocess.run(
        ['sh', '-c', shell_command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )


def _is_port_listening(port: int, host: str = '127.0.0.1', timeout: float = 0.5) -> bool:
    """Checks if a TCP port is reachable from the current process."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_key_or_400(service: str) -> str:
    """Normalizes and validates a service key from route parameters."""
    service_key = (service or '').strip().lower()
    if service_key not in SERVICE_SCRIPT_NAME:
        raise HTTPException(400, f'不支持的服务: {service_key}')
    return service_key


def _service_script_name(service: str) -> str:
    """Returns the startup script name for a valid service key."""
    return SERVICE_SCRIPT_NAME[_service_key_or_400(service)]


def _service_log_path(service: str) -> str:
    """Builds a deterministic per-service launch log file path."""
    return os.path.join(SYSTEM_LOG_DIR, f'{service}-launcher.log')


def _load_env_file(path: str) -> Dict[str, str]:
    """Parses an env file using KEY=VALUE lines.

    Args:
        path: Absolute path of .env-like file.

    Returns:
        Dict with parsed key-value entries.
    """
    if not os.path.exists(path):
        return {}

    parsed: Dict[str, str] = {}
    with open(path, 'r', encoding='utf-8') as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
                value = value[1:-1]
            if key:
                parsed[key] = value
    return parsed


def _effective_env_map() -> Dict[str, str]:
    """Merges runtime env with .env and .env.local values.

    Env file values follow the same precedence as local run scripts:
    .env then .env.local, then shell env as highest priority.
    """
    merged: Dict[str, str] = {}
    merged.update(_load_env_file(os.path.join(REPO_ROOT, '.env')))
    merged.update(_load_env_file(os.path.join(REPO_ROOT, '.env.local')))
    merged.update({k: str(v) for k, v in os.environ.items() if v is not None})
    return merged


def _validate_service_start_env(service: str) -> None:
    """Validates required environment values before starting a service."""
    service_key = _service_key_or_400(service)
    if service_key != 'crawler':
        return

    env_map = _effective_env_map()
    missing = [name for name in ('TG_API_ID', 'TG_API_HASH', 'TG_PHONE') if not env_map.get(name, '').strip()]
    if missing:
        raise HTTPException(400, f"缺少 crawler 配置: {', '.join(missing)}。请在 .env.local 或系统环境变量中设置")


def _collect_windows_process_status() -> Dict[str, List[int]]:
    """Collects process IDs for web/crawler/minio in Windows host mode."""
    repo_q = _ps_quote(REPO_ROOT)
    minio_data_q = _ps_quote(MINIO_DATA_DIR)
    command = (
        f"$repo = {repo_q};"
        f"$minioData = {minio_data_q};"
        "$webDir = Join-Path $repo 'web';"
        "$crawlerDir = Join-Path $repo 'crawler';"
        "$webEsc = [regex]::Escape($webDir);"
        "$crawlerEsc = [regex]::Escape($crawlerDir);"
        "$minioDataEsc = [regex]::Escape($minioData);"
        "$webProc = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match $webEsc -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'main:app' };"
        "$crawlerProc = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine -match $crawlerEsc -and $_.CommandLine -match 'main.py' };"
        "$minioProc = Get-CimInstance Win32_Process -Filter \"Name='minio.exe'\" | "
        "Where-Object { $_.CommandLine -and ($_.CommandLine -match ':9000' -or $_.CommandLine -match $minioDataEsc) };"
        "[pscustomobject]@{"
        "web_pids = @($webProc | ForEach-Object { $_.ProcessId });"
        "crawler_pids = @($crawlerProc | ForEach-Object { $_.ProcessId });"
        "minio_pids = @($minioProc | ForEach-Object { $_.ProcessId })"
        "} | ConvertTo-Json -Compress -Depth 4"
    )
    result = _run_powershell(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'PowerShell status command failed')

    raw = (result.stdout or '').strip()
    if not raw:
        return {'web_pids': [], 'crawler_pids': [], 'minio_pids': []}

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}

    def _to_int_list(value: Any) -> List[int]:
        if value is None:
            return []
        if isinstance(value, list):
            return [int(v) for v in value]
        return [int(value)]

    return {
        'web_pids': _to_int_list(parsed.get('web_pids')),
        'crawler_pids': _to_int_list(parsed.get('crawler_pids')),
        'minio_pids': _to_int_list(parsed.get('minio_pids')),
    }


def _collect_unix_process_status() -> Dict[str, List[int]]:
    """Collects process IDs for web/crawler/minio on macOS/Linux via ps."""
    web_pids: List[int] = []
    crawler_pids: List[int] = []
    minio_pids: List[int] = []

    try:
        result = subprocess.run(
            ['ps', '-eo', 'pid,command'],
            capture_output=True, text=True, check=False, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) < 2:
                    continue
                pid_str, cmd = parts
                try:
                    pid = int(pid_str)
                except ValueError:
                    continue

                if 'uvicorn' in cmd and 'main:app' in cmd and REPO_ROOT in cmd:
                    web_pids.append(pid)
                elif 'main.py' in cmd and os.path.join(REPO_ROOT, 'crawler') in cmd:
                    crawler_pids.append(pid)
                elif 'minio' in cmd and (':9000' in cmd or MINIO_DATA_DIR in cmd):
                    minio_pids.append(pid)
    except Exception:
        pass

    return {
        'web_pids': web_pids,
        'crawler_pids': crawler_pids,
        'minio_pids': minio_pids,
    }


def _collect_process_status() -> Dict[str, List[int]]:
    """Collects process IDs for web/crawler/minio on current platform."""
    if PLATFORM_IS_WINDOWS:
        return _collect_windows_process_status()
    return _collect_unix_process_status()


def _collect_runtime_status(db) -> Dict[str, Any]:
    """Builds runtime health status for DB and local processes."""
    db_ready = True
    try:
        db_execute(db, 'SELECT 1').fetchone()
    except Exception:
        db_ready = False

    proc = {'web_pids': [], 'crawler_pids': [], 'minio_pids': []}
    process_error = ''
    try:
        proc = _collect_process_status()
    except Exception as exc:
        process_error = str(exc)
        LOGGER.warning('Failed to collect process status: %s', exc)

    minio_api_ready = _is_port_listening(MINIO_API_PORT)
    minio_console_ready = _is_port_listening(MINIO_CONSOLE_PORT)

    return {
        'database': {'reachable': db_ready},
        'services': {
            'web': {'running': len(proc['web_pids']) > 0, 'pids': proc['web_pids']},
            'crawler': {'running': len(proc['crawler_pids']) > 0, 'pids': proc['crawler_pids']},
            'minio': {
                'running': len(proc['minio_pids']) > 0,
                'pids': proc['minio_pids'],
                'api_port': MINIO_API_PORT,
                'console_port': MINIO_CONSOLE_PORT,
                'api_port_ready': minio_api_ready,
                'console_port_ready': minio_console_ready,
            },
        },
        'ready': {
            'crawler_pipeline': db_ready and minio_api_ready and len(proc['crawler_pids']) > 0,
        },
        'warnings': {'process_probe': process_error},
    }


def _wait_for_service_state(db, service: str, expected_running: bool, timeout_sec: float) -> Dict[str, Any]:
    """Polls service state until expected_running or timeout is reached."""
    service_key = _service_key_or_400(service)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        status = _collect_runtime_status(db)
        running = bool(status['services'][service_key]['running'])
        if running == expected_running:
            return status
        time.sleep(0.25)
    return _collect_runtime_status(db)


def _acquire_system_action_lock() -> None:
    """Acquires the system control lock or raises an HTTP conflict."""
    acquired = SYSTEM_ACTION_LOCK.acquire(timeout=SYSTEM_ACTION_LOCK_TIMEOUT_SEC)
    if not acquired:
        raise HTTPException(409, '系统操作繁忙，请稍后重试')


def _release_system_action_lock() -> None:
    """Releases the system control lock when currently held."""
    if SYSTEM_ACTION_LOCK.locked():
        SYSTEM_ACTION_LOCK.release()


def _local_script_path(script_name: str) -> str:
    """Resolves and validates a local startup script path."""
    path = os.path.join(SCRIPTS_LOCAL_DIR, script_name)
    if not os.path.exists(path):
        raise HTTPException(500, f'启动脚本不存在: {path}')
    return path


def _start_local_service_script(script_name: str) -> str:
    """Starts a local service script in detached mode and returns log path."""
    script_path = _local_script_path(script_name)
    service_name = script_name.replace('run-', '').replace('.ps1', '').replace('.sh', '')
    os.makedirs(SYSTEM_LOG_DIR, exist_ok=True)
    log_path = _service_log_path(service_name)

    if PLATFORM_IS_WINDOWS:
        launch_command = f"& {_ps_quote(script_path)} *>> {_ps_quote(log_path)}"
        creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        subprocess.Popen(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', launch_command],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=False,
        )
    else:
        log_handle = open(log_path, 'a', encoding='utf-8')
        subprocess.Popen(
            ['bash', script_path],
            cwd=REPO_ROOT,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )

    return log_path


def _stop_local_service(service: str) -> List[int]:
    """Stops local crawler or minio process group by commandline fingerprint."""
    service_key = _service_key_or_400(service)

    if PLATFORM_IS_WINDOWS:
        return _stop_local_service_windows(service_key)
    return _stop_local_service_unix(service_key)


def _stop_local_service_windows(service_key: str) -> List[int]:
    """Stops a service on Windows using PowerShell CIM queries."""
    repo_q = _ps_quote(REPO_ROOT)
    minio_data_q = _ps_quote(MINIO_DATA_DIR)
    if service_key == 'crawler':
        command = (
            f"$repo = {repo_q};"
            "$crawlerDir = Join-Path $repo 'crawler';"
            "$crawlerEsc = [regex]::Escape($crawlerDir);"
            "$targets = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -and $_.CommandLine -match $crawlerEsc -and $_.CommandLine -match 'main.py' };"
            "$ids = @($targets | ForEach-Object { $_.ProcessId });"
            "if ($ids.Count -gt 0) { Stop-Process -Id $ids -Force };"
            "[pscustomobject]@{ killed = @($ids) } | ConvertTo-Json -Compress"
        )
    else:
        command = (
            f"$minioData = {minio_data_q};"
            "$minioDataEsc = [regex]::Escape($minioData);"
            "$targets = Get-CimInstance Win32_Process -Filter \"Name='minio.exe'\" | "
            "Where-Object { $_.CommandLine -and ($_.CommandLine -match ':9000' -or $_.CommandLine -match $minioDataEsc) };"
            "$ids = @($targets | ForEach-Object { $_.ProcessId });"
            "if ($ids.Count -gt 0) { Stop-Process -Id $ids -Force };"
            "[pscustomobject]@{ killed = @($ids) } | ConvertTo-Json -Compress"
        )

    result = _run_powershell(command)
    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip() or result.stdout.strip() or '停止服务失败')

    raw = (result.stdout or '').strip()
    if not raw:
        return []

    data = json.loads(raw)
    killed = data.get('killed') if isinstance(data, dict) else []
    if killed is None:
        return []
    if isinstance(killed, list):
        return [int(v) for v in killed]
    return [int(killed)]


def _stop_local_service_unix(service_key: str) -> List[int]:
    """Stops a service on macOS/Linux using ps + kill."""
    proc_status = _collect_process_status()
    pids = proc_status.get(f'{service_key}_pids', [])
    if not pids:
        return []

    killed: List[int] = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            pass

    time.sleep(0.5)
    for pid in killed:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    return killed


@app.exception_handler(LoginRedirect)
async def _login_redirect_handler(request: Request, exc: LoginRedirect):
    return RedirectResponse(url='/login', status_code=302)


def get_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def _upsert_profile(db, msg_id: int, payload: Dict[str, Any]):
    payload = dict(payload)
    payload['internal_code'] = _normalize_code(payload.get('internal_code'))

    existing = db_execute(
        db,
        'SELECT id FROM profiles WHERE message_id = %s ORDER BY id LIMIT 1',
        (msg_id,),
    ).fetchone()

    fields = [
        'display_nickname',
        'internal_code',
        'province',
        'city',
        'age',
        'height',
        'weight',
        'cup_size',
        'occupation',
        'introduction_fee',
        'monthly_allowance',
    ]

    if existing:
        sql = """
            UPDATE profiles
            SET display_nickname = %s,
                internal_code = %s,
                province = %s,
                city = %s,
                age = %s,
                height = %s,
                weight = %s,
                cup_size = %s,
                occupation = %s,
                introduction_fee = %s,
                monthly_allowance = %s,
                updated_at = NOW()
            WHERE id = %s
        """
        values = [payload.get(f) for f in fields] + [existing['id']]
        db_execute(db, sql, tuple(values))
    else:
        sql = """
            INSERT INTO profiles (
                message_id,
                display_nickname,
                internal_code,
                province,
                city,
                age,
                height,
                weight,
                cup_size,
                occupation,
                introduction_fee,
                monthly_allowance
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = [msg_id] + [payload.get(f) for f in fields]
        db_execute(db, sql, tuple(values))


# ==================== 页面路由 ====================


@app.get('/', response_class=HTMLResponse)
async def index(
    request: Request,
    status: Optional[str] = Query(None),
    province: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    age_min: Optional[str] = Query(None),
    age_max: Optional[str] = Query(None),
    fee_min: Optional[str] = Query(None),
    fee_max: Optional[str] = Query(None),
    cup: Optional[str] = Query(None),
    occupation: Optional[str] = Query(None),
    confidence_min: Optional[str] = Query(None),
    confidence_max: Optional[str] = Query(None),
    has_media: Optional[str] = Query(None),
    is_flagged: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    order_by: str = Query('telegram_date'),
    order_dir: str = Query('desc'),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    runtime_status = _collect_runtime_status(db)

    age_min_num = _parse_int(age_min)
    age_max_num = _parse_int(age_max)
    fee_min_num = _parse_float(fee_min)
    fee_max_num = _parse_float(fee_max)
    conf_min_num = _parse_float(confidence_min)
    conf_max_num = _parse_float(confidence_max)
    has_media_bool = _parse_bool(has_media)
    flagged_bool = _parse_bool(is_flagged)
    page_size = _require_positive_page_size(page_size)

    age_expr = "COALESCE(p.age, CASE WHEN (m.extracted_json->>'age') ~ '^[0-9]+$' THEN (m.extracted_json->>'age')::int END)"
    fee_expr = "COALESCE(p.introduction_fee, CASE WHEN (m.extracted_json->>'intro_fee') ~ '^[0-9]+(\\.[0-9]+)?$' THEN (m.extracted_json->>'intro_fee')::numeric END)"

    conditions = ['1=1']
    params: Dict[str, Any] = {}
    _append_message_scope(user, conditions, params, alias='m')
    _append_message_scope(user, conditions, params, alias='m')

    if status:
        conditions.append('m.review_status = %(status)s')
        params['status'] = status
    if province:
        conditions.append("(p.province ILIKE %(province)s OR m.extracted_json->>'province' ILIKE %(province)s)")
        params['province'] = f'%{province}%'
    if city:
        conditions.append("(p.city ILIKE %(city)s OR m.extracted_json->>'city' ILIKE %(city)s)")
        params['city'] = f'%{city}%'
    if age_min_num is not None:
        conditions.append(f'{age_expr} >= %(age_min)s')
        params['age_min'] = age_min_num
    if age_max_num is not None:
        conditions.append(f'{age_expr} <= %(age_max)s')
        params['age_max'] = age_max_num
    if fee_min_num is not None:
        conditions.append(f'{fee_expr} >= %(fee_min)s')
        params['fee_min'] = fee_min_num
    if fee_max_num is not None:
        conditions.append(f'{fee_expr} <= %(fee_max)s')
        params['fee_max'] = fee_max_num
    if cup:
        conditions.append("COALESCE(p.cup_size, m.extracted_json->>'cup') ILIKE %(cup)s")
        params['cup'] = f'%{cup}%'
    if occupation:
        conditions.append("COALESCE(p.occupation, m.extracted_json->>'occupation') ILIKE %(occ)s")
        params['occ'] = f'%{occupation}%'
    if conf_min_num is not None:
        conditions.append('m.extract_confidence >= %(conf_min)s')
        params['conf_min'] = conf_min_num
    if conf_max_num is not None:
        conditions.append('m.extract_confidence <= %(conf_max)s')
        params['conf_max'] = conf_max_num
    if has_media_bool is not None:
        conditions.append('m.has_media = %(has_media)s')
        params['has_media'] = has_media_bool
    if flagged_bool is not None:
        conditions.append('m.is_flagged = %(flagged)s')
        params['flagged'] = flagged_bool
    if keyword:
        conditions.append("(m.text_content ILIKE %(kw)s OR m.extracted_json::text ILIKE %(kw)s OR EXISTS (SELECT 1 FROM media_files mf WHERE mf.message_id = m.id AND mf.ocr_text ILIKE %(kw)s))")
        params['kw'] = f'%{keyword}%'

    where_clause = ' AND '.join(conditions)

    allowed_orders = {
        'telegram_date': 'm.telegram_date',
        'extract_confidence': 'm.extract_confidence',
        'created_at': 'm.created_at',
        'introduction_fee': fee_expr,
    }
    sort_col = allowed_orders.get(order_by, 'm.telegram_date')
    sort_dir = 'DESC' if order_dir.lower() == 'desc' else 'ASC'

    summary_sql = f"""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE m.review_status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE m.review_status = 'approved') AS approved,
            COUNT(*) FILTER (WHERE m.review_status = 'rejected') AS rejected,
            COUNT(*) FILTER (WHERE m.review_status = 'need_review') AS need_review,
            COUNT(*) FILTER (WHERE m.is_flagged = true) AS flagged,
            COUNT(*) FILTER (WHERE m.has_media = true) AS with_media
        FROM messages m
        LEFT JOIN profiles p ON p.message_id = m.id
        WHERE {where_clause}
    """
    filtered_stats = db_execute(db, summary_sql, dict(params)).fetchone()
    total = filtered_stats['total']

    overview_stats = db_execute(
        db,
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE review_status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE review_status = 'approved') AS approved,
            COUNT(*) FILTER (WHERE review_status = 'rejected') AS rejected,
            COUNT(*) FILTER (WHERE review_status = 'need_review') AS need_review,
            COUNT(*) FILTER (WHERE is_flagged = true) AS flagged,
            COUNT(*) FILTER (WHERE has_media = true) AS with_media,
            COUNT(*) FILTER (WHERE created_at >= date_trunc('day', NOW())) AS today
        FROM messages
        """,
    ).fetchone()

    top_channels = db_execute(
        db,
        """
        SELECT c.username, COUNT(*) AS cnt
        FROM messages m
        LEFT JOIN channels c ON c.id = m.channel_id
        GROUP BY c.username
        ORDER BY cnt DESC
        LIMIT 6
        """,
    ).fetchall()

    offset = (page - 1) * page_size
    query_sql = f"""
        SELECT
            m.id, m.telegram_message_id, m.telegram_date, m.text_content,
            m.extract_confidence, m.review_status, m.is_flagged, m.has_media,
            m.extracted_json, m.created_at, m.manual_tags,
            COALESCE(p.display_nickname, m.extracted_json->>'nickname') AS nickname,
            COALESCE(p.province, m.extracted_json->>'province') AS province,
            COALESCE(p.city, m.extracted_json->>'city') AS city,
            {age_expr} AS age,
            COALESCE(p.height, CASE WHEN (m.extracted_json->>'height') ~ '^[0-9]+$' THEN (m.extracted_json->>'height')::int END) AS height,
            COALESCE(p.weight, CASE WHEN (m.extracted_json->>'weight') ~ '^[0-9]+$' THEN (m.extracted_json->>'weight')::int END) AS weight,
            COALESCE(p.cup_size, m.extracted_json->>'cup') AS cup_size,
            COALESCE(p.occupation, m.extracted_json->>'occupation') AS occupation,
            {fee_expr} AS introduction_fee,
            COALESCE(p.monthly_allowance, CASE WHEN (m.extracted_json->>'monthly_allowance') ~ '^[0-9]+(\\.[0-9]+)?$' THEN (m.extracted_json->>'monthly_allowance')::numeric END) AS monthly_allowance,
            c.username AS channel_name,
            (SELECT COUNT(*) FROM media_files WHERE message_id = m.id) AS media_count
        FROM messages m
        LEFT JOIN profiles p ON p.message_id = m.id
        LEFT JOIN channels c ON c.id = m.channel_id
        WHERE {where_clause}
        ORDER BY {sort_col} {sort_dir}, m.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    query_params = dict(params)
    query_params['limit'] = page_size
    query_params['offset'] = offset
    rows = db_execute(db, query_sql, query_params).fetchall()

    provinces = db_execute(
        db,
        """
        SELECT DISTINCT COALESCE(p.province, m.extracted_json->>'province') AS p
        FROM messages m
        LEFT JOIN profiles p ON p.message_id = m.id
        WHERE COALESCE(p.province, m.extracted_json->>'province') IS NOT NULL
        ORDER BY p
        """,
    ).fetchall()

    total_pages = (total + page_size - 1) // page_size

    filter_values = {
        'status': status or '',
        'province': province or '',
        'city': city or '',
        'age_min': age_min or '',
        'age_max': age_max or '',
        'fee_min': fee_min or '',
        'fee_max': fee_max or '',
        'cup': cup or '',
        'occupation': occupation or '',
        'confidence_min': confidence_min or '',
        'confidence_max': confidence_max or '',
        'has_media': 'true' if has_media_bool else '',
        'is_flagged': 'true' if flagged_bool else '',
        'keyword': keyword or '',
        'order_by': order_by,
        'order_dir': order_dir,
        'page_size': page_size,
    }
    page_query = _query_string(filter_values)

    sort_latest_query = _query_string({**filter_values, 'order_by': 'telegram_date', 'order_dir': 'desc'})
    sort_conf_query = _query_string({**filter_values, 'order_by': 'extract_confidence', 'order_dir': 'desc'})
    sort_fee_query = _query_string({**filter_values, 'order_by': 'introduction_fee', 'order_dir': 'desc'})

    return templates.TemplateResponse(
        request=request,
        name='list.html',
        context={
            'user': user,
            'rows': rows,
            'runtime_status': runtime_status,
            'provinces': [r['p'] for r in provinces],
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': total_pages},
            'filters': filter_values,
            'stats_overview': overview_stats,
            'stats_filtered': filtered_stats,
            'top_channels': top_channels,
            'sort_latest_query': sort_latest_query,
            'sort_conf_query': sort_conf_query,
            'sort_fee_query': sort_fee_query,
            'page_query': page_query,
        },
    )


@app.get('/ops', response_class=HTMLResponse)
async def ops_page(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin_user(user)
    runtime_status = _collect_runtime_status(db)
    return templates.TemplateResponse(
        request=request,
        name='ops.html',
        context={
            'user': user,
            'runtime_status': runtime_status,
        },
    )


@app.get('/users', response_class=HTMLResponse)
async def users_page(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin_user(user)

    rows = db_execute(
        db,
        """
        SELECT
            id,
            username,
            COALESCE(full_name, '') AS full_name,
            COALESCE(email, '') AS email,
            role,
            is_active,
            COALESCE(must_change_password, false) AS must_change_password,
            created_at
        FROM reviewers
        ORDER BY id ASC
        """,
    ).fetchall()

    return templates.TemplateResponse(
        request=request,
        name='users.html',
        context={
            'user': user,
            'rows': rows,
        },
    )


@app.get('/account', response_class=HTMLResponse)
async def account_page(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(
        request=request,
        name='account.html',
        context={
            'user': user,
        },
    )


@app.get('/settings', response_class=HTMLResponse)
async def settings_page(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    cfg = db_execute(
        db,
        """
        SELECT
            user_id,
            tg_api_id,
            COALESCE(tg_api_hash, '') AS tg_api_hash,
            COALESCE(tg_phone, '') AS tg_phone,
            COALESCE(tg_proxy_type, '') AS tg_proxy_type,
            COALESCE(tg_proxy_host, '') AS tg_proxy_host,
            tg_proxy_port,
            COALESCE(tg_proxy_username, '') AS tg_proxy_username,
            COALESCE(tg_proxy_password, '') AS tg_proxy_password,
            COALESCE(target_channels, '{}'::text[]) AS target_channels,
            updated_at
        FROM user_crawler_settings
        WHERE user_id = %s
        """,
        (user['id'],),
    ).fetchone()

    if not cfg:
        cfg = {
            'tg_api_id': None,
            'tg_api_hash': '',
            'tg_phone': '',
            'tg_proxy_type': '',
            'tg_proxy_host': '',
            'tg_proxy_port': None,
            'tg_proxy_username': '',
            'tg_proxy_password': '',
            'target_channels': [],
            'updated_at': None,
        }

    return templates.TemplateResponse(
        request=request,
        name='settings.html',
        context={
            'user': user,
            'cfg': cfg,
        },
    )


@app.get('/persons', response_class=HTMLResponse)
async def persons_page(
    request: Request,
    keyword: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    province: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    age_min: Optional[str] = Query(None),
    age_max: Optional[str] = Query(None),
    fee_min: Optional[str] = Query(None),
    fee_max: Optional[str] = Query(None),
    has_media: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    age_min_num = _parse_int(age_min)
    age_max_num = _parse_int(age_max)
    fee_min_num = _parse_float(fee_min)
    fee_max_num = _parse_float(fee_max)
    has_media_bool = _parse_bool(has_media)
    page_size = _require_positive_page_size(page_size)

    code_norm_expr = "LOWER(REGEXP_REPLACE(COALESCE(p.internal_code, ''), '[^a-zA-Z0-9]+', '', 'g'))"
    person_key_expr = (
        f"CASE "
        f"WHEN {code_norm_expr} <> '' THEN 'code:' || {code_norm_expr} "
        f"WHEN m.media_group_id IS NOT NULL THEN 'album:' || m.channel_id::text || ':' || m.media_group_id::text "
        f"ELSE 'msg:' || m.id::text END"
    )

    conditions = ['1=1']
    params: Dict[str, Any] = {}
    if keyword:
        kw_code_norm = _normalize_code_key(keyword)
        conditions.append(
            f"(" \
            f"COALESCE(p.display_nickname, '') ILIKE %(kw)s OR " \
            f"COALESCE(p.internal_code, '') ILIKE %(kw)s OR " \
            f"COALESCE(m.text_content, '') ILIKE %(kw)s OR " \
            f"COALESCE(c.username, '') ILIKE %(kw)s OR " \
            f"{code_norm_expr} ILIKE %(kw_code)s)"
        )
        params['kw'] = f'%{keyword}%'
        params['kw_code'] = f"%{kw_code_norm or ''}%"
    if code:
        code_norm = _normalize_code_key(code)
        if code_norm:
            conditions.append(f"{code_norm_expr} ILIKE %(code)s")
            params['code'] = f'%{code_norm}%'
        else:
            conditions.append('COALESCE(p.internal_code, \'\') ILIKE %(code_raw)s')
            params['code_raw'] = f'%{code}%'
    if province:
        conditions.append('COALESCE(p.province, \'\') ILIKE %(province)s')
        params['province'] = f'%{province}%'
    if city:
        conditions.append('COALESCE(p.city, \'\') ILIKE %(city)s')
        params['city'] = f'%{city}%'
    if age_min_num is not None:
        conditions.append('p.age >= %(age_min)s')
        params['age_min'] = age_min_num
    if age_max_num is not None:
        conditions.append('p.age <= %(age_max)s')
        params['age_max'] = age_max_num
    if fee_min_num is not None:
        conditions.append('p.introduction_fee >= %(fee_min)s')
        params['fee_min'] = fee_min_num
    if fee_max_num is not None:
        conditions.append('p.introduction_fee <= %(fee_max)s')
        params['fee_max'] = fee_max_num
    if has_media_bool is not None:
        if has_media_bool:
            conditions.append('EXISTS (SELECT 1 FROM media_files mf WHERE mf.message_id = m.id)')
        else:
            conditions.append('NOT EXISTS (SELECT 1 FROM media_files mf WHERE mf.message_id = m.id)')

    where_clause = ' AND '.join(conditions)

    count_sql = f"""
        WITH base AS (
            SELECT {person_key_expr} AS person_key
            FROM profiles p
            LEFT JOIN messages m ON m.id = p.message_id
            LEFT JOIN channels c ON c.id = m.channel_id
            WHERE {where_clause}
        )
        SELECT COUNT(DISTINCT person_key) AS cnt FROM base
    """
    total = db_execute(db, count_sql, params).fetchone()['cnt']

    offset = (page - 1) * page_size
    query_sql = f"""
        WITH base AS (
            SELECT
                p.id AS person_id,
                p.message_id,
                p.display_nickname,
                p.internal_code,
                p.province,
                p.city,
                p.age,
                p.height,
                p.weight,
                p.cup_size,
                p.occupation,
                p.introduction_fee,
                p.monthly_allowance,
                p.tags,
                p.contact_info,
                p.updated_at,
                m.telegram_message_id,
                m.telegram_date,
                c.username AS channel_name,
                COALESCE(mc.media_count, 0) AS media_count,
                mp.preview_url,
                mp.media_type AS preview_media_type,
                mp.s3_url AS preview_s3_url,
                {person_key_expr} AS person_key
            FROM profiles p
            LEFT JOIN messages m ON m.id = p.message_id
            LEFT JOIN channels c ON c.id = m.channel_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS media_count
                FROM media_files mf
                WHERE mf.message_id = m.id
            ) mc ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    mf.media_type,
                    COALESCE(mf.thumb_url, mf.s3_url) AS preview_url,
                    mf.s3_url
                FROM media_files mf
                WHERE mf.message_id = m.id
                ORDER BY
                    CASE WHEN mf.media_type = 'photo' THEN 0 WHEN mf.media_type = 'video' THEN 1 ELSE 2 END,
                    mf.id ASC
                LIMIT 1
            ) mp ON TRUE
            WHERE {where_clause}
        ), ranked AS (
            SELECT
                b.*,
                ROW_NUMBER() OVER (PARTITION BY b.person_key ORDER BY b.updated_at DESC NULLS LAST, b.person_id DESC) AS rn,
                COUNT(*) OVER (PARTITION BY b.person_key) AS grouped_records,
                SUM(b.media_count) OVER (PARTITION BY b.person_key) AS grouped_media_count
            FROM base b
        )
        SELECT *
        FROM ranked
        WHERE rn = 1
        ORDER BY updated_at DESC NULLS LAST, person_id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """

    query_params = dict(params)
    query_params['limit'] = page_size
    query_params['offset'] = offset
    rows = db_execute(db, query_sql, query_params).fetchall()

    provinces = db_execute(
        db,
        'SELECT DISTINCT province FROM profiles WHERE province IS NOT NULL ORDER BY province',
    ).fetchall()

    total_pages = (total + page_size - 1) // page_size
    filters = {
        'keyword': keyword or '',
        'code': code or '',
        'province': province or '',
        'city': city or '',
        'age_min': age_min or '',
        'age_max': age_max or '',
        'fee_min': fee_min or '',
        'fee_max': fee_max or '',
        'has_media': 'true' if has_media_bool else ('false' if has_media_bool is False else ''),
        'page_size': page_size,
    }
    page_query = _query_string(filters)

    return templates.TemplateResponse(
        request=request,
        name='persons.html',
        context={
            'user': user,
            'rows': rows,
            'provinces': [r['province'] for r in provinces],
            'filters': filters,
            'page_query': page_query,
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': total_pages},
        },
    )


@app.get('/persons/group', response_class=HTMLResponse)
async def person_group_page(
    request: Request,
    person_key: str = Query(...),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    code_norm_expr = "LOWER(REGEXP_REPLACE(COALESCE(p.internal_code, ''), '[^a-zA-Z0-9]+', '', 'g'))"
    params: Dict[str, Any] = {}
    if person_key.startswith('code:'):
        code_norm = _normalize_code_key(person_key[5:])
        if not code_norm:
            raise HTTPException(400, '无效人物分组 key')
        where_clause = f"{code_norm_expr} = %(code_norm)s"
        params['code_norm'] = code_norm
        group_label = f'编号 {code_norm.upper()}'
    elif person_key.startswith('album:'):
        album_value = person_key[6:]
        parts = album_value.split(':', 1)
        if len(parts) != 2:
            raise HTTPException(400, '无效人物分组 key')
        channel_id = _parse_int(parts[0])
        media_group_id = _parse_int(parts[1])
        if not channel_id or not media_group_id:
            raise HTTPException(400, '无效人物分组 key')
        where_clause = 'm.channel_id = %(channel_id)s AND m.media_group_id = %(media_group_id)s'
        params['channel_id'] = channel_id
        params['media_group_id'] = media_group_id
        group_label = f'图集 {channel_id}:{media_group_id}'
    elif person_key.startswith('msg:'):
        msg_id = _parse_int(person_key[4:])
        if not msg_id:
            raise HTTPException(400, '无效人物分组 key')
        where_clause = 'm.id = %(msg_id)s'
        params['msg_id'] = msg_id
        group_label = f'Message {msg_id}'
    else:
        raise HTTPException(400, '无效人物分组 key')

    if not is_admin(user):
        where_clause = f'({where_clause}) AND m.owner_user_id = %(viewer_user_id)s'
        params['viewer_user_id'] = user['id']

    source_rows = db_execute(
        db,
        f"""
        SELECT
            p.id AS person_id,
            p.message_id,
            p.display_nickname,
            p.internal_code,
            p.province,
            p.city,
            p.age,
            p.height,
            p.weight,
            p.cup_size,
            p.occupation,
            p.introduction_fee,
            p.monthly_allowance,
            p.tags,
            p.contact_info,
            p.updated_at,
            m.telegram_message_id,
            m.telegram_date,
            m.text_content,
            m.review_status,
            m.extract_confidence,
            c.username AS channel_name,
            COALESCE(mc.media_count, 0) AS media_count
        FROM profiles p
        LEFT JOIN messages m ON m.id = p.message_id
        LEFT JOIN channels c ON c.id = m.channel_id
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS media_count FROM media_files mf WHERE mf.message_id = m.id
        ) mc ON TRUE
        WHERE {where_clause}
        ORDER BY m.telegram_date DESC NULLS LAST, p.id DESC
        """,
        params,
    ).fetchall()

    if not source_rows:
        raise HTTPException(404, '未找到人物分组数据')

    message_ids = [r['message_id'] for r in source_rows if r.get('message_id')]
    media_rows = []
    if message_ids:
        media_rows = db_execute(
            db,
            """
            SELECT
                mf.*,
                m.telegram_message_id,
                m.telegram_date
            FROM media_files mf
            LEFT JOIN messages m ON m.id = mf.message_id
            WHERE mf.message_id = ANY(%s)
            ORDER BY m.telegram_date DESC NULLS LAST, mf.id ASC
            """,
            (message_ids,),
        ).fetchall()

    summary = source_rows[0]
    return templates.TemplateResponse(
        request=request,
        name='person_group.html',
        context={
            'user': user,
            'person_key': person_key,
            'group_label': group_label,
            'summary': summary,
            'source_rows': source_rows,
            'media_rows': media_rows,
            'total_messages': len(source_rows),
            'total_media': len(media_rows),
        },
    )


@app.get('/detail/{msg_id}', response_class=HTMLResponse)
async def detail(msg_id: int, request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    _ensure_message_access(db, user, msg_id)

    msg = db_execute(
        db,
        'SELECT m.*, c.username as channel_name, c.title as channel_title FROM messages m LEFT JOIN channels c ON c.id = m.channel_id WHERE m.id = %s',
        (msg_id,),
    ).fetchone()
    if not msg:
        raise HTTPException(404, '消息不存在')

    profile = db_execute(db, 'SELECT * FROM profiles WHERE message_id = %s ORDER BY id LIMIT 1', (msg_id,)).fetchone()
    media = db_execute(db, 'SELECT * FROM media_files WHERE message_id = %s ORDER BY id', (msg_id,)).fetchall()
    logs = db_execute(
        db,
        'SELECT l.*, r.username as reviewer_name FROM audit_logs l LEFT JOIN reviewers r ON r.id = l.reviewer_id WHERE l.message_id = %s ORDER BY l.created_at DESC',
        (msg_id,),
    ).fetchall()

    prev_msg = db_execute(db, 'SELECT id FROM messages WHERE id < %s ORDER BY id DESC LIMIT 1', (msg_id,)).fetchone()
    next_msg = db_execute(db, 'SELECT id FROM messages WHERE id > %s ORDER BY id ASC LIMIT 1', (msg_id,)).fetchone()

    return templates.TemplateResponse(
        request=request,
        name='detail.html',
        context={
            'user': user,
            'msg': msg,
            'profile': profile,
            'media': media,
            'logs': logs,
            'prev_msg_id': prev_msg['id'] if prev_msg else None,
            'next_msg_id': next_msg['id'] if next_msg else None,
        },
    )


@app.get('/audit', response_class=HTMLResponse)
async def audit_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    conditions = ['1=1']
    params: Dict[str, Any] = {}
    if not is_admin(user):
        conditions.append('m.owner_user_id = %(viewer_user_id)s')
        params['viewer_user_id'] = user['id']
    where_clause = ' AND '.join(conditions)

    total = db_execute(
        db,
        f"""
        SELECT COUNT(*) AS cnt
        FROM audit_logs l
        LEFT JOIN messages m ON m.id = l.message_id
        WHERE {where_clause}
        """,
        params,
    ).fetchone()['cnt']
    offset = (page - 1) * page_size
    rows = db_execute(
        db,
        f"""
        SELECT l.*, r.username AS reviewer_name, m.telegram_message_id, c.username AS channel_name
        FROM audit_logs l
        LEFT JOIN reviewers r ON r.id = l.reviewer_id
        LEFT JOIN messages m ON m.id = l.message_id
        LEFT JOIN channels c ON c.id = m.channel_id
        WHERE {where_clause}
        ORDER BY l.created_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, 'limit': page_size, 'offset': offset},
    ).fetchall()

    total_pages = (total + page_size - 1) // page_size
    return templates.TemplateResponse(
        request=request,
        name='audit.html',
        context={
            'user': user,
            'rows': rows,
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': total_pages},
        },
    )


@app.get('/crawl-logs', response_class=HTMLResponse)
async def crawl_logs_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    conditions = ['1=1']
    params: Dict[str, Any] = {}
    if not is_admin(user):
        conditions.append('l.owner_user_id = %(viewer_user_id)s')
        params['viewer_user_id'] = user['id']
    if channel:
        conditions.append('c.username = %(channel)s')
        params['channel'] = channel
    if status:
        conditions.append('l.status = %(status)s')
        params['status'] = status

    where_clause = ' AND '.join(conditions)
    count_sql = f"""
        SELECT COUNT(*) AS cnt
        FROM crawl_logs l
        LEFT JOIN channels c ON c.id = l.channel_id
        WHERE {where_clause}
    """
    total = db_execute(db, count_sql, params).fetchone()['cnt']
    offset = (page - 1) * page_size

    query_sql = f"""
        SELECT
            l.*,
            c.username AS channel_name,
            EXTRACT(EPOCH FROM (COALESCE(l.run_ended_at, NOW()) - l.run_started_at))::INT AS duration_sec
        FROM crawl_logs l
        LEFT JOIN channels c ON c.id = l.channel_id
        WHERE {where_clause}
        ORDER BY l.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    query_params = dict(params)
    query_params['limit'] = page_size
    query_params['offset'] = offset
    rows = db_execute(db, query_sql, query_params).fetchall()

    channels = db_execute(
        db,
        'SELECT DISTINCT username FROM channels WHERE username IS NOT NULL ORDER BY username',
    ).fetchall()

    total_pages = (total + page_size - 1) // page_size
    filters = {
        'channel': channel or '',
        'status': status or '',
        'page_size': page_size,
    }
    page_query = _query_string(filters)

    return templates.TemplateResponse(
        request=request,
        name='crawl_logs.html',
        context={
            'user': user,
            'rows': rows,
            'channels': [r['username'] for r in channels],
            'filters': filters,
            'page_query': page_query,
            'pagination': {'page': page, 'page_size': page_size, 'total': total, 'total_pages': total_pages},
        },
    )


# ==================== API ====================


@app.get('/api/system/status')
async def api_system_status(request: Request, db=Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin_user(user)
    return {'ok': True, 'status': _collect_runtime_status(db)}


@app.post('/api/system/start-all')
async def api_system_start_all(request: Request, db=Depends(get_db)):
    """Starts MinIO and crawler in order with runtime verification."""
    user = get_current_user(request, db)
    _require_admin_user(user)

    _acquire_system_action_lock()
    try:
        before = _collect_runtime_status(db)
        actions: List[str] = []
        errors: List[str] = []
        launch_logs: Dict[str, str] = {}

        if not before['services']['minio']['running']:
            try:
                log_path = _start_local_service_script(_service_script_name('minio'))
                launch_logs['minio'] = log_path
                actions.append('minio_start_triggered')
            except Exception as exc:
                errors.append(f'minio: {exc}')
        else:
            actions.append('minio_already_running')

        after_minio = _wait_for_service_state(db, 'minio', expected_running=True, timeout_sec=SERVICE_START_TIMEOUT_SEC)
        if 'minio_start_triggered' in actions and not after_minio['services']['minio']['running']:
            log_path = launch_logs.get('minio', '-')
            errors.append(f'minio: 已触发启动但未检测到进程，请检查日志 {log_path}')

        if not after_minio['services']['crawler']['running']:
            try:
                _validate_service_start_env('crawler')
                log_path = _start_local_service_script(_service_script_name('crawler'))
                launch_logs['crawler'] = log_path
                actions.append('crawler_start_triggered')
            except Exception as exc:
                errors.append(f'crawler: {exc}')
        else:
            actions.append('crawler_already_running')

        after = _wait_for_service_state(db, 'crawler', expected_running=True, timeout_sec=SERVICE_START_TIMEOUT_SEC)
        if 'crawler_start_triggered' in actions and not after['services']['crawler']['running']:
            log_path = launch_logs.get('crawler', '-')
            errors.append(f'crawler: 已触发启动但未检测到进程，请检查日志 {log_path}')

        return {
            'ok': len(errors) == 0,
            'actions': actions,
            'errors': errors,
            'launch_logs': launch_logs,
            'status': after,
        }
    finally:
        _release_system_action_lock()


@app.post('/api/system/{service}/start')
async def api_system_start_service(service: str, request: Request, db=Depends(get_db)):
    """Starts one local service and verifies process availability."""
    user = get_current_user(request, db)
    _require_admin_user(user)

    service_key = _service_key_or_400(service)
    _acquire_system_action_lock()
    try:
        status_before = _collect_runtime_status(db)
        if status_before['services'][service_key]['running']:
            return {'ok': True, 'service': service_key, 'action': 'already_running', 'status': status_before}

        _validate_service_start_env(service_key)
        log_path = _start_local_service_script(_service_script_name(service_key))
        status_after = _wait_for_service_state(db, service_key, expected_running=True, timeout_sec=SERVICE_START_TIMEOUT_SEC)
        if not status_after['services'][service_key]['running']:
            return {
                'ok': False,
                'service': service_key,
                'action': 'start_triggered_but_not_running',
                'errors': [f'{service_key} 已触发启动但未检测到进程，请检查日志 {log_path}'],
                'launch_log': log_path,
                'status': status_after,
            }
        return {
            'ok': True,
            'service': service_key,
            'action': 'started',
            'launch_log': log_path,
            'status': status_after,
        }
    finally:
        _release_system_action_lock()


@app.post('/api/system/{service}/stop')
async def api_system_stop_service(service: str, request: Request, db=Depends(get_db)):
    """Stops one local service and verifies target process termination."""
    user = get_current_user(request, db)
    _require_admin_user(user)

    service_key = _service_key_or_400(service)
    _acquire_system_action_lock()
    try:
        killed_pids = _stop_local_service(service_key)
        status_after = _wait_for_service_state(db, service_key, expected_running=False, timeout_sec=SERVICE_STOP_TIMEOUT_SEC)
        still_running = bool(status_after['services'][service_key]['running'])
        errors = [] if not still_running else [f'{service_key} 进程仍在运行，请稍后重试或手动检查']
        return {
            'ok': not still_running,
            'service': service_key,
            'killed_pids': killed_pids,
            'errors': errors,
            'status': status_after,
        }
    finally:
        _release_system_action_lock()


@app.post('/api/users')
async def api_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form('user'),
    full_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    must_change_password: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    _require_admin_user(user)

    normalized_username = (username or '').strip().lower()
    if len(normalized_username) < 3:
        raise HTTPException(400, '用户名至少 3 个字符')
    if len(password or '') < 6:
        raise HTTPException(400, '密码至少 6 位')

    normalized_role = (role or 'user').strip().lower()
    if normalized_role not in {'admin', 'user'}:
        raise HTTPException(400, '角色仅支持 admin 或 user')

    exists = db_execute(
        db,
        'SELECT id FROM reviewers WHERE LOWER(username) = LOWER(%s) LIMIT 1',
        (normalized_username,),
    ).fetchone()
    if exists:
        raise HTTPException(400, '用户名已存在')

    forced_change = _parse_bool(must_change_password)
    forced_change = True if forced_change is None else bool(forced_change)

    hashed = hash_password(password)
    row = db_execute(
        db,
        """
        INSERT INTO reviewers (username, password_hash, role, full_name, email, is_active, must_change_password)
        VALUES (%s, %s, %s, %s, %s, true, %s)
        RETURNING id
        """,
        (
            normalized_username,
            hashed,
            normalized_role,
            (full_name or '').strip() or None,
            (email or '').strip() or None,
            forced_change,
        ),
    ).fetchone()
    db.commit()
    return {'ok': True, 'id': row['id']}


@app.post('/api/users/{user_id}/password')
async def api_reset_user_password(
    user_id: int,
    request: Request,
    password: str = Form(...),
    must_change_password: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    _require_admin_user(user)

    if len(password or '') < 6:
        raise HTTPException(400, '密码至少 6 位')

    target = db_execute(
        db,
        'SELECT id, username FROM reviewers WHERE id = %s LIMIT 1',
        (user_id,),
    ).fetchone()
    if not target:
        raise HTTPException(404, '用户不存在')

    forced_change = _parse_bool(must_change_password)
    forced_change = True if forced_change is None else bool(forced_change)

    db_execute(
        db,
        'UPDATE reviewers SET password_hash = %s, must_change_password = %s WHERE id = %s',
        (hash_password(password), forced_change, user_id),
    )
    db.commit()
    return {'ok': True}


@app.post('/api/users/{user_id}/status')
async def api_update_user_status(
    user_id: int,
    request: Request,
    is_active: str = Form(...),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    _require_admin_user(user)

    active = _parse_bool(is_active)
    if active is None:
        raise HTTPException(400, 'is_active 参数无效')

    target = db_execute(
        db,
        'SELECT id, role FROM reviewers WHERE id = %s LIMIT 1',
        (user_id,),
    ).fetchone()
    if not target:
        raise HTTPException(404, '用户不存在')
    if target['id'] == user['id'] and not active:
        raise HTTPException(400, '不能禁用当前登录账号')

    db_execute(db, 'UPDATE reviewers SET is_active = %s WHERE id = %s', (active, user_id))
    db.commit()
    return {'ok': True}


@app.post('/api/account/password')
async def api_change_self_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    if len(new_password or '') < 6:
        raise HTTPException(400, '新密码至少 6 位')

    row = db_execute(
        db,
        'SELECT id, password_hash FROM reviewers WHERE id = %s LIMIT 1',
        (user['id'],),
    ).fetchone()
    if not row or not verify_password(old_password, row['password_hash']):
        raise HTTPException(400, '旧密码错误')

    db_execute(
        db,
        'UPDATE reviewers SET password_hash = %s, must_change_password = false WHERE id = %s',
        (hash_password(new_password), user['id']),
    )
    db.commit()
    return {'ok': True}


@app.post('/api/settings/crawler')
async def api_update_crawler_settings(
    request: Request,
    tg_api_id: Optional[str] = Form(None),
    tg_api_hash: Optional[str] = Form(None),
    tg_phone: Optional[str] = Form(None),
    tg_proxy_type: Optional[str] = Form(None),
    tg_proxy_host: Optional[str] = Form(None),
    tg_proxy_port: Optional[str] = Form(None),
    tg_proxy_username: Optional[str] = Form(None),
    tg_proxy_password: Optional[str] = Form(None),
    target_channels: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    proxy_port = _parse_int(tg_proxy_port)
    api_id = _parse_int(tg_api_id)
    channels = _parse_channel_lines(target_channels)
    if channels and len(channels) > 200:
        raise HTTPException(400, '频道数量过多，请控制在 200 以内')

    db_execute(
        db,
        """
        INSERT INTO user_crawler_settings (
            user_id, tg_api_id, tg_api_hash, tg_phone,
            tg_proxy_type, tg_proxy_host, tg_proxy_port, tg_proxy_username, tg_proxy_password,
            target_channels, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            tg_api_id = EXCLUDED.tg_api_id,
            tg_api_hash = EXCLUDED.tg_api_hash,
            tg_phone = EXCLUDED.tg_phone,
            tg_proxy_type = EXCLUDED.tg_proxy_type,
            tg_proxy_host = EXCLUDED.tg_proxy_host,
            tg_proxy_port = EXCLUDED.tg_proxy_port,
            tg_proxy_username = EXCLUDED.tg_proxy_username,
            tg_proxy_password = EXCLUDED.tg_proxy_password,
            target_channels = EXCLUDED.target_channels,
            updated_at = NOW()
        """,
        (
            user['id'],
            api_id,
            (tg_api_hash or '').strip() or None,
            (tg_phone or '').strip() or None,
            (tg_proxy_type or '').strip().lower() or None,
            (tg_proxy_host or '').strip() or None,
            proxy_port,
            (tg_proxy_username or '').strip() or None,
            (tg_proxy_password or '').strip() or None,
            channels,
        ),
    )
    db.commit()
    return {'ok': True, 'channels': len(channels)}


@app.post('/api/messages/{msg_id}/review')
async def update_review(
    msg_id: int,
    request: Request,
    review_status: str = Form(...),
    review_notes: Optional[str] = Form(None),
    is_flagged: Optional[str] = Form(None),
    manual_tags: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    _ensure_message_access(db, user, msg_id)
    flagged = _parse_bool(is_flagged)
    flagged = bool(flagged) if flagged is not None else False
    tags = _parse_tags(manual_tags)

    old = db_execute(
        db,
        'SELECT review_status, is_flagged, review_notes, manual_tags FROM messages WHERE id = %s',
        (msg_id,),
    ).fetchone()

    db_execute(
        db,
        'UPDATE messages SET review_status = %s, review_notes = %s, is_flagged = %s, manual_tags = %s, reviewer_id = %s, review_time = NOW() WHERE id = %s',
        (review_status, review_notes, flagged, tags, user['id'], msg_id),
    )

    db_execute(
        db,
        'INSERT INTO audit_logs (message_id, reviewer_id, action, old_values, new_values) VALUES (%s, %s, %s, %s, %s)',
        (
            msg_id,
            user['id'],
            'review',
            _json_dumps(dict(old)) if old else None,
            _json_dumps({'status': review_status, 'flagged': flagged, 'notes': review_notes, 'tags': tags}),
        ),
    )
    db.commit()
    return {'ok': True}


@app.post('/api/messages/bulk-review')
async def bulk_review(
    request: Request,
    message_ids: str = Form(...),
    review_status: str = Form(...),
    review_notes: Optional[str] = Form(None),
    is_flagged: Optional[str] = Form(None),
    manual_tags: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)

    try:
        ids = json.loads(message_ids)
        ids = [int(x) for x in ids]
    except Exception as exc:
        raise HTTPException(400, f'无效的 message_ids: {exc}')

    ids = sorted(set(i for i in ids if i > 0))
    if not ids:
        raise HTTPException(400, '没有可更新的消息 ID')

    effective_ids = ids
    if not is_admin(user):
        rows = db_execute(
            db,
            'SELECT id FROM messages WHERE id = ANY(%s) AND owner_user_id = %s',
            (ids, user['id']),
        ).fetchall()
        effective_ids = [r['id'] for r in rows]
        if not effective_ids:
            raise HTTPException(403, '选中的消息均无权限更新')

    flagged = _parse_bool(is_flagged)
    flagged = bool(flagged) if flagged is not None else False
    tags = _parse_tags(manual_tags)

    new_values = {'status': review_status, 'flagged': flagged, 'notes': review_notes, 'tags': tags}

    db_execute(
        db,
        """
        INSERT INTO audit_logs (message_id, reviewer_id, action, old_values, new_values)
        SELECT id, %s, 'bulk_review',
               jsonb_build_object('status', review_status, 'flagged', is_flagged, 'notes', review_notes, 'tags', manual_tags),
               %s::jsonb
        FROM messages
        WHERE id = ANY(%s)
        """,
        (user['id'], _json_dumps(new_values), effective_ids),
    )

    db_execute(
        db,
        """
        UPDATE messages
        SET review_status = %s,
            review_notes = %s,
            is_flagged = %s,
            manual_tags = %s,
            reviewer_id = %s,
            review_time = NOW()
        WHERE id = ANY(%s)
        """,
        (review_status, review_notes, flagged, tags, user['id'], effective_ids),
    )

    db.commit()
    return {'ok': True, 'updated': len(effective_ids)}


@app.post('/api/messages/{msg_id}/profile')
async def update_profile(
    msg_id: int,
    request: Request,
    display_nickname: Optional[str] = Form(None),
    internal_code: Optional[str] = Form(None),
    province: Optional[str] = Form(None),
    city: Optional[str] = Form(None),
    age: Optional[str] = Form(None),
    height: Optional[str] = Form(None),
    weight: Optional[str] = Form(None),
    cup_size: Optional[str] = Form(None),
    occupation: Optional[str] = Form(None),
    introduction_fee: Optional[str] = Form(None),
    monthly_allowance: Optional[str] = Form(None),
    db=Depends(get_db),
):
    user = get_current_user(request, db)
    _ensure_message_access(db, user, msg_id)

    old = db_execute(db, 'SELECT * FROM profiles WHERE message_id = %s ORDER BY id LIMIT 1', (msg_id,)).fetchone()
    payload = {
        'display_nickname': (display_nickname or '').strip() or None,
        'internal_code': (internal_code or '').strip() or None,
        'province': (province or '').strip() or None,
        'city': (city or '').strip() or None,
        'age': _parse_int(age),
        'height': _parse_int(height),
        'weight': _parse_int(weight),
        'cup_size': (cup_size or '').strip() or None,
        'occupation': (occupation or '').strip() or None,
        'introduction_fee': _parse_float(introduction_fee),
        'monthly_allowance': _parse_float(monthly_allowance),
    }

    _upsert_profile(db, msg_id, payload)
    db_execute(
        db,
        'INSERT INTO audit_logs (message_id, reviewer_id, action, old_values, new_values) VALUES (%s, %s, %s, %s, %s)',
        (
            msg_id,
            user['id'],
            'profile_update',
            _json_dumps(dict(old)) if old else None,
            _json_dumps(payload),
        ),
    )
    db.commit()
    return {'ok': True}


# ==================== Auth ====================


@app.get('/login', response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name='login.html')


@app.post('/login')
async def do_login(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    normalized_username = (username or '').strip().lower()
    user = db_execute(
        db,
        'SELECT id, password_hash FROM reviewers WHERE LOWER(username) = LOWER(%s) AND is_active = true',
        (normalized_username,),
    ).fetchone()
    if not user or not verify_password(password, user['password_hash']):
        return templates.TemplateResponse(
            request=request,
            name='login.html',
            context={'error': '用户名或密码错误'},
            status_code=401,
        )

    token = create_token(user['id'])
    response = RedirectResponse(url='/', status_code=302)
    response.set_cookie(key='session', value=token, httponly=True, max_age=604800)
    return response


@app.get('/logout')
async def logout():
    response = RedirectResponse(url='/login')
    response.delete_cookie('session')
    return response


def _ensure_identity_schema(conn):
    cur = conn.cursor()

    cur.execute("ALTER TABLE reviewers ADD COLUMN IF NOT EXISTS full_name VARCHAR(255)")
    cur.execute("ALTER TABLE reviewers ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    cur.execute("ALTER TABLE reviewers ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT false")
    cur.execute("ALTER TABLE reviewers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewers_username_ci ON reviewers (LOWER(username))")
    cur.execute("UPDATE reviewers SET role = 'user' WHERE role IS NULL OR role = '' OR role = 'reviewer'")

    cur.execute("ALTER TABLE channels ADD COLUMN IF NOT EXISTS owner_user_id BIGINT")
    cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS owner_user_id BIGINT")
    cur.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS owner_user_id BIGINT")
    cur.execute("ALTER TABLE media_files ADD COLUMN IF NOT EXISTS owner_user_id BIGINT")
    cur.execute("ALTER TABLE crawl_logs ADD COLUMN IF NOT EXISTS owner_user_id BIGINT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_channels_owner ON channels(owner_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_owner ON messages(owner_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_profiles_owner ON profiles(owner_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_media_owner ON media_files(owner_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_crawl_logs_owner ON crawl_logs(owner_user_id)")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_crawler_settings (
            user_id BIGINT PRIMARY KEY REFERENCES reviewers(id) ON DELETE CASCADE,
            tg_api_id BIGINT,
            tg_api_hash TEXT,
            tg_phone VARCHAR(64),
            tg_proxy_type VARCHAR(20),
            tg_proxy_host VARCHAR(255),
            tg_proxy_port INTEGER,
            tg_proxy_username VARCHAR(255),
            tg_proxy_password VARCHAR(255),
            target_channels TEXT[] DEFAULT '{}',
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    conn.commit()
    cur.close()


def _backfill_owner_scope(conn, admin_id: int):
    cur = conn.cursor()
    cur.execute('UPDATE channels SET owner_user_id = %s WHERE owner_user_id IS NULL', (admin_id,))
    cur.execute('UPDATE messages SET owner_user_id = %s WHERE owner_user_id IS NULL', (admin_id,))
    cur.execute(
        """
        UPDATE profiles p
        SET owner_user_id = COALESCE(m.owner_user_id, %s)
        FROM messages m
        WHERE p.message_id = m.id
          AND p.owner_user_id IS NULL
        """,
        (admin_id,),
    )
    cur.execute(
        """
        UPDATE media_files mf
        SET owner_user_id = COALESCE(m.owner_user_id, %s)
        FROM messages m
        WHERE mf.message_id = m.id
          AND mf.owner_user_id IS NULL
        """,
        (admin_id,),
    )
    cur.execute(
        """
        UPDATE crawl_logs l
        SET owner_user_id = COALESCE(c.owner_user_id, %s)
        FROM channels c
        WHERE l.channel_id = c.id
          AND l.owner_user_id IS NULL
        """,
        (admin_id,),
    )
    cur.execute('UPDATE crawl_logs SET owner_user_id = %s WHERE owner_user_id IS NULL', (admin_id,))
    conn.commit()
    cur.close()


@app.on_event('startup')
async def init_admin():
    conn = psycopg2.connect(DB_URL)
    _ensure_identity_schema(conn)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM reviewers WHERE role = %s', ('admin',))
    if cur.fetchone()[0] == 0:
        hashed = hash_password('admin123')
        cur.execute(
            """
            INSERT INTO reviewers (username, password_hash, role, full_name, is_active, must_change_password)
            VALUES (%s, %s, %s, %s, true, true)
            """,
            ('admin', hashed, 'admin', 'Platform Admin'),
        )
        conn.commit()
        print('Default admin created: admin / admin123 (must change password)')
    cur.execute('SELECT id FROM reviewers WHERE role = %s ORDER BY id ASC LIMIT 1', ('admin',))
    row = cur.fetchone()
    if row:
        _backfill_owner_scope(conn, int(row[0]))
    cur.close()
    conn.close()
