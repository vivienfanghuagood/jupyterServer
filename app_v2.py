import os
import uuid
import json
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

# =============================================================================
# Configuration & Database
# =============================================================================

# Database configuration
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_NAME = os.environ.get("POSTGRES_DB", "flaskdb")
DB_USER = os.environ.get("POSTGRES_USER", "flaskuser")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "flaskpass")
DATABASE_PATH = "sessions.db"
DEFAULT_IMAGE = "vivienfanghua/amd_tutorial:unsloth"

# Try to import database libraries
USE_POSTGRES = False
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
    if USE_POSTGRES:
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
            conn.commit()
    else:
        conn = get_conn()
        cur = conn.cursor()
        # Drop and recreate sessions table
        cur.execute("DROP TABLE IF EXISTS sessions;")
        cur.execute(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pod_name TEXT UNIQUE,
                url TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now')),
                assigned_at TIMESTAMP
            );
            """
        )
        # Drop and recreate logs table
        cur.execute("DROP TABLE IF EXISTS logs;")
        cur.execute(
            """
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                started_at TIMESTAMP
            );
            """
        )
        conn.commit()
        conn.close()


# Create app and initialize DB (will clear existing tables)
app = FastAPI()
init_db()


# =============================================================================
# Helpers — URL, user ID, DB ops
# =============================================================================

def compose_url(pod_name: str, nb_name: Optional[str] = None) -> str:
    """
    Compose the Jupyter URL that the frontend should use.
    Adjust this function to match your Nginx/Ingress path rules.
    Example rule: /jupyter/{pod_name}/lab/tree/{nb_name}.ipynb
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
      3) Cookie: uid=<value>
    """
    uid = request.headers.get("X-User-Id")
    if not uid:
        uid = request.query_params.get("uid")
    if not uid:
        uid = request.cookies.get("uid")
    return uid


def get_assigned_pod_for_user(user_id: str) -> Optional[str]:
    """
    Find the pod already assigned to the given user_id, if any.
    """
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pod_name FROM sessions WHERE email = %s LIMIT 1",
                    (user_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT pod_name FROM sessions WHERE email = ? LIMIT 1",
            (user_id,),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None


def claim_idle_session_atomic(user_id: str) -> Optional[str]:
    """
    Atomically claim an idle session (email IS NULL) and assign it to user_id.
    Returns the claimed pod_name or None if no idle session is available.

    PostgreSQL: Uses FOR UPDATE SKIP LOCKED to avoid blocking under concurrency.
    SQLite: Uses BEGIN IMMEDIATE to lock the database for the update section.
    """
    if USE_POSTGRES:
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
    else:
        conn = get_conn()
        try:
            # Autocommit off to manually control transaction
            conn.isolation_level = None
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                SELECT id, pod_name
                FROM sessions
                WHERE email IS NULL
                ORDER BY created_at
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                cur.execute("COMMIT")
                return None
            sess_id, pod_name = row[0], row[1]
            cur.execute(
                "UPDATE sessions SET email = ?, assigned_at = ? WHERE id = ?",
                (user_id, datetime.now(), sess_id),
            )
            cur.execute("COMMIT")
            return pod_name
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


# =============================================================================
# Container lifecycle — required by your request
# =============================================================================

def update_session_url(email: str, url: str, pod_name: str):
    """
    Update the session row identified by the bootstrap 'email' with URL and Pod name.
    """
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET url = %s, pod_name = %s WHERE email = %s",
                    (url, pod_name, email),
                )
            conn.commit()
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE sessions SET url = ?, pod_name = ? WHERE email = ?",
            (url, pod_name, email),
        )
        conn.commit()
        conn.close()


def log_container_start(email: str):
    """
    Insert a log entry recording when a container was started.
    """
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO logs(email, started_at) VALUES (%s, NOW())",
                    (email,),
                )
            conn.commit()
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs(email, started_at) VALUES (?, ?)",
            (email, datetime.now()),
        )
        conn.commit()
        conn.close()


def launch_container(email: str, image: str = DEFAULT_IMAGE):
    """
    Launch a container for the given email (bootstrap record).
    This follows the exact signature you provided.

    Steps:
      1) Start the pod and obtain its base Jupyter URL.
      2) Update the DB row identified by 'email' with (url, pod_name).
      3) Log the container start.
    """
    from container_manager import start_pod_and_get_jupyter_url
    pod_name, jupyter_url = start_pod_and_get_jupyter_url(image)
    if jupyter_url:
        update_session_url(email, jupyter_url, pod_name)
        log_container_start(email)


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/")
async def root():
    """
    Basic health endpoint.
    """
    return {"status": "ok"}


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
        running: set[str] = list_running_launcher_pods(prefix=prefix)
    except Exception as e:
        return JSONResponse({"error": f"Kubernetes API error: {e}"}, status_code=500)

    # 2) Fetch existing pod_names from DB
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pod_name FROM sessions WHERE pod_name IS NOT NULL")
                rows = cur.fetchall()
                existing: set[str] = {r[0] for r in rows if r and r[0]}
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT pod_name FROM sessions WHERE pod_name IS NOT NULL")
        rows = cur.fetchall()
        existing = {r[0] for r in rows if r and r[0]}
        conn.close()

    # 3) Compute delta sets
    to_insert = sorted(running - existing)
    to_delete = sorted(existing - running)
    to_reset  = sorted(running & existing)

    inserted = 0
    deleted  = 0
    reset    = 0

    # 4) Apply changes to DB
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Reset email/assigned_at for pods that are running and already exist in DB
                if to_reset:
                    placeholders = ", ".join(["%s"] * len(to_reset))
                    cur.execute(
                        f"UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name IN ({placeholders})",
                        tuple(to_reset),
                    )
                    reset = cur.rowcount

                # Insert missing rows for running pods (mark idle)
                for name in to_insert:
                    # Use compose_url(name) for convenience; adjust if you prefer NULL
                    cur.execute(
                        """
                        INSERT INTO sessions(pod_name, url, email, assigned_at)
                        VALUES (%s, %s, NULL, NULL)
                        ON CONFLICT (pod_name) DO NOTHING
                        """,
                        (name, compose_url(name)),
                    )
                    # rowcount is 1 if inserted, 0 if conflict ignored
                    inserted += cur.rowcount

                # Delete stale rows that no longer exist in Kubernetes
                if to_delete:
                    placeholders = ", ".join(["%s"] * len(to_delete))
                    cur.execute(
                        f"DELETE FROM sessions WHERE pod_name IN ({placeholders})",
                        tuple(to_delete),
                    )
                    deleted = cur.rowcount
            conn.commit()
    else:
        conn = get_conn()
        cur = conn.cursor()
        try:
            # Reset email/assigned_at for pods that are running and already exist in DB
            if to_reset:
                placeholders = ", ".join(["?"] * len(to_reset))
                cur.execute(
                    f"UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name IN ({placeholders})",
                    tuple(to_reset),
                )
                reset = cur.rowcount

            # Insert missing rows (SQLite: use INSERT OR IGNORE for idempotency)
            for name in to_insert:
                cur.execute(
                    "INSERT OR IGNORE INTO sessions(pod_name, url, email, assigned_at) VALUES (?, ?, NULL, NULL)",
                    (name, compose_url(name)),
                )
                # rowcount in SQLite for INSERT OR IGNORE can be 1 or 0 depending on conflict
                if cur.rowcount and cur.rowcount > 0:
                    inserted += 1

            # Delete stale rows
            if to_delete:
                placeholders = ", ".join(["?"] * len(to_delete))
                cur.execute(
                    f"DELETE FROM sessions WHERE pod_name IN ({placeholders})",
                    tuple(to_delete),
                )
                deleted = cur.rowcount

            conn.commit()
        finally:
            conn.close()

    # 5) Return summary
    return JSONResponse({
        "prefix": prefix,
        "running_count": len(running),
        "db_existing_count": len(existing),
        "inserted": inserted,
        "reset": reset,
        "deleted": deleted,
        "running_pods_synced": sorted(list(running)),
        "deleted_pods": to_delete,
    })


@app.get("/{nb_name}.ipynb")
async def route_notebook(request: Request, nb_name: str):
    """
    Allocation endpoint for Jupyter notebooks.
    When the frontend requests host/<nb_name>.ipynb:
      1) Extract user unique identifier (X-User-Id header / ?uid= / cookie: uid)
      2) If already assigned => return URL built from pod_name
      3) Else atomically claim an idle session (email IS NULL) by setting email=user_id
      4) If no idle session is available => return 'no_gpu'

    Response on success:
      { "url": "<computed-url>", "pod_name": "<name>", "user": "<user_id>" }

    Response when no idle session:
      plain text "no_gpu"
    """
    user_id = get_user_id_from_request(request)
    if not user_id:
        return JSONResponse(
            {"error": "User identifier missing. Provide X-User-Id header, ?uid= query, or 'uid' cookie."},
            status_code=400
        )

    # Check if this user already has an assigned session
    pod_name = get_assigned_pod_for_user(user_id)

    # If not, try to claim a new idle session
    if not pod_name:
        pod_name = claim_idle_session_atomic(user_id)

    if not pod_name:
        # No idle GPU/session available
        return PlainTextResponse("no_gpu", status_code=200)

    url = compose_url(pod_name, nb_name)
    return RedirectResponse(url=url, status_code=302)


@app.get("/tasks")
async def list_tasks():
    """
    Return all tasks from DB (ordered by created_at DESC).
    """
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, pod_name, url, email, created_at, assigned_at FROM sessions ORDER BY created_at DESC"
                )
                rows = cur.fetchall()
                data = []
                for r in rows:
                    data.append({
                        "id": r[0],
                        "pod_name": r[1],
                        "url": r[2],
                        "email": r[3],
                        "created_at": r[4].isoformat() if r[4] else None,
                        "assigned_at": r[5].isoformat() if r[5] else None,
                    })
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, pod_name, url, email, created_at, assigned_at FROM sessions ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
        conn.close()
        data = []
        for r in rows:
            data.append({
                "id": r[0],
                "pod_name": r[1],
                "url": r[2],
                "email": r[3],
                "created_at": r[4],      # SQLite returns text for timestamps
                "assigned_at": r[5],
            })
    return JSONResponse({"tasks": data, "count": len(data)})


@app.post("/tasks/add")
async def add_idle_tasks(
    count: int = Query(1, ge=1, le=1000, description="How many idle tasks to add"),
    prefix: str = Query("jupyter-task", description="Prefix for generated pod_name (used in bootstrap email tag)"),
    image: str = Query(DEFAULT_IMAGE, description="Container image to launch")
):
    """
    Create 'count' idle sessions (tasks) by:
      - Inserting a bootstrap row with a unique temporary 'email' (marks a row to be filled).
      - Calling launch_container(email, image) to start pod and set (url, pod_name).
      - Resetting email back to NULL so that the session becomes IDLE (per the 'email is NULL' rule).

    Returns:
      { "created": [<pod_name>, ...], "count": N }
    """
    created_pods = []

    # Helper to insert a bootstrap row
    def _insert_bootstrap_row(bootstrap_email: str):
        if USE_POSTGRES:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sessions(pod_name, url, email, assigned_at) VALUES (%s, NULL, %s, NULL)",
                        (None, bootstrap_email),
                    )
                conn.commit()
        else:
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO sessions(pod_name, url, email, assigned_at) VALUES (?, NULL, ?, NULL)",
                    (None, bootstrap_email),
                )
                conn.commit()
            finally:
                conn.close()

    # Helper to fetch pod_name by bootstrap email
    def _get_pod_by_email(bootstrap_email: str) -> Optional[str]:
        if USE_POSTGRES:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pod_name FROM sessions WHERE email = %s LIMIT 1",
                        (bootstrap_email,),
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT pod_name FROM sessions WHERE email = ? LIMIT 1",
                (bootstrap_email,),
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else None

    # Helper to set the row (identified by pod_name) back to idle (email=NULL)
    def _mark_idle_by_pod(pod_name: str):
        if USE_POSTGRES:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name = %s",
                        (pod_name,),
                    )
                conn.commit()
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "UPDATE sessions SET email = NULL, assigned_at = NULL WHERE pod_name = ?",
                (pod_name,),
            )
            conn.commit()
            conn.close()

    # Helper to delete a bootstrap row if startup failed
    def _delete_by_email(bootstrap_email: str):
        if USE_POSTGRES:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM sessions WHERE email = %s",
                        (bootstrap_email,),
                    )
                conn.commit()
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM sessions WHERE email = ?",
                (bootstrap_email,),
            )
            conn.commit()
            conn.close()

    # Batch create
    for _ in range(count):
        bootstrap_email = f"__BOOTSTRAP__::{prefix}::{uuid.uuid4().hex}"
        # 1) Insert a bootstrap row
        _insert_bootstrap_row(bootstrap_email)

        try:
            # 2) Start container and update the bootstrap row (url, pod_name)
            launch_container(bootstrap_email, image=image)

            # 3) Fetch the pod_name written by launch_container
            pod_name = _get_pod_by_email(bootstrap_email)
            if not pod_name:
                # Startup failed to populate pod_name; clean the row
                _delete_by_email(bootstrap_email)
                continue

            # 4) Mark the row back to idle (email=NULL, assigned_at=NULL)
            _mark_idle_by_pod(pod_name)
            created_pods.append(pod_name)

        except Exception:
            # On any failure, remove the bootstrap row
            _delete_by_email(bootstrap_email)
            continue

    return JSONResponse({"created": created_pods, "count": len(created_pods)})


@app.delete("/tasks/by_user/{user_id}")
async def delete_task_by_user(user_id: str):
    """
    Delete session by user identifier.
    As requested: deletion implementation can be left empty (placeholder).
    """
    # TODO: Implement actual deletion if needed (e.g., delete k8s Pod, free DB record)
    return JSONResponse({"message": "TODO: delete not implemented", "user": user_id})


@app.get("/db_status")
async def db_status():
    """
    Return database connectivity status and DB engine type.
    """
    db_type = "PostgreSQL" if USE_POSTGRES else "SQLite"
    try:
        conn = get_conn()
        conn.close()
        return JSONResponse({"status": "connected", "database": db_type})
    except Exception as e:
        return JSONResponse({"status": "error", "database": db_type, "error": str(e)}, status_code=500)


# =============================================================================
# Notebook allocation endpoint (must stay after other fixed routes)
# =============================================================================

@app.get("/{nb_name}.ipynb")
async def _shadow_route_notebook(request: Request, nb_name: str):
    """
    This duplicate route definition is intentionally placed last to ensure
    it does not shadow fixed top-level routes (e.g., /tasks/*, /db_status).
    FastAPI matches in declaration order, so we keep this as the last fallback.
    """
    # Delegate to the earlier handler
    return await route_notebook(request, nb_name)


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "5000")), reload=True)
