from flask import Flask, render_template, jsonify, request
from container_manager import start_pod_and_get_jupyter_url
import threading
import sqlite3
import uuid



DB_PATH = "sessions.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, url TEXT)"
        )

app = Flask(__name__)
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


@app.route("/no_gpu")
def no_gpu():
    """Inform the user that no GPUs are currently available."""
    return render_template("gpu_unavailable.html")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/launch", methods=["POST"])
def launch():
    session_id = str(uuid.uuid4())
    create_session(session_id)
    thread = threading.Thread(target=launch_container, args=(session_id,))
    thread.start()
    return jsonify(
        {"message": "Pod is launching, please wait...", "session_id": session_id}
    )


@app.route("/get_url", methods=["GET"])
def get_url():
    session_id = request.args.get("session_id")
    url = get_session_url(session_id) if session_id else None
    return jsonify({"url": url})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
