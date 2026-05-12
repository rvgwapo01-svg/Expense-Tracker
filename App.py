# ─────────────────────────────────────────────────────────────
#  app.py — Expense Tracker (FIXED VERCEL VERSION)
# ─────────────────────────────────────────────────────────────

import os
import traceback
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# ─────────────────────────────────────────────
# DATABASE (FIXED: remove broken pg8000 usage)
# ─────────────────────────────────────────────
import psycopg2 # pyright: ignore[reportMissingModuleSource]


_ROOT = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(_ROOT, "static")

app = Flask(
    __name__,
    template_folder=os.path.join(_ROOT, "templates"),
    static_folder=_STATIC if os.path.isdir(_STATIC) else None,
)

app.secret_key = os.environ.get("SECRET_KEY", "dev-fallback-change-me")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


# ─────────────────────────────────────────────
# JINJA FILTERS (FIXED CRASH)
# ─────────────────────────────────────────────
@app.template_filter("peso")
def peso(value):
    try:
        return "{:,.2f}".format(float(value))
    except:
        return "0.00"


@app.template_filter("commaint")
def commaint(value):
    try:
        return "{:,}".format(int(value))
    except:
        return "0"


# ─────────────────────────────────────────────
# DB CONNECTION (SIMPLIFIED & VERCEL SAFE)
# ─────────────────────────────────────────────
def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing in Vercel env vars")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ─────────────────────────────────────────────
# LOGIN REQUIRED
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            return redirect(url_for("dashboard"))

        flash("Invalid login")

    return render_template("login.html")


# ─────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = generate_password_hash(request.form.get("password"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (username, password),
        )

        conn.commit()
        conn.close()

        return redirect(url_for("login"))

    return render_template("register.html")


# ─────────────────────────────────────────────
# DASHBOARD (FIXED net_balance ERROR)
# ─────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE user_id=%s", (uid,))
    total = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE user_id=%s", (uid,))
    latest_total_money = cur.fetchone()[0]

    net_balance = latest_total_money - total

    conn.close()

    return render_template(
        "dashboard.html",
        total=total,
        latest_total_money=latest_total_money,
        net_balance=net_balance,
    )


# ─────────────────────────────────────────────
# EXPENSES (SAFE BASIC VERSION)
# ─────────────────────────────────────────────
@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    uid = session["user_id"]

    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        amount = request.form.get("amount")
        desc = request.form.get("description")

        cur.execute(
            "INSERT INTO expenses (user_id, amount, description, date) VALUES (%s,%s,%s,%s)",
            (uid, amount, desc, datetime.now().strftime("%Y-%m-%d")),
        )
        conn.commit()

    cur.execute("SELECT * FROM expenses WHERE user_id=%s ORDER BY id DESC", (uid,))
    data = cur.fetchall()

    conn.close()

    return render_template("expenses.html", expense_list=data)


# ─────────────────────────────────────────────
# DELETE
# ─────────────────────────────────────────────
@app.route("/expense/delete/<int:id>", methods=["POST"])
@login_required
def delete_expense(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("expenses"))


# ─────────────────────────────────────────────
# RUN LOCAL
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)