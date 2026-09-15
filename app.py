import os
from datetime import datetime
from urllib.parse import urlsplit

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash

load_dotenv(".env.local")


def _connection_kwargs(database_url):
    # Supabase URL에는 psycopg2가 모르는 pooler 전용 쿼리 파라미터가 붙어 있어
    # 필요한 값만 직접 뽑아서 연결 인자로 사용한다.
    parts = urlsplit(database_url)
    return {
        "dbname": parts.path.lstrip("/"),
        "user": parts.username,
        "password": parts.password,
        "host": parts.hostname,
        "port": parts.port or 5432,
        "sslmode": "require",
    }


CONNECTION_KWARGS = _connection_kwargs(os.environ["POSTGRES_URL"])

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")


def get_db():
    conn = psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **CONNECTION_KWARGS)
    return conn


def init_db():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                done BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL
            )
            """
        )
    conn.commit()
    conn.close()


@app.route("/")
def index():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM todos ORDER BY done ASC, id DESC")
        todos = cur.fetchall()
    conn.close()
    return render_template("index.html", todos=todos)


@app.route("/add", methods=["POST"])
def add():
    title = request.form.get("title", "").strip()
    if not title:
        flash("할 일 내용을 입력해주세요.")
        return redirect(url_for("index"))

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO todos (title, done, created_at) VALUES (%s, FALSE, %s)",
            (title, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/toggle/<int:todo_id>", methods=["POST"])
def toggle(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE todos SET done = NOT done WHERE id = %s", (todo_id,)
        )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:todo_id>", methods=["POST"])
def delete(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM todos WHERE id = %s", (todo_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)
