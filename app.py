
"""
app.py — Smart Classroom & Timetable Scheduler, backend.

How the 5 files connect
------------------------------------------------------------------------
schema.sql  --read & executed by-->  app.py (init_db(), on startup)
app.py      --serves-->               index.html, style.css, app.js
                                       (via explicit routes, see bottom)
app.py      --exposes-->              /api/* JSON endpoints
index.html  --loads-->                style.css (<link>) and app.js (<script>)
app.js      --calls-->                every /api/* endpoint with fetch()
                                       and renders the JSON straight into
                                       the DOM elements defined in index.html
------------------------------------------------------------------------

Run:
    Configure DATABASE_URL and SECRET_KEY in your environment.
    pip install flask psycopg2-binary
    python3 app.py
"""
import os
import io
import re
import uuid
import mimetypes
from collections import defaultdict
from functools import wraps

from flask import Flask, request, session, jsonify, send_from_directory, send_file, g
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors as rl_colors
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY")

DATABASE_URL = os.environ.get(
    "DATABASE_URL"
)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
YEAR_LABELS = [
    ("FY", "First Year", ("I", "II")),
    ("SY", "Second Year", ("III", "IV")),
    ("TY", "Third Year", ("V", "VI")),
    ("LY", "Last Year", ("VII", "VIII")),
]

# ============================================================ DB HELPERS
def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def init_db():
    with get_conn() as conn, conn.cursor() as cur:
        with open(os.path.join(BASE_DIR, "schema.sql"), "r") as f:
            cur.execute(f.read())
        conn.commit()


def q_all(cur, sql, params=None):
    cur.execute(sql, params or ())
    return [dict(r) for r in cur.fetchall()]


def q_one(cur, sql, params=None):
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return dict(row) if row else None


def notify(cur, admin_id, message, division_ids=(), faculty_ids=()):
    """Records a timetable change. Both portals poll these rows. Empty
    division_ids AND faculty_ids = broadcast to every student and teacher."""
    cur.execute(
        "INSERT INTO notification (admin_id, message, division_ids, faculty_ids) VALUES (%s,%s,%s::int[],%s::int[])",
        (admin_id, message, list(division_ids), list(faculty_ids)),
    )


# ============================================================ AUTH
def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "admin_id" not in session:
            return jsonify({"error": "Not logged in"}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(force=True)
    login_id = (data.get("login_id") or "").strip()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    confirm = data.get("confirm") or ""
    if not (login_id and name and password):
        return jsonify({"error": "All fields are required."}), 400
    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    with get_conn() as conn, conn.cursor() as cur:
        if q_one(cur, "SELECT id FROM admin WHERE login_id=%s", (login_id,)):
            return jsonify({"error": "That login ID is already registered."}), 400
        admin = q_one(
            cur,
            "INSERT INTO admin (login_id, name, password_hash) VALUES (%s,%s,%s) RETURNING id, name",
            (login_id, name, generate_password_hash(password)),
        )
        cur.execute("INSERT INTO institute (admin_id) VALUES (%s)", (admin["id"],))
        conn.commit()
    session["admin_id"] = admin["id"]
    return jsonify({"ok": True, "admin": admin})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    login_id = (data.get("login_id") or "").strip()
    password = data.get("password") or ""
    with get_conn() as conn, conn.cursor() as cur:
        admin = q_one(cur, "SELECT * FROM admin WHERE login_id=%s", (login_id,))
    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify({"error": "Invalid login ID or password."}), 401
    session["admin_id"] = admin["id"]
    return jsonify({"ok": True, "admin": {"id": admin["id"], "name": admin["name"]}})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    if "admin_id" not in session:
        return jsonify({"error": "Not logged in"}), 401
    with get_conn() as conn, conn.cursor() as cur:
        admin = q_one(cur, "SELECT id, name FROM admin WHERE id=%s", (session["admin_id"],))
    return jsonify({"admin": admin})


# ============================================================ INSTITUTE + BREAKS
@app.route("/api/institute", methods=["GET", "POST"])
@require_auth
def institute():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            data = request.get_json(force=True)
            cur.execute(
                """UPDATE institute SET name=%s, day_start=%s, day_end=%s, period_minutes=%s,
                   working_days=%s, holidays=%s WHERE admin_id=%s""",
                (
                    data.get("name", "My Institute"),
                    data.get("day_start", "09:00"),
                    data.get("day_end", "17:00"),
                    int(data.get("period_minutes", 55)),
                    data.get("working_days") or ["Mon", "Tue", "Wed", "Thu", "Fri"],
                    data.get("holidays") or [],
                    aid,
                ),
            )
            conn.commit()
        inst = q_one(cur, "SELECT * FROM institute WHERE admin_id=%s", (aid,))
        breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s ORDER BY start_time", (inst["id"],))
    return jsonify({"institute": inst, "breaks": breaks})


@app.route("/api/breaks", methods=["POST"])
@require_auth
def add_break():
    aid = session["admin_id"]
    data = request.get_json(force=True)
    with get_conn() as conn, conn.cursor() as cur:
        inst = q_one(cur, "SELECT id FROM institute WHERE admin_id=%s", (aid,))
        cur.execute(
            "INSERT INTO break_slot (institute_id, label, kind, start_time, end_time) VALUES (%s,%s,%s,%s,%s)",
            (inst["id"], data.get("label", "Break"), data.get("kind", "short"),
             data["start_time"], data["end_time"]),
        )
        conn.commit()
        breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s ORDER BY start_time", (inst["id"],))
    return jsonify({"breaks": breaks})


@app.route("/api/breaks/<int:break_id>", methods=["DELETE"])
@require_auth
def delete_break(break_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM break_slot WHERE id=%s AND institute_id IN (SELECT id FROM institute WHERE admin_id=%s)",
            (break_id, session["admin_id"]),
        )
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ YEARS
@app.route("/api/years", methods=["GET", "POST"])
@require_auth
def years():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            data = request.get_json(force=True)
            exists = q_one(
                cur, "SELECT id FROM academic_year WHERE admin_id=%s AND label=%s AND sem_no=%s",
                (aid, data["label"], data["sem_no"]),
            )
            if exists:
                return jsonify({"error": f"{data['label']} Sem {data['sem_no']} already exists."}), 400
            cur.execute(
                "INSERT INTO academic_year (admin_id, label, parity, sem_no) VALUES (%s,%s,%s,%s)",
                (aid, data["label"], data["parity"], data["sem_no"]),
            )
            conn.commit()
        rows = q_all(cur, "SELECT * FROM academic_year WHERE admin_id=%s ORDER BY label, sem_no", (aid,))
    return jsonify({"years": rows, "year_labels": YEAR_LABELS})


@app.route("/api/years/<int:year_id>", methods=["DELETE"])
@require_auth
def delete_year(year_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM academic_year WHERE id=%s AND admin_id=%s", (year_id, session["admin_id"]))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ DIVISIONS + BATCHES
def _clean_home_room(cur, admin_id, text):
    """-> (room number to store or None, error text). A room that already exists must be a classroom,
    not a lab. A room that does not exist yet is accepted (classrooms are created in the next steps)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return None, None
    room = q_one(cur, "SELECT room_no, room_type FROM classroom WHERE admin_id=%s AND lower(room_no)=lower(%s)",
                 (admin_id, text))
    if room and room["room_type"] != "classroom":
        return None, f"{room['room_no']} is a lab. A division's assigned room must be a classroom (labs are picked automatically)."
    return (room["room_no"] if room else text), None


@app.route("/api/divisions/<int:div_id>/room", methods=["POST"])
@require_auth
def set_division_room(div_id):
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        home, err = _clean_home_room(cur, aid, (request.get_json(force=True) or {}).get("home_room"))
        if err:
            return jsonify({"error": err}), 400
        cur.execute("""UPDATE division SET home_room=%s WHERE id=%s
                       AND year_id IN (SELECT id FROM academic_year WHERE admin_id=%s)""", (home, div_id, aid))
        conn.commit()
    return jsonify({"ok": True, "home_room": home})


@app.route("/api/divisions", methods=["GET", "POST"])
@require_auth
def divisions():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            data = request.get_json(force=True)
            home, err = _clean_home_room(cur, aid, data.get("home_room"))
            if err:
                return jsonify({"error": err}), 400
            cur.execute(
                "INSERT INTO division (year_id, name, class_teacher, strength, home_room) VALUES (%s,%s,%s,%s,%s)",
                (data["year_id"], data["name"], data.get("class_teacher"), int(data.get("strength") or 0), home),
            )
            conn.commit()
        divs = q_all(
            cur,
            """SELECT d.*, ay.label AS year_label, ay.sem_no AS year_sem
               FROM division d JOIN academic_year ay ON ay.id = d.year_id
               WHERE ay.admin_id=%s ORDER BY d.name""",
            (aid,),
        )
        rc = {r["division_id"]: r["file_name"] for r in
              q_all(cur, "SELECT division_id, file_name FROM division_rollcall WHERE admin_id=%s", (aid,))}
        for d in divs:
            d["batches"] = q_all(cur, "SELECT * FROM batch WHERE division_id=%s ORDER BY name", (d["id"],))
            d["rollcall_name"] = rc.get(d["id"])
    return jsonify({"divisions": divs})


@app.route("/api/divisions/<int:div_id>", methods=["DELETE"])
@require_auth
def delete_division(div_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM division WHERE id=%s AND year_id IN (SELECT id FROM academic_year WHERE admin_id=%s)",
            (div_id, session["admin_id"]),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/divisions/<int:div_id>/batches", methods=["POST"])
@require_auth
def add_batch(div_id):
    data = request.get_json(force=True)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO batch (division_id, name, num_students) VALUES (%s,%s,%s)",
            (div_id, data["name"], int(data.get("num_students") or 0)),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/batches/<int:batch_id>", methods=["DELETE"])
@require_auth
def delete_batch(batch_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM batch WHERE id=%s", (batch_id,))
        conn.commit()
    return jsonify({"ok": True})


def _rollcall_table(c, x, y, title, count, width=170 * mm):
    """Draws one roll-call table (Roll No / Name / Signature) starting at (x, y),
    numbered 1..count. Returns the y position just below the table."""
    row_h = 8 * mm
    col_roll, col_name, col_sign = 20 * mm, 100 * mm, width - 20 * mm - 100 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, title)
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 9)
    header_y = y
    c.rect(x, header_y - row_h, width, row_h)
    c.line(x + col_roll, header_y - row_h, x + col_roll, header_y)
    c.line(x + col_roll + col_name, header_y - row_h, x + col_roll + col_name, header_y)
    c.drawString(x + 2 * mm, header_y - row_h + 2.5 * mm, "Roll No")
    c.drawString(x + col_roll + 2 * mm, header_y - row_h + 2.5 * mm, "Student Name")
    c.drawString(x + col_roll + col_name + 2 * mm, header_y - row_h + 2.5 * mm, "Signature")
    y -= row_h

    c.setFont("Helvetica", 9)
    for n in range(1, count + 1):
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = 280 * mm
        c.rect(x, y - row_h, width, row_h)
        c.line(x + col_roll, y - row_h, x + col_roll, y)
        c.line(x + col_roll + col_name, y - row_h, x + col_roll + col_name, y)
        c.drawString(x + 2 * mm, y - row_h + 2.5 * mm, str(n))
        y -= row_h
    return y - 6 * mm


# ---- uploaded roll call sheet (any format). Admin uploads; portals only read it.
ROLLCALL_MAX_BYTES = 10 * 1024 * 1024
# Only these are shown in the browser; everything else (xlsx, docx, html...) is always sent as a download,
# so an uploaded file can never run as a web page.
ROLLCALL_INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


def _send_rollcall(cur, admin_id, div_id, inline=False):
    row = q_one(cur, "SELECT file_name, mime_type, file_data FROM division_rollcall WHERE division_id=%s AND admin_id=%s",
                (div_id, admin_id))
    if not row:
        return jsonify({"error": "No roll call has been uploaded for this division yet."}), 404
    mime = row["mime_type"] or "application/octet-stream"
    show = inline and mime in ROLLCALL_INLINE_TYPES
    resp = send_file(io.BytesIO(bytes(row["file_data"])), download_name=row["file_name"],
                     as_attachment=not show, mimetype=mime if show else "application/octet-stream")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/api/divisions/<int:div_id>/rollcall/upload", methods=["POST"])
@require_auth
def rollcall_upload(div_id):
    aid = session["admin_id"]
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a file to upload."}), 400
    data = file.read()
    if not data:
        return jsonify({"error": "That file is empty."}), 400
    if len(data) > ROLLCALL_MAX_BYTES:
        return jsonify({"error": "File is too large (limit 10 MB)."}), 400
    name = os.path.basename(file.filename.replace("\\", "/"))[:200] or "rollcall"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with get_conn() as conn, conn.cursor() as cur:
        div = q_one(cur, """SELECT d.id FROM division d JOIN academic_year ay ON ay.id=d.year_id
                            WHERE d.id=%s AND ay.admin_id=%s""", (div_id, aid))
        if not div:
            return jsonify({"error": "Division not found"}), 404
        cur.execute("""INSERT INTO division_rollcall (admin_id, division_id, file_name, mime_type, file_data)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (division_id) DO UPDATE
                       SET file_name=EXCLUDED.file_name, mime_type=EXCLUDED.mime_type,
                           file_data=EXCLUDED.file_data, updated_at=now()""",
                    (aid, div_id, name, mime, psycopg2.Binary(data)))
        conn.commit()
    return jsonify({"ok": True, "file_name": name})


@app.route("/api/divisions/<int:div_id>/rollcall/file", methods=["GET", "DELETE"])
@require_auth
def rollcall_file(div_id):
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "DELETE":
            cur.execute("DELETE FROM division_rollcall WHERE division_id=%s AND admin_id=%s", (div_id, aid))
            conn.commit()
            return jsonify({"ok": True})
        return _send_rollcall(cur, aid, div_id, inline=bool(request.args.get("inline")))


@app.route("/api/divisions/<int:div_id>/rollcall.pdf")
@require_auth
def rollcall_pdf(div_id):
    """
    Generates a printable roll-call / attendance register for a division:
    one continuous table for the whole division (1..strength), followed
    by one table per batch (1..num_students each) for lab attendance.
    Names/signatures are left blank to fill by hand — we only store
    headcounts, not individual student records.
    """
    with get_conn() as conn, conn.cursor() as cur:
        div = q_one(
            cur,
            """SELECT d.*, ay.label AS year_label, ay.sem_no AS year_sem
               FROM division d JOIN academic_year ay ON ay.id=d.year_id
               WHERE d.id=%s AND ay.admin_id=%s""",
            (div_id, session["admin_id"]),
        )
        if not div:
            return jsonify({"error": "Division not found"}), 404
        batches = q_all(cur, "SELECT * FROM batch WHERE division_id=%s ORDER BY name", (div_id,))
        inst = q_one(cur, "SELECT name FROM institute WHERE admin_id=%s", (session["admin_id"],))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    x, y = 20 * mm, 280 * mm

    c.setFont("Helvetica-Bold", 15)
    c.drawString(x, y, inst["name"] if inst else "Institute")
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x, y, f"Roll Call / Attendance Register — {div['name']}")
    y -= 7 * mm
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"{div['year_label']} · Semester {div['year_sem']}    Class Teacher: {div['class_teacher'] or '—'}")
    y -= 6 * mm
    c.drawString(x, y, "Date: _______________     Subject: _______________     Period: _______________")
    y -= 10 * mm

    y = _rollcall_table(c, x, y, f"Full Division — {div['strength']} students", div["strength"] or 0)

    for b in batches:
        y -= 6 * mm
        if y < 40 * mm:
            c.showPage()
            y = 280 * mm
        y = _rollcall_table(c, x, y, f"Batch {b['name']} — {b['num_students']} students", b["num_students"] or 0)

    c.save()
    buf.seek(0)
    return send_file(buf, download_name=f"{div['name']}_rollcall.pdf", as_attachment=True, mimetype="application/pdf")


# ============================================================ FACULTY
@app.route("/api/faculty", methods=["GET", "POST"])
@require_auth
def faculty():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            data = request.get_json(force=True)
            quals = [q.strip() for q in (data.get("qualifications") or "").split(",") if q.strip()]
            cur.execute(
                "INSERT INTO faculty (admin_id, name, qualifications, max_periods_per_day, designation) VALUES (%s,%s,%s,%s,%s)",
                (aid, data["name"], quals, int(data.get("max_periods_per_day") or 6), (data.get("designation") or "").strip() or None),
            )
            conn.commit()
        rows = q_all(cur, "SELECT * FROM faculty WHERE admin_id=%s ORDER BY name", (aid,))
    return jsonify({"faculty": rows})


@app.route("/api/faculty/<int:fac_id>", methods=["DELETE"])
@require_auth
def delete_faculty(fac_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM faculty WHERE id=%s AND admin_id=%s", (fac_id, session["admin_id"]))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ CLASSROOMS
@app.route("/api/classrooms", methods=["GET", "POST"])
@require_auth
def classrooms():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            data = request.get_json(force=True)
            cur.execute(
                "INSERT INTO classroom (admin_id, room_no, room_type, capacity) VALUES (%s,%s,%s,%s)",
                (aid, data["room_no"], data.get("room_type", "classroom"), int(data.get("capacity") or 60)),
            )
            conn.commit()
        rows = q_all(cur, "SELECT * FROM classroom WHERE admin_id=%s ORDER BY room_no", (aid,))
    return jsonify({"classrooms": rows})


@app.route("/api/classrooms/<int:room_id>", methods=["DELETE"])
@require_auth
def delete_classroom(room_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM classroom WHERE id=%s AND admin_id=%s", (room_id, session["admin_id"]))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ SUBJECTS + ALLOCATION
PRIORITY_CHOICES = ("before_lunch", "after_lunch", "any", "first", "last")


@app.route("/api/subjects", methods=["GET", "POST"])
@require_auth
def subjects():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "POST":
            f = request.form
            file = request.files.get("syllabus")
            syllabus_name, syllabus_data = None, None
            if file and file.filename:
                syllabus_name = file.filename
                syllabus_data = psycopg2.Binary(file.read())
            combine_divisions = [int(x) for x in f.getlist("combine_divisions") if x]
            external_fixed_days = [d for d in f.getlist("external_fixed_days") if d]
            priority = f.get("priority", "any")
            if priority not in PRIORITY_CHOICES:
                priority = "any"
            if f.get("type") == "lab" and priority == "first":
                priority = "any"      # labs are never allowed in the first two periods
            try:
                max_per_day = min(3, max(1, int(f.get("max_per_day") or 2)))
            except ValueError:
                max_per_day = 2
            cur.execute(
                """INSERT INTO subject (year_id, code, name, type, lectures_per_week,
                   priority_first_period, priority_slot, max_per_day, external_fixed_days, combine_divisions,
                   multi_faculty, syllabus_name, syllabus_data)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    f["year_id"], f["code"], f["name"], f.get("type", "lecture"),
                    int(f.get("lectures_per_week") or 1),
                    priority == "first", priority, max_per_day,
                    external_fixed_days,
                    combine_divisions,
                    f.get("multi_faculty") == "true",
                    syllabus_name, syllabus_data,
                ),
            )
            conn.commit()
        subs = q_all(
            cur,
            """SELECT s.id, s.year_id, s.code, s.name, s.type, s.lectures_per_week,
                      s.priority_first_period, s.priority_slot, s.max_per_day, s.external_fixed_days, s.combine_divisions,
                      s.multi_faculty, s.syllabus_name,
                      ay.label AS year_label, ay.sem_no AS year_sem
               FROM subject s JOIN academic_year ay ON ay.id = s.year_id
               WHERE ay.admin_id=%s ORDER BY s.code""",
            (aid,),
        )
        for s in subs:
            s["allocations"] = q_all(
                cur,
                """SELECT fa.id, f.name AS faculty_name, d.name AS division_name,
                          (SELECT array_agg(b.name ORDER BY b.name) FROM batch b WHERE b.id = ANY(fa.batch_ids)) AS batch_names
                   FROM faculty_allocation fa
                   JOIN faculty f ON f.id = fa.faculty_id
                   JOIN division d ON d.id = fa.division_id
                   WHERE fa.subject_id=%s""",
                (s["id"],),
            )
    return jsonify({"subjects": subs})


@app.route("/api/subjects/<int:subj_id>", methods=["DELETE"])
@require_auth
def delete_subject(subj_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM subject WHERE id=%s AND year_id IN (SELECT id FROM academic_year WHERE admin_id=%s)",
            (subj_id, session["admin_id"]),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/subjects/<int:subj_id>/syllabus")
@require_auth
def get_syllabus(subj_id):
    with get_conn() as conn, conn.cursor() as cur:
        row = q_one(cur, "SELECT syllabus_name, syllabus_data FROM subject WHERE id=%s", (subj_id,))
    if not row or not row["syllabus_data"]:
        return jsonify({"error": "No syllabus uploaded"}), 404
    return send_file(io.BytesIO(bytes(row["syllabus_data"])), download_name=row["syllabus_name"], as_attachment=True)


@app.route("/api/subjects/<int:subj_id>/allocate", methods=["POST"])
@require_auth
def allocate_faculty(subj_id):
    """
    faculty_ids: 1+ faculty (2+ = co-teaching, if the subject's multi_faculty
    flag is set). batch_ids: 0+ batches — empty means "whole division", one
    id is a normal single batch, 2+ ids means those batches are merged into
    one session taught together by this faculty (e.g. a combined lab).
    Each faculty in faculty_ids gets its own row, all sharing the same
    division/batch_ids.
    """
    data = request.get_json(force=True)
    faculty_ids = data.get("faculty_ids")
    if not faculty_ids:
        single = data.get("faculty_id")
        faculty_ids = [single] if single else []
    if not faculty_ids:
        return jsonify({"error": "Select at least one faculty."}), 400
    division_id = data["division_id"]
    batch_ids = [int(b) for b in (data.get("batch_ids") or []) if b]
    with get_conn() as conn, conn.cursor() as cur:
        for fid in faculty_ids:
            cur.execute(
                "INSERT INTO faculty_allocation (subject_id, faculty_id, division_id, batch_ids) VALUES (%s,%s,%s,%s)",
                (subj_id, fid, division_id, batch_ids),
            )
        conn.commit()
    return jsonify({"ok": True, "count": len(faculty_ids)})



@app.route("/api/allocations/<int:alloc_id>", methods=["DELETE"])
@require_auth
def delete_allocation(alloc_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM faculty_allocation WHERE id=%s", (alloc_id,))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ LOOKUPS (dropdown hydration)
@app.route("/api/lookups")
@require_auth
def lookups():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        years_ = q_all(cur, "SELECT * FROM academic_year WHERE admin_id=%s ORDER BY label", (aid,))
        divs = q_all(
            cur,
            """SELECT d.*, ay.label AS year_label FROM division d
               JOIN academic_year ay ON ay.id=d.year_id WHERE ay.admin_id=%s ORDER BY d.name""",
            (aid,),
        )
        batches = q_all(
            cur,
            """SELECT b.*, d.name AS division_name FROM batch b
               JOIN division d ON d.id=b.division_id
               JOIN academic_year ay ON ay.id=d.year_id WHERE ay.admin_id=%s ORDER BY b.name""",
            (aid,),
        )
        fac = q_all(cur, "SELECT * FROM faculty WHERE admin_id=%s ORDER BY name", (aid,))
        rooms = q_all(cur, "SELECT * FROM classroom WHERE admin_id=%s ORDER BY room_no", (aid,))
    return jsonify({"years": years_, "divisions": divs, "batches": batches, "faculty": fac, "classrooms": rooms})


# ============================================================ SCHEDULER
def _to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def build_period_grid(institute_row, breaks):
    start = _to_minutes(institute_row["day_start"])
    end = _to_minutes(institute_row["day_end"])
    length = institute_row["period_minutes"] or 55
    break_windows = sorted((_to_minutes(b["start_time"]), _to_minutes(b["end_time"]), b["label"],
                            "lunch" if (b.get("kind") == "lunch" or "lunch" in (b.get("label") or "").lower()) else "short")
                           for b in breaks)

    periods = []
    cur, idx = start, 0
    while cur < end:
        hit = next((bw for bw in break_windows if bw[0] <= cur < bw[1]), None)
        if hit:
            periods.append({"index": idx, "start": cur, "end": hit[1], "is_break": True, "label": hit[2], "kind": hit[3]})
            cur, idx = hit[1], idx + 1
            continue
        slot_end = min(cur + length, end)
        next_break_start = next((bw[0] for bw in break_windows if bw[0] > cur), None)
        if next_break_start and next_break_start < slot_end:
            slot_end = next_break_start
        if slot_end - cur < 20:
            cur = slot_end
            continue
        periods.append({"index": idx, "start": cur, "end": slot_end, "is_break": False, "label": None})
        cur, idx = slot_end, idx + 1
    return periods


class Session:
    """
    One unit of work: a single lecture period, or a 2-period back-to-back
    lab/tutorial. `batches` is a list — empty means "whole division",
    one entry means a normal single batch, two-or-more entries means a
    merged lab/tutorial (one faculty, one room, several batches at once).
    """
    __slots__ = ("subject", "division", "batches", "faculty_ids", "length", "fixed_days",
                 "priority", "max_per_day", "combine_divisions", "room_type", "capacity_needed", "group_key")

    def __init__(self, subject, division, batches, faculty_ids):
        self.subject = subject
        self.division = division
        self.batches = batches or []
        self.faculty_ids = faculty_ids
        self.length = 2 if subject["type"] in ("lab", "tutorial") else 1
        self.fixed_days = subject["external_fixed_days"] or []
        self.priority = subject.get("priority_slot") or ("first" if subject["priority_first_period"] else "any")
        if subject["type"] == "lab" and self.priority == "first":
            self.priority = "any"         # labs can never sit in the first two periods
        self.max_per_day = min(3, max(1, subject.get("max_per_day") or 2))
        self.combine_divisions = subject["combine_divisions"] or []
        self.room_type = "lab" if subject["type"] == "lab" else "classroom"
        cap = sum(b["num_students"] for b in self.batches) if self.batches else division["strength"]
        self.capacity_needed = cap or 1
        self.group_key = str(uuid.uuid4())[:8]

    def score(self):
        s = 0
        if self.fixed_days: s -= 100
        if self.priority == "first": s -= 50
        elif self.priority == "last": s -= 45
        elif self.priority in ("before_lunch", "after_lunch"): s -= 20
        if self.combine_divisions: s -= 30
        if self.length == 2: s -= 10
        if len(self.batches) > 1: s -= 15   # merged-batch labs are also worth placing early
        return s


def build_sessions(cur, admin_id):
    subs = q_all(
        cur,
        """SELECT s.* FROM subject s JOIN academic_year ay ON ay.id = s.year_id WHERE ay.admin_id=%s""",
        (admin_id,),
    )
    sessions = []
    for subject in subs:
        allocs = q_all(cur, "SELECT * FROM faculty_allocation WHERE subject_id=%s", (subject["id"],))
        if not allocs:
            continue
        div_cache, batch_cache = {}, {}

        def get_division(did):
            if did not in div_cache:
                div_cache[did] = q_one(cur, "SELECT * FROM division WHERE id=%s", (did,))
            return div_cache[did]

        def get_batch(bid):
            if bid is None:
                return None
            if bid not in batch_cache:
                batch_cache[bid] = q_one(cur, "SELECT * FROM batch WHERE id=%s", (bid,))
            return batch_cache[bid]

        def get_batches(bids):
            return [b for b in (get_batch(bid) for bid in (bids or [])) if b]

        if subject["combine_divisions"]:
            # Same lecture, different divisions, different faculty, split across rooms.
            per_div = defaultdict(list)
            for a in allocs:
                per_div[a["division_id"]].append(a)
            reps = []
            for did, alist in per_div.items():
                reps.append((get_division(did), get_batches(alist[0]["batch_ids"]), [a["faculty_id"] for a in alist]))
            for _ in range(subject["lectures_per_week"]):
                group_key = str(uuid.uuid4())[:8]
                combined = []
                for division, batches, fac_ids in reps:
                    s = Session(subject, division, batches, fac_ids)
                    s.group_key = group_key
                    combined.append(s)
                sessions.append(combined)

        elif subject["multi_faculty"]:
            # Two-or-more faculty co-teaching the same division/batch(es) at
            # once (e.g. mini-project presentations).
            by_key = defaultdict(list)
            for a in allocs:
                key = (a["division_id"], tuple(sorted(a["batch_ids"] or [])))
                by_key[key].append(a["faculty_id"])
            for (did, batch_ids), fac_ids in by_key.items():
                batches = get_batches(batch_ids)
                for _ in range(subject["lectures_per_week"]):
                    sessions.append([Session(subject, get_division(did), batches, fac_ids)])

        else:
            # Plain allocations. batch_ids on the allocation itself decides
            # everything: empty = whole division, one id = a normal single
            # batch, 2+ ids = those batches taught together as one merged
            # session by this one faculty (e.g. a combined lab) — chosen
            # directly when the faculty was allocated, no separate setup.
            for a in allocs:
                batches = get_batches(a["batch_ids"])
                for _ in range(subject["lectures_per_week"]):
                    sessions.append([Session(subject, get_division(a["division_id"]), batches, [a["faculty_id"]])])
    return sessions
def pair_batch_sessions(groups):
    """Pair two batch sessions of the same division so they run at the same time
    (e.g. A1+A2 in DBMS lab while A3+A4 are in MP lab)."""
    def is_batch(g):
        return len(g) == 1 and g[0].batches and not g[0].combine_divisions
    singles = [g for g in groups if is_batch(g)]
    others = [g for g in groups if not is_batch(g)]
    used, paired = set(), []
    for i, g in enumerate(singles):
        if i in used:
            continue
        used.add(i)
        s = g[0]
        best, best_score = None, None
        for j in range(i + 1, len(singles)):
            if j in used:
                continue
            t = singles[j][0]
            if t.division["id"] != s.division["id"] or t.length != s.length:
                continue
            if {b["id"] for b in s.batches} & {b["id"] for b in t.batches}:
                continue   # same batch cannot be in two places
            if set(s.faculty_ids) & set(t.faculty_ids):
                continue   # same teacher cannot take both
            score = (t.subject["id"] == s.subject["id"], t.priority != s.priority)
            if best is None or score < best_score:
                best, best_score = j, score
        if best is None:
            paired.append(g)
        else:
            used.add(best)
            t = singles[best][0]
            pri = s.priority if s.priority != "any" else t.priority
            s.priority = t.priority = pri
            paired.append([s, t])
    return others + paired


def generate_timetable(admin_id):
    with get_conn() as conn, conn.cursor() as cur:
        inst = q_one(cur, "SELECT * FROM institute WHERE admin_id=%s", (admin_id,))
        if not inst:
            return {"placed": 0, "unplaced": [{"reason": "Institute hours not configured yet"}]}
        breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s", (inst["id"],))
        periods = build_period_grid(inst, breaks)
        teach_periods = [p for p in periods if not p["is_break"]]
        working_days = [d for d in inst["working_days"] if d not in (inst["holidays"] or [])]

        rooms = q_all(cur, "SELECT * FROM classroom WHERE admin_id=%s", (admin_id,))
        rooms_by_type = defaultdict(list)
        for r in rooms:
            rooms_by_type[r["room_type"]].append(r)
        for lst in rooms_by_type.values():
            lst.sort(key=lambda r: r["capacity"])

        # Assigned (home) classroom per division: every whole-division lecture/tutorial goes there.
        room_by_no = {r["room_no"].strip().lower(): r for r in rooms}
        home_room_by_div, home_problems = {}, []
        for d_ in q_all(cur, """SELECT d.id, d.name, d.home_room FROM division d
                                JOIN academic_year ay ON ay.id=d.year_id
                                WHERE ay.admin_id=%s AND COALESCE(d.home_room,'') <> ''""", (admin_id,)):
            r_ = room_by_no.get(d_["home_room"].strip().lower())
            if r_ and r_["room_type"] == "classroom":
                home_room_by_div[d_["id"]] = r_
            else:
                home_problems.append((d_["name"], f"Assigned classroom '{d_['home_room']}' is not a classroom in step 06, "
                                                  f"so this division's lectures were placed in any free classroom"))

        fac_rows = q_all(cur, "SELECT id, name, max_periods_per_day FROM faculty WHERE admin_id=%s", (admin_id,))
        fac_names = {f["id"]: f["name"] for f in fac_rows}
        faculty_max = {f["id"]: (f["max_periods_per_day"] or 6) for f in fac_rows}

        session_groups = build_sessions(cur, admin_id)
        unpaired_groups = list(session_groups)
        session_groups = pair_batch_sessions(session_groups)
        week_periods = len(teach_periods) * len(working_days)
        whole_hours, batch_hours = defaultdict(int), defaultdict(int)
        div_names, batch_names = {}, {}
        for grp in unpaired_groups:
            for s in grp:
                div_names[s.division["id"]] = s.division["name"]
                if s.batches:
                    for b in s.batches:
                        batch_hours[(s.division["id"], b["id"])] += s.length
                        batch_names[b["id"]] = b["name"]
                else:
                    whole_hours[s.division["id"]] += s.length
        hours_msgs = []
        for did, dname in div_names.items():
            totals = {bid: whole_hours[did] + hrs
                      for (d, bid), hrs in batch_hours.items() if d == did}
            if not totals:
                totals = {None: whole_hours[did]}
            for bid, tot in totals.items():
                if tot == week_periods:
                    continue
                who = dname + (" · " + batch_names[bid] if bid else "")
                if tot < week_periods:
                    msg = f"Only {tot} of {week_periods} periods have a subject. Add {week_periods - tot} more hours"
                else:
                    msg = f"{tot} hours entered but only {week_periods} periods exist. Remove {tot - week_periods} hours"
                hours_msgs.append((who, msg))
        fac_load = defaultdict(int)
        for grp in session_groups:
            for s in grp:
                for fid in s.faculty_ids:
                    fac_load[fid] += s.length
        session_groups.sort(key=lambda grp: (min(s.score() for s in grp),
                                             -max(fac_load[fid] for s in grp for fid in s.faculty_ids)))

        faculty_busy, room_busy, division_busy, batch_busy = set(), set(), set(), set()
        # A whole-division session blocks every batch in that division. Batch
        # sessions, however, may run in parallel when they use different
        # batches and different rooms (for example A1&A2 in LAB-1 and
        # A3&A4 in LAB-2 at the same day/time).
        division_whole_busy = set()
        division_batch_ids = defaultdict(set)
        for b in q_all(cur, "SELECT id, division_id FROM batch WHERE division_id IN (SELECT d.id FROM division d JOIN academic_year ay ON ay.id=d.year_id WHERE ay.admin_id=%s)", (admin_id,)):
            division_batch_ids[b["division_id"]].add(b["id"])
        faculty_day_count = defaultdict(int)
        faculty_lecture_periods = defaultdict(set)
        # (division, day, subject) -> periods holding a single lecture of that subject
        div_subject_periods = defaultdict(set)
        placed, unplaced, insert_rows = 0, [], []
        lecture_count = defaultdict(int)
        day_subject = defaultdict(int)
        batch_placed = defaultdict(int)
        batch_day = set()
        EXTRA_ALLOWED = 0   # 0 = exact weekly count (some slots may stay empty); 1 or 2 = allow extra sessions to fill gaps

        # Lunch position: used by the "before / after lunch" priorities.
        lunch_pos = next((p["index"] for p in periods if p["is_break"] and p.get("kind") == "lunch"), None)
        first_two = {p["index"] for p in teach_periods[:2]}

        def lab_blocked(sess, ps):
            # HARD RULE: a lab never starts in the first two teaching periods.
            return sess.subject["type"] == "lab" and ps[0] in first_two

        def slot_matches(sess, ps):
            """Does this slot satisfy the subject's period priority?"""
            pr = sess.priority
            if pr == "first":
                return ps[0] == teach_periods[0]["index"]
            if pr == "last":
                return ps[-1] == teach_periods[-1]["index"]
            if lunch_pos is None:          # no lunch break configured -> before/after = any time
                return True
            if pr == "before_lunch":
                return ps[-1] < lunch_pos
            if pr == "after_lunch":
                return ps[0] > lunch_pos
            return True

        def priority_ok(sess, ps):
            if lab_blocked(sess, ps):
                return False
            if not teach_periods or sess.priority == "any":
                return True
            return slot_matches(sess, ps)

        def division_or_batch_free(sess, day, p_indices):
            did = sess.division["id"]
            batch_ids = {b["id"] for b in sess.batches}

            for pi in p_indices:
                # A whole-division lecture/tutorial conflicts with every
                # batch in that division.
                if (did, day, pi) in division_whole_busy:
                    return False

                if not batch_ids:
                    # This session uses the complete division, so it cannot
                    # share the slot with an already scheduled batch session.
                    if any((bid, day, pi) in batch_busy for bid in division_batch_ids.get(did, ())):
                        return False
                else:
                    # Batch-based sessions can share the same timetable cell
                    # when their batches are disjoint.
                    if any((bid, day, pi) in batch_busy for bid in batch_ids):
                        return False
            return True

        def adjacent_same_subject(sess, day, p_indices):
            """True if this single lecture would sit directly next to another
            lecture of the same subject for the same division."""
            if sess.length != 1 or sess.batches:
                return False
            existing = div_subject_periods.get((sess.division["id"], day, sess.subject["id"]), set())
            pi = p_indices[0]
            return (pi - 1) in existing or (pi + 1) in existing

        def faculty_free(fid, day, p_indices, is_lecture):
            for pi in p_indices:
                if (fid, day, pi) in faculty_busy:
                    return False
            if faculty_day_count[(fid, day)] + len(p_indices) > faculty_max.get(fid, 6):
                return False
            if is_lecture:
                existing = faculty_lecture_periods.get((fid, day), set())
                for pi in p_indices:
                    if (pi - 1) in existing or (pi + 1) in existing:
                        return False
            return True

        def room_free(room, day, p_indices):
            return all((room["id"], day, pi) not in room_busy for pi in p_indices)

        # Lab spreading: try to give every division lab sessions on at least
        # LAB_DAYS_TARGET different days (4 of 5, or 4 of 6 working days).
        LAB_DAYS_TARGET = min(4, len(working_days))
        lab_days = defaultdict(set)      # division id -> days that have a lab
        lab_slots = defaultdict(set)     # division id -> distinct (day, first period) lab slots

        # ---- Same classroom for back-to-back lectures --------------------------------
        # If a division has 2 or 3 classroom periods in a row (no break between them) they all
        # stay in ONE room, so nobody walks to another room between consecutive lectures.
        # (A lab/batch session uses its own lab room, so it is not part of this rule.)
        div_room_at = {}                      # (division, day, period) -> room id of that whole-division class
        div_day_rooms = defaultdict(list)     # (division, day) -> rooms already used that day, in order

        def neighbour_room(sess, day, p_indices):
            """-> (room id the neighbouring period already uses or None, ok). ok=False when the periods
            on either side use two different rooms, so no single room could join them."""
            if sess.room_type != "classroom" or sess.batches:
                return None, True
            did = sess.division["id"]
            near = {div_room_at.get((did, day, p_indices[0] - 1)), div_room_at.get((did, day, p_indices[-1] + 1))}
            near.discard(None)
            if len(near) > 1:
                return None, False
            return (next(iter(near)) if near else None), True

        def pick_room(sess, day, p_indices, pool, exclude=()):
            home = home_room_by_div.get(sess.division["id"]) if sess.room_type == "classroom" else None
            if home is not None:
                if not sess.batches:
                    # the division's lectures ALWAYS use its assigned classroom (no capacity juggling)
                    return home if (home["id"] not in exclude and room_free(home, day, p_indices)) else None
                # batch tutorials: assigned classroom first; another classroom only if that one is taken
                cands = [r for r in pool if r["id"] not in exclude and r["capacity"] >= sess.capacity_needed
                         and room_free(r, day, p_indices)]
                cands.sort(key=lambda r: 0 if r["id"] == home["id"] else 1)
                return cands[0] if cands else None
            req, ok = neighbour_room(sess, day, p_indices)
            if not ok:
                return None
            cands = [r for r in pool if r["id"] not in exclude and r["capacity"] >= sess.capacity_needed
                     and room_free(r, day, p_indices)]
            if req is not None:                      # next to another lecture: must be that same room
                return next((r for r in cands if r["id"] == req), None)
            used = div_day_rooms.get((sess.division["id"], day), ())
            if used and sess.room_type == "classroom" and not sess.batches:
                cands.sort(key=lambda r: 0 if r["id"] in used else 1)   # stay in the day's room when possible
            return cands[0] if cands else None

        def commit(sess, day, p_indices, room):
            if sess.room_type == "classroom" and not sess.batches:
                for pi in p_indices:
                    div_room_at[(sess.division["id"], day, pi)] = room["id"]
                if room["id"] not in div_day_rooms[(sess.division["id"], day)]:
                    div_day_rooms[(sess.division["id"], day)].append(room["id"])
            if sess.subject["type"] == "lab":
                lab_days[sess.division["id"]].add(day)
                lab_slots[sess.division["id"]].add((day, p_indices[0]))
            for pi in p_indices:
                did = sess.division["id"]
                if sess.batches:
                    for b in sess.batches:
                        batch_busy.add((b["id"], day, pi))
                else:
                    division_busy.add((did, day, pi))
                    division_whole_busy.add((did, day, pi))
                room_busy.add((room["id"], day, pi))
                for fid in sess.faculty_ids:
                    faculty_busy.add((fid, day, pi))
            for fid in sess.faculty_ids:
                faculty_day_count[(fid, day)] += sess.length
                if sess.length == 1:
                    faculty_lecture_periods[(fid, day)].add(p_indices[0])
            if sess.length == 1 and not sess.batches:
                lecture_count[(sess.division["id"], sess.subject["id"])] += 1
                day_subject[(sess.division["id"], day, sess.subject["id"])] += 1
                div_subject_periods[(sess.division["id"], day, sess.subject["id"])].add(p_indices[0])
            if sess.batches:
                bkey = (sess.subject["id"], tuple(sess.faculty_ids), tuple(sorted(b["id"] for b in sess.batches)))
                batch_placed[bkey] += 1
                batch_day.add((day, bkey))       
                    
            # One row per (period x batch) so a merged-batch lab shows up
            # correctly for every batch involved; "whole division" sessions
            # (no batches) still get exactly one row per period, as before.
            for pi in p_indices:
                targets = sess.batches if sess.batches else [None]
                for b in targets:
                    insert_rows.append((
                        admin_id, day, pi, sess.subject["id"], sess.division["id"],
                        b["id"] if b else None, room["id"],
                        sess.faculty_ids, sess.group_key, sess.subject["type"],
                    ))

        early_idx = set()
        for p in periods:
                    if p["is_break"]:
                        break
                    early_idx.add(p["index"])
        else:
                    early_idx = set()
        def candidate_slots_base(sess):
            days = [d for d in sess.fixed_days if d in working_days] if sess.fixed_days else working_days
            slots = []
            for day in days:
                if sess.length == 1:
                    for p in teach_periods:
                        slots.append((day, [p["index"]]))
                else:
                    for i in range(len(teach_periods) - 1):
                        a, b = teach_periods[i]["index"], teach_periods[i + 1]["index"]
                        if b == a + 1:
                            slots.append((day, [a, b]))
            if not teach_periods:
                return slots
            slots = [x for x in slots if not lab_blocked(sess, x[1])]
            if sess.priority == "any":
                if sess.subject["type"] == "lab" and early_idx:
                    late = [x for x in slots if x[1][0] not in early_idx]
                    early = [x for x in slots if x[1][0] in early_idx]
                    return late + early
                return slots
            preferred = [x for x in slots if slot_matches(sess, x[1])]
            return preferred + [x for x in slots if x not in preferred]

        def candidate_slots(sess):
            slots = candidate_slots_base(sess)
            if sess.subject["type"] == "lab":
                used = lab_days[sess.division["id"]]
                if len(used) < LAB_DAYS_TARGET:
                    # not enough lab days yet -> days without a lab come first
                    slots = [x for x in slots if x[0] not in used] + [x for x in slots if x[0] in used]
            return slots

        def try_place(group, relaxed=False):
            nonlocal placed
            for day, p_indices in candidate_slots(group[0]):
                all_fit, chosen = True, {}
                for sess in group:                   
                    if sess.length == 1 and not sess.batches:
                        used_today = day_subject[(sess.division["id"], day, sess.subject["id"])]
                        if used_today >= sess.max_per_day or (used_today >= 1 and not relaxed):
                            all_fit = False
                            break
                    if adjacent_same_subject(sess, day, p_indices):
                        all_fit = False      # no same subject in consecutive periods
                        break
                    if not division_or_batch_free(sess, day, p_indices):
                        all_fit = False
                        break
                    is_lecture = sess.length == 1 and not relaxed
                    if not all(faculty_free(fid, day, p_indices, is_lecture) for fid in sess.faculty_ids):
                        all_fit = False
                        break
                    pool = rooms_by_type.get(sess.room_type, [])
                    taken = {r["id"] for r in chosen.values()}
                    room = pick_room(sess, day, p_indices, pool, taken)
                    if not room:
                        all_fit = False
                        break
                    chosen[id(sess)] = room
                if not all_fit:
                    continue
                for sess in group:
                    commit(sess, day, p_indices, chosen[id(sess)])
                placed += len(group)
                return True
            return False

        failed = [g for g in session_groups if not try_place(g)]
        split = []
        for g in failed:
            if len(g) > 1 and not g[0].combine_divisions:
                split.extend([[s] for s in g])
            else:
                split.append(g)
        failed = [g for g in split if not try_place(g)]
        still_failed = [g for g in failed if not try_place(g, relaxed=True)]
        # ---- Pass 3: fill fully empty division periods with extra lectures
        div_templates = defaultdict(dict)
        
        for grp in unpaired_groups:
             s0 = grp[0]
             if len(grp) == 1 and s0.length == 1 and not s0.batches and not s0.combine_divisions:
                div_templates[s0.division["id"]].setdefault((s0.subject["id"], tuple(s0.faculty_ids)), s0)
        filled = 0
        for strict in (True, False):
            for did, tmpls in div_templates.items():
                for day in working_days:
                    for p in teach_periods:
                        pi = p["index"]
                        order = sorted(tmpls.values(),
                                       key=lambda t: lecture_count[(did, t.subject["id"])])
                        for t in order:
                            if not division_or_batch_free(t, day, [pi]):
                                break
                            if adjacent_same_subject(t, day, [pi]):
                                continue
                            if lecture_count[(did, t.subject["id"])] >= t.subject["lectures_per_week"] + EXTRA_ALLOWED:
                                continue
                            if day_subject[(did, day, t.subject["id"])] >= t.max_per_day:
                                continue
                            if strict and day_subject[(did, day, t.subject["id"])] >= 1:
                                continue
                            if not priority_ok(t, [pi]):
                                continue
                            if not all(faculty_free(fid, day, [pi], False) for fid in t.faculty_ids):
                                continue
                            room = pick_room(t, day, [pi], rooms_by_type.get("classroom", []))
                            if not room:
                                continue
                            commit(Session(t.subject, t.division, [], t.faculty_ids), day, [pi], room)
                            filled += 1
                            break

        # ---- Pass 4: fill gaps where some batches are free (e.g. A1,A2 in lab, A3,A4 free)
        batch_templates = defaultdict(dict)
        for grp in unpaired_groups:
            s0 = grp[0]
            if len(grp) == 1 and s0.batches and not s0.combine_divisions:
                key = (s0.subject["id"], tuple(s0.faculty_ids), tuple(sorted(b["id"] for b in s0.batches)))
                batch_templates[s0.division["id"]].setdefault(key, s0)

        
        gap_filled = 0
        for did, tmpls in batch_templates.items():
            for day in working_days:
                for i, p in enumerate(teach_periods):
                    order = sorted(tmpls.items(), key=lambda kv: batch_placed[kv[0]])
                    for key, t in order:
                        if t.length == 1:
                            ps = [p["index"]]
                        else:
                            if i + 1 >= len(teach_periods) or teach_periods[i + 1]["index"] != p["index"] + 1:
                                continue
                            ps = [p["index"], teach_periods[i + 1]["index"]]
                        if not division_or_batch_free(t, day, ps):
                            continue
                        if batch_placed[key] >= t.subject["lectures_per_week"] + EXTRA_ALLOWED:
                            continue
                        if (day, key) in batch_day:
                            continue
                        if not priority_ok(t, ps):
                            continue
                        if not all(faculty_free(fid, day, ps, False) for fid in t.faculty_ids):
                            continue
                        pool = rooms_by_type.get(t.room_type, [])
                        room = next((r for r in pool if r["capacity"] >= t.capacity_needed
                                     and room_free(r, day, ps)), None)
                        if not room:
                            continue
                        commit(Session(t.subject, t.division, t.batches, t.faculty_ids), day, ps, room)
                        
                        gap_filled += 1
                        break

        placed += filled + gap_filled

        # ---- Explain every subject that could not be placed
        def explain(sess):
            slots = candidate_slots(sess)
            if not slots and sess.subject["type"] == "lab":
                return "Labs are not allowed in the first two periods, and no other 2-period slot exists (check fixed days / break timings)"
            div_ok = [(d, ps) for d, ps in slots if division_or_batch_free(sess, d, ps)]
            if not div_ok:
                return "The division/batch timetable is already full (no free period left)"
            if all(adjacent_same_subject(sess, d, ps) for d, ps in div_ok):
                return "Every free period would put this subject back-to-back with itself. Swap another lecture or raise this subject's max per day spacing"
            fac_ok = [(d, ps) for d, ps in div_ok
                      if all(faculty_free(fid, d, ps, False) for fid in sess.faculty_ids)]
            if not fac_ok:
                names = ", ".join(fac_names.get(fid, str(fid)) for fid in sess.faculty_ids)
                return f"Faculty ({names}) is busy or at the daily limit in every free period of this division"
            home = home_room_by_div.get(sess.division["id"]) if sess.room_type == "classroom" and not sess.batches else None
            if home is not None and not any(room_free(home, d, ps) for d, ps in fac_ok):
                return (f"Assigned classroom {home['room_no']} is already used by another class in every period "
                        f"where this division and its faculty are free")
            pool = rooms_by_type.get(sess.room_type, [])
            if not any(r["capacity"] >= sess.capacity_needed for r in pool):
                return f"No {sess.room_type} with enough capacity ({sess.capacity_needed} seats)"
            plain = [(d, ps) for d, ps in fac_ok
                     if any(r["capacity"] >= sess.capacity_needed and room_free(r, d, ps) for r in pool)]
            if plain and sess.room_type == "classroom":
                return ("Back-to-back lectures of a division must stay in one classroom, and the room used by the "
                        "neighbouring period is taken in every remaining slot. Add another classroom or free that slot")
            return "No free room when both the division and faculty are free"

        for group in still_failed:
            for sess in group:
                unplaced.append({
                    "subject": sess.subject["name"], "division": sess.division["name"],
                    "reason": explain(sess),
                })
        for who, msg in home_problems:
            unplaced.append({"subject": "ASSIGNED CLASSROOM", "division": who, "reason": msg})
        for who, msg in hours_msgs:
            unplaced.append({"subject": "HOURS CHECK", "division": who, "reason": msg})
        for did in sorted(lab_slots):
            if len(lab_days[did]) >= LAB_DAYS_TARGET:
                continue
            n_slots = len(lab_slots[did])
            if n_slots < LAB_DAYS_TARGET:
                why = (f"Only {n_slots} lab session slot(s) per week, so labs can't reach {LAB_DAYS_TARGET} days. "
                       f"Add lab sessions/week or merge fewer batches")
            else:
                why = (f"Labs fall on {len(lab_days[did])} days; {LAB_DAYS_TARGET} wanted but faculty/room/fixed-day "
                       f"clashes prevented a wider spread")
            unplaced.append({"subject": "LAB DAYS CHECK", "division": div_names.get(did, str(did)), "reason": why})
        cur.execute("DELETE FROM timetable_entry WHERE admin_id=%s", (admin_id,))
        cur.execute("DELETE FROM schedule_run WHERE admin_id=%s", (admin_id,))
        if insert_rows:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO timetable_entry
                   (admin_id, day, period_index, subject_id, division_id, batch_id,
                    classroom_id, faculty_ids, session_group_id, entry_type) VALUES %s""",
                insert_rows,
            )
        cur.execute(
            "INSERT INTO schedule_run (admin_id, placed_count, unplaced) VALUES (%s,%s,%s)",
            (admin_id, placed, psycopg2.extras.Json(unplaced)),
        )
        conn.commit()
        return {"placed": placed, "filled": filled + gap_filled, "unplaced": unplaced}
@app.route("/api/timetable/generate", methods=["POST"])
@require_auth
def api_generate():
    result = generate_timetable(session["admin_id"])
    with get_conn() as conn, conn.cursor() as cur:
        notify(cur, session["admin_id"], "The administrator regenerated the timetable. Please review your schedule.")
        conn.commit()
    return jsonify(result)


def _grid_payload(admin_id, extra_where="", extra_params=()):
    with get_conn() as conn, conn.cursor() as cur:
        inst = q_one(cur, "SELECT * FROM institute WHERE admin_id=%s", (admin_id,))
        breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s", (inst["id"],)) if inst else []
        periods = build_period_grid(inst, breaks) if inst else []
        working_days = [d for d in (inst["working_days"] if inst else [])
                         if d not in ((inst["holidays"] if inst else []) or [])]
        fac_lookup = {f["id"]: f["name"] for f in q_all(cur, "SELECT id, name FROM faculty WHERE admin_id=%s", (admin_id,))}

        sql = """SELECT te.*, sub.code AS subject_code, sub.name AS subject_name,
                         d.name AS division_name, b.name AS batch_name, c.room_no
                  FROM timetable_entry te
                  LEFT JOIN subject sub ON sub.id = te.subject_id
                  LEFT JOIN division d ON d.id = te.division_id
                  LEFT JOIN batch b ON b.id = te.batch_id
                  LEFT JOIN classroom c ON c.id = te.classroom_id
                  WHERE te.admin_id=%s """ + extra_where
        rows = q_all(cur, sql, (admin_id,) + extra_params)
        last_run = q_one(cur, "SELECT * FROM schedule_run WHERE admin_id=%s ORDER BY id DESC LIMIT 1", (admin_id,))

    entries = defaultdict(list)
    for r in rows:
        r["faculty_names"] = [fac_lookup.get(fid, "?") for fid in (r["faculty_ids"] or [])]
        entries[f'{r["day"]}|{r["period_index"]}'].append(r)
    return {"periods": periods, "working_days": working_days, "entries": entries, "last_run": last_run}


@app.route("/api/timetable/master")
@require_auth
def timetable_master():
    return jsonify(_grid_payload(session["admin_id"]))


@app.route("/api/timetable/class/<int:division_id>")
@require_auth
def timetable_class(division_id):
    return jsonify(_grid_payload(session["admin_id"], "AND te.division_id=%s", (division_id,)))


@app.route("/api/timetable/classroom/<int:room_id>")
@require_auth
def timetable_classroom(room_id):
    return jsonify(_grid_payload(session["admin_id"], "AND te.classroom_id=%s", (room_id,)))


@app.route("/api/timetable/faculty/<int:faculty_id>")
@require_auth
def timetable_faculty(faculty_id):
    return jsonify(_grid_payload(session["admin_id"], "AND %s = ANY(te.faculty_ids)", (faculty_id,)))


# ============================================================ MANUAL TIMETABLE EDITING
# Lets the admin click a slot in the grid and place a session by hand (e.g. one
# the generator could not place), or remove a session. Same clash rules as the
# generator: division/batch, faculty and room must all be free.
@app.route("/api/timetable/options")
@require_auth
def timetable_options():
    """Sessions that can be placed for one division, with how many are already placed."""
    aid = session["admin_id"]
    division_id = int(request.args["division_id"])
    with get_conn() as conn, conn.cursor() as cur:
        allocs = q_all(
            cur,
            """SELECT fa.subject_id, fa.faculty_id, fa.batch_ids, f.name AS faculty_name,
                      s.code, s.name, s.type, s.lectures_per_week
               FROM faculty_allocation fa
               JOIN subject s ON s.id = fa.subject_id
               JOIN faculty f ON f.id = fa.faculty_id
               JOIN division d ON d.id = fa.division_id
               JOIN academic_year ay ON ay.id = d.year_id
               WHERE fa.division_id=%s AND ay.admin_id=%s
               ORDER BY s.code, fa.id""",
            (division_id, aid),
        )
        rows = q_all(
            cur,
            "SELECT session_group_id, subject_id, batch_id FROM timetable_entry WHERE admin_id=%s AND division_id=%s",
            (aid, division_id),
        )
    groups = defaultdict(set)                       # session_group_id -> (subject_id, {batch ids})
    for r in rows:
        g = groups[r["session_group_id"]]
        g.add(r["subject_id"])
        if r["batch_id"]:
            g.add(("b", r["batch_id"]))
    placed = defaultdict(int)
    for g in groups.values():
        sid = next(x for x in g if not isinstance(x, tuple))
        bs = tuple(sorted(x[1] for x in g if isinstance(x, tuple)))
        placed[(sid, bs)] += 1

    opts = {}
    for a in allocs:
        bs = tuple(sorted(a["batch_ids"] or []))
        key = (a["subject_id"], bs)
        o = opts.setdefault(key, {
            "subject_id": a["subject_id"], "code": a["code"], "name": a["name"], "type": a["type"],
            "per_week": a["lectures_per_week"], "batch_ids": list(bs),
            "faculty_ids": [], "faculty_names": [], "placed": placed.get(key, 0),
        })
        o["faculty_ids"].append(a["faculty_id"])
        o["faculty_names"].append(a["faculty_name"])
    out = sorted(opts.values(), key=lambda o: (o["placed"] - (o["per_week"] or 0), o["code"]))
    return jsonify({"options": out})


@app.route("/api/timetable/entry", methods=["POST"])
@require_auth
def add_manual_entry():
    aid = session["admin_id"]
    d = request.get_json(force=True)
    day = d.get("day")
    pi = int(d.get("period_index"))
    division_id = int(d.get("division_id"))
    subject_id = int(d.get("subject_id"))
    room_id = int(d.get("classroom_id"))
    faculty_ids = [int(x) for x in (d.get("faculty_ids") or [])]
    batch_ids = [int(x) for x in (d.get("batch_ids") or [])]
    if not faculty_ids:
        return jsonify({"error": "This session has no faculty allocated."}), 400

    with get_conn() as conn, conn.cursor() as cur:
        inst = q_one(cur, "SELECT * FROM institute WHERE admin_id=%s", (aid,))
        if not inst:
            return jsonify({"error": "Institute hours not configured."}), 400
        breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s", (inst["id"],))
        by_idx = {p["index"]: p for p in build_period_grid(inst, breaks)}

        sub = q_one(cur, """SELECT s.* FROM subject s JOIN academic_year ay ON ay.id=s.year_id
                            WHERE s.id=%s AND ay.admin_id=%s""", (subject_id, aid))
        room = q_one(cur, "SELECT * FROM classroom WHERE id=%s AND admin_id=%s", (room_id, aid))
        div = q_one(cur, """SELECT dv.* FROM division dv JOIN academic_year ay ON ay.id=dv.year_id
                            WHERE dv.id=%s AND ay.admin_id=%s""", (division_id, aid))
        if not (sub and room and div):
            return jsonify({"error": "Subject, room or division not found."}), 404

        length = 2 if sub["type"] in ("lab", "tutorial") else 1
        p_indices = [pi + i for i in range(length)]
        for i in p_indices:
            p = by_idx.get(i)
            if not p or p["is_break"]:
                return jsonify({"error": f"A {sub['type']} needs {length} back-to-back teaching periods starting here "
                                         f"- the next period is a break or past the end of the day."}), 409

        for i in p_indices:
            p = by_idx[i]
            when = f"{day} {_hhmm(p['start'])}-{_hhmm(p['end'])}"
            if batch_ids:
                cur.execute("""SELECT 1 FROM timetable_entry WHERE admin_id=%s AND day=%s AND period_index=%s
                               AND division_id=%s AND (batch_id IS NULL OR batch_id = ANY(%s)) LIMIT 1""",
                            (aid, day, i, division_id, batch_ids))
            else:
                cur.execute("""SELECT 1 FROM timetable_entry WHERE admin_id=%s AND day=%s AND period_index=%s
                               AND division_id=%s LIMIT 1""", (aid, day, i, division_id))
            if cur.fetchone():
                return jsonify({"error": f"{div['name']} (or one of its batches) already has a session at {when}."}), 409
            cur.execute("""SELECT 1 FROM timetable_entry WHERE admin_id=%s AND day=%s AND period_index=%s
                           AND faculty_ids && %s::int[] LIMIT 1""", (aid, day, i, faculty_ids))
            if cur.fetchone():
                return jsonify({"error": f"The faculty is already teaching another class at {when}."}), 409
            cur.execute("""SELECT 1 FROM timetable_entry WHERE admin_id=%s AND day=%s AND period_index=%s
                           AND classroom_id=%s LIMIT 1""", (aid, day, i, room_id))
            if cur.fetchone():
                return jsonify({"error": f"Room {room['room_no']} is already in use at {when}."}), 409

        group = str(uuid.uuid4())[:8]
        rows = [(aid, day, i, subject_id, division_id, b, room_id, faculty_ids, group, sub["type"])
                for i in p_indices for b in (batch_ids or [None])]
        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO timetable_entry
               (admin_id, day, period_index, subject_id, division_id, batch_id,
                classroom_id, faculty_ids, session_group_id, entry_type) VALUES %s""",
            rows,
        )
        # keep the "unplaced" summary in step: drop one matching item, bump the placed count
        lr = q_one(cur, "SELECT id, unplaced FROM schedule_run WHERE admin_id=%s ORDER BY id DESC LIMIT 1", (aid,))
        if lr:
            items = list(lr["unplaced"] or [])
            for k, u in enumerate(items):
                if u.get("subject") == sub["name"] and u.get("division") == div["name"]:
                    items.pop(k)
                    break
            cur.execute("UPDATE schedule_run SET placed_count = placed_count + %s, unplaced = %s WHERE id = %s",
                        (length, psycopg2.extras.Json(items), lr["id"]))
        notify(cur, aid,
               f"{sub['code']} ({sub['name']}) for {div['name']} was added on "
               f"{day} at {_hhmm(by_idx[pi]['start'])} in room {room['room_no']}.",
               [division_id], faculty_ids)
        conn.commit()
    return jsonify({"ok": True, "periods": length})


@app.route("/api/timetable/session/<group_id>", methods=["DELETE"])
@require_auth
def delete_manual_session(group_id):
    """Removes a whole session (every period and batch row that shares the group id)."""
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        gone = q_all(cur, """SELECT te.day, te.period_index, te.division_id, te.faculty_ids,
                                    sub.code, d.name AS division_name
                             FROM timetable_entry te
                             LEFT JOIN subject sub ON sub.id = te.subject_id
                             LEFT JOIN division d ON d.id = te.division_id
                             WHERE te.admin_id=%s AND te.session_group_id=%s
                             ORDER BY te.period_index""", (aid, group_id))
        cur.execute("DELETE FROM timetable_entry WHERE admin_id=%s AND session_group_id=%s",
                    (aid, group_id))
        n = cur.rowcount
        if gone:
            first = gone[0]
            inst = q_one(cur, "SELECT * FROM institute WHERE admin_id=%s", (aid,))
            at = ""
            if inst:
                breaks = q_all(cur, "SELECT * FROM break_slot WHERE institute_id=%s", (inst["id"],))
                p = {x["index"]: x for x in build_period_grid(inst, breaks)}.get(first["period_index"])
                if p:
                    at = f" at {_hhmm(p['start'])}"
            facs = sorted({f for r in gone for f in (r["faculty_ids"] or [])})
            divs = sorted({r["division_id"] for r in gone if r["division_id"]})
            notify(cur, aid, f"{first['code']} for {first['division_name']} on {first['day']}{at} was removed from the timetable.",
                   divs, facs)
        conn.commit()
    return jsonify({"ok": True, "removed_rows": n})


# ============================================================ EDITABLE PDF DOWNLOAD
# Builds a fillable (AcroForm) PDF laid out like the college's printed timetable:
# 3-line centred heading, class strip, day columns, time rows, merged break rows,
# 2-period labs merged vertically, HOD block at the bottom right. Every cell,
# heading and footer line is a text field, so it can be edited in any PDF reader
# (Adobe Reader, Edge, Chrome, Foxit...) and saved again.
# The scheduler itself is NOT touched — this only reads timetable_entry.
DEFAULT_PDF_TEMPLATE = {
    "college": "K.I.T's College of Engineering (Autonomous), Kolhapur",
    "department": "Department of CSE-AIML and CSE-DS",
    "title": "Time Table A.Y. 2026-27 Odd Semester",
    "hod_name": "Dr. Uma Gurav",
    "hod_dept": "CSE-AIML & CSE-DS",
}


def _faculty_initials(name):
    skip = {"mr", "mrs", "ms", "dr", "prof", "smt", "shri"}
    parts = [p for p in re.split(r"[\s.]+", name or "") if p and p.lower() not in skip]
    return "".join(p[0].upper() for p in parts) or (name or "?")


def _cell_text(rows, view):
    """Text for one day/period cell, e.g. 'DBMS (AK)\\nC3,C4\\nLAB 3'."""
    groups = {}
    for r in rows:
        key = (r.get("subject_code") or "", tuple(r.get("faculty_names") or []),
               r.get("room_no") or "", r.get("entry_type") or "lecture", r.get("division_name") or "")
        g = groups.setdefault(key, [])
        if r.get("batch_name"):
            g.append(r["batch_name"])
    blocks = []
    for (code, facs, room, etype, div), batches in sorted(groups.items(), key=lambda kv: str(kv[0])):
        inits = ",".join(_faculty_initials(f) for f in facs)
        head = f"{code} ({inits})" if inits else code
        if view != "class" and div:
            head = f"{div}: {head}"
        lines = [head]
        if batches:
            lines.append(",".join(sorted(set(batches))))
        if room and (etype in ("lab", "tutorial") or view != "class"):
            lines.append(room)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def build_timetable_pdf(payload, view, class_line, tpl):
    periods, days, entries = payload["periods"], payload["working_days"], payload["entries"]
    W, H = landscape(A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(tpl["title"])
    form = c.acroForm
    counter = [0]
    BLACK, WHITE = rl_colors.black, rl_colors.white
    GREY, HEAD = rl_colors.Color(.88, .88, .88), rl_colors.Color(.93, .93, .93)

    def field(x, y, w, h, value, size=8, border=True, bold=False, fill=WHITE, multiline=True):
        counter[0] += 1
        form.textfield(
            name=f"f{counter[0]}", value=value or "", x=x, y=y, width=w, height=h,
            fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size,
            borderWidth=0.7 if border else 0, borderColor=BLACK if border else WHITE,
            fillColor=fill, textColor=BLACK, forceBorder=border,
            fieldFlags="multiline" if multiline else "",
        )

    def centred(y, text, size, bold=True):
        font = "Helvetica-Bold" if bold else "Helvetica"
        w = min(pdfmetrics.stringWidth(text, font, size) + 24 * mm, W - 20 * mm)
        field((W - w) / 2, y, w, size * 1.7, text, size=size, border=False, bold=bold, multiline=False)

    margin = 12 * mm
    y = H - margin
    for text, size in ((tpl["college"], 13), (tpl["department"], 11.5), (tpl["title"], 11.5)):
        y -= size * 1.7 + 1
        centred(y, text, size)
    y -= 3 * mm
    tw = W - 2 * margin
    y -= 8 * mm
    field(margin, y, tw, 8 * mm, class_line, size=10, bold=True, multiline=False)
    y -= 1 * mm

    time_w = 26 * mm
    day_w = (tw - time_w) / max(len(days), 1)
    head_h = 8 * mm
    y -= head_h
    field(margin, y, time_w, head_h, "", fill=HEAD, multiline=False)
    for i, d in enumerate(days):
        field(margin + time_w + i * day_w, y, day_w, head_h, d, size=10, bold=True, fill=HEAD, multiline=False)

    footer_h = 24 * mm
    avail = y - margin - footer_h
    n_t = sum(1 for p in periods if not p["is_break"])
    n_b = len(periods) - n_t
    row_h = min(26 * mm, avail / max(n_t + 0.45 * n_b, 1))
    break_h = max(row_h * 0.45, 6 * mm)
    heights = [break_h if p["is_break"] else row_h for p in periods]

    # texts + which cells are merged with the one below (2-period labs/tutorials)
    size = 7.5 if view == "class" else 6.5
    texts, span = {}, set()
    skip = set()
    for pos, p in enumerate(periods):
        if p["is_break"]:
            continue
        for d in days:
            texts[(d, pos)] = _cell_text(entries.get(f'{d}|{p["index"]}', []), view)
    for pos in range(len(periods) - 1):
        a, b = periods[pos], periods[pos + 1]
        if a["is_break"] or b["is_break"]:
            continue
        for d in days:
            rows = entries.get(f'{d}|{a["index"]}', [])
            if rows and texts[(d, pos)] and texts[(d, pos)] == texts[(d, pos + 1)] \
                    and any(r.get("entry_type") in ("lab", "tutorial") for r in rows) \
                    and (d, pos) not in skip:
                span.add((d, pos))
                skip.add((d, pos + 1))

    top = y
    cursor = top
    for pos, p in enumerate(periods):
        h = heights[pos]
        ybot = cursor - h
        label = f"{_hhmm(p['start'])}-{_hhmm(p['end'])}"
        field(margin, ybot, time_w, h, label, size=8, bold=True, fill=HEAD)
        if p["is_break"]:
            field(margin + time_w, ybot, day_w * len(days), h, p["label"] or "Break",
                  size=9, bold=True, fill=GREY, multiline=False)
        else:
            for i, d in enumerate(days):
                if (d, pos) in skip:
                    continue
                hh = h + (heights[pos + 1] if (d, pos) in span else 0)
                field(margin + time_w + i * day_w, cursor - hh, day_w, hh,
                      texts.get((d, pos), ""), size=size)
        cursor = ybot

    # footer — HOD block bottom right, plus a free note line bottom left
    fw = 70 * mm
    fx = W - margin - fw
    fy = cursor - 5 * mm
    for line, bold in ((tpl["hod_name"], True), ("H.O.D.", True), (tpl["hod_dept"], True)):
        fy -= 5.5 * mm
        field(fx, fy, fw, 5.5 * mm, line, size=10, border=False, bold=bold, multiline=False)
    field(margin, cursor - 5 * mm - 16.5 * mm, 110 * mm, 5.5 * mm, "", size=9, border=False, multiline=False)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def _load_pdf_template(cur, admin_id):
    row = q_one(cur, """SELECT file_name, college, department, title, hod_name, hod_dept,
                               (file_data IS NOT NULL) AS has_file
                        FROM pdf_template WHERE admin_id=%s""", (admin_id,))
    tpl = dict(DEFAULT_PDF_TEMPLATE)
    if row:
        for k in DEFAULT_PDF_TEMPLATE:
            if row.get(k):
                tpl[k] = row[k]
    return tpl, row


@app.route("/api/pdf-template", methods=["GET", "POST", "DELETE"])
@require_auth
def pdf_template():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "DELETE":
            cur.execute("DELETE FROM pdf_template WHERE admin_id=%s", (aid,))
            conn.commit()
        elif request.method == "POST":
            f = request.form
            file = request.files.get("template")
            fname, fdata = None, None
            if file and file.filename:
                fname, fdata = file.filename, psycopg2.Binary(file.read())
            vals = {k: (f.get(k) or DEFAULT_PDF_TEMPLATE[k]).strip() for k in DEFAULT_PDF_TEMPLATE}
            cur.execute(
                """INSERT INTO pdf_template (admin_id, file_name, file_data, college, department, title, hod_name, hod_dept)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (admin_id) DO UPDATE SET
                     college=EXCLUDED.college, department=EXCLUDED.department, title=EXCLUDED.title,
                     hod_name=EXCLUDED.hod_name, hod_dept=EXCLUDED.hod_dept,
                     file_name=COALESCE(EXCLUDED.file_name, pdf_template.file_name),
                     file_data=COALESCE(EXCLUDED.file_data, pdf_template.file_data),
                     updated_at=now()""",
                (aid, fname, fdata, vals["college"], vals["department"], vals["title"],
                 vals["hod_name"], vals["hod_dept"]),
            )
            conn.commit()
        tpl, row = _load_pdf_template(cur, aid)
    return jsonify({"template": tpl, "saved": bool(row),
                    "file_name": row["file_name"] if row else None,
                    "has_file": bool(row and row["has_file"])})


@app.route("/api/pdf-template/file")
@require_auth
def pdf_template_file():
    with get_conn() as conn, conn.cursor() as cur:
        row = q_one(cur, "SELECT file_name, file_data FROM pdf_template WHERE admin_id=%s", (session["admin_id"],))
    if not row or not row["file_data"]:
        return jsonify({"error": "No template uploaded"}), 404
    return send_file(io.BytesIO(bytes(row["file_data"])), download_name=row["file_name"],
                     mimetype=mimetypes.guess_type(row["file_name"])[0] or "application/octet-stream")


def _timetable_pdf(aid, view, rid, use_template):
    """Builds the editable timetable PDF for one view. Shared by the admin download
    and the student/teacher portals, so everybody gets the SAME layout and the
    heading/signature wording the admin saved (Upload template section).
    Returns (BytesIO, download_name); raises ValueError((message, http_status))."""
    with get_conn() as conn, conn.cursor() as cur:
        tpl, row = _load_pdf_template(cur, aid)
        if use_template == "saved":
            use_template = "template" if row else "default"
        if use_template == "template":
            if not row:
                raise ValueError(("Upload a template / last timetable first.", 400))
        else:
            tpl = dict(DEFAULT_PDF_TEMPLATE)
        if view == "class" and rid:
            d = q_one(cur, """SELECT d.name, d.class_teacher, ay.label, ay.sem_no FROM division d
                              JOIN academic_year ay ON ay.id=d.year_id WHERE d.id=%s AND ay.admin_id=%s""", (rid, aid))
            if not d:
                raise ValueError(("Division not found", 404))
            name = d["name"]
            line = f"{name}   ({d['label']} Sem {d['sem_no']})      Class Teacher: {d['class_teacher'] or ''}"
        elif view in ("classroom", "lab") and rid:
            d = q_one(cur, "SELECT room_no, room_type FROM classroom WHERE id=%s AND admin_id=%s", (rid, aid))
            if not d:
                raise ValueError(("Room not found", 404))
            name = d["room_no"]
            line = f"{'Lab' if d['room_type'] == 'lab' else 'Classroom'}: {name}"
        elif view == "faculty" and rid:
            d = q_one(cur, "SELECT name FROM faculty WHERE id=%s AND admin_id=%s", (rid, aid))
            if not d:
                raise ValueError(("Faculty not found", 404))
            name = d["name"]
            line = f"Faculty: {name}"
        else:
            view, name, line = "master", "Master", "Master Timetable \u2014 all divisions"

    if view == "class":
        payload = _grid_payload(aid, "AND te.division_id=%s", (rid,))
    elif view in ("classroom", "lab"):
        payload = _grid_payload(aid, "AND te.classroom_id=%s", (rid,))
    elif view == "faculty":
        payload = _grid_payload(aid, "AND %s = ANY(te.faculty_ids)", (rid,))
    else:
        payload = _grid_payload(aid)
    if not payload["periods"]:
        raise ValueError(("Set up institute hours and generate a timetable first.", 400))

    buf = build_timetable_pdf(payload, view, line, tpl)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "timetable"
    return buf, f"{safe}_timetable_editable.pdf"


@app.route("/api/timetable/download.pdf")
@require_auth
def download_timetable_pdf():
    """?view=master|class|classroom|lab|faculty &id=<row id> &source=default|template"""
    try:
        buf, fname = _timetable_pdf(session["admin_id"], request.args.get("view", "master"),
                                    request.args.get("id", type=int), request.args.get("source", "default"))
    except ValueError as e:
        msg, code = e.args[0]
        return jsonify({"error": msg}), code
    return send_file(buf, download_name=fname, as_attachment=True, mimetype="application/pdf")


# ============================================================ FACULTY ALLOCATION IMPORT (Excel / CSV / PDF)
# The admin uploads the college's faculty-load sheet; this reads it and fills in
# faculty (name, subject codes they can teach, max periods per day) AND the
# subject -> faculty -> division/batch allocations, exactly as written in the file.
# Columns are found by their HEADING (any order, many spellings), so the sheet
# needs no special layout. Recognised headings:
#   faculty  : Faculty / Faculty Name / Teacher / Staff / Name ...
#   code     : Course Code / Subject Code / Code ...              (several codes per cell are fine)
#   name     : Course Name / Subject Name / Subject / Title       (used if a subject must be created)
#   division : Division / Div / Class / Section                   (blank = every division of that year)
#   batch    : Batch / Batches   ("A1, A2" = separate batches, "A1+A2" = one merged session)
#   max      : Max periods per day / Max load per day ...
#   type     : Type (lecture / lab / tutorial)                    lpw: Lectures (periods/hours) per week
# Faculty/max cells left blank under a name (merged cells, or a PDF table that
# repeats a name only once) are carried down from the row above.
_FIELD_ALIASES = {
    "faculty": {"faculty", "facultyname", "nameoffaculty", "teacher", "teachername", "nameofteacher", "staff", "staffname",
                "professor", "facultyallotted", "facultyallocated", "allocatedfaculty", "allottedfaculty", "name",
                "facultymember", "teachingstaff"},
    "code": {"coursecode", "subjectcode", "code", "courseid", "subjectid", "papercode", "subcode", "ccode", "coursecodes",
             "subjectcodes"},
    "sname": {"coursename", "subjectname", "coursetitle", "subjecttitle", "nameofcourse", "nameofsubject", "subject",
              "course", "title", "subjects", "courses"},
    "division": {"division", "div", "divisions", "class", "classdivision", "section", "classname", "divs"},
    "batch": {"batch", "batches", "batchname", "batchno", "batchnames"},
    "type": {"type", "coursetype", "subjecttype", "category", "lecturelab", "lt", "mode"},
    "designation": {"designation", "post", "rank", "position", "cadre"},
    "theory": {"theory", "theoryhours", "theoryhrs", "th", "lecturehours", "lecturehrs"},
    "tutorial": {"tutorial", "tutorials", "tut", "tutorialhours", "tutorialhrs"},
    "practical": {"practical", "practicals", "pr", "practicalhours", "practicalhrs", "labhours", "labhrs"},
    "ndiv": {"noofdivisions", "noofdivision", "nodivisions", "numberofdivisions", "ndivisions"},
}


def _norm_key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _classify_header(cell):
    k = _norm_key(cell)
    if not k:
        return None
    load_words = ("period", "lecture", "hour", "hrs", "load", "lect")
    if ("max" in k and (any(w in k for w in load_words) or "perday" in k)) or \
       (("perday" in k or "daily" in k or k.endswith("day")) and any(w in k for w in load_words)):
        return "max"
    if ("week" in k or "pw" in k) and any(w in k for w in load_words):
        return "lpw"
    if "code" in k and ("name" in k or "title" in k):      # "Course Name (Course Code)": one cell holds both
        return "combo"
    for field, names in _FIELD_ALIASES.items():
        if k in names:
            return field
    return None


def _header_mapping(cells):
    m = {}
    for i, c in enumerate(cells):
        f = _classify_header(c)
        if f and f not in m:
            m[f] = i
    if "faculty" in m and ("code" in m or "sname" in m or "combo" in m):
        return m
    return None


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _to_int(s):
    mt = re.search(r"\d+", s or "")
    return int(mt.group()) if mt else None


def _read_upload_blocks(file):
    """Returns a list of row-lists (one per Excel sheet / CSV / run of PDF tables)."""
    name = (file.filename or "").lower()
    data = file.read()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        blocks = []
        for ws in wb.worksheets:
            for rng in list(ws.merged_cells.ranges):          # spread merged values over their whole range
                v = ws.cell(rng.min_row, rng.min_col).value
                ws.unmerge_cells(str(rng))
                for r in range(rng.min_row, rng.max_row + 1):
                    for c in range(rng.min_col, rng.max_col + 1):
                        ws.cell(r, c).value = v
            blocks.append([list(r) for r in ws.iter_rows(values_only=True)])
        return blocks
    if name.endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(file_contents=data)
        return [[sh.row_values(i) for i in range(sh.nrows)] for sh in wb.sheets()]
    if name.endswith((".csv", ".txt")):
        import csv
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [list(csv.reader(io.StringIO(text), dialect))]
    if name.endswith(".pdf"):
        import pdfplumber
        rows, text_lines = [], []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables() or []:
                    rows.extend(tbl)
                text_lines.extend((page.extract_text() or "").splitlines())
        if rows:
            return [rows]
        # no ruled table found: treat each text line as a row, columns split on tabs / pipes / 2+ spaces
        return [[re.split(r"\t|\|\s*|\s{2,}", ln.strip()) for ln in text_lines if ln.strip()]]
    raise ValueError("Upload an Excel (.xlsx), CSV or PDF file.")


def _parse_allocation_blocks(blocks):
    records, columns = [], set()
    for rows in blocks:
        mapping, last_fac, last_max, last_desig = None, "", None, ""
        for rnum, row in enumerate(rows, 1):
            cells = [_cell(c) for c in row]
            if not any(cells):
                continue
            m = _header_mapping(cells)
            if m:
                mapping, last_fac, last_max, last_desig = m, "", None, ""
                columns |= set(m)
                continue
            if not mapping:
                continue

            def get(field):
                i = mapping.get(field)
                return cells[i] if i is not None and i < len(cells) else ""

            fac = re.sub(r"\s+", " ", get("faculty")).strip()
            if fac.lower().startswith(("total", "grand total", "note")):
                continue
            if fac and fac != last_fac:                      # a new person starts here
                last_fac, last_max, last_desig = fac, None, ""
            if not last_fac:
                continue
            mx = _to_int(get("max"))
            if mx:
                last_max = mx
            desig = re.sub(r"\s+", " ", get("designation")).strip()
            if desig:
                last_desig = desig

            sname = re.sub(r"\s+", " ", get("sname") or get("combo")).strip()
            codes = [c.strip() for c in re.split(r"[,;\n&]+", get("code")) if c.strip()]
            if not codes:                                    # "Subject Name (UAMPC0702)" -> code inside the brackets
                mt = re.search(r"\(([^()]*\d[^()]*)\)\s*$", sname)
                if mt:
                    codes = [mt.group(1).strip()]
                    sname = sname[:mt.start()].strip()
            divs = [d.strip() for d in re.split(r"[,;\n&]+", get("division")) if d.strip()]
            class_label = re.sub(r"\s+", " ", get("division")).strip()
            batches = [[b.strip() for b in grp.split("+") if b.strip()]
                       for grp in re.split(r"[,;\n&]+", get("batch")) if grp.strip()]

            th, tu, pr = _to_int(get("theory")), _to_int(get("tutorial")), _to_int(get("practical"))
            htype, hlpw = "", None                           # hours columns decide lecture / lab / tutorial
            if th:
                htype, hlpw = "lecture", th
            elif pr:
                htype, hlpw = "lab", max(1, -(-pr // 2))     # a lab session = 2 periods
            elif tu:
                htype, hlpw = "tutorial", max(1, -(-tu // 2))

            if not codes and not sname and not mx and not desig:
                continue
            records.append({"row": rnum, "faculty": last_fac, "codes": codes, "sname": sname, "divisions": divs,
                            "class_label": class_label, "batches": batches, "max": last_max, "designation": last_desig,
                            "type": get("type").lower() or htype, "lpw": _to_int(get("lpw")) or hlpw,
                            "hours": (th, tu, pr) if (th or tu or pr) else None})
    return records, columns


def _norm_person(name):
    n = re.sub(r"[^a-z ]", " ", (name or "").lower())
    n = re.sub(r"\b(dr|prof|professor|mr|mrs|ms|miss|smt|shri)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


_YEAR_PATTERNS = [
    ("LY", r"final|last\s*year|fourth|4th|\bl\.?\s*y\b"),
    ("TY", r"third|3rd|\bt\.?\s*y\b"),
    ("SY", r"second|2nd|\bs\.?\s*y\b"),
    ("FY", r"first|1st|\bf\.?\s*y\b"),
]


def _match_class(text, pool):
    """Maps a free-text class such as 'B.Tech.Final Year(AIML-A)', 'SY(AIML)-B' or 'T.Y(AIML B )' onto the
    divisions the admin created (year label + section letter + optional branch word). Returns a list."""
    t = (text or "").lower()
    year = next((lab for lab, pat in _YEAR_PATTERNS if re.search(pat, t)), None)
    cand = [d for d in pool if year is None or d.get("year_label") == year]
    inside = re.search(r"\(([^)]*)\)", text or "")
    if inside:
        toks = re.findall(r"[A-Za-z0-9]+", inside.group(1) + " " + (text or "")[inside.end():])
    else:
        toks = [x for x in re.findall(r"[A-Za-z0-9]+", text or "")
                if not any(re.fullmatch(p, x.lower()) for _, p in _YEAR_PATTERNS) and x.lower() not in ("year", "btech", "b", "tech")]
    letter = next((x.upper() for x in toks if len(x) == 1 and x.isalpha()), None)
    branches = [x.upper() for x in toks if len(x) > 1]

    def words(d):
        return re.findall(r"[A-Za-z0-9]+", d["name"].upper())
    with_branch = []
    if branches:
        with_branch = [d for d in cand if any(b in words(d) or b in _norm_key(d["name"]).upper() for b in branches)]
        cand = with_branch or cand
    if year is None and not letter and not with_branch:
        return []                      # nothing in the text pointed at a real division (e.g. "ZZ")
    if letter:
        cand = [d for d in cand if letter in words(d) or _norm_key(d["name"]).upper().endswith(letter)]
    return cand


@app.route("/api/faculty-workload")
@require_auth
def faculty_workload():
    """One block per faculty member, laid out like the college's workload sheet:
    Sr. No | Name | Designation | Class | Course (Code) | Theory | Tutorial | Practical | No. of Divisions | Sum.
    Hours are the ones written in the uploaded file; manual allocations fall back to the subject's weekly load."""
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        rows = q_all(cur, """SELECT f.id AS fid, f.name AS fname, f.designation,
                                    s.id AS sid, s.code, s.name AS sname, s.type, s.lectures_per_week,
                                    fa.id AS aid, fa.class_label, fa.theory_hrs, fa.tutorial_hrs, fa.practical_hrs,
                                    d.id AS did, d.name AS division_name
                             FROM faculty f
                             LEFT JOIN faculty_allocation fa ON fa.faculty_id = f.id
                             LEFT JOIN subject s ON s.id = fa.subject_id
                             LEFT JOIN division d ON d.id = fa.division_id
                             WHERE f.admin_id=%s ORDER BY f.id, s.code, fa.id""", (aid,))
    out, by_fac = [], {}
    for r in rows:
        blk = by_fac.get(r["fid"])
        if blk is None:
            blk = by_fac[r["fid"]] = {"faculty_id": r["fid"], "name": r["fname"], "designation": r["designation"],
                                      "rows": [], "_groups": {}, "total": 0}
            out.append(blk)
        if r["aid"] is None:
            continue
        key = (r["sid"], r["class_label"] or "")
        g_ = blk["_groups"].get(key)
        if g_ is None:
            if r["theory_hrs"] is not None or r["tutorial_hrs"] is not None or r["practical_hrs"] is not None:
                th, tu, pr = r["theory_hrs"], r["tutorial_hrs"], r["practical_hrs"]
            else:
                lpw = r["lectures_per_week"] or 0
                th = lpw if r["type"] == "lecture" else None
                tu = lpw * 2 if r["type"] == "tutorial" else None
                pr = lpw * 2 if r["type"] == "lab" else None
            g_ = blk["_groups"][key] = {"class": r["class_label"] or "", "divisions": [], "course": f"{r['sname']} ({r['code']})",
                                        "theory": th, "tutorial": tu, "practical": pr}
            blk["rows"].append(g_)
        if r["division_name"] not in g_["divisions"]:
            g_["divisions"].append(r["division_name"])
    for blk in out:
        for g_ in blk["rows"]:
            g_["class"] = g_["class"] or ", ".join(g_["divisions"])
            g_["n_divisions"] = len(g_["divisions"])
            blk["total"] += (g_["theory"] or 0) + (g_["tutorial"] or 0) + (g_["practical"] or 0)
        del blk["_groups"]
    return jsonify({"faculty": out})


@app.route("/api/faculty-allocation/import", methods=["POST"])
@require_auth
def import_faculty_allocation():
    aid = session["admin_id"]
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose an Excel, CSV or PDF file first."}), 400
    try:
        blocks = _read_upload_blocks(file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except ImportError as e:
        return jsonify({"error": f"A reader library is missing on the server ({e.name}). "
                                 f"Run: pip install openpyxl pdfplumber xlrd"}), 500
    except Exception as e:
        return jsonify({"error": f"Could not read that file ({e})."}), 400

    records, columns = _parse_allocation_blocks(blocks)
    if "faculty" not in columns:
        return jsonify({"error": "Could not find a heading row. The file needs at least a Faculty Name column and a "
                                 "Course/Subject Code (or Subject Name) column."}), 400
    if not records:
        return jsonify({"error": "Found the headings but no data rows under them."}), 400

    rep = {"rows": len(records), "columns": sorted(columns), "faculty_created": [], "faculty_updated": [],
           "subjects_created": [], "subjects_updated": [], "allocations_created": 0, "duplicates": 0, "skipped": []}

    with get_conn() as conn, conn.cursor() as cur:
        divs = q_all(cur, """SELECT dv.id, dv.name, dv.year_id, ay.label AS year_label FROM division dv
                             JOIN academic_year ay ON ay.id=dv.year_id WHERE ay.admin_id=%s""", (aid,))
        batches = q_all(cur, """SELECT b.id, b.name, b.division_id FROM batch b JOIN division dv ON dv.id=b.division_id
                                JOIN academic_year ay ON ay.id=dv.year_id WHERE ay.admin_id=%s""", (aid,))
        subs = q_all(cur, """SELECT s.id, s.code, s.name, s.year_id, s.type, s.lectures_per_week FROM subject s
                             JOIN academic_year ay ON ay.id=s.year_id WHERE ay.admin_id=%s""", (aid,))
        fac_rows = q_all(cur, "SELECT id, name, qualifications, max_periods_per_day, designation FROM faculty WHERE admin_id=%s", (aid,))
        fac_by_key = {_norm_person(f["name"]): f for f in fac_rows}

        def skip(rec, why):
            rep["skipped"].append({"row": rec["row"], "faculty": rec["faculty"], "reason": why})

        def find_divs(tokens, year_id):
            """-> (list of division dicts, error text)."""
            pool = [d for d in divs if year_id is None or d["year_id"] == year_id]
            if not tokens or any(_norm_key(t) in ("all", "allall") for t in tokens):
                return pool, None
            out = []
            for t in tokens:
                k = _norm_key(t)
                hit = [d for d in pool if _norm_key(d["name"]) == k] or \
                      [d for d in pool if _norm_key(d["name"]).endswith(k)]
                if len(hit) > 1:
                    return [], f"division '{t}' matches several divisions - write it like 'SY A'"
                if not hit:
                    hit = _match_class(t, pool)             # free text like "SY(AIML)-B"
                    if not hit:
                        return [], f"division '{t}' not found"
                out.extend(d for d in hit if d not in out)
            return out, None

        for rec in records:
            cur.execute("SAVEPOINT rec")
            try:
                # ---- faculty (create / update from the file)
                fkey = _norm_person(rec["faculty"])
                fac = fac_by_key.get(fkey)
                file_codes = list(rec["codes"])
                if fac is None:
                    quals = list(dict.fromkeys(file_codes))
                    row = q_one(cur, """INSERT INTO faculty (admin_id, name, qualifications, max_periods_per_day, designation)
                                        VALUES (%s,%s,%s,%s,%s)
                                        RETURNING id, name, qualifications, max_periods_per_day, designation""",
                                (aid, rec["faculty"], quals, rec["max"] or 6, rec["designation"] or None))
                    fac = fac_by_key[fkey] = row
                    rep["faculty_created"].append(rec["faculty"])
                else:
                    quals = list(fac["qualifications"] or [])
                    have = {q.lower() for q in quals}
                    new_q = [c for c in file_codes if c.lower() not in have]
                    new_max = rec["max"] if rec["max"] and rec["max"] != fac["max_periods_per_day"] else None
                    new_desig = rec["designation"] if rec["designation"] and rec["designation"] != fac.get("designation") else None
                    if new_q or new_max or new_desig:
                        cur.execute("UPDATE faculty SET qualifications=%s, max_periods_per_day=%s, designation=%s WHERE id=%s",
                                    (quals + new_q, new_max or fac["max_periods_per_day"],
                                     new_desig or fac.get("designation"), fac["id"]))
                        fac["qualifications"] = quals + new_q
                        if new_desig:
                            fac["designation"] = new_desig
                        if new_max:
                            fac["max_periods_per_day"] = new_max
                        if rec["faculty"] not in rep["faculty_updated"] and rec["faculty"] not in rep["faculty_created"]:
                            rep["faculty_updated"].append(rec["faculty"])

                # ---- subject(s) this row names
                items = [(c, rec["sname"] if len(rec["codes"]) == 1 else "") for c in rec["codes"]]
                if not items and rec["sname"]:
                    items = [("", rec["sname"])]
                if not items:
                    cur.execute("RELEASE SAVEPOINT rec")
                    continue            # a row that only carried the faculty name / max periods

                for code, sname in items:
                    cand = []
                    if code:
                        cand = [s for s in subs if s["code"].strip().lower() == code.lower()]
                    if not cand:
                        probe = (sname or code).strip().lower()
                        cand = [s for s in subs if s["name"].strip().lower() == probe]
                    # the same code may be used by a theory course AND its lab (e.g. "AI (U0702)" / "AI LAB (U0702)"):
                    # when the file says which kind this row is, only reuse a subject of that kind
                    want = rec["type"] if rec["type"] in ("lecture", "lab", "tutorial") else ""
                    if want and cand:
                        cand = [s for s in cand if s["type"] == want]
                    # a subject code can exist in several years: let the division decide
                    div_tokens = rec["divisions"]
                    if len(cand) > 1:
                        years = {d["year_id"] for d in find_divs(div_tokens, None)[0]}
                        cand = [s for s in cand if s["year_id"] in years] or cand
                    if len(cand) > 1:
                        skip(rec, f"subject '{code or sname}' exists in several years - add the division to the row")
                        continue
                    subj = cand[0] if cand else None
                    year_id = subj["year_id"] if subj else None
                    target_divs, err = find_divs(div_tokens, year_id)
                    if err:
                        if year_id is not None and not find_divs(div_tokens, None)[1]:
                            err = (f"{err.split(' not found')[0]} is in a different year than subject "
                                   f"{subj['code']} - the same code can't be used for two years")
                        skip(rec, err)
                        continue
                    if not target_divs:
                        skip(rec, "no divisions exist for this year yet (step 04)")
                        continue

                    # ---- create the subject from the file when it is not set up yet
                    if subj is None:
                        years = {d["year_id"] for d in target_divs}
                        if not code:
                            skip(rec, f"subject '{sname}' is not set up and the row has no course code to create it with")
                            continue
                        if len(years) != 1:
                            skip(rec, f"subject '{code}' is not set up - add a division so its year is known")
                            continue
                        blob = f"{rec['type']} {sname} {code}".lower()
                        if rec["type"] in ("lecture", "lab", "tutorial"):
                            stype = rec["type"]
                        else:
                            stype = "lab" if re.search(r"\b(lab|labs|laboratory|practical|practicals)\b|^lab|^pract", blob) else \
                                    "tutorial" if re.search(r"\btut", blob) else "lecture"
                        lpw = rec["lpw"] or (3 if stype == "lecture" else 1)
                        subj = q_one(cur, """INSERT INTO subject (year_id, code, name, type, lectures_per_week)
                                             VALUES (%s,%s,%s,%s,%s)
                                             RETURNING id, code, name, year_id, type, lectures_per_week""",
                                     (years.copy().pop(), code, sname or code, stype, lpw))
                        subs.append(subj)
                        rep["subjects_created"].append(code)
                    elif rec["lpw"] and rec["lpw"] != subj["lectures_per_week"]:
                        cur.execute("UPDATE subject SET lectures_per_week=%s WHERE id=%s", (rec["lpw"], subj["id"]))
                        subj["lectures_per_week"] = rec["lpw"]
                        rep["subjects_updated"].append(subj["code"])

                    # ---- allocations: division x batch groups
                    for dv in target_divs:
                        div_batches = [b for b in batches if b["division_id"] == dv["id"]]
                        groups = []
                        if not rec["batches"]:
                            groups = [[]]
                        else:
                            for grp in rec["batches"]:
                                ids = []
                                for t in grp:
                                    hit = [b for b in div_batches if _norm_key(b["name"]) == _norm_key(t)]
                                    if hit:
                                        ids.append(hit[0]["id"])
                                    elif len(target_divs) == 1:
                                        skip(rec, f"batch '{t}' not found in {dv['name']}")
                                if len(ids) == len(grp):
                                    groups.append(ids)
                        for ids in groups:
                            th, tu, pr = rec["hours"] or (None, None, None)
                            cur.execute("""SELECT id FROM faculty_allocation WHERE subject_id=%s AND faculty_id=%s
                                           AND division_id=%s AND batch_ids = %s::int[] LIMIT 1""",
                                        (subj["id"], fac["id"], dv["id"], ids))
                            dup = cur.fetchone()
                            if dup:
                                rep["duplicates"] += 1
                                if rec["hours"]:             # keep the hours table in step with a re-uploaded sheet
                                    cur.execute("""UPDATE faculty_allocation SET class_label=%s, theory_hrs=%s,
                                                   tutorial_hrs=%s, practical_hrs=%s WHERE id=%s""",
                                                (rec["class_label"] or None, th, tu, pr, dup["id"]))
                                continue
                            cur.execute("""INSERT INTO faculty_allocation (subject_id, faculty_id, division_id, batch_ids,
                                                  class_label, theory_hrs, tutorial_hrs, practical_hrs)
                                           VALUES (%s,%s,%s,%s::int[],%s,%s,%s,%s)""",
                                        (subj["id"], fac["id"], dv["id"], ids, rec["class_label"] or None, th, tu, pr))
                            rep["allocations_created"] += 1
                cur.execute("RELEASE SAVEPOINT rec")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT rec")
                skip(rec, f"could not be saved ({e})")
        conn.commit()

    for k in ("subjects_created", "subjects_updated"):
        rep[k] = list(dict.fromkeys(rep[k]))
    return jsonify(rep)


# ============================================================ FACULTY DEMO SHEET (download -> fill -> upload again)
def _build_default_demo_sheet(division_names):
    """A ready-to-fill Excel sheet using exactly the headings the importer understands,
    with sample rows that use the admin's real division names."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Faculty Workload"
    heads = ["Sr. No.", "Name of Faculty", "Designation", "Max Periods per Day", "Class",
             "Course Name (Course Code)", "Theory", "Tutorial", "Practical", "Batch", "No. of Divisions", "Sum"]
    ws.append(["Faculty Workload (hours per week) - replace the sample rows below with your own data"])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(heads))
    ws["A1"].font = Font(bold=True, size=13)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append(heads)
    d1 = division_names[0] if division_names else "SY A"
    d2 = division_names[1] if len(division_names) > 1 else d1
    samples = [
        ("Dr. Sample Teacher One", "Professor", 5, [
            (d1, "Data Structures (CS201)", 3, None, None, None, 1),
            (d1, "Data Structures Lab (CS201L)", None, None, 4, "A1", 1),
            (d2, "Data Structures (CS201)", 3, None, None, None, 1)]),
        ("Prof. Sample Teacher Two", "Assistant Professor", 6, [
            (d2, "Database Systems (CS202)", 3, None, None, None, 1),
            (d2, "Database Systems Tutorial (CS202T)", None, 1, None, None, 1)]),
    ]
    thin = Side(style="thin", color="999999")
    r = 3
    for i, (name, desig, mx, rows) in enumerate(samples, 1):
        first = r
        for cls, course, th, tu, pr, batch, nd in rows:
            ws.append([i if r == first else None, name if r == first else None, desig if r == first else None,
                       mx if r == first else None, cls, course, th, tu, pr, batch, nd, None])
            r += 1
        last = r - 1
        ws.cell(first, 12).value = f"=SUM(G{first}:I{last})"
        for col in (1, 2, 3, 4, 12):
            ws.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)
            ws.cell(first, col).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    hdr_fill = PatternFill("solid", fgColor="E6E0CE")
    for c in ws[2]:
        c.font = Font(bold=True)
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2, max_row=r - 1, max_col=len(heads)):
        for c in row:
            c.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col, w in zip("ABCDEFGHIJKL", (7, 26, 20, 12, 14, 42, 8, 9, 9, 8, 11, 7)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A3"

    h = wb.create_sheet("How to fill")
    for line in [
        "How to use this sheet",
        "1. Edit the 'Faculty Workload' sheet: delete the two sample teachers and type your own, one row per course.",
        "2. Name of Faculty and the course code are required. Put the code in brackets after the course name, e.g. Machine Learning (UAMPC0501).",
        "3. Class = the division, written like your divisions in step 04 (for example: SY A). Leave it blank to give the course to every division of its year.",
        "4. Theory / Tutorial / Practical = hours per week. Theory makes a lecture, Practical a lab, Tutorial a tutorial.",
        "5. Max Periods per Day is optional (6 is used if left blank). Batch is optional (A1, or A1+A2 for one merged session).",
        "6. Designation, No. of Divisions and Sum are for your records. A faculty member's name, designation and max periods can be merged across their rows.",
        "7. Do not rename the heading row. Columns can be in any order. Save as .xlsx and upload it in the Faculty step.",
    ]:
        h.append([line])
    h["A1"].font = Font(bold=True, size=13)
    h.column_dimensions["A"].width = 140
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


@app.route("/api/faculty-demo-sheet")
@require_auth
def faculty_demo_sheet():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        row = q_one(cur, "SELECT file_name, file_data FROM faculty_demo_sheet WHERE admin_id=%s", (aid,))
        if row and row["file_data"]:
            return send_file(io.BytesIO(bytes(row["file_data"])), download_name=row["file_name"] or "faculty_demo_sheet.xlsx",
                             as_attachment=True,
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        names = [d["name"] for d in q_all(cur, """SELECT dv.name FROM division dv JOIN academic_year ay ON ay.id=dv.year_id
                                                  WHERE ay.admin_id=%s ORDER BY ay.label, dv.name""", (aid,))]
    return send_file(_build_default_demo_sheet(names), download_name="faculty_demo_sheet.xlsx", as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/faculty-demo-sheet/info")
@require_auth
def faculty_demo_sheet_info():
    with get_conn() as conn, conn.cursor() as cur:
        row = q_one(cur, "SELECT file_name FROM faculty_demo_sheet WHERE admin_id=%s AND file_data IS NOT NULL", (session["admin_id"],))
    return jsonify({"custom": row["file_name"] if row else None})


@app.route("/api/faculty-demo-sheet", methods=["POST"])
@require_auth
def upload_faculty_demo_sheet():
    """The admin can replace the built-in demo with their own Excel sheet (e.g. the college's real format)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Choose an Excel file first."}), 400
    if not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"error": "The demo sheet must be an Excel .xlsx file."}), 400
    data = f.read()
    if len(data) > 5 * 1024 * 1024:
        return jsonify({"error": "That file is larger than 5 MB."}), 400
    try:
        class _F:  # the importer reads .filename and .read()
            filename = f.filename
            def read(self_inner): return data
        blocks = _read_upload_blocks(_F())
        _, columns = _parse_allocation_blocks(blocks)
        found = any(_header_mapping([_cell(c) for c in row]) for blk in blocks for row in blk)
    except Exception as e:
        return jsonify({"error": f"Could not read that file ({e})."}), 400
    if not found:
        return jsonify({"error": "That sheet has no heading row the importer understands. It needs a Faculty Name "
                                 "column and a Course/Subject Code (or Course Name) column."}), 400
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO faculty_demo_sheet (admin_id, file_name, file_data) VALUES (%s,%s,%s)
                       ON CONFLICT (admin_id) DO UPDATE SET file_name=EXCLUDED.file_name,
                       file_data=EXCLUDED.file_data, updated_at=now()""",
                    (session["admin_id"], f.filename, psycopg2.Binary(data)))
        conn.commit()
    return jsonify({"ok": True, "file_name": f.filename})


@app.route("/api/faculty-demo-sheet", methods=["DELETE"])
@require_auth
def reset_faculty_demo_sheet():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM faculty_demo_sheet WHERE admin_id=%s", (session["admin_id"],))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ PORTALS (student + teacher)
# Both portals are READ-ONLY views over the same timetable_entry table the
# admin edits, so they are always in sync. The admin session key is
# session["admin_id"]; portal logins use a DIFFERENT key (session["portal_user_id"]),
# so a student/teacher can never call an admin endpoint (master timetable,
# generate, edit...) - those all check require_auth.
def _portal_public(u):
    return {"id": u["id"], "name": u["name"], "role": u["role"],
            "faculty_id": u["faculty_id"], "division_id": u["division_id"]}


def require_portal(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = session.get("portal_user_id")
            if not uid:
                return jsonify({"error": "Not logged in"}), 401
            with get_conn() as conn, conn.cursor() as cur:
                u = q_one(cur, "SELECT * FROM portal_user WHERE id=%s", (uid,))
            if not u:
                session.pop("portal_user_id", None)
                return jsonify({"error": "Not logged in"}), 401
            if role and u["role"] != role:
                return jsonify({"error": "This page is not available for your account."}), 403
            g.portal_user = u
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "user"


# ---- admin side: manage portal accounts
@app.route("/api/portal-users", methods=["GET", "POST"])
@require_auth
def portal_users():
    aid = session["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        if request.method == "GET":
            users = q_all(cur, """SELECT pu.id, pu.role, pu.login_id, pu.name, pu.faculty_id, pu.division_id,
                                         f.name AS faculty_name, d.name AS division_name
                                  FROM portal_user pu
                                  LEFT JOIN faculty f ON f.id = pu.faculty_id
                                  LEFT JOIN division d ON d.id = pu.division_id
                                  WHERE pu.admin_id=%s ORDER BY pu.role, pu.name""", (aid,))
            return jsonify({"users": users})

        d = request.get_json(force=True)
        role = d.get("role")
        login_id = (d.get("login_id") or "").strip()
        name = (d.get("name") or "").strip()
        password = d.get("password") or ""
        if role not in ("student", "teacher"):
            return jsonify({"error": "Role must be student or teacher."}), 400
        if not (login_id and name and password):
            return jsonify({"error": "Name, login ID and password are required."}), 400
        faculty_id = division_id = None
        if role == "teacher":
            faculty_id = int(d.get("faculty_id") or 0)
            if not q_one(cur, "SELECT id FROM faculty WHERE id=%s AND admin_id=%s", (faculty_id, aid)):
                return jsonify({"error": "Pick the faculty member this login belongs to."}), 400
        else:
            division_id = int(d.get("division_id") or 0)
            if not q_one(cur, """SELECT dv.id FROM division dv JOIN academic_year ay ON ay.id=dv.year_id
                                 WHERE dv.id=%s AND ay.admin_id=%s""", (division_id, aid)):
                return jsonify({"error": "Pick the division this login belongs to."}), 400
        if q_one(cur, "SELECT id FROM portal_user WHERE login_id=%s", (login_id,)):
            return jsonify({"error": "That login ID is already taken."}), 400
        seen = q_one(cur, "SELECT COALESCE(MAX(id),0) AS v FROM notification WHERE admin_id=%s", (aid,))["v"]
        cur.execute("""INSERT INTO portal_user (admin_id, role, login_id, name, password_hash,
                                                faculty_id, division_id, last_seen_notification_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (aid, role, login_id, name, generate_password_hash(password), faculty_id, division_id, seen))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/portal-users/bulk", methods=["POST"])
@require_auth
def portal_users_bulk():
    """One click: a teacher login for every faculty member (login = name slug), or
    one shared student login per division (login = division slug). Skips anything that already has one."""
    aid = session["admin_id"]
    d = request.get_json(force=True)
    role = d.get("role")
    password = d.get("password") or ""
    if role not in ("student", "teacher") or not password:
        return jsonify({"error": "Choose a role and a starting password."}), 400
    created = []
    with get_conn() as conn, conn.cursor() as cur:
        seen = q_one(cur, "SELECT COALESCE(MAX(id),0) AS v FROM notification WHERE admin_id=%s", (aid,))["v"]
        if role == "teacher":
            targets = q_all(cur, """SELECT f.id, f.name FROM faculty f WHERE f.admin_id=%s
                                    AND NOT EXISTS (SELECT 1 FROM portal_user p WHERE p.faculty_id=f.id)""", (aid,))
        else:
            targets = q_all(cur, """SELECT dv.id, dv.name FROM division dv JOIN academic_year ay ON ay.id=dv.year_id
                                    WHERE ay.admin_id=%s
                                    AND NOT EXISTS (SELECT 1 FROM portal_user p WHERE p.division_id=dv.id AND p.role='student')""", (aid,))
        for t in targets:
            base = _slug(t["name"])
            login = base
            if q_one(cur, "SELECT id FROM portal_user WHERE login_id=%s", (login,)):
                login = f"{base}_{t['id']}"
            cur.execute("""INSERT INTO portal_user (admin_id, role, login_id, name, password_hash,
                                                    faculty_id, division_id, last_seen_notification_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (aid, role, login, t["name"], generate_password_hash(password),
                         t["id"] if role == "teacher" else None,
                         t["id"] if role == "student" else None, seen))
            created.append({"login_id": login, "name": t["name"]})
        conn.commit()
    return jsonify({"ok": True, "created": created})


@app.route("/api/portal-users/<int:uid>", methods=["DELETE"])
@require_auth
def delete_portal_user(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM portal_user WHERE id=%s AND admin_id=%s", (uid, session["admin_id"]))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/portal-users/<int:uid>/password", methods=["POST"])
@require_auth
def reset_portal_password(uid):
    password = (request.get_json(force=True).get("password") or "")
    if not password:
        return jsonify({"error": "Enter a new password."}), 400
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE portal_user SET password_hash=%s WHERE id=%s AND admin_id=%s",
                    (generate_password_hash(password), uid, session["admin_id"]))
        conn.commit()
    return jsonify({"ok": True})


# ---- portal side
@app.route("/api/portal/login", methods=["POST"])
def portal_login():
    d = request.get_json(force=True)
    login_id = (d.get("login_id") or "").strip()
    password = d.get("password") or ""
    role = d.get("role")
    with get_conn() as conn, conn.cursor() as cur:
        u = q_one(cur, "SELECT * FROM portal_user WHERE login_id=%s", (login_id,))
    if not u or u["role"] != role or not check_password_hash(u["password_hash"], password):
        return jsonify({"error": "Invalid login ID or password for this portal."}), 401
    session["portal_user_id"] = u["id"]
    return jsonify({"ok": True, "user": _portal_public(u)})


@app.route("/api/portal/logout", methods=["POST"])
def portal_logout():
    session.pop("portal_user_id", None)
    return jsonify({"ok": True})


@app.route("/api/portal/me")
@require_portal()
def portal_me():
    return jsonify({"user": _portal_public(g.portal_user)})


@app.route("/api/portal/options")
@require_portal()
def portal_options():
    u = g.portal_user
    aid = u["admin_id"]
    with get_conn() as conn, conn.cursor() as cur:
        divisions = q_all(cur, """SELECT dv.id, dv.name, ay.label AS year_label
                                  FROM division dv JOIN academic_year ay ON ay.id=dv.year_id
                                  WHERE ay.admin_id=%s ORDER BY ay.label, dv.name""", (aid,))
        inst = q_one(cur, "SELECT name FROM institute WHERE admin_id=%s", (aid,))
        out = {"institute": inst["name"] if inst else "", "divisions": divisions}
        if u["role"] == "teacher":
            out["classrooms"] = q_all(cur, """SELECT id, room_no, room_type, capacity FROM classroom
                                              WHERE admin_id=%s ORDER BY room_no""", (aid,))
    return jsonify(out)


def _portal_scope(u, view, rid):
    """What this portal user may open. Returns (rid, error_response_or_None).
    Students: division timetables only. Teachers: class / classroom / lab, plus ONLY
    their own faculty timetable (any id they send is replaced with their own).
    Nobody here can reach the master timetable."""
    allowed = ("class",) if u["role"] == "student" else ("class", "classroom", "lab", "faculty")
    if view not in allowed:
        return rid, (jsonify({"error": "That timetable is not available for your account."}), 403)
    if view == "faculty":
        rid = u["faculty_id"]
    elif view in ("classroom", "lab"):
        with get_conn() as conn, conn.cursor() as cur:
            room = q_one(cur, "SELECT room_type FROM classroom WHERE id=%s AND admin_id=%s", (rid, u["admin_id"]))
        wanted = "lab" if view == "lab" else "classroom"
        if not room or room["room_type"] != wanted:
            return rid, (jsonify({"error": "Room not found."}), 404)
    if not rid:
        return rid, (jsonify({"error": "Nothing selected."}), 400)
    return rid, None


@app.route("/api/portal/timetable")
@require_portal()
def portal_timetable():
    u = g.portal_user
    view = request.args.get("view", "")
    rid, err = _portal_scope(u, view, request.args.get("id", type=int) or 0)
    if err:
        return err
    where = {"faculty": "AND %s = ANY(te.faculty_ids)", "class": "AND te.division_id=%s"}.get(view, "AND te.classroom_id=%s")
    payload = _grid_payload(u["admin_id"], where, (rid,))
    payload["last_run"] = None      # unplaced-session diagnostics are admin-only
    return jsonify(payload)


@app.route("/api/portal/timetable/download.pdf")
@require_portal()
def portal_timetable_pdf():
    """Same PDF the admin downloads, built from the template the admin saved
    (heading, department, HOD...). Same permission rules as the grid above."""
    u = g.portal_user
    view = request.args.get("view", "")
    rid, err = _portal_scope(u, view, request.args.get("id", type=int) or 0)
    if err:
        return err
    try:
        buf, fname = _timetable_pdf(u["admin_id"], view, rid, "saved")
    except ValueError as e:
        msg, code = e.args[0]
        return jsonify({"error": msg}), code
    return send_file(buf, download_name=fname, as_attachment=True, mimetype="application/pdf")


def _portal_rollcall_division(u, requested):
    """-> (division id, error response or None). A student only ever gets their own division;
    a teacher may open any division of their institute. Nobody here can upload or delete."""
    if u["role"] == "student":
        did = u["division_id"]
        if not did:
            return None, (jsonify({"error": "Your account is not linked to a division."}), 404)
        return did, None
    with get_conn() as conn, conn.cursor() as cur:
        ok = q_one(cur, """SELECT d.id FROM division d JOIN academic_year ay ON ay.id=d.year_id
                           WHERE d.id=%s AND ay.admin_id=%s""", (requested or 0, u["admin_id"]))
    if not ok:
        return None, (jsonify({"error": "Division not found."}), 404)
    return ok["id"], None


@app.route("/api/portal/rollcall")
@require_portal()
def portal_rollcall_info():
    u = g.portal_user
    did, err = _portal_rollcall_division(u, request.args.get("division_id", type=int))
    if err:
        return err
    with get_conn() as conn, conn.cursor() as cur:
        div = q_one(cur, "SELECT name FROM division WHERE id=%s", (did,))
        rc = q_one(cur, "SELECT file_name, mime_type, updated_at FROM division_rollcall WHERE division_id=%s AND admin_id=%s",
                   (did, u["admin_id"]))
    return jsonify({
        "division_id": did, "division_name": div["name"] if div else "",
        "available": bool(rc), "file_name": rc["file_name"] if rc else None,
        "can_view": bool(rc and rc["mime_type"] in ROLLCALL_INLINE_TYPES),
        "updated_at": rc["updated_at"].isoformat() if rc else None,
    })


@app.route("/api/portal/rollcall/file")
@require_portal()
def portal_rollcall_file():
    u = g.portal_user
    did, err = _portal_rollcall_division(u, request.args.get("division_id", type=int))
    if err:
        return err
    with get_conn() as conn, conn.cursor() as cur:
        return _send_rollcall(cur, u["admin_id"], did, inline=bool(request.args.get("inline")))


def _notif_scope(u):
    """SQL fragment + param: which notification rows concern this user."""
    if u["role"] == "student":
        return ("((cardinality(division_ids)=0 AND cardinality(faculty_ids)=0) OR %s = ANY(division_ids))",
                u["division_id"])
    return ("((cardinality(division_ids)=0 AND cardinality(faculty_ids)=0) OR %s = ANY(faculty_ids))",
            u["faculty_id"])


@app.route("/api/portal/notifications")
@require_portal()
def portal_notifications():
    u = g.portal_user
    aid = u["admin_id"]
    scope, key = _notif_scope(u)
    with get_conn() as conn, conn.cursor() as cur:
        # version = newest change of ANY kind; the portals re-fetch their grid whenever it moves
        version = q_one(cur, "SELECT COALESCE(MAX(id),0) AS v FROM notification WHERE admin_id=%s", (aid,))["v"]
        items = q_all(cur, f"""SELECT id, message, created_at FROM notification
                               WHERE admin_id=%s AND {scope} ORDER BY id DESC LIMIT 30""", (aid, key))
        unread = q_one(cur, f"""SELECT COUNT(*) AS c FROM notification
                                WHERE admin_id=%s AND id > %s AND {scope}""",
                       (aid, u["last_seen_notification_id"] or 0, key))["c"]
    for it in items:
        it["unread"] = it["id"] > (u["last_seen_notification_id"] or 0)
    return jsonify({"version": version, "unread_count": unread, "items": items})


@app.route("/api/portal/notifications/read", methods=["POST"])
@require_portal()
def portal_notifications_read():
    u = g.portal_user
    with get_conn() as conn, conn.cursor() as cur:
        v = q_one(cur, "SELECT COALESCE(MAX(id),0) AS v FROM notification WHERE admin_id=%s", (u["admin_id"],))["v"]
        cur.execute("UPDATE portal_user SET last_seen_notification_id=%s WHERE id=%s", (v, u["id"]))
        conn.commit()
    return jsonify({"ok": True})


# ============================================================ STATIC FILES (the other 3 of the 5 files)
@app.route("/")
def index_page():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/style.css")
def style_css():
    return send_from_directory(BASE_DIR, "style.css")


@app.route("/app.js")
def app_js():
    return send_from_directory(BASE_DIR, "app.js")


@app.route("/portal")
def portal_page():
    return send_from_directory(BASE_DIR, "portal.html")


@app.route("/portal.js")
def portal_js():
    return send_from_directory(BASE_DIR, "portal.js")

import traceback

@app.errorhandler(Exception)
def handle_any_error(e):
    traceback.print_exc()
    return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)