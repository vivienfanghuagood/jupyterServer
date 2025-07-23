from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from container_manager import start_pod_and_get_jupyter_url
import sqlite3
import uuid



DB_PATH = "sessions.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, url TEXT)"
        )

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
init_db()
def create_session(session_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO sessions(session_id, url) VALUES (?, NULL)", (session_id,)
        )


def update_session_url(session_id: str, url: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE sessions SET url = ? WHERE session_id = ?", (url, session_id)
        )


def get_session_url(session_id: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "SELECT url FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def launch_container(session_id: str):
    jupyter_url = start_pod_and_get_jupyter_url()
    if jupyter_url:
        update_session_url(session_id, jupyter_url)


@app.get("/no_gpu", response_class=HTMLResponse)
async def no_gpu(request: Request):
    """Inform the user that no GPUs are currently available."""
    return templates.TemplateResponse("gpu_unavailable.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/launch")
async def launch(background_tasks: BackgroundTasks):
    session_id = str(uuid.uuid4())
    create_session(session_id)
    background_tasks.add_task(launch_container, session_id)
    return JSONResponse(
        {"message": "Pod is launching, please wait...", "session_id": session_id}
    )


@app.get("/get_url")
async def get_url(session_id: str | None = None):
    url = get_session_url(session_id) if session_id else None
    return JSONResponse({"url": url})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
