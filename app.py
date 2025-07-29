from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from container_manager import start_pod_and_get_jupyter_url
import sqlite3

DB_PATH = "sessions.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (email TEXT PRIMARY KEY, url TEXT)"
        )


app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
init_db()


def create_session(email: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions(email, url) VALUES (?, NULL)", (email,)
        )


def update_session_url(email: str, url: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE sessions SET url = ? WHERE email = ?", (url, email)
        )


def get_session_url(email: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT url FROM sessions WHERE email = ?", (email,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def launch_container(email: str):
    jupyter_url = start_pod_and_get_jupyter_url()
    if jupyter_url:
        update_session_url(email, jupyter_url)


@app.get("/no_gpu", response_class=HTMLResponse)
async def no_gpu(request: Request):
    """Inform the user that no GPUs are currently available."""
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

    existing = get_session_url(email)
    if existing:
        return JSONResponse({"url": existing})

    create_session(email)
    background_tasks.add_task(launch_container, email)
    return JSONResponse({"message": "Pod is launching, please wait...", "email": email})


@app.get("/get_url")
async def get_url(email: str | None = None):
    url = get_session_url(email) if email else None
    return JSONResponse({"url": url})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
