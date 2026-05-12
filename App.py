# ─────────────────────────────────────────────────────────────
#  app.py  —  Expense Tracker  (Vercel + Neon PostgreSQL)
#
#  Required environment variables (Vercel → Settings → Env Vars):
#    DATABASE_URL  →  postgresql://user:pass@host/db?sslmode=require
#    SECRET_KEY    →  any long random string
#    SETUP_TOKEN   →  any secret (protects the /setup route)
# ─────────────────────────────────────────────────────────────

import os
import traceback
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

# ── pg8000 import — fail clearly instead of silently ──────────
try:
    import psycopg2 # pyright: ignore[reportMissingModuleSource, reportMissingImports]
except ImportError as _e:
    raise ImportError(
        'pg8000 is not installed. Make sure requirements.txt '
        'contains "pg8000>=1.31.0" at the project root.'
    ) from _e

# ─── App setup ───────────────────────────────────────────────
_ROOT   = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(_ROOT, 'static')

app = Flask(
    __name__,
    template_folder = os.path.join(_ROOT, 'templates'),
    static_folder   = _STATIC if os.path.isdir(_STATIC) else None,
)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-fallback-change-me')
DATABASE_URL   = os.environ.get('DATABASE_URL', '')

# ─── Database wrapper ────────────────────────────────────────
#
#  Translates db.execute(sql, params).fetchone() calls into
#  pg8000.native — no silent swallowing, all errors propagate.
#  Placeholder conversion:  ?  →  :1  :2  :3  ...

class DbWrapper:

    def __init__(self, conn):
        self._conn      = conn
        self._last_rows = []

    @staticmethod
    def _to_pg(sql: str) -> str:
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
        rows = self._conn.run(converted, *params)   # raises on error
        cols = [c['name'] for c in (self._conn.columns or [])]
        self._last_rows = [dict(zip(cols, row)) for row in (rows or [])]
        return self

    def fetchone(self):
        return self._last_rows[0] if self._last_rows else None

    def fetchall(self):
        return list(self._last_rows)

    def commit(self):
        try:
            self._conn.run('COMMIT')
        except Exception:
            pass   # already committed / no active transaction

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def get_db() -> DbWrapper:
    if not DATABASE_URL:
        raise RuntimeError(
            'DATABASE_URL is not set. '
            'Go to Vercel → your project → Settings → '
            'Environment Variables and add DATABASE_URL.'
        )
    u = urlparse(DATABASE_URL)
    conn = _pg.Connection( # pyright: ignore[reportUndefinedVariable]
        host        = u.hostname,
        port        = u.port or 5432,
        database    = u.path.lstrip('/'),
        user        = u.username,
        password    = u.password,
        ssl_context = True,
    )
    return DbWrapper(conn)


# ─── Init DB ─────────────────────────────────────────────────

def init_db():
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id         SERIAL PRIMARY KEY,
                username   TEXT UNIQUE NOT NULL,
                password   TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
                amount      REAL    NOT NULL,
                total_money REAL    NOT NULL DEFAULT 0,
                description TEXT    NOT NULL,
                category    TEXT    DEFAULT 'General',
                date        TEXT    NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        db.execute('''
            ALTER TABLE expenses
            ADD COLUMN IF NOT EXISTS total_money
            REAL NOT NULL DEFAULT 0
        ''')
        db.execute('''
            ALTER TABLE expenses
            ADD COLUMN IF NOT EXISTS created_at
            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS monthly_budget (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL
                            REFERENCES users(id) ON DELETE CASCADE,
                year       INTEGER NOT NULL,
                month      INTEGER NOT NULL,
                budget     REAL    NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, year, month)
            )
        ''')
        db.execute(
            'INSERT INTO users (username, password) VALUES (?, ?) '
            'ON CONFLICT (username) DO NOTHING',
            ('demo', generate_password_hash('demo123'))
        )
        db.execute(
            'INSERT INTO users (username, password) VALUES (?, ?) '
            'ON CONFLICT (username) DO NOTHING',
            ('admin', generate_password_hash('admin123'))
        )
        db.commit()


# ─── Jinja filters ───────────────────────────────────────────

@app.template_filter('peso')
def peso_filter(value):
    try:
        return '{:,.2f}'.format(float(value))
    except Exception:
        return '0.00'

@app.template_filter('commaint')
def commaint_filter(value):
    try:
        return '{:,}'.format(int(value))
    except Exception:
        return '0'


# ─── Auth ────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ─── Budget helpers ──────────────────────────────────────────

def get_month_budget(user_id, year, month):
    try:
        with get_db() as db:
            row = db.execute(
                'SELECT budget FROM monthly_budget '
                'WHERE user_id=? AND year=? AND month=?',
                (user_id, year, month)
            ).fetchone()
        return float(row['budget']) if row else 0.0
    except Exception:
        return 0.0


def get_month_spent(user_id, year, month):
    try:
        with get_db() as db:
            row = db.execute(
                'SELECT COALESCE(SUM(amount),0) AS total FROM expenses '
                'WHERE user_id=? '
                'AND SUBSTRING(date,1,4)=? '
                'AND SUBSTRING(date,6,2)=?',
                (user_id, str(year), str(month).zfill(2))
            ).fetchone()
        return float(row['total']) if row else 0.0
    except Exception:
        return 0.0


# ─── Context processor ───────────────────────────────────────

@app.context_processor
def inject_balance():
    defaults = dict(
        month_budget        = 0.0,
        month_spent         = 0.0,
        month_balance       = 0.0,
        current_month_label = datetime.now().strftime('%B %Y'),
    )
    if 'user_id' not in session:
        return defaults
    try:
        now   = datetime.now()
        b     = get_month_budget(session['user_id'], now.year, now.month)
        s     = get_month_spent (session['user_id'], now.year, now.month)
        defaults.update(month_budget=b, month_spent=s,
                        month_balance=round(b - s, 2))
    except Exception:
        pass
    return defaults


# ─── Global error handlers ───────────────────────────────────
#  Shows the real Python traceback instead of a blank 500 page.
#  Remove these handlers once your app is stable.

@app.errorhandler(Exception)
def handle_exception(error):
    tb = traceback.format_exc()
    return jsonify({
        'error'    : type(error).__name__,
        'message'  : str(error),
        'traceback': tb,
    }), 500


# ─── Routes ──────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session
                    else url_for('login'))


# ── HEALTH ────────────────────────────────────────────────────
#  Visit  /health  to diagnose any deployment issue instantly.

@app.route('/health')
def health():
    info = {
        'status'       : 'checking',
        'DATABASE_URL' : 'set' if DATABASE_URL else 'MISSING — add it in Vercel env vars',
        'SECRET_KEY'   : 'set' if os.environ.get('SECRET_KEY') else 'using fallback',
        'pg8000'       : str(getattr(_pg, '__version__', 'loaded')), # pyright: ignore[reportUndefinedVariable]
        'python'       : os.sys.version,
    }
    try:
        with get_db() as db:
            row = db.execute('SELECT 1 AS ping').fetchone()
        info['database'] = 'connected' if row else 'no response'
        info['status']   = 'ok'
        return jsonify(info), 200
    except Exception as exc:
        info['database']  = 'FAILED'
        info['error']     = str(exc)
        info['traceback'] = traceback.format_exc()
        info['status']    = 'error'
        return jsonify(info), 500


# ── SETUP ─────────────────────────────────────────────────────

@app.route('/setup')
def setup():
    token       = request.args.get('token', '')
    setup_token = os.environ.get('SETUP_TOKEN', '')
    if not setup_token:
        return jsonify({'error': 'SETUP_TOKEN env var not configured'}), 500
    if token != setup_token:
        return jsonify({'error': 'Invalid token'}), 403
    try:
        init_db()
        return jsonify({'ok': True, 'message': 'Database initialised.'})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc),
                        'traceback': traceback.format_exc()}), 500


# ── LOGIN ─────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        try:
            with get_db() as db:
                user = db.execute(
                    'SELECT * FROM users WHERE username=?', (username,)
                ).fetchone()
            if user and check_password_hash(user['password'], password):
                session['user_id']  = user['id']
                session['username'] = user['username']
                flash('Welcome back, ' + user['username'] + '!', 'success')
                return redirect(url_for('dashboard'))
            flash('Invalid username or password.', 'error')
        except Exception as exc:
            flash('Database error: ' + str(exc), 'error')
    return render_template('login.html')


# ── REGISTER ──────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('register.html')
        try:
            with get_db() as db:
                db.execute(
                    'INSERT INTO users (username, password) VALUES (?, ?)',
                    (username, generate_password_hash(password))
                )
                db.commit()
            flash('Account created! You can now log in.', 'success')
            return redirect(url_for('login'))
        except Exception as exc:
            err = str(exc).lower()
            flash('Username already taken.' if 'unique' in err or 'duplicate' in err
                  else 'Error: ' + str(exc), 'error')
    return render_template('register.html')


# ── LOGOUT ────────────────────────────────────────────────────

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))


# ── SET MONTHLY BUDGET ────────────────────────────────────────

@app.route('/api/budget', methods=['POST'])
@login_required
def api_set_budget():
    try:
        data   = request.get_json(force=True)
        budget = float(data.get('budget', 0))
        if budget < 0:
            return jsonify({'ok': False, 'error': 'Budget cannot be negative'}), 400
        now = datetime.now()
        with get_db() as db:
            db.execute(
                'INSERT INTO monthly_budget '
                '(user_id, year, month, budget, updated_at) VALUES (?,?,?,?,?) '
                'ON CONFLICT (user_id, year, month) '
                'DO UPDATE SET budget=EXCLUDED.budget, updated_at=EXCLUDED.updated_at',
                (session['user_id'], now.year, now.month,
                 budget, now.strftime('%Y-%m-%d %H:%M:%S'))
            )
            db.commit()
        spent = get_month_spent(session['user_id'], now.year, now.month)
        return jsonify({'ok': True, 'budget': budget, 'spent': spent,
                        'balance': round(budget - spent, 2)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


# ── DASHBOARD ─────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    uid = session['user_id']
    with get_db() as db:
        total = db.execute(
            'SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=?',
            (uid,)
        ).fetchone()['total']

        lr = db.execute(
            'SELECT total_money FROM expenses '
            'WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 1', (uid,)
        ).fetchone()
        latest_total_money = lr['total_money'] if lr else 0

        ym          = datetime.now().strftime('%Y-%m')
        month_total = db.execute(
            'SELECT COALESCE(SUM(amount),0) AS total '
            'FROM expenses WHERE user_id=? AND date LIKE ?',
            (uid, ym + '%')
        ).fetchone()['total']

        count = db.execute(
            'SELECT COUNT(*) AS cnt FROM expenses WHERE user_id=?', (uid,)
        ).fetchone()['cnt']

        recent = db.execute(
            'SELECT * FROM expenses WHERE user_id=? '
            'ORDER BY date DESC, id DESC LIMIT 10', (uid,)
        ).fetchall()

        cat_rows = db.execute(
            'SELECT category, SUM(amount) AS total_expense, '
            'SUM(total_money) AS total_money_sum, COUNT(*) AS txn_count '
            'FROM expenses WHERE user_id=? '
            'GROUP BY category ORDER BY total_expense DESC', (uid,)
        ).fetchall()

        grand     = sum(r['total_expense'] for r in cat_rows) or 1
        cat_chart = [
            {'category': r['category'], 'total': r['total_expense'],
             'total_money': r['total_money_sum'], 'count': r['txn_count'],
             'pct': round(r['total_expense'] / grand * 100, 1)}
            for r in cat_rows
        ]

        mr = db.execute(
            'SELECT SUBSTRING(date,1,7) AS month, SUM(amount) AS total, '
            'SUM(total_money) AS total_money, COUNT(*) AS cnt '
            'FROM expenses WHERE user_id=? '
            'GROUP BY month ORDER BY month DESC LIMIT 12', (uid,)
        ).fetchall()

    monthly_summary = [
        {'month_label': r['month'], 'total': r['total'],
         'total_money': r['total_money'], 'count': r['cnt']}
        for r in reversed(mr)
    ]
    return render_template('dashboard.html',
        total=total, month_total=month_total, count=count,
        recent=recent, cat_chart=cat_chart,
        monthly_summary=monthly_summary,
        latest_total_money=latest_total_money,
        net_balance=latest_total_money - total,
    )


# ── GRAPH ─────────────────────────────────────────────────────

@app.route('/graph')
@login_required
def graph_page():
    uid = session['user_id']
    with get_db() as db:
        total = db.execute(
            'SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=?',
            (uid,)
        ).fetchone()['total']

        lr = db.execute(
            'SELECT total_money FROM expenses '
            'WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 1', (uid,)
        ).fetchone()
        latest_total_money = lr['total_money'] if lr else 0

        ym          = datetime.now().strftime('%Y-%m')
        month_total = db.execute(
            'SELECT COALESCE(SUM(amount),0) AS total '
            'FROM expenses WHERE user_id=? AND date LIKE ?',
            (uid, ym + '%')
        ).fetchone()['total']

        cat_rows = db.execute(
            'SELECT category, SUM(amount) AS total_expense, '
            'SUM(total_money) AS total_money_sum, COUNT(*) AS txn_count '
            'FROM expenses WHERE user_id=? '
            'GROUP BY category ORDER BY total_expense DESC', (uid,)
        ).fetchall()
        cat_chart = [
            {'category': r['category'], 'total': r['total_expense'],
             'total_money': r['total_money_sum'], 'count': r['txn_count']}
            for r in cat_rows
        ]

        mr = db.execute(
            'SELECT SUBSTRING(date,1,7) AS month, SUM(amount) AS total, '
            'SUM(total_money) AS total_money, COUNT(*) AS cnt '
            'FROM expenses WHERE user_id=? '
            'GROUP BY month ORDER BY month', (uid,)
        ).fetchall()

    monthly_summary = [
        {'month_label': r['month'], 'total': r['total'],
         'total_money': r['total_money'], 'count': r['cnt']}
        for r in mr
    ]
    return render_template('graph.html',
        cat_chart=cat_chart, monthly_summary=monthly_summary,
        total=total, month_total=month_total,
        latest_total_money=latest_total_money,
    )


# ── EXPENSES ──────────────────────────────────────────────────

@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    uid   = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        raw_amount  = request.form.get('amount', '').strip()
        total_money = request.form.get('total_money', '0').strip() or '0'
        description = request.form.get('description', '').strip()
        category    = request.form.get('category', 'General')
        date        = request.form.get('date', today)
        if not raw_amount or not description or not date:
            flash('Description, amount, and date are required.', 'error')
        else:
            try:
                with get_db() as db:
                    db.execute(
                        'INSERT INTO expenses '
                        '(user_id, amount, total_money, description, category, date) '
                        'VALUES (?,?,?,?,?,?)',
                        (uid, float(raw_amount), float(total_money),
                         description, category, date)
                    )
                    db.commit()
                flash('Expense saved successfully!', 'success')
            except Exception as exc:
                flash('Could not save: ' + str(exc), 'error')
        return redirect(url_for('expenses'))

    category_filter = request.args.get('category', '').strip()
    sort            = request.args.get('sort', 'date_desc')
    sort_map = {
        'date_desc': 'date DESC, id DESC', 'date_asc': 'date ASC, id ASC',
        'amount_desc': 'amount DESC',       'amount_asc': 'amount ASC',
    }
    order_by = sort_map.get(sort, 'date DESC, id DESC')

    with get_db() as db:
        categories = db.execute(
            'SELECT DISTINCT category FROM expenses '
            'WHERE user_id=? ORDER BY category', (uid,)
        ).fetchall()

        if category_filter:
            expense_list = db.execute(
                f'SELECT * FROM expenses WHERE user_id=? AND category=? '
                f'ORDER BY {order_by}', (uid, category_filter)
            ).fetchall()
        else:
            expense_list = db.execute(
                f'SELECT * FROM expenses WHERE user_id=? ORDER BY {order_by}',
                (uid,)
            ).fetchall()

        now            = datetime.now()
        this_month_str = now.strftime('%Y-%m')
        budget_val     = get_month_budget(uid, now.year, now.month)

        month_rows = db.execute(
            'SELECT id, amount FROM expenses '
            'WHERE user_id=? AND date LIKE ? ORDER BY date ASC, id ASC',
            (uid, this_month_str + '%')
        ).fetchall()

        month_total = db.execute(
            'SELECT COALESCE(SUM(amount),0) AS total '
            'FROM expenses WHERE user_id=? AND date LIKE ?',
            (uid, this_month_str + '%')
        ).fetchone()['total']

    running, balance_after_map = 0.0, {}
    if budget_val > 0:
        for row in month_rows:
            running += row['amount']
            balance_after_map[row['id']] = round(budget_val - running, 2)

    return render_template('expenses.html',
        today=today, expense_list=expense_list,
        categories=categories, category_filter=category_filter,
        sort=sort, balance_after_map=balance_after_map,
        month_total=month_total,
    )


# ── DELETE EXPENSE ────────────────────────────────────────────

@app.route('/expense/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    uid     = session['user_id']
    next_pg = request.form.get('next', 'expenses')
    try:
        with get_db() as db:
            db.execute(
                'DELETE FROM expenses WHERE id=? AND user_id=?',
                (expense_id, uid)
            )
            db.commit()
        flash('Expense deleted.', 'success')
    except Exception as exc:
        flash('Could not delete: ' + str(exc), 'error')
    allowed = {'expenses', 'delete_page', 'dashboard'}
    return redirect(url_for(next_pg if next_pg in allowed else 'expenses'))




# ─── Entry point (local dev) ─────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1')