from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from container_manager import start_pod_and_get_jupyter_url
import os
import subprocess
from datetime import datetime

# Database configuration
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_NAME = os.environ.get("POSTGRES_DB", "flaskdb")
DB_USER = os.environ.get("POSTGRES_USER", "flaskuser")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "flaskpass")
DATABASE_PATH = "sessions.db"

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
    """Initialize database tables for either PostgreSQL or SQLite."""
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Create sessions table if it doesn't exist
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        email TEXT PRIMARY KEY,
                        url TEXT,
                        pod_name TEXT
                    );
                    """
                )

                # Create logs table if it doesn't exist
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS logs (
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
        
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                email TEXT PRIMARY KEY,
                url TEXT,
                pod_name TEXT
            );
            """
        )
        
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                started_at TIMESTAMP
            );
            """
        )
        conn.commit()
        conn.close()


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
init_db()


def create_session(email: str):
    """Create a new session for the given email."""
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sessions(email, url, pod_name) VALUES (%s, NULL, NULL) ON CONFLICT DO NOTHING",
                    (email,),
                )
            conn.commit()
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO sessions(email, url, pod_name) VALUES (?, NULL, NULL)",
            (email,),
        )
        conn.commit()
        conn.close()


def update_session_url(email: str, url: str, pod_name: str | None):
    """Update session URL and pod name for the given email."""
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


def get_session_url(email: str) -> tuple[str | None, str | None]:
    """Get session URL and pod name for the given email."""
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT url, pod_name FROM sessions WHERE email = %s", (email,)
                )
                row = cur.fetchone()
                return (row[0], row[1]) if row else (None, None)
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT url, pod_name FROM sessions WHERE email = ?", (email,)
        )
        row = cur.fetchone()
        conn.close()
        return (row[0], row[1]) if row else (None, None)


def log_container_start(email: str):
    """Insert a log entry recording when a container was started."""
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


def update_nginx_proxy():
    """Update nginx configuration with new pod mappings."""
    # Removed - nginx proxy update not needed with current static configuration
    pass


def launch_container(email: str):
    """Launch a container for the given email."""
    pod_name, jupyter_url = start_pod_and_get_jupyter_url()
    if jupyter_url:
        update_session_url(email, jupyter_url, pod_name)
        log_container_start(email)
        # nginx proxy update not needed - using static configuration


@app.get("/no_gpu", response_class=HTMLResponse)
async def no_gpu(request: Request):
    return templates.TemplateResponse("gpu_unavailable.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/launch")
async def launch(background_tasks: BackgroundTasks, request: Request):
    data = await request.json()
    email = data.get("email")
    if not email:
        return JSONResponse({"error": "email required"}, status_code=400)

    existing_url, _ = get_session_url(email)
    if existing_url:
        return JSONResponse({"url": existing_url})

    create_session(email)
    background_tasks.add_task(launch_container, email)
    return JSONResponse({"message": "Pod is launching, please wait...", "email": email})


@app.get("/get_url")
async def get_url(email: str | None = None):
    url, pod_name = get_session_url(email) if email else (None, None)
    return JSONResponse({"url": url, "pod_name": pod_name})


@app.post("/update_proxy")
async def update_proxy():
    """Manually trigger nginx proxy configuration update."""
    # nginx proxy update not needed - using static configuration
    return JSONResponse({"message": "Nginx uses static configuration - no update needed"})


@app.get("/pod_mappings")
async def get_pod_mappings():
    """Get current pod to port mappings."""
    import json
    mapping_file = "/tmp/jupyter_pod_mappings.json"
    
    try:
        if os.path.exists(mapping_file):
            with open(mapping_file, 'r') as f:
                mappings = json.load(f)
            return JSONResponse({"mappings": mappings})
        else:
            return JSONResponse({"mappings": {}, "message": "No pod mappings found"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/db_status")
async def db_status():
    """Check database connection status."""
    db_type = "PostgreSQL" if USE_POSTGRES else "SQLite"
    try:
        conn = get_conn()
        if USE_POSTGRES:
            conn.close()
        else:
            conn.close()
        return JSONResponse({"status": "connected", "database": db_type})
    except Exception as e:
        return JSONResponse({"status": "error", "database": db_type, "error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)