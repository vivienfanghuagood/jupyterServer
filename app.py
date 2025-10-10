"""
FastAPI application for managing Jupyter notebook containers with Kubernetes.
"""

import os
import re
import uuid
from datetime import datetime
from typing import Optional, Tuple, Dict
from contextlib import contextmanager

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from container_manager import start_pod_and_get_jupyter_url, start_pod_with_github_repo, start_ip_pod_and_get_jupyter_url, start_pod_with_single_notebook

# ========== Configuration ==========
class Config:
    """Application configuration"""
    # Database settings
    DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
    DB_NAME = os.environ.get("POSTGRES_DB", "flaskdb")
    DB_USER = os.environ.get("POSTGRES_USER", "flaskuser")
    DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "flaskpass")
    SQLITE_PATH = "sessions.db"

    # GitHub URL pattern
    GITHUB_URL_PATTERN = r"github\.com/([^/]+)/([^/]+)/(?:blob|tree)/([^/]+)/(.+\.ipynb)"

# ========== Database Layer ==========
class DatabaseManager:
    """Manages database connections and operations"""

    def __init__(self):
        self.use_postgres = self._init_postgres()
        self._init_tables()

    def _init_postgres(self) -> bool:
        """Try to initialize PostgreSQL connection"""
        try:
            import psycopg2
            test_conn = psycopg2.connect(
                host=Config.DB_HOST,
                dbname=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
            )
            test_conn.close()
            print("Using PostgreSQL database")
            return True
        except (ImportError, Exception) as e:
            print(f"PostgreSQL not available ({e}), falling back to SQLite")
            import sqlite3
            return False

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        if self.use_postgres:
            import psycopg2
            conn = psycopg2.connect(
                host=Config.DB_HOST,
                dbname=Config.DB_NAME,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
            )
        else:
            import sqlite3
            conn = sqlite3.connect(Config.SQLITE_PATH)

        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            cur = conn.cursor()

            if self.use_postgres:
                # PostgreSQL table creation
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        email TEXT PRIMARY KEY,
                        url TEXT,
                        pod_name TEXT
                    );
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id SERIAL PRIMARY KEY,
                        email TEXT,
                        started_at TIMESTAMP
                    );
                """)
            else:
                # SQLite table creation
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        email TEXT PRIMARY KEY,
                        url TEXT,
                        pod_name TEXT
                    );
                """)

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT,
                        started_at TIMESTAMP
                    );
                """)

# ========== Session Management ==========
class SessionManager:
    """Manages user sessions and pod assignments"""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def create_session(self, email: str):
        """Create a new session for the given email"""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if self.db.use_postgres:
                cur.execute(
                    "INSERT INTO sessions(email, url, pod_name) VALUES (%s, NULL, NULL) ON CONFLICT DO NOTHING",
                    (email,)
                )
            else:
                cur.execute(
                    "INSERT OR IGNORE INTO sessions(email, url, pod_name) VALUES (?, NULL, NULL)",
                    (email,)
                )

    def update_session_url(self, email: str, url: str, pod_name: Optional[str]):
        """Update session URL and pod name for the given email"""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if self.db.use_postgres:
                cur.execute(
                    "UPDATE sessions SET url = %s, pod_name = %s WHERE email = %s",
                    (url, pod_name, email)
                )
            else:
                cur.execute(
                    "UPDATE sessions SET url = ?, pod_name = ? WHERE email = ?",
                    (url, pod_name, email)
                )

    def get_session_url(self, email: str) -> Tuple[Optional[str], Optional[str]]:
        """Get session URL and pod name for the given email"""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if self.db.use_postgres:
                cur.execute(
                    "SELECT url, pod_name FROM sessions WHERE email = %s",
                    (email,)
                )
            else:
                cur.execute(
                    "SELECT url, pod_name FROM sessions WHERE email = ?",
                    (email,)
                )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)

    def log_container_start(self, email: str):
        """Insert a log entry recording when a container was started"""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if self.db.use_postgres:
                cur.execute(
                    "INSERT INTO logs(email, started_at) VALUES (%s, NOW())",
                    (email,)
                )
            else:
                cur.execute(
                    "INSERT INTO logs(email, started_at) VALUES (?, ?)",
                    (email, datetime.now())
                )

# ========== Container Launch Handler ==========
class ContainerLauncher:
    """Handles container launching operations"""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        # Store GitHub launch sessions temporarily
        self.github_sessions: Dict[str, dict] = {}

    def launch_container(self, email: str):
        """Launch a container for the given email"""
        pod_name, jupyter_url = start_pod_and_get_jupyter_url()
        if jupyter_url:
            self.session_manager.update_session_url(email, jupyter_url, pod_name)
            self.session_manager.log_container_start(email)
        
    def launch_video_gen_container(self, email: str):
        """Launch a container for the given email"""
        pod_name, jupyter_url = start_ip_pod_and_get_jupyter_url()
        if jupyter_url:
            self.session_manager.update_session_url(email, jupyter_url, pod_name)
            self.session_manager.log_container_start(email)

    def launch_github_container(self, session_id: str, owner: str, repo: str,
                                branch: str, notebook_path: str):
        """Launch a GitHub container and track its status"""
        # Store initial status
        self.github_sessions[session_id] = {
            "status": "launching",
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "notebook_path": notebook_path,
            "url": None,
            "pod_name": None,
            "error": None
        }

        try:
            # Launch the container
            pod_name, jupyter_url = start_pod_with_single_notebook(
                owner=owner,
                repo=repo,
                branch=branch,
                notebook_path=notebook_path
            )

            if jupyter_url and jupyter_url != "/no_gpu":
                self.github_sessions[session_id]["status"] = "ready"
                self.github_sessions[session_id]["url"] = jupyter_url
                self.github_sessions[session_id]["pod_name"] = pod_name
            else:
                self.github_sessions[session_id]["status"] = "error"
                self.github_sessions[session_id]["error"] = "No GPU available or launch failed"
        except Exception as e:
            self.github_sessions[session_id]["status"] = "error"
            self.github_sessions[session_id]["error"] = str(e)

    def get_github_session_status(self, session_id: str) -> Optional[dict]:
        """Get the status of a GitHub launch session"""
        return self.github_sessions.get(session_id)

    def parse_github_url(self, github_url: str) -> Optional[dict]:
        """Parse GitHub URL to extract repository information"""
        match = re.search(Config.GITHUB_URL_PATTERN, github_url)
        if not match:
            return None

        return {
            "owner": match.group(1),
            "repo": match.group(2),
            "branch": match.group(3),
            "notebook_path": match.group(4)
        }

# ========== Application Setup ==========
app = FastAPI(title="Jupyter Container Manager")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize managers
db_manager = DatabaseManager()
session_manager = SessionManager(db_manager)
container_launcher = ContainerLauncher(session_manager)

# ========== API Routes ==========
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/no_gpu", response_class=HTMLResponse)
async def no_gpu(request: Request):
    """Render the GPU unavailable page"""
    return templates.TemplateResponse("gpu_unavailable.html", {"request": request})

@app.post("/launch")
async def launch(background_tasks: BackgroundTasks, request: Request):
    """Launch a container for a user email"""
    data = await request.json()
    email = data.get("email")

    if not email:
        return JSONResponse({"error": "email required"}, status_code=400)

    existing_url, _ = session_manager.get_session_url(email)
    if existing_url:
        return JSONResponse({"url": existing_url})

    session_manager.create_session(email)
    background_tasks.add_task(container_launcher.launch_container, email)

    return JSONResponse({
        "message": "Pod is launching, please wait...",
        "email": email
    })

@app.post("/video_gen_launch")
async def launch(background_tasks: BackgroundTasks, request: Request):
    """Launch a container for a user email"""
    data = await request.json()
    email = data.get("email")

    if not email:
        return JSONResponse({"error": "email required"}, status_code=400)

    existing_url, _ = session_manager.get_session_url(email)
    if existing_url:
        return JSONResponse({"url": existing_url})

    session_manager.create_session(email)
    background_tasks.add_task(container_launcher.launch_video_gen_container, email)

    return JSONResponse({
        "message": "Pod is launching, please wait...",
        "email": email
    })

@app.get("/github/{owner}/{repo}/blob/{branch:path}", response_class=HTMLResponse)
@app.get("/github/{owner}/{repo}/tree/{branch:path}", response_class=HTMLResponse)
async def launch_github(request: Request, owner: str, repo: str, branch: str):
    """Launch a Jupyter pod with a GitHub repository from URL path"""
    # Extract notebook path from branch parameter if it contains it
    # branch parameter will contain: "main/path/to/notebook.ipynb"
    parts = branch.split('/', 1)
    actual_branch = parts[0]
    notebook_path = parts[1] if len(parts) > 1 else ""

    # Generate a session ID for tracking
    session_id = str(uuid.uuid4())

    # Return the loading page immediately
    return templates.TemplateResponse(
        "github_loading.html",
        {
            "request": request,
            "owner": owner,
            "repo": repo,
            "branch": actual_branch,
            "notebook_path": notebook_path,
            "session_id": session_id
        }
    )

@app.post("/launch_github")
async def launch_github_post(request: Request):
    """Launch a Jupyter pod with a GitHub repository (POST endpoint)"""
    data = await request.json()
    github_url = data.get("github_url")

    if not github_url:
        return JSONResponse({"error": "github_url required"}, status_code=400)

    # Parse GitHub URL
    repo_info = container_launcher.parse_github_url(github_url)
    if not repo_info:
        return JSONResponse({
            "error": "Invalid GitHub URL format. Expected: github.com/owner/repo/blob/branch/path/to/notebook.ipynb"
        }, status_code=400)

    # Launch container with GitHub repo
    pod_name, jupyter_url = start_pod_with_single_notebook(**repo_info)

    if jupyter_url and jupyter_url != "/no_gpu":
        return JSONResponse({
            "url": jupyter_url,
            "pod_name": pod_name,
            "repo": f"{repo_info['owner']}/{repo_info['repo']}",
            "notebook": repo_info['notebook_path']
        })
    else:
        return JSONResponse({
            "error": "Failed to start pod",
            "redirect": jupyter_url or "/no_gpu"
        }, status_code=503)

@app.get("/get_url")
async def get_url(email: Optional[str] = None):
    """Get the URL for a user's session"""
    if not email:
        return JSONResponse({"url": None, "pod_name": None})

    url, pod_name = session_manager.get_session_url(email)
    return JSONResponse({"url": url, "pod_name": pod_name})

@app.post("/api/launch_github")
async def api_launch_github(background_tasks: BackgroundTasks, request: Request):
    """API endpoint to launch GitHub repository in background"""
    data = await request.json()

    session_id = data.get("session_id")
    owner = data.get("owner")
    repo = data.get("repo")
    branch = data.get("branch")
    notebook_path = data.get("notebook_path", "")

    if not all([session_id, owner, repo, branch]):
        return JSONResponse({
            "error": "Missing required parameters"
        }, status_code=400)

    # Launch container in background
    background_tasks.add_task(
        container_launcher.launch_github_container,
        session_id, owner, repo, branch, notebook_path
    )

    return JSONResponse({
        "status": "launching",
        "session_id": session_id
    })

@app.get("/api/github_status/{session_id}")
async def get_github_status(session_id: str):
    """Get the status of a GitHub launch session"""
    status = container_launcher.get_github_session_status(session_id)

    if not status:
        return JSONResponse({
            "status": "unknown",
            "message": "Session not found"
        }, status_code=404)

    return JSONResponse({
        "status": status["status"],
        "url": status.get("url"),
        "pod_name": status.get("pod_name"),
        "error": status.get("error")
    })

@app.get("/db_status")
async def db_status():
    """Check database connection status"""
    db_type = "PostgreSQL" if db_manager.use_postgres else "SQLite"

    try:
        with db_manager.get_connection() as conn:
            # Connection successful
            pass
        return JSONResponse({"status": "connected", "database": db_type})
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "database": db_type,
            "error": str(e)
        }, status_code=500)

# ========== Main ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)