import calendar
import os
from datetime import date, datetime
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
                created_at TEXT NOT NULL,
                due_date DATE
            )
            """
        )
        cur.execute("ALTER TABLE todos ADD COLUMN IF NOT EXISTS due_date DATE")
    conn.commit()
    conn.close()


def _get_calendar_context(year, month):
    today = date.today()

    # 1~12 범위를 벗어나면 연도를 넘겨가며 보정한다.
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM todos
            WHERE due_date IS NOT NULL
              AND EXTRACT(YEAR FROM due_date) = %s
              AND EXTRACT(MONTH FROM due_date) = %s
            ORDER BY due_date ASC, id ASC
            """,
            (year, month),
        )
        month_todos = cur.fetchall()
    conn.close()

    todos_by_day = {}
    for todo in month_todos:
        todos_by_day.setdefault(todo["due_date"].day, []).append(todo)

    cal = calendar.Calendar(firstweekday=6)  # 일요일 시작
    weeks = cal.monthdayscalendar(year, month)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return {
        "year": year,
        "month": month,
        "weeks": weeks,
        "todos_by_day": todos_by_day,
        "today": today,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
    }


@app.route("/")
def index():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM todos ORDER BY done ASC, due_date NULLS LAST, id DESC")
        todos = cur.fetchall()
    conn.close()

    calendar_ctx = _get_calendar_context(year, month)
    return render_template("index.html", todos=todos, **calendar_ctx)


def _redirect_to_index():
    # 캘린더에서 다른 달을 보던 중이었다면 그 달을 유지한 채로 돌아간다.
    year = request.form.get("view_year", type=int)
    month = request.form.get("view_month", type=int)
    if year and month:
        return redirect(url_for("index", year=year, month=month))
    return redirect(url_for("index"))


@app.route("/add", methods=["POST"])
def add():
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date") or None
    if not title:
        flash("할 일 내용을 입력해주세요.")
        return _redirect_to_index()

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO todos (title, done, created_at, due_date) VALUES (%s, FALSE, %s, %s)",
            (title, datetime.now().strftime("%Y-%m-%d %H:%M"), due_date),
        )
    conn.commit()
    conn.close()
    return _redirect_to_index()


@app.route("/toggle/<int:todo_id>", methods=["POST"])
def toggle(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE todos SET done = NOT done WHERE id = %s", (todo_id,)
        )
    conn.commit()
    conn.close()
    return _redirect_to_index()


@app.route("/delete/<int:todo_id>", methods=["POST"])
def delete(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM todos WHERE id = %s", (todo_id,))
    conn.commit()
    conn.close()
    return _redirect_to_index()


init_db()

if __name__ == "__main__":
    app.run(debug=True)
