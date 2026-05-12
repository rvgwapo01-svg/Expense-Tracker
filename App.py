import os
import traceback
from datetime import datetime
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash


# ─────────────────────────────
# CONFIG
# ─────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key")

DATABASE_URL = os.environ.get("DATABASE_URL")


# ─────────────────────────────
# DATABASE CONNECTION
# ─────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ─────────────────────────────
# LOGIN REQUIRED DECORATOR
# ─────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ─────────────────────────────
# INIT DB
# ─────────────────────────────
def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            total_money REAL DEFAULT 0,
            description TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monthly_budget (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            year INTEGER,
            month INTEGER,
            budget REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, year, month)
        )
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────
# HOME
# ─────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


# ─────────────────────────────
# LOGIN
# ─────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()

        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))

        flash("Invalid login")

    return render_template("login.html")


# ─────────────────────────────
# REGISTER
# ─────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])

        conn = get_db()
        cur = conn.cursor()

        try:
            cur.execute(
                "INSERT INTO users (username, password) VALUES (%s, %s)",
                (username, password)
            )
            conn.commit()
            return redirect(url_for("login"))
        except Exception as e:
            flash("User already exists")
        finally:
            conn.close()

    return render_template("register.html")


# ─────────────────────────────
# LOGOUT
# ─────────────────────────────
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─────────────────────────────
# DASHBOARD
# ─────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=%s", (uid,))
    total = cur.fetchone()["total"]

    cur.execute("""
        SELECT * FROM expenses
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 10
    """, (uid,))
    recent = cur.fetchall()

    conn.close()

    return render_template("dashboard.html", total=total, recent=recent)


# ─────────────────────────────
# ADD EXPENSE
# ─────────────────────────────
@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    uid = session["user_id"]

    if request.method == "POST":
        amount = request.form["amount"]
        desc = request.form["description"]
        category = request.form["category"]
        date = request.form["date"]

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO expenses (user_id, amount, description, category, date)
            VALUES (%s, %s, %s, %s, %s)
        """, (uid, amount, desc, category, date))

        conn.commit()
        conn.close()

        return redirect(url_for("expenses"))

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM expenses WHERE user_id=%s ORDER BY id DESC", (uid,))
    data = cur.fetchall()

    conn.close()

    return render_template("expenses.html", expenses=data)


# ─────────────────────────────
# HEALTH CHECK (Vercel)
# ─────────────────────────────
@app.route("/health")
def health():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─────────────────────────────
# RUN LOCALLY
# ─────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(debug=True)