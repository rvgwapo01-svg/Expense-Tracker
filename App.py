from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = 'expense_tracker_secret_2024'

DB_PATH = 'expense_tracker.db'

# ─── Database ─────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            total_money REAL NOT NULL DEFAULT 0,
            description TEXT NOT NULL,
            category TEXT DEFAULT 'General',
            date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # Safe migration: add total_money column to existing databases
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(expenses)").fetchall()]
    if 'total_money' not in existing_cols:
        c.execute("ALTER TABLE expenses ADD COLUMN total_money REAL NOT NULL DEFAULT 0")
    if 'created_at' not in existing_cols:
        c.execute("ALTER TABLE expenses ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    # Monthly budget table — stores each user's self-set budget per calendar month
    c.execute('''
        CREATE TABLE IF NOT EXISTS monthly_budget (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            year       INTEGER NOT NULL,
            month      INTEGER NOT NULL,
            budget     REAL    NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, year, month)
        )
    ''')

    try:
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('demo', 'demo123'))
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)", ('admin', 'admin123'))
    except sqlite3.IntegrityError:
        pass

    conn.commit()
    conn.close()

# ─── Filters ─────────────────────────────────────────

@app.template_filter('peso')
def peso_filter(value):
    try:
        return '{:,.2f}'.format(float(value))
    except:
        return '0.00'

@app.template_filter('commaint')
def commaint_filter(value):
    try:
        return '{:,}'.format(int(value))
    except:
        return '0'

# ─── Auth ─────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ─── Budget helpers ───────────────────────────────────

def get_month_budget(user_id, year, month):
    """Return the user's set budget for a given month, or 0 if not set."""
    conn = get_db()
    row  = conn.execute(
        'SELECT budget FROM monthly_budget WHERE user_id=? AND year=? AND month=?',
        (user_id, year, month)
    ).fetchone()
    conn.close()
    return float(row['budget']) if row else 0.0

def get_month_spent(user_id, year, month):
    """Return total expenses recorded for a given month."""
    conn = get_db()
    row  = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses "
        "WHERE user_id=? AND strftime('%Y', date)=? AND strftime('%m', date)=?",
        (user_id, str(year), str(month).zfill(2))
    ).fetchone()
    conn.close()
    return float(row['total']) if row else 0.0

# ─── Context processor — runs for every request ───────
# Injects month_budget, month_spent, month_balance, current_month_label
# into ALL templates automatically. No base_data blocks needed.

@app.context_processor
def inject_balance():
    if 'user_id' not in session:
        return dict(month_budget=0.0, month_spent=0.0,
                    month_balance=0.0, current_month_label='')
    now    = datetime.now()
    budget = get_month_budget(session['user_id'], now.year, now.month)
    spent  = get_month_spent(session['user_id'], now.year, now.month)
    return dict(
        month_budget       = budget,
        month_spent        = spent,
        month_balance      = round(budget - spent, 2),
        current_month_label= now.strftime('%B %Y'),
    )

# ─── Routes ─────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

# ── LOGIN ──────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        conn = get_db()
        user = conn.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Welcome back, ' + user['username'] + '!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password.', 'error')

    return render_template('login.html')

# ── REGISTER ───────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('Username and password are required.', 'error')
            return render_template('register.html')
        conn = get_db()
        try:
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            flash('Account created! You can now log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already taken.', 'error')
        finally:
            conn.close()
    return render_template('register.html')

# ── LOGOUT ─────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# ── SET MONTHLY BUDGET (JSON API) ─────────────────────
@app.route('/api/budget', methods=['POST'])
@login_required
def api_set_budget():
    try:
        data   = request.get_json(force=True)
        budget = float(data.get('budget', 0))
        if budget < 0:
            return jsonify({'ok': False, 'error': 'Budget cannot be negative'}), 400
        now = datetime.now()
        conn = get_db()
        conn.execute(
            '''INSERT INTO monthly_budget (user_id, year, month, budget, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, year, month)
               DO UPDATE SET budget=excluded.budget, updated_at=excluded.updated_at''',
            (session['user_id'], now.year, now.month,
             budget, now.strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()
        spent   = get_month_spent(session['user_id'], now.year, now.month)
        balance = round(budget - spent, 2)
        return jsonify({'ok': True, 'budget': budget,
                        'spent': spent, 'balance': balance})
    except (ValueError, TypeError) as e:
        return jsonify({'ok': False, 'error': str(e)}), 400

# ── DASHBOARD (unified — includes graphs) ──────────────
@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    uid  = session['user_id']

    # Core totals
    total = conn.execute(
        'SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=?', (uid,)
    ).fetchone()[0]

    # Latest total_money value = most recent balance recorded by the user
    latest_row = conn.execute(
        'SELECT total_money FROM expenses WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 1', (uid,)
    ).fetchone()
    latest_total_money = latest_row[0] if latest_row else 0

    this_month  = datetime.now().strftime('%Y-%m')
    month_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=? AND date LIKE ?",
        (uid, this_month + '%')
    ).fetchone()[0]

    count = conn.execute(
        'SELECT COUNT(*) FROM expenses WHERE user_id=?', (uid,)
    ).fetchone()[0]

    # Recent 10 for full-width table
    recent = conn.execute(
        'SELECT * FROM expenses WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 10', (uid,)
    ).fetchall()

    # Category breakdown (for merged graph section)
    category_data = conn.execute(
        '''SELECT category,
                  SUM(amount)      AS total_expense,
                  SUM(total_money) AS total_money_sum,
                  COUNT(*)         AS txn_count
           FROM expenses WHERE user_id=?
           GROUP BY category ORDER BY total_expense DESC''', (uid,)
    ).fetchall()

    grand = sum(r['total_expense'] for r in category_data) or 1
    cat_chart = [
        {
            "category":        r["category"],
            "total":           r["total_expense"],
            "total_money":     r["total_money_sum"],
            "count":           r["txn_count"],
            "pct":             round((r["total_expense"] / grand) * 100, 1),
        }
        for r in category_data
    ]

    # Monthly summary
    monthly_data = conn.execute(
        '''SELECT SUBSTR(date,1,7) AS month,
                  SUM(amount)       AS total,
                  SUM(total_money)  AS total_money,
                  COUNT(*)          AS cnt
           FROM expenses WHERE user_id=?
           GROUP BY month ORDER BY month DESC LIMIT 12''', (uid,)
    ).fetchall()

    monthly_summary = [
        {
            "month_label": r["month"],
            "total":       r["total"],
            "total_money": r["total_money"],
            "count":       r["cnt"],
        }
        for r in reversed(monthly_data)
    ]

    conn.close()

    net_balance = latest_total_money - total

    return render_template('dashboard.html',
        total=total,
        month_total=month_total,
        count=count,
        recent=recent,
        cat_chart=cat_chart,
        monthly_summary=monthly_summary,
        latest_total_money=latest_total_money,
        net_balance=net_balance,
    )

# ── GRAPH PAGE ─────────────────────────────────────────
@app.route('/graph')
@login_required
def graph_page():
    conn = get_db()
    uid  = session['user_id']

    total = conn.execute(
        'SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=?', (uid,)
    ).fetchone()[0]

    latest_row = conn.execute(
        'SELECT total_money FROM expenses WHERE user_id=? ORDER BY date DESC, id DESC LIMIT 1', (uid,)
    ).fetchone()
    latest_total_money = latest_row[0] if latest_row else 0

    this_month  = datetime.now().strftime('%Y-%m')
    month_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=? AND date LIKE ?",
        (uid, this_month + '%')
    ).fetchone()[0]

    category_data = conn.execute(
        '''SELECT category,
                  SUM(amount)      AS total_expense,
                  SUM(total_money) AS total_money_sum,
                  COUNT(*)         AS txn_count
           FROM expenses WHERE user_id=?
           GROUP BY category ORDER BY total_expense DESC''', (uid,)
    ).fetchall()

    cat_chart = [
        {
            "category":    r["category"],
            "total":       r["total_expense"],
            "total_money": r["total_money_sum"],
            "count":       r["txn_count"],
        }
        for r in category_data
    ]

    monthly_data = conn.execute(
        '''SELECT SUBSTR(date,1,7) AS month,
                  SUM(amount)       AS total,
                  SUM(total_money)  AS total_money,
                  COUNT(*)          AS cnt
           FROM expenses WHERE user_id=?
           GROUP BY month ORDER BY month''', (uid,)
    ).fetchall()

    monthly_summary = [
        {
            "month_label": r["month"],
            "total":       r["total"],
            "total_money": r["total_money"],
            "count":       r["cnt"],
        }
        for r in monthly_data
    ]

    conn.close()

    return render_template('graph.html',
        cat_chart=cat_chart,
        monthly_summary=monthly_summary,
        total=total,
        month_total=month_total,
        latest_total_money=latest_total_money,
    )

# ── EXPENSES ───────────────────────────────────────────
@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    uid   = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')

    if request.method == 'POST':
        amount      = request.form.get('amount', '').strip()
        total_money = request.form.get('total_money', '0').strip() or '0'
        description = request.form.get('description', '').strip()
        category    = request.form.get('category', 'General')
        date        = request.form.get('date', today)

        if not amount or not description or not date:
            flash('Description, amount, and date are required.', 'error')
        else:
            try:
                float(amount)
                float(total_money)
                conn = get_db()
                conn.execute(
                    '''INSERT INTO expenses (user_id, amount, total_money, description, category, date)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (uid, float(amount), float(total_money), description, category, date)
                )
                conn.commit()
                conn.close()
                flash('Expense saved successfully!', 'success')
            except ValueError:
                flash('Amount and Total Money must be valid numbers.', 'error')
        return redirect(url_for('expenses'))

    # Filter & sort
    category_filter = request.args.get('category', '').strip()
    sort            = request.args.get('sort', 'date_desc')

    sort_map = {
        'date_desc':   'date DESC, id DESC',
        'date_asc':    'date ASC,  id ASC',
        'amount_desc': 'amount DESC',
        'amount_asc':  'amount ASC',
    }
    order_by = sort_map.get(sort, 'date DESC, id DESC')

    conn = get_db()
    categories = conn.execute(
        'SELECT DISTINCT category FROM expenses WHERE user_id=? ORDER BY category', (uid,)
    ).fetchall()

    if category_filter:
        expense_list = conn.execute(
            f'SELECT * FROM expenses WHERE user_id=? AND category=? ORDER BY {order_by}',
            (uid, category_filter)
        ).fetchall()
    else:
        expense_list = conn.execute(
            f'SELECT * FROM expenses WHERE user_id=? ORDER BY {order_by}', (uid,)
        ).fetchall()

    # ── Running balance per expense (this month only, chronological) ──
    now            = datetime.now()
    this_month_str = now.strftime('%Y-%m')
    budget_val     = get_month_budget(uid, now.year, now.month)

    month_rows = conn.execute(
        "SELECT id, amount FROM expenses WHERE user_id=? AND date LIKE ? ORDER BY date ASC, id ASC",
        (uid, this_month_str + '%')
    ).fetchall()

    running           = 0.0
    balance_after_map = {}
    if budget_val > 0:                        # only meaningful when budget is set
        for row in month_rows:
            running += row['amount']
            balance_after_map[row['id']] = round(budget_val - running, 2)

    month_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE user_id=? AND date LIKE ?",
        (uid, this_month_str + '%')
    ).fetchone()[0]

    conn.close()

    return render_template('expenses.html',
        today             = today,
        expense_list      = expense_list,
        categories        = categories,
        category_filter   = category_filter,
        sort              = sort,
        balance_after_map = balance_after_map,
        month_total       = month_total,
    )

# ── DELETE EXPENSE ─────────────────────────────────────
@app.route('/expense/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    uid     = session['user_id']
    next_pg = request.form.get('next', 'expenses')
    conn = get_db()
    conn.execute('DELETE FROM expenses WHERE id=? AND user_id=?', (expense_id, uid))
    conn.commit()
    conn.close()
    flash('Expense deleted.', 'success')
    allowed = {'expenses', 'delete_page', 'dashboard'}
    return redirect(url_for(next_pg if next_pg in allowed else 'expenses'))

# ── DELETE PAGE ────────────────────────────────────────
@app.route('/delete-page')
@login_required
def delete_page():
    conn     = get_db()
    uid      = session['user_id']
    expenses = conn.execute(
        'SELECT * FROM expenses WHERE user_id=? ORDER BY date DESC', (uid,)
    ).fetchall()
    conn.close()
    return render_template('delete_expense.html', expenses=expenses)

# ─── RUN ─────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(debug=True)