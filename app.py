import calendar
import os
from datetime import date, datetime
from functools import wraps
from urllib.parse import urlsplit

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

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
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
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
        cur.execute("ALTER TABLE todos ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)")
    conn.commit()
    conn.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        flash("아이디와 비밀번호를 모두 입력해주세요.")
        return redirect(url_for("signup"))

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            conn.close()
            flash("이미 사용 중인 아이디입니다.")
            return redirect(url_for("signup"))

        cur.execute(
            "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
            (username, generate_password_hash(password)),
        )
        user_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()

    session["user_id"] = user_id
    session["username"] = username
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
    conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        flash("아이디 또는 비밀번호가 올바르지 않습니다.")
        return redirect(url_for("login"))

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def _get_calendar_context(year, month, user_id):
    today = date.today()

    # 1~12 범위를 벗어나면 연도를 넘겨가며 보정한다.
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM todos
            WHERE user_id = %s
              AND due_date IS NOT NULL
              AND EXTRACT(YEAR FROM due_date) = %s
              AND EXTRACT(MONTH FROM due_date) = %s
            ORDER BY due_date ASC, id ASC
            """,
            (user_id, year, month),
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
@login_required
def index():
    user_id = session["user_id"]
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM todos WHERE user_id = %s ORDER BY done ASC, due_date NULLS LAST, id DESC",
            (user_id,),
        )
        todos = cur.fetchall()
    conn.close()

    calendar_ctx = _get_calendar_context(year, month, user_id)
    return render_template(
        "index.html", todos=todos, username=session["username"], **calendar_ctx
    )


def _redirect_to_index():
    # 캘린더에서 다른 달을 보던 중이었다면 그 달을 유지한 채로 돌아간다.
    year = request.form.get("view_year", type=int)
    month = request.form.get("view_month", type=int)
    if year and month:
        return redirect(url_for("index", year=year, month=month))
    return redirect(url_for("index"))


@app.route("/add", methods=["POST"])
@login_required
def add():
    title = request.form.get("title", "").strip()
    due_date = request.form.get("due_date") or None
    if not title:
        flash("할 일 내용을 입력해주세요.")
        return _redirect_to_index()

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO todos (title, done, created_at, due_date, user_id) VALUES (%s, FALSE, %s, %s, %s)",
            (title, datetime.now().strftime("%Y-%m-%d %H:%M"), due_date, session["user_id"]),
        )
    conn.commit()
    conn.close()
    return _redirect_to_index()


@app.route("/toggle/<int:todo_id>", methods=["POST"])
@login_required
def toggle(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE todos SET done = NOT done WHERE id = %s AND user_id = %s",
            (todo_id, session["user_id"]),
        )
    conn.commit()
    conn.close()
    return _redirect_to_index()


@app.route("/delete/<int:todo_id>", methods=["POST"])
@login_required
def delete(todo_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM todos WHERE id = %s AND user_id = %s",
            (todo_id, session["user_id"]),
        )
    conn.commit()
    conn.close()
    return _redirect_to_index()


init_db()

if __name__ == "__main__":
    app.run(debug=True)
