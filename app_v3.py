# app_v2.py
# -----------------------------------------------------------------------------
# FastAPI backend for notebook session allocation (PostgreSQL only) with:
# - Tables reset on startup (DROP + CREATE)
# - Pooled tasks (sessions) with "idle = email IS NULL"
# - Batch task creation via launch_container()
# - /{nb_name}.ipynb redirects to composed Jupyter URL
# - Auto-generate temporary uid (cookie) if missing, with TTL and background cleanup
# - Sync tasks with Kubernetes running pods by prefix
# - List all tasks
# - Optional delete-by-user placeholder
# -----------------------------------------------------------------------------

import os
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Set

import psycopg2
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

# Optional: Kubernetes client for /tasks/sync and optional deletion on TTL expire
try:
    from kubernetes import client as k8s_client, config as k8s_config
except Exception:
    k8s_client = None
    k8s_config = None

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Mock pod creation (for local/dev). Set to "0"/"false" in production and implement real start logic.
MOCK_PODS = str(os.getenv("MOCK_PODS", "1")).lower() in ("1", "true", "yes")

# Temporary UID TTL and cleanup settings
TEMP_UID_TTL_SECONDS = int(os.getenv("TEMP_UID_TTL_SECONDS", "3600"))       # default 1 hour
TTL_CLEANUP_INTERVAL_SECONDS = int(os.getenv("TTL_CLEANUP_INTERVAL_SECONDS", "60"))
TTL_CLEANUP_MODE = os.getenv("TTL_CLEANUP_MODE", "release").lower()         # 'release' | 'delete'

# Optional: Delete Kubernetes pods on expiry when TTL_CLEANUP_MODE='delete'
K8S_DELETE_ON_EXPIRE = str(os.getenv("K8S_DELETE_ON_EXPIRE", "0")).lower() in ("1", "true", "yes")
K8S_NAMESPACE = os.getenv("K8S_NAMESPACE")  # required if you want to delete pods on expiry

# Cookie behavior for UID
UID_COOKIE_NAME = os.getenv("UID_COOKIE_NAME", "uid")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "Lax")   # 'Lax' | 'Strict' | 'None'
COOKIE_SECURE = str(os.getenv("COOKIE_SECURE", "0")).lower() in ("1", "true", "yes")

# -----------------------------------------------------------------------------
# Database (PostgreSQL only)
# -----------------------------------------------------------------------------

# Database configuration
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_NAME = os.environ.get("POSTGRES_DB", "flaskdb")
DB_USER = os.environ.get("POSTGRES_USER", "flaskuser")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "flaskpass")
DATABASE_PATH = "sessions.db"
DEFAULT_IMAGE = "vivienfanghua/amd_tutorial:unsloth"

# Try to import database libraries
USE_POSTGRES = True
try:
    import psycopg2
    # Try to connect to PostgreSQL
    test_conn = psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    test_conn.close()
    USE_POSTGRES = True
    print("Using PostgreSQL database")
except (ImportError, psycopg2.OperationalError) as e:
    print(f"PostgreSQL not available ({e}), falling back to SQLite")
    import sqlite3

def get_conn():
    """Get database connection based on available backend."""
    if USE_POSTGRES:
        return psycopg2.connect(
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
    else:
        return sqlite3.connect(DATABASE_PATH)

def init_db():
    """
    Initialize database schema by DROPPING and RECREATING tables.
    WARNING: This wipes existing data on every startup by design.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Drop and recreate sessions table
            cur.execute("DROP TABLE IF EXISTS sessions;")
            cur.execute(
                """
                CREATE TABLE sessions (
                    id SERIAL PRIMARY KEY,
                    pod_name TEXT UNIQUE,
                    url TEXT,
                    email TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    assigned_at TIMESTAMP NULL
                );
                """
            )

            # Drop and recreate logs table
            cur.execute("DROP TABLE IF EXISTS logs;")
            cur.execute(
                """
                CREATE TABLE logs (
                    id SERIAL PRIMARY KEY,
                    email TEXT,
                    started_at TIMESTAMP
                );
                """
            )

            # Drop and recreate temp_uids table
            cur.execute("DROP TABLE IF EXISTS temp_uids;")
            cur.execute(
                """
                CREATE TABLE temp_uids (
                    uid TEXT PRIMARY KEY,
                    pod_name TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP NOT NULL
                );
                """
            )
        conn.commit()


# -----------------------------------------------------------------------------
# App creation and init
# -----------------------------------------------------------------------------

app = FastAPI()
init_db()


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------

def compose_url(pod_name: str, nb_name: Optional[str] = None) -> str:
    """
    Compose the Jupyter URL for a given pod. Adjust to match your routing/proxy.
    Example: /jupyter/{pod_name}/lab/tree/{nb_name}.ipynb
    """
    base_path = f"/jupyter/{pod_name}"
    if nb_name:
        return f"{base_path}/lab/tree/{nb_name}.ipynb"
    return base_path


def get_user_id_from_request(request: Request) -> Optional[str]:
    """
    Extract the 'user unique identifier' from the request.
    Priority:
      1) Header: X-User-Id
      2) Query:  ?uid=<value>
      3) Cookie: UID_COOKIE_NAME
    """
    uid = request.headers.get("X-User-Id")
    if not uid:
        uid = request.query_params.get("uid")
    if not uid:
        uid = request.cookies.get(UID_COOKIE_NAME)
    return uid


def get_assigned_pod_for_user(user_id: str) -> Optional[str]:
    """
    Return the pod_name already assigned to the given user_id, if any.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pod_name FROM sessions WHERE email = %s LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None


def claim_idle_session_atomic(user_id: str) -> Optional[str]:
    """
    Atomically claim an idle session (email IS NULL) and assign it to user_id.
    Returns the claimed pod_name or None if no idle session is available.

    PostgreSQL:
      - Use SELECT ... FOR UPDATE SKIP LOCKED to avoid blocking under concurrency.
    """
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, pod_name
                    FROM sessions
                    WHERE email IS NULL
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None
                sess_id, pod_name = row[0], row[1]
                cur.execute(
                    "UPDATE sessions SET email = %s, assigned_at = NOW() WHERE id = %s",
                    (user_id, sess_id),
                )
            conn.commit()
            return pod_name
        except Exception:
            conn.rollback()
            raise
def update_session_url(email: str, url: str, pod_name: str):
    """
    Update the session row identified by the bootstrap 'email' with URL and Pod name.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET url = %s, pod_name = %s WHERE email = %s",
                (url, pod_name, email),
            )
        conn.commit()


def log_container_start(email: str):
    """
    Insert a log entry recording when a container was started.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO logs(email, started_at) VALUES (%s, NOW())",
                (email,),
            )
        conn.commit()


def start_pod_and_get_jupyter_url(image: str) -> Tuple[str, str]:
    """
    Start a container/pod for Jupyter and return (pod_name, jupyter_url).
    In mock mode, this returns a synthetic pod name and base URL.
    Replace with real orchestration logic in production.
    """
    if MOCK_PODS:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        pod_name = f"pod-{ts}-{uuid.uuid4().hex[:8]}"
        jupyter_url = compose_url(pod_name)
        return pod_name, jupyter_url

    # TODO: Implement real start logic (e.g., Kubernetes API)
    # pod_name = create_k8s_pod(image=..., ...)
    # wait_until_ready(pod_name)
    # jupyter_url = reverse_proxy_path_for(pod_name)
    # return pod_name, jupyter_url

    raise NotImplementedError("start_pod_and_get_jupyter_url requires implementation when MOCK_PODS is false")


def launch_container(email: str, image: str = DEFAULT_IMAGE):
    """
    Launch a container for the given email (bootstrap record).
    Steps:
      1) Start the pod and obtain its base Jupyter URL.
      2) Update the DB row identified by 'email' with (url, pod_name).
      3) Log the container start.
    """
    pod_name, jupyter_url = start_pod_and_get_jupyter_url(image)
    if jupyter_url:
        update_session_url(email, jupyter_url, pod_name)
        log_container_start(email)


# -----------------------------------------------------------------------------
# Temporary UID helpers
# -----------------------------------------------------------------------------

def generate_temp_uid() -> str:
    """
    Generate a random temporary uid (not PII).
    """
    return f"tmp-{uuid.uuid4().hex}"


def upsert_temp_uid(uid: str, ttl_seconds: int = TEMP_UID_TTL_SECONDS):
    """
    Insert or extend a temp uid with an expiration time.
    If the uid already exists, keep created_at and just extend expires_at.
    """
    expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO temp_uids(uid, pod_name, expires_at)
                VALUES (%s, NULL, %s)
                ON CONFLICT (uid)
                DO UPDATE SET expires_at = EXCLUDED.expires_at
                """,
                (uid, expires_at),
            )
        conn.commit()


def link_temp_uid_to_pod(uid: str, pod_name: str):
    """
    Link a temp uid to the assigned pod (for cleanup reference).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE temp_uids SET pod_name = %s WHERE uid = %s", (pod_name, uid))
        conn.commit()


def delete_temp_uid(uid: str):
    """
    Delete a temp uid row (after cleanup).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM temp_uids WHERE uid = %s", (uid,))
        conn.commit()


def list_expired_temp_uids() -> List[Tuple[str, Optional[str]]]:
    """
    Return a list of (uid, pod_name) whose expires_at <= now (UTC).
    """
    now_utc = datetime.utcnow()
    expired: List[Tuple[str, Optional[str]]] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT uid, pod_name, expires_at FROM temp_uids")
            rows = cur.fetchall()
            for uid, pod_name, expires_at in rows:
                if expires_at and expires_at <= now_utc:
                    expired.append((uid, pod_name))
    return expired


# -----------------------------------------------------------------------------
# Background cleanup loop
# -----------------------------------------------------------------------------

@app.on_event("startup")
async def start_cleanup_task():
    """
    Background loop for cleaning expired temp uids and releasing/deleting tasks.
    """
    from container_manager import maybe_delete_pod
    async def _loop():
        while True:
            try:
                expired = list_expired_temp_uids()
                for uid, pod_name in expired:
                    if TTL_CLEANUP_MODE == "delete":
                        # Delete the DB row(s) and (optionally) the Kubernetes pod
                        if pod_name:
                            maybe_delete_pod(pod_name)
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                if pod_name:
                                    cur.execute("DELETE FROM sessions WHERE pod_name = %s AND email = %s", (pod_name, uid))
                                else:
                                    cur.execute("DELETE FROM sessions WHERE email = %s", (uid,))
                            conn.commit()
                    else:
                        # Default 'release' mode: mark session as idle
                        with get_conn() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE sessions SET email = NULL, assigned_at = NULL WHERE email = %s",
                                    (uid,)
                                )
                            conn.commit()

                    # Remove the temp uid record
                    delete_temp_uid(uid)
            except Exception:
                # Never crash the loop; continue next tick
                pass

            await asyncio.sleep(TTL_CLEANUP_INTERVAL_SECONDS)

    asyncio.create_task(_loop())


# -----------------------------------------------------------------------------
# API Endpoints (define fixed routes before the catch-all notebook route)
# -----------------------------------------------------------------------------

@app.get("/")
async def root():
    """
    Basic health endpoint.
    """
    return {"status": "ok"}


@app.post("/tasks/add")
async def add_idle_tasks(
    count: int = Query(1, ge=1, le=1000, description="How many idle tasks to add"),
    prefix: str = Query("jupyter-task", description="Prefix for bootstrap email tag"),
    image: str = Query(DEFAULT_IMAGE, description="Container image to launch")
):
    """
    Create 'count' idle sessions (tasks) by:
      - Inserting a bootstrap row with a unique temporary 'email'
      - Calling launch_container(email, image) to start pod and set (url, pod_name)
      - Resetting email back to NULL so that the session becomes IDLE
    """
    created_pods: List[str] = []

    def _insert_bootstrap_row(bootstrap_email: str):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions(pod_name, url, email, assigned_at) VALUES (%s, NULL, %s, NULL)",
                    (None, bootstrap_email),
                )
            conn.commit()

    def _get_pod_by_email(bootstrap_email: str) -> Optional[str]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pod_name FROM sessions WHERE email = %s LIMIT 1",
                    (bootstrap_email,),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def _mark_idle_by_pod(pod_name: str):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name = %s",
                    (pod_name,),
                )
            conn.commit()

    def _delete_by_email(bootstrap_email: str):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE email = %s",
                    (bootstrap_email,),
                )
            conn.commit()

    for _ in range(count):
        bootstrap_email = f"__BOOTSTRAP__::{prefix}::{uuid.uuid4().hex}"
        _insert_bootstrap_row(bootstrap_email)

        try:
            launch_container(bootstrap_email, image=image)
            pod_name = _get_pod_by_email(bootstrap_email)
            if not pod_name:
                _delete_by_email(bootstrap_email)
                continue
            _mark_idle_by_pod(pod_name)
            created_pods.append(pod_name)
        except Exception:
            _delete_by_email(bootstrap_email)
            continue

    return JSONResponse({"created": created_pods, "count": len(created_pods)})


@app.post("/tasks/sync")
async def sync_tasks(
    prefix: str = Query("jupyter-launcher-", description="Pod name prefix to match")
):
    """
    Sync DB tasks with Running Kubernetes pods whose names start with the given prefix.

    Rules:
      - If a running pod exists and a DB row exists -> reset email=NULL, assigned_at=NULL.
      - If a running pod exists and no DB row exists -> insert a new row (idle).
      - If a DB row exists but its pod_name is not in the running set -> delete that row.
    """
    from container_manager import list_running_launcher_pods
    # 1) Fetch running pod names from k8s
    try:
        running: Set[str] = list_running_launcher_pods(prefix=prefix)
    except Exception as e:
        return JSONResponse({"error": f"Kubernetes API error: {e}"}, status_code=500)

    # 2) Fetch existing pod_names from DB
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pod_name FROM sessions WHERE pod_name IS NOT NULL")
            rows = cur.fetchall()
            existing: Set[str] = {r[0] for r in rows if r and r[0]}

    # 3) Compute deltas
    to_insert = sorted(running - existing)
    to_delete = sorted(existing - running)
    to_reset  = sorted(running & existing)

    inserted = 0
    deleted  = 0
    reset    = 0

    # 4) Apply changes to DB
    with get_conn() as conn:
        with conn.cursor() as cur:
            if to_reset:
                placeholders = ", ".join(["%s"] * len(to_reset))
                cur.execute(
                    f"UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name IN ({placeholders})",
                    tuple(to_reset),
                )
                reset = cur.rowcount

            for name in to_insert:
                cur.execute(
                    """
                    INSERT INTO sessions(pod_name, url, email, assigned_at)
                    VALUES (%s, %s, NULL, NULL)
                    ON CONFLICT (pod_name) DO NOTHING
                    """,
                    (name, compose_url(name)),
                )
                # rowcount: 1 if inserted, 0 if conflict ignored
                inserted += cur.rowcount

            if to_delete:
                placeholders = ", ".join(["%s"] * len(to_delete))
                cur.execute(
                    f"DELETE FROM sessions WHERE pod_name IN ({placeholders})",
                    tuple(to_delete),
                )
                deleted = cur.rowcount

        conn.commit()

    return JSONResponse({
        "prefix": prefix,
        "running_count": len(running),
        "db_existing_count": len(existing),
        "inserted": inserted,
        "reset": reset,
        "deleted": deleted,
        "running_pods_synced": to_reset + to_insert,
        "deleted_pods": to_delete,
    })


@app.get("/tasks")
async def list_tasks():
    """
    Return all tasks from DB (ordered by created_at DESC).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, pod_name, url, email, created_at, assigned_at FROM sessions ORDER BY created_at DESC"
            )
            rows = cur.fetchall()

    data = []
    for r in rows:
        created_at = r[4].isoformat() if r[4] else None
        assigned_at = r[5].isoformat() if r[5] else None
        data.append({
            "id": r[0],
            "pod_name": r[1],
            "url": r[2],
            "email": r[3],
            "created_at": created_at,
            "assigned_at": assigned_at,
        })
    return JSONResponse({"tasks": data, "count": len(data)})


@app.delete("/tasks/by_user/{user_id}")
async def delete_task_by_user(user_id: str):
    """
    Delete session by user identifier (placeholder).
    Implement real deletion if needed (e.g., delete k8s Pod, free DB record).
    """
    return JSONResponse({"message": "TODO: delete not implemented", "user": user_id})


@app.get("/db_status")
async def db_status():
    """
    Return database connectivity status and engine type.
    """
    try:
        with get_conn() as conn:
            with conn.cursor():
                pass
        return JSONResponse({"status": "connected", "database": "PostgreSQL"})
    except Exception as e:
        return JSONResponse({"status": "error", "database": "PostgreSQL", "error": str(e)}, status_code=500)


# -----------------------------------------------------------------------------
# Catch-all notebook route (place after fixed routes to avoid shadowing)
# -----------------------------------------------------------------------------

@app.get("/{nb_name}.ipynb")
async def route_notebook(request: Request, nb_name: str):
    """
    Allocation endpoint for Jupyter notebooks.
    Behavior:
      - If uid is missing, auto-generate a temporary uid, persist with TTL,
        set it as a cookie, and proceed with allocation.
      - If the user already has an assigned session -> reuse it.
      - Else atomically claim an idle session (email IS NULL).
      - If no idle session is available -> return 'no_gpu'.
      - On success, redirect (302) to the composed Jupyter URL.
    """
    user_id = get_user_id_from_request(request)
    generated_temp_uid = False

    if not user_id:
        user_id = generate_temp_uid()
        generated_temp_uid = True
        upsert_temp_uid(user_id, ttl_seconds=TEMP_UID_TTL_SECONDS)

    pod_name = get_assigned_pod_for_user(user_id)
    if not pod_name:
        pod_name = claim_idle_session_atomic(user_id)

    if not pod_name:
        return PlainTextResponse("no_gpu", status_code=200)

    # Link temp uid to pod (no-op if user_id is not in temp_uids)
    try:
        link_temp_uid_to_pod(user_id, pod_name)
    except Exception:
        pass
    url = compose_url(pod_name, nb_name)
    resp = RedirectResponse(url=url, status_code=302)

    if generated_temp_uid:
        # Set a cookie with TTL (Max-Age) so subsequent requests carry the uid
        resp.set_cookie(
            key=UID_COOKIE_NAME,
            value=user_id,
            path="/",
            max_age=TEMP_UID_TTL_SECONDS,
            samesite=COOKIE_SAMESITE,
            secure=COOKIE_SECURE,
            httponly=False,  # set True if you do not want JS access
        )

    return resp
# -----------------------------------------------------------------------------
# Entrypoint (run directly for local dev)
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    # Use module:app path so this file can be launched directly.
    uvicorn.run("app_v3:app", host="0.0.0.0", port=int(os.getenv("PORT", "5000")), reload=True)
