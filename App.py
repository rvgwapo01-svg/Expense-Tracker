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
from pg8000.native import Connection # pyright: ignore[reportMissingImports]

# ─────────────────────────────
# APP SETUP
# ─────────────────────────────
app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-key")

DATABASE_URL = os.environ.get("DATABASE_URL", "")


# ─────────────────────────────
# DB CONNECTION
# ─────────────────────────────
def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")

    u = urlparse(DATABASE_URL)

    conn = Connection(
        user=u.username,
        password=u.password,
        host=u.hostname,
        port=u.port or 5432,
        database=u.path.lstrip("/"),
        ssl_context=True
    )

    return conn


# ─────────────────────────────
# INIT DB
# ─────────────────────────────
def init_db():
    db = get_db()

    db.run("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT
        )
    """)

    db.run("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id INT,
            amount FLOAT,
            description TEXT,
            date TEXT
        )
    """)

    db.run("""
        INSERT INTO users (username, password)
        VALUES ('demo', 'demo')
        ON CONFLICT DO NOTHING
    """)

@app.template_filter('peso')
def peso(value):
    try:
        return "{:,.2f}".format(float(value))
    except:
        return "0.00"
# ─────────────────────────────
# ERROR HANDLER
# ─────────────────────────────
@app.errorhandler(Exception)
def error(e):
    return jsonify({
        "error": str(e),
        "trace": traceback.format_exc()
    }), 500


# ─────────────────────────────
# ROUTES
# ─────────────────────────────
@app.route("/")
def index():
    return redirect("/login")


# 🔥 LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect("/dashboard")
    return render_template("login.html")


# 🔥 REGISTER (IMPORTANT FIX)
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        db.run(
            "INSERT INTO users (username, password) VALUES (:1, :2)",
            (username, generate_password_hash(password))
        )

        return redirect("/login")

    return render_template("register.html")


# 🔥 DASHBOARD
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# 🔥 HEALTH CHECK (VERCEL DEBUG)
@app.route("/health")
def health():
    try:
        db = get_db()
        db.run("SELECT 1")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


# ─────────────────────────────
# ENTRY
# ─────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True)