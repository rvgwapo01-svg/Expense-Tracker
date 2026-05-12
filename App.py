import os
import traceback
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Flask, flash, jsonify, redirect,
    render_template, request, session, url_for
)

from werkzeug.security import check_password_hash, generate_password_hash

# ─────────────────────────────────────────────
# PostgreSQL (CLEAN FIX - pg8000 native)
# ─────────────────────────────────────────────
from pg8000.native import Connection


# ─── App setup ───────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(_ROOT, 'static')

app = Flask(
    __name__,
    template_folder=os.path.join(_ROOT, 'templates'),
    static_folder=_STATIC if os.path.isdir(_STATIC) else None,
)

app.secret_key = os.environ.get('SECRET_KEY', 'dev-fallback-change-me')
DATABASE_URL = os.environ.get('DATABASE_URL', '')


# ─────────────────────────────────────────────
# DB Wrapper (pg8000 native compatible)
# ─────────────────────────────────────────────
class DbWrapper:
    def __init__(self, conn):
        self._conn = conn
        self._last_rows = []
        self._columns = []

    def _to_pg(self, sql: str) -> str:
        out, n = [], 0
        for ch in sql:
            if ch == '?':
                n += 1
                out.append(f':{n}')
            else:
                out.append(ch)
        return ''.join(out)

    def execute(self, sql, params=()):
        converted = self._to_pg(sql)

        result = self._conn.run(converted, *params)

        # pg8000.native returns tuple rows
        if result and isinstance(result, list):
            self._last_rows = result
        else:
            self._last_rows = []

        # column names (safe fallback)
        self._columns = self._conn.columns or []

        return self

    def fetchone(self):
        return self._last_rows[0] if self._last_rows else None

    def fetchall(self):
        return self._last_rows

    def commit(self):
        try:
            self._conn.run("COMMIT")
        except:
            pass

    def close(self):
        try:
            self._conn.close()
        except:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ─────────────────────────────────────────────
# CONNECT DB
# ─────────────────────────────────────────────
def get_db() -> DbWrapper:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing in Vercel env vars")

    u = urlparse(DATABASE_URL)

    conn = Connection(
        user=u.username,
        password=u.password,
        host=u.hostname,
        port=u.port or 5432,
        database=u.path.lstrip('/'),
        ssl_context=True
    )

    return DbWrapper(conn)


# ─────────────────────────────────────────────
# INIT DB
# ─────────────────────────────────────────────
def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                amount REAL NOT NULL,
                total_money REAL NOT NULL DEFAULT 0,
                description TEXT NOT NULL,
                category TEXT DEFAULT 'General',
                date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS monthly_budget (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                budget REAL NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, year, month)
            )
        """)

        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?) ON CONFLICT (username) DO NOTHING",
            ("demo", generate_password_hash("demo123"))
        )

        db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?) ON CONFLICT (username) DO NOTHING",
            ("admin", generate_password_hash("admin123"))
        )

        db.commit()


# ─────────────────────────────────────────────
# ERROR HANDLER (DEBUG)
# ─────────────────────────────────────────────
@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({
        "error": str(e),
        "traceback": traceback.format_exc()
    }), 500


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        with get_db() as db:
            user = db.execute(
                "SELECT * FROM users WHERE username=?",
                (username,)
            ).fetchone()

        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            return redirect("/dashboard")

        flash("Invalid login")

    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/health")
def health():
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True)