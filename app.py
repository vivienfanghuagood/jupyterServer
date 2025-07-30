from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from container_manager import start_pod_and_get_jupyter_url
import psycopg2
import os

DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_NAME = os.environ.get("POSTGRES_DB", "flaskdb")
DB_USER = os.environ.get("POSTGRES_USER", "flaskuser")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "flaskpass")


def get_conn():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'sessions'
                    ) THEN
                        CREATE TABLE sessions (
                            email TEXT PRIMARY KEY,
                            url TEXT
                        );
                    END IF;
                END
                $$;
            """)
        conn.commit()



app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
init_db()


def create_session(email: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions(email, url) VALUES (%s, NULL) ON CONFLICT DO NOTHING", (email,)
            )
        conn.commit()


def update_session_url(email: str, url: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET url = %s WHERE email = %s", (url, email)
            )
        conn.commit()


def get_session_url(email: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT url FROM sessions WHERE email = %s", (email,)
            )
            row = cur.fetchone()
            return row[0] if row else None


def launch_container(email: str):
    jupyter_url = start_pod_and_get_jupyter_url()
    if jupyter_url:
        update_session_url(email, jupyter_url)


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
