import os
import threading
import json
from datetime import date, datetime
from functools import wraps
from typing import Optional, Dict, Any
import threading

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_from_directory, g, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

import mysql.connector
import requests

# ---------------------------
# Config (change via env vars)
# ---------------------------
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "")
DB_NAME = os.getenv("DB_NAME", "gayathrischool")

MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY", "b1f7c05f3f46afaf86137924e7adb4cb959c04ee")
MSG91_SENDER_ID = os.getenv("MSG91_SENDER_ID", "GVSCHL")
MSG91_ROUTE = os.getenv("MSG91_ROUTE", "4")
MSG91_COUNTRY = os.getenv("MSG91_COUNTRY", "91")

LOGO_PATH = os.getenv("LOGO_PATH", "static/logo/favicon.png")

import secrets
print(secrets.token_hex(32))
SECRET_KEY = os.getenv("FLASK_SECRET", "dev-secret-change-me-please-change")

HOMEWORK_UPLOAD_DIR = "static/homework_files"

# Ensure it's a directory
if os.path.exists(HOMEWORK_UPLOAD_DIR):
    if not os.path.isdir(HOMEWORK_UPLOAD_DIR):
        # Remove the file and create directory
        os.remove(HOMEWORK_UPLOAD_DIR)
        os.makedirs(HOMEWORK_UPLOAD_DIR)
else:
    os.makedirs(HOMEWORK_UPLOAD_DIR)

TIMETABLE_UPLOAD_DIR = "static/uploads/timetable"

# ---------------------------
# App init
# ---------------------------
app = Flask(__name__)
app.secret_key = SECRET_KEY

@app.context_processor
def inject_now():
    return {"current_year": datetime.now().year}

# ---------------------------
# DB helpers (flask.g)
# ---------------------------
def get_db():
    """Return per-request mysql.connector connection stored in flask.g"""
    if "db" not in g:
        g.db = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=False,
            auth_plugin="mysql_native_password"
        )
    return g.db

def get_db_connection():
    return get_db()

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass

# ---------------------------
# Provider helpers (MSG91 SMS)
# ---------------------------
def send_msg91_sms_sync(to_number: str, message: str) -> Dict[str, Any]:
    if not MSG91_AUTH_KEY:
        return {"ok": False, "error": "MSG91_AUTH_KEY not configured"}
    n = to_number.strip()
    if n.startswith('+'):
        n = n[1:]
    url = "https://control.msg91.com/api/sendhttp.php"
    params = {
        "authkey": MSG91_AUTH_KEY,
        "mobiles": n,
        "message": message,
        "sender": MSG91_SENDER_ID,
        "route": MSG91_ROUTE,
        "country": MSG91_COUNTRY
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        return {"ok": r.status_code == 200, "status_code": r.status_code, "response": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def send_async_message(to: str, body: str, channel: str = "sms", extra: Optional[dict] = None):
    def job():
        res = {"ok": False, "error": "unsupported"}
        try:
            if channel == "sms":
                res = send_msg91_sms_sync(to, body)
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        # best-effort log to sms_logs
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO sms_logs (to_number, channel, purpose, content, status, provider_message_sid, error)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (to, channel, (extra or {}).get("purpose", "ad-hoc"), body,
                  ("sent" if res.get("ok") else "failed"),
                  json.dumps(res.get("response")) if res.get("ok") else None,
                  res.get("error")))
            conn.commit()
            cur.close()
        except Exception:
            pass
    threading.Thread(target=job, daemon=True).start()
    return {"ok": True, "queued": True}

# ---------------------------
# DB creation + Safe migrations
# ---------------------------
def ensure_default_accounts_and_schema():
    """Create DB and minimal tables if missing."""
    try:
        cn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, auth_plugin="mysql_native_password")
        c = cn.cursor()
        c.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` DEFAULT CHARACTER SET utf8mb4")
        cn.commit()
        c.close()
        cn.close()
    except Exception as e:
        print("DB create warning:", e)

    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME, auth_plugin="mysql_native_password")
        cur = conn.cursor()
        # create tables (idempotent)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) UNIQUE,
                password VARCHAR(255),
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(32),
                username VARCHAR(100) UNIQUE,
                password VARCHAR(255),
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS classes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(64),
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255),
                roll_no VARCHAR(64),
                username VARCHAR(100),
                password VARCHAR(255),
                email VARCHAR(255),
                class_id INT,
                father_name VARCHAR(255),
                mother_name VARCHAR(255),
                dob DATE,
                parent_contact VARCHAR(32),
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS homework (
                id INT AUTO_INCREMENT PRIMARY KEY,
                class_id INT,
                teacher_id INT,
                title VARCHAR(255),
                description TEXT,
                due_date DATE,
                file_path VARCHAR(512),
                posted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT,
                class_id INT,
                date DATE,
                status ENUM('Present','Absent'),
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_attendance (student_id,date)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                class_id INT,
                teacher_id INT,
                message TEXT,
                status ENUM('Pending','Sent') DEFAULT 'Pending',
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sms_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                to_number VARCHAR(32),
                channel ENUM('sms','whatsapp'),
                purpose VARCHAR(50),
                class_id INT NULL,
                student_id INT NULL,
                teacher_id INT NULL,
                content TEXT,
                status ENUM('queued','sent','failed') DEFAULT 'queued',
                provider_message_sid VARCHAR(256),
                error TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS timetable (
                id INT AUTO_INCREMENT PRIMARY KEY,
                class_id INT,
                file_name VARCHAR(255),
                uploaded_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fees (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_id INT,
                total_amount DECIMAL(10,2) DEFAULT 0,
                paid_amount DECIMAL(10,2) DEFAULT 0,
                balance DECIMAL(10,2) DEFAULT 0,
                last_payment_date DATE,
                created_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        # populate classes if empty
        cur.execute("SELECT COUNT(*) FROM classes")
        if cur.fetchone()[0] == 0:
            default_classes = ['PreKG','LKG','UKG','1st','2nd','3rd','4th','5th','6th','7th','8th','9th','10th']
            for c_name in default_classes:
                cur.execute("INSERT INTO classes (name) VALUES (%s)", (c_name,))
            conn.commit()

        # default admin & teacher
        cur.execute("SELECT id FROM admins WHERE username='admin' LIMIT 1")
        if not cur.fetchone():
            cur.execute("INSERT INTO admins (username, password) VALUES (%s,%s)", ('admin', generate_password_hash('admin123')))
            conn.commit()

        cur.execute("SELECT id FROM teachers WHERE username='teacher' LIMIT 1")
        if not cur.fetchone():
            cur.execute("INSERT INTO teachers (name,email,phone,username,password) VALUES (%s,%s,%s,%s,%s)",
                        ('Class Teacher','teacher@gayathrischool.com','9876543210','teacher', generate_password_hash('teacher123')))
            conn.commit()

        cur.close(); conn.close()
    except Exception as e:
        print("ensure_default_accounts_and_schema error:", e)

def ensure_table_columns():
    """Add commonly-missing columns (non-destructive)."""
    needed = {
        "students": {
            "email": "VARCHAR(255)",
            "parent_contact": "VARCHAR(32)"
        },
        "homework": {
            "file_path": "VARCHAR(512)"
        },
        "teachers": {
            "email": "VARCHAR(255)",
            "phone": "VARCHAR(32)"
        },
        "sms_logs": {
            "provider_message_sid": "VARCHAR(256)",
            "error": "TEXT"
        }
    }
    try:
        conn = mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME, auth_plugin="mysql_native_password")
        cur = conn.cursor()
        for table, cols in needed.items():
            for col, coltype in cols.items():
                cur.execute("""
                    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s
                """, (DB_NAME, table, col))
                if cur.fetchone()[0] == 0:
                    try:
                        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {coltype} NULL")
                        conn.commit()
                        print(f"Added column {table}.{col}")
                    except Exception as e:
                        print(f"Could not add column {table}.{col}: {e}")
        cur.close(); conn.close()
    except Exception as e:
        print("ensure_table_columns error:", e)

# run DB ensures
ensure_default_accounts_and_schema()
ensure_table_columns()

# ---------------------------
# Role decorator
# ---------------------------
def require_role(role):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                if role == "admin":
                    return redirect(url_for("login"))
                if role == "teacher":
                    return redirect(url_for("teacher_login"))
                return redirect(url_for("home"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# ---- Fee Reminder Functions ----
def send_fee_due_reminders():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT s.name, s.parent_contact, f.balance
        FROM students s
        JOIN fees f ON s.id = f.student_id
        WHERE f.balance > 0
    """)
    due_students = cur.fetchall()

    for st in due_students:
        if not st["parent_contact"]: 
            continue
        
        msg = f"Reminder: Pending school fee for {st['name']}. Please pay balance ₹{st['balance']}."
        num = "+91" + st["parent_contact"] if len(st["parent_contact"]) == 10 else st["parent_contact"]

        send_async_message(num, msg, channel="sms", extra={"purpose": "fee_due"})

    print("✅ Fee reminder SMS sent to pending parents.")


def fee_scheduler():
    import time
    while True:
        try:
            send_fee_due_reminders()
        except Exception as e:
            print("Fee scheduler error:", e)
        time.sleep(86400)  # 24 hours
threading.Thread(target=fee_scheduler, daemon=True).start()

@app.context_processor
def inject_now():
    return {"current_year": datetime.now().year}

# (Your DB helper functions and ensure_default_accounts_and_schema,
# ensure_table_columns, send_async_message, fee scheduler, etc. remain the same.)
# For brevity I will not re-paste the whole original code here — keep your
# existing logic as-is. Below are the key PWA-specific routes and small additions.

# PWA: ensure `manifest.json` and `sw.js` are accessible from static/ (they are)
# Add a route to serve the manifest if you need (optional) - static will serve it.

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def service_worker():
    # Service worker must be served from site root (no cache headers that prevent update)
    response = send_from_directory("static", "sw.js")
    # Ensure correct content-type: JS
    response.headers["Content-Type"] = "application/javascript"
    return response

# ---------------------------
# Public routes
# ---------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/logo")
def logo():
    if os.path.exists(LOGO_PATH):
        return send_from_directory(os.path.dirname(LOGO_PATH), os.path.basename(LOGO_PATH))
    fallback = "static/logo/favicon.png"
    if os.path.exists(fallback):
        return send_from_directory("static/logo", "favicon.png")
    return "", 404

# ---------------------------
# Auth - Admin
# ---------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("SELECT id, password FROM admins WHERE username=%s", (username,))
            row = cur.fetchone()
            cur.close()
            if row:
                admin_id, pwd_hash = row
                if pwd_hash and check_password_hash(pwd_hash, password):
                    session.clear()
                    session["role"] = "admin"
                    session["admin_id"] = admin_id
                    session["admin_user"] = username
                    return redirect(url_for("admin_dashboard"))
        except Exception as e:
            print("login error:", e)
        flash("Invalid credentials", "danger")
    return render_template("login.html", role="Admin")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("home"))

# ---------------------------
# Auth - Teacher
# ---------------------------
from werkzeug.security import check_password_hash

@app.route("/teacher/login", methods=["GET", "POST"])
def teacher_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)

            cur.execute("SELECT id, password, name FROM teachers WHERE username=%s", (username,))
            row = cur.fetchone()

            cur.close()
            conn.close()

            if row and check_password_hash(row["password"], password):
                session.clear()
                session["role"] = "teacher"
                session["teacher_id"] = row["id"]
                session["teacher_name"] = row["name"]
                return redirect(url_for("teacher_home"))

        except Exception as e:
            print("teacher_login error:", e)

        flash("Invalid credentials", "danger")

    return render_template("teacher_login.html")

@app.route("/teacher/home")
@require_role("teacher")
def teacher_home():
    return render_template("teacher_home.html")

# ---------------------------
# Admin dashboard + students
# ---------------------------
@app.route("/admin/dashboard")
@require_role("admin")
def admin_dashboard():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS total_students FROM students")
        total_students = cur.fetchone()["total_students"] or 0

        cur.execute("SELECT COUNT(*) AS attendance_today FROM attendance WHERE date = CURDATE() AND status='Present'")
        attendance_today = cur.fetchone()["attendance_today"] or 0

        cur.execute("SELECT COUNT(*) AS homework_count FROM homework")
        recent_homework = cur.fetchone()["homework_count"] or 0

        cur.execute("SELECT COUNT(*) AS pending_fees FROM fees WHERE balance > 0")
        pending_fees_count = cur.fetchone()["pending_fees"] or 0


        cur.execute("""
            SELECT s.id, s.name, c.name as class_name, s.parent_contact
            FROM students s LEFT JOIN classes c ON s.class_id=c.id
            ORDER BY s.id DESC LIMIT 5
        """)
        latest_students = cur.fetchall()
    except Exception as e:
        print("admin_dashboard error:", e)
        total_students = attendance_today = recent_homework = 0
        latest_students = []
    finally:
        cur.close()
    return render_template("admin_dashboard.html",
                       total_students=total_students,
                       total_attendance_today=attendance_today,
                       recent_homework=recent_homework,
                       latest_students=latest_students,
                       pending_fees_count=pending_fees_count)

# Admin: students CRUD
@app.route("/admin/students", methods=["GET", "POST"])
@require_role("admin")
def admin_students():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    if request.method == "POST":
        name = request.form.get("name")
        class_id = request.form.get("class_id") or None
        father = request.form.get("father") or None
        mother = request.form.get("mother") or None
        dob = request.form.get("dob") or None
        parent_contact = request.form.get("parent_contact") or None
        email = request.form.get("email") or None
        username = (name.split()[0].lower() if name else "student") + datetime.now().strftime("%d%m%y%H%M%S")
        pwd_hash = generate_password_hash("student123")
        cur.execute("""
            INSERT INTO students (name, roll_no, username, password, email, class_id, father_name, mother_name, dob, parent_contact)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (name, 0, username, pwd_hash, email, class_id, father, mother, dob, parent_contact))
        sid = cur.lastrowid
        try:
            cur.execute("INSERT INTO fees (student_id, total_amount, paid_amount, balance) VALUES (%s,0,0,0)", (sid,))
        except Exception:
            pass
        conn.commit()
        flash("Student added (default password: student123).", "success")
        return redirect(url_for("admin_students"))

    class_id = request.args.get("class_id")
    if class_id:
        cur.execute("SELECT s.*, c.name as class_name FROM students s LEFT JOIN classes c ON s.class_id=c.id WHERE s.class_id=%s ORDER BY s.id DESC", (class_id,))
    else:
        cur.execute("SELECT s.*, c.name as class_name FROM students s LEFT JOIN classes c ON s.class_id=c.id ORDER BY s.id DESC")
    students = cur.fetchall()
    cur.execute("SELECT * FROM classes ORDER BY id")
    classes = cur.fetchall()
    cur.close()
    return render_template("admin_students.html", students=students, classes=classes, class_id=class_id)

@app.route("/admin/student/delete/<int:id>", methods=["POST"])
@require_role("admin")
def admin_student_delete(id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE id=%s", (id,))
    conn.commit(); cur.close()
    flash("Student deleted.", "info")
    return redirect(url_for("admin_students"))

# Admin memos (create, view, optionally send)
@app.route("/admin/memos", methods=["GET", "POST"])
@require_role("admin")
def admin_memos():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # POST => create memo (and optionally send)
    if request.method == "POST":
        class_id = request.form.get("class_id") or None  # None means All
        message = request.form.get("message", "").strip()
        send_now = True if request.form.get("send_now") == "on" else False

        if not message:
            flash("Announcement message cannot be empty.", "warning")
            cur.close(); return redirect(url_for("admin_memos"))

        try:
            cur.execute("INSERT INTO memos (class_id, teacher_id, message, status) VALUES (%s,%s,%s,%s)",
                        (class_id, None, message, ("Sent" if send_now else "Pending")))
            conn.commit()
            # If send_now then queue SMS to parents
            if send_now:
                # fetch recipients: either class parents or all parents
                if class_id:
                    cur.execute("SELECT parent_contact FROM students WHERE class_id=%s", (class_id,))
                else:
                    cur.execute("SELECT parent_contact FROM students")
                parents = cur.fetchall()
                # message text (prefix)
                sms_text = f"Gayathri Vidyalaya Announcement: {message}"
                for p in parents:
                    pnum = p.get("parent_contact") if isinstance(p, dict) else p[0]
                    if not pnum: 
                        continue
                    to_num = ("+91" + pnum) if len(pnum) == 10 and not pnum.startswith("+") else pnum
                    send_async_message(to_num, sms_text, channel="sms", extra={"purpose":"announcement", "class_id": class_id})
            flash("Announcement saved." + (" Sent to parents." if send_now else ""), "success")
        except Exception as e:
            print("admin_memos POST error:", e)
            flash("Could not save announcement.", "danger")
        cur.close(); return redirect(url_for("admin_memos"))

    # GET => list memos and classes for the form
    try:
        cur.execute("""SELECT m.*, c.name AS class_name
                       FROM memos m
                       LEFT JOIN classes c ON c.id = m.class_id
                       ORDER BY m.created_on DESC""")
        memos = cur.fetchall()
    except Exception as e:
        print("admin_memos fetch error:", e)
        memos = []

    # classes for the dropdown (include an "All" option)
    try:
        cur.execute("SELECT id, name FROM classes ORDER BY id")
        classes = cur.fetchall()
    except Exception:
        classes = []

    cur.close()
    return render_template("admin_memos.html", memos=memos, classes=classes)

@app.route("/admin/memos/delete/<int:memo_id>", methods=["POST"])
@require_role("admin")
def delete_memo(memo_id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM memos WHERE id=%s", (memo_id,))
    conn.commit(); cur.close()
    flash("Announcement deleted.", "info")
    return redirect(url_for("admin_memos"))

@app.route("/admin/fees", methods=["GET", "POST"])
@require_role("admin")
def admin_fees():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # Update fee for student
    if request.method == "POST":
        student_id = request.form.get("student_id")
        total = float(request.form.get("total") or 0)
        paid = float(request.form.get("paid") or 0)

        # Calculate balance
        balance = total - paid

        # Update fees table
        cur.execute("""
            UPDATE fees SET total_amount=%s, paid_amount=%s, balance=%s, last_payment_date=CURDATE()
            WHERE student_id=%s
        """, (total, paid, balance, student_id))
        conn.commit()

        flash("✅ Fee updated!", "success")
        return redirect(url_for("admin_fees"))

    # Fetch student + fee data
    cur.execute("""
        SELECT s.id, s.name, c.name as class_name,
               f.total_amount, f.paid_amount, f.balance
        FROM students s
        LEFT JOIN fees f ON s.id = f.student_id
        LEFT JOIN classes c ON s.class_id = c.id
        ORDER BY c.id, s.name
    """)
    fee_data = cur.fetchall()
    cur.close()

    return render_template("admin_fees.html", students=fee_data)

# ---------------------------
# Homework (Admin + Teacher)
# ---------------------------
@app.route("/admin/homework", methods=["GET", "POST"])
@require_role("admin")
def admin_homework():
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            class_id = request.form.get("class_id")
            title = request.form.get("title") or ""
            description = request.form.get("description") or ""
            due_date = request.form.get("due_date") or None

            # Handle file upload
            file_path = None
            f = request.files.get("file")
            if f and f.filename:
                filename = secure_filename(f.filename)
                file_path = os.path.join(HOMEWORK_UPLOAD_DIR, filename)
                f.save(file_path)

            cur.execute(
                """
                INSERT INTO homework (class_id, teacher_id, title, description, due_date, file_path)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (class_id, None, title, description, due_date, file_path)
            )
            conn.commit()
            flash("Homework posted successfully.", "success")
            return redirect(url_for("admin_homework"))

        # GET: fetch homework & classes
        cur.execute("""
            SELECT h.*, c.name AS class_name
            FROM homework h
            LEFT JOIN classes c ON h.class_id = c.id
            ORDER BY h.posted_on DESC
        """)
        homework_list = cur.fetchall()

        cur.execute("SELECT * FROM classes ORDER BY id")
        classes = cur.fetchall()

    except Exception as e:
        print("Admin homework error:", e)
        flash("Unable to load or post homework.", "danger")
        homework_list = classes = []

    finally:
        if cur: cur.close()
        if conn: conn.close()

    return render_template("admin_homework.html", homework_list=homework_list, classes=classes)

@app.route("/admin/homework/delete/<int:hw_id>", methods=["POST"])
def delete_homework(hw_id):
    conn = get_db_connection()
    try:
        with conn.cursor(dictionary=True) as cur:
            # Find file path
            cur.execute("SELECT file_path FROM homework WHERE id=%s", (hw_id,))
            hw = cur.fetchone()

            if hw:
                # Remove file from filesystem
                if hw["file_path"] and os.path.exists(hw["file_path"]):
                    try:
                        os.remove(hw["file_path"])
                    except Exception as e:
                        print(f"Failed to delete file {hw['file_path']}: {e}")

                # Delete DB row
                cur.execute("DELETE FROM homework WHERE id=%s", (hw_id,))
                conn.commit()
                flash("Homework deleted successfully.", "success")
            else:
                flash("Homework not found.", "danger")
    finally:
        conn.close()

    # Redirect to the admin homework listing page
    return redirect(url_for("admin_homework"))  # <-- ensure this matches your route name


@app.route("/teacher/homework", methods=["GET", "POST"])
@require_role("teacher")
def teacher_homework():
    conn = cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            class_id = request.form.get("class_id")
            title = request.form.get("title") or ""
            description = request.form.get("description") or ""
            due_date = request.form.get("due_date") or None

            # Handle optional file upload
            file_path = None
            f = request.files.get("file")
            if f and f.filename:
                filename = secure_filename(f.filename)
                file_path = os.path.join(HOMEWORK_UPLOAD_DIR, filename)
                f.save(file_path)

            teacher_id = session.get("teacher_id")
            cur.execute("""
                INSERT INTO homework (class_id, teacher_id, title, description, due_date, file_path)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (class_id, teacher_id, title, description, due_date, file_path))
            conn.commit()

            # Notify parents via SMS if contact exists
            cur.execute("SELECT parent_contact FROM students WHERE class_id=%s", (class_id,))
            parents = cur.fetchall()
            message = f"New Homework: {title} - due {due_date or 'N/A'}"
            for p in parents:
                pnum = p['parent_contact']
                if not pnum:
                    continue
                to_num = ("+91" + pnum) if len(pnum) == 10 and not pnum.startswith("+") else pnum
                send_async_message(to_num, message, channel="sms", extra={"purpose": "homework", "class_id": class_id})

            flash("Homework posted & parents notified (if contacts present).", "success")
            return redirect(url_for("teacher_homework"))

        # GET: fetch homework & classes
        cur.execute("""
            SELECT h.*, c.name AS class_name
            FROM homework h
            LEFT JOIN classes c ON h.class_id = c.id
            ORDER BY h.posted_on DESC
        """)
        homework = cur.fetchall()

        cur.execute("SELECT * FROM classes ORDER BY id")
        classes = cur.fetchall()

    except Exception as e:
        print("Teacher homework error:", e)
        flash("Unable to load or post homework.", "danger")
        homework = classes = []

    finally:
        if cur: cur.close()
        if conn: conn.close()

    return render_template("teacher_homework.html", homework=homework, classes=classes)

# ---------------------------
# Attendance (admin page supports add/delete/mark)
# ---------------------------
@app.route("/admin/attendance", methods=["GET", "POST"])
@require_role("admin")
def admin_attendance():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)

    # Fetch classes
    cur.execute("SELECT * FROM classes ORDER BY id")
    classes = cur.fetchall()

    class_id = request.args.get("class_id") or request.form.get("class_id")
    selected_date = request.args.get("date") or request.form.get("date") or date.today().isoformat()

    # ✅ Add student
    if request.method == "POST" and request.form.get("action") == "add_student":
        name = request.form.get("student_name")
        parent = request.form.get("parent_contact")

        if not name:
            flash("Student name required", "warning")
            return redirect(url_for("admin_attendance", class_id=class_id))

        username = (name.split()[0].lower() if name else "student") + datetime.now().strftime("%d%m%H%M")
        pwd_hash = generate_password_hash("student123")

        try:
            cur2 = conn.cursor()
            cur2.execute("""
                INSERT INTO students (name, username, password, class_id, parent_contact)
                VALUES (%s,%s,%s,%s,%s)
            """, (name, username, pwd_hash, class_id, parent))
            conn.commit(); cur2.close()
            flash("Student added! Default password: student123", "success")
        except Exception as e:
            print("Add student error:", e)
            flash("Error adding student", "danger")

        return redirect(url_for("admin_attendance", class_id=class_id))

    # ✅ Delete student
    if request.method == "POST" and request.form.get("action") == "delete_student":
        sid = request.form.get("student_id")
        try:
            cur2 = conn.cursor()
            cur2.execute("DELETE FROM students WHERE id=%s", (sid,))
            conn.commit(); cur2.close()
            flash("Student deleted.", "info")
        except Exception as e:
            print("Delete student error:", e)
            flash("Could not delete", "danger")
        return redirect(url_for("admin_attendance", class_id=class_id))

    # ✅ Fetch students of class
    students = []
    if class_id:
        cur.execute("SELECT * FROM students WHERE class_id=%s ORDER BY name", (class_id,))
        students = cur.fetchall()

    # ✅ Submit attendance
    if request.method == "POST" and request.form.get("action") == "submit_attendance":
        if not class_id:
            flash("Select class first!", "warning")
            return redirect(url_for("admin_attendance"))

        for s in students:
            status = "Present" if request.form.get(f"att_{s['id']}") else "Absent"
            try:
                cur2 = conn.cursor()
                cur2.execute("""
                    INSERT INTO attendance (student_id, class_id, date, status)
                    VALUES (%s,%s,%s,%s)
                """, (s['id'], class_id, selected_date, status))
                conn.commit(); cur2.close()
            except mysql.connector.errors.IntegrityError:
                cur2 = conn.cursor()
                cur2.execute("""
                    UPDATE attendance SET status=%s 
                    WHERE student_id=%s AND date=%s
                """, (status, s['id'], selected_date))
                conn.commit(); cur2.close()

        flash("✅ Attendance saved successfully!", "success")
        return redirect(url_for("admin_attendance", class_id=class_id, date=selected_date))

    # ✅ Show saved attendance records
    attendance_records = []
    if class_id:
        cur.execute("""
            SELECT a.date, s.name, c.name AS class_name, a.status
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            JOIN classes c ON a.class_id = c.id
            WHERE a.class_id=%s AND a.date=%s
            ORDER BY s.name ASC
        """, (class_id, selected_date))
        attendance_records = cur.fetchall()

    # ✅ Attendance Percentage for class
    attendance_percentages = []
    if class_id:
        cur2 = conn.cursor(dictionary=True)
        cur2.execute("""
            SELECT 
                s.id, 
                s.name,
                COALESCE(
                    ROUND(
                        (SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) / NULLIF(COUNT(a.id),0)) * 100
                    ,2)
                ,0) AS attendance_percent
            FROM students s
            LEFT JOIN attendance a 
                ON s.id = a.student_id AND a.class_id = %s
            WHERE s.class_id = %s
            GROUP BY s.id
            ORDER BY s.name ASC
        """, (class_id, class_id))
        attendance_percentages = cur2.fetchall()
        cur2.close()

    cur.close()
    return render_template("admin_attendance.html",
                           classes=classes,
                           students=students,
                           class_id=class_id,
                           today=selected_date,
                           attendance_records=attendance_records,
                           attendance_percentages=attendance_percentages)

@app.route("/teacher/attendance", methods=["GET", "POST"])
@require_role("teacher")
def teacher_attendance():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)

    # Fetch classes
    cur.execute("SELECT * FROM classes ORDER BY id")
    classes = cur.fetchall()

    class_id = request.args.get("class_id") or request.form.get("class_id")
    selected_date = request.args.get("date") or request.form.get("date") or date.today().isoformat()

    # ✅ Add Student
    if request.method == "POST" and request.form.get("action") == "add_student":
        name = request.form.get("student_name")
        parent = request.form.get("parent_contact")
        if not name:
            flash("Student name required", "warning")
            return redirect(url_for("teacher_attendance", class_id=class_id))

        username = (name.split()[0].lower() if name else "student") + datetime.now().strftime("%d%m%H%M")
        pwd_hash = generate_password_hash("student123")

        try:
            cur2 = conn.cursor()
            cur2.execute("""
                INSERT INTO students (name, username, password, class_id, parent_contact)
                VALUES (%s,%s,%s,%s,%s)
            """, (name, username, pwd_hash, class_id, parent))
            conn.commit(); cur2.close()
            flash("✅ Student added! Default password: student123", "success")
        except Exception as e:
            print("Teacher add student error:", e)
            flash("⚠️ Error adding student", "danger")

        return redirect(url_for("teacher_attendance", class_id=class_id))

    # ✅ Delete Student
    if request.method == "POST" and request.form.get("action") == "delete_student":
        sid = request.form.get("student_id")
        try:
            cur2 = conn.cursor()
            cur2.execute("DELETE FROM attendance WHERE student_id=%s", (sid,))
            cur2.execute("DELETE FROM students WHERE id=%s", (sid,))
            conn.commit(); cur2.close()
            flash("🗑️ Student deleted.", "info")
        except Exception as e:
            print("Teacher delete student error:", e)
            flash("⚠️ Cannot delete. Student has attendance records.", "danger")
        return redirect(url_for("teacher_attendance", class_id=class_id))

    # Fetch students list
    students = []
    if class_id:
        cur.execute("SELECT * FROM students WHERE class_id=%s ORDER BY name", (class_id,))
        students = cur.fetchall()

    # ✅ Submit Attendance
    if request.method == "POST" and request.form.get("action") == "submit_attendance":
        for s in students:
            status = "Present" if request.form.get(f"att_{s['id']}") else "Absent"
            try:
                cur2 = conn.cursor()
                cur2.execute("""
                    INSERT INTO attendance (student_id, class_id, date, status)
                    VALUES (%s,%s,%s,%s)
                """, (s['id'], class_id, selected_date, status))
                conn.commit(); cur2.close()
            except mysql.connector.errors.IntegrityError:
                cur2 = conn.cursor()
                cur2.execute("""
                    UPDATE attendance SET status=%s
                    WHERE student_id=%s AND date=%s
                """, (status, s['id'], selected_date))
                conn.commit(); cur2.close()

        flash("✅ Attendance saved successfully!", "success")
        return redirect(url_for("teacher_attendance", class_id=class_id, date=selected_date))

    # ✅ Show Saved Attendance for the Date
    attendance_records = []
    if class_id:
        cur.execute("""
            SELECT a.date, s.name, c.name AS class_name, a.status
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            JOIN classes c ON a.class_id = c.id
            WHERE a.class_id=%s AND a.date=%s
            ORDER BY s.name ASC
        """, (class_id, selected_date))
        attendance_records = cur.fetchall()

    cur.close()
    return render_template(
        "teacher_attendance.html",
        classes=classes,
        students=students,
        class_id=class_id,
        today=selected_date,
        attendance_records=attendance_records
    )

# ---------------------------
# Timetable upload
# ---------------------------
@app.route("/admin/timetable", methods=["GET", "POST"])
@require_role("admin")
def admin_timetable():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    if request.method == "POST":
        class_id = request.form.get("class_id")
        f = request.files.get("file")
        if not f or f.filename == "":
            flash("No file uploaded", "warning"); cur.close(); return redirect(url_for("admin_timetable"))
        os.makedirs(TIMETABLE_UPLOAD_DIR, exist_ok=True)
        filename = secure_filename(f.filename)
        save_path = os.path.join(TIMETABLE_UPLOAD_DIR, filename)
        f.save(save_path)
        cur.execute("INSERT INTO timetable (class_id, file_name) VALUES (%s,%s)", (class_id, filename))
        conn.commit()
        cur.close()
        flash("Timetable uploaded.", "success")
        return redirect(url_for("admin_timetable"))
    cur.execute("SELECT * FROM classes ORDER BY id")
    classes = cur.fetchall()
    cur.execute("SELECT t.*, c.name as class_name FROM timetable t LEFT JOIN classes c ON t.class_id=c.id ORDER BY t.uploaded_on DESC")
    timetables = cur.fetchall()
    cur.close()
    return render_template("admin_timetable.html", classes=classes, timetables=timetables)

# ---------------------------
# Student public portal (no login)
# ---------------------------
@app.route("/student")
def student_portal():
    """Show big 5D class cards (Pre-KG -> 10th)."""
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM classes ORDER BY id")
    classes = cur.fetchall() or []
    cur.close()
    conn.close()
    return render_template("student_select_class.html", classes=classes)

@app.route("/student/class/<int:class_id>")
def student_class_dashboard(class_id):
    """
    Class dashboard view for public students (no login).
    Shows homework, memos/announcements and a simple attendance %
    for a given student name *if* student_name query param is provided.
    """
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # fetch class info
    cur.execute("SELECT id, name FROM classes WHERE id=%s", (class_id,))
    class_info = cur.fetchone()
    if not class_info:
        cur.close(); conn.close()
        flash("Class not found.", "danger")
        return redirect(url_for("student_portal"))

    # fetch homework for class (latest first)
    cur.execute("""
        SELECT id, class_id, title, description, due_date, file_path, posted_on
        FROM homework
        WHERE class_id=%s
        ORDER BY posted_on DESC
    """, (class_id,))
    homework = cur.fetchall() or []

    # fetch memos/announcements for class
    cur.execute("""
        SELECT id, class_id, message, created_on
        FROM memos
        WHERE class_id=%s
        ORDER BY created_on DESC
        LIMIT 10
    """, (class_id,))
    memos = cur.fetchall() or []

    # optional attendance: if student_name query param is present, compute their %
    student_name = request.args.get("student_name")
    attendance_percent = None
    if student_name:
        # find student
        cur.execute("SELECT id FROM students WHERE name=%s AND class_id=%s LIMIT 1", (student_name, class_id))
        s = cur.fetchone()
        if s:
            sid = s["id"]
            cur.execute("SELECT COUNT(*) AS total FROM attendance WHERE student_id=%s", (sid,))
            total = cur.fetchone()["total"] or 0
            cur.execute("SELECT COUNT(*) AS present FROM attendance WHERE student_id=%s AND status='Present'", (sid,))
            present = cur.fetchone()["present"] or 0
            attendance_percent = round((present / total * 100) if total else 0, 2)
        else:
            attendance_percent = 0

    cur.close()
    conn.close()

    return render_template(
        "student_class_dashboard.html",
        class_info=class_info,
        homework=homework,
        memos=memos,
        student_name=student_name,
        attendance_percent=attendance_percent
    )


@app.route("/student/homework")
def student_homework_public():
    """Detailed homework list for a class. Query param: class_id (required)."""
    class_id = request.args.get("class_id")
    if not class_id:
        flash("Please select a class to view homework.", "warning")
        return redirect(url_for("student_portal"))

    try:
        class_id = int(class_id)
    except ValueError:
        flash("Invalid class id.", "danger")
        return redirect(url_for("student_portal"))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM classes WHERE id=%s", (class_id,))
    class_info = cur.fetchone()
    if not class_info:
        cur.close(); conn.close()
        flash("Class not found.", "danger")
        return redirect(url_for("student_portal"))

    cur.execute("""
        SELECT id, title, description, due_date, file_path, posted_on
        FROM homework
        WHERE class_id=%s
        ORDER BY posted_on DESC
    """, (class_id,))
    homework = cur.fetchall() or []

    cur.close(); conn.close()
    return render_template("student_homework.html", class_info=class_info, homework=homework)

@app.route("/student/attendance")
def student_attendance_public():
    student_name = request.args.get("student_name")
    class_id = request.args.get("class_id")

    if not student_name or not class_id:
        flash("Student name and class ID are required", "warning")
        return redirect(url_for("student_portal"))

    try:
        class_id = int(class_id)
    except ValueError:
        flash("Invalid class ID.", "danger")
        return redirect(url_for("student_portal"))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    # Fetch student ID
    cur.execute("SELECT id FROM students WHERE name=%s AND class_id=%s LIMIT 1", (student_name, class_id))
    student = cur.fetchone()
    if not student:
        flash("Student not found in this class.", "warning")
        cur.close()
        conn.close()
        return redirect(url_for("student_portal"))

    student_id = student["id"]

    # Fetch attendance records
    cur.execute("SELECT date, status FROM attendance WHERE student_id=%s ORDER BY date DESC", (student_id,))
    attendance = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("student_attendance.html", attendance=attendance, student_name=student_name)


@app.route("/student/memos")
def student_memos_public():
    class_id = request.args.get("class_id")
    if not class_id:
        flash("Class ID is required", "warning")
        return redirect(url_for("student_portal"))

    try:
        class_id = int(class_id)
    except ValueError:
        flash("Invalid class ID.", "danger")
        return redirect(url_for("student_portal"))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT message, created_on FROM memos WHERE class_id=%s ORDER BY created_on DESC", (class_id,))
    memos = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("student_memos.html", memos=memos, class_id=class_id)

# Admin SMS logs
@app.route("/admin/sms-logs")
@require_role("admin")
def admin_sms_logs():
    conn = get_db_connection(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM sms_logs ORDER BY created_on DESC LIMIT 200")
    logs = cur.fetchall()
    cur.close()
    return render_template("admin_sms_logs.html", logs=logs)

@app.route("/coming-soon")
def coming_soon():
    return "<h3 style='padding:20px;'>Fees Module Coming Soon</h3>"

if __name__ == "__main__":
    # Avoid double thread run in debug
    if not app.debug:
        threading.Thread(target=fee_scheduler, daemon=True).start()

    app.run(host="0.0.0.0", port=5000, debug=True)
    