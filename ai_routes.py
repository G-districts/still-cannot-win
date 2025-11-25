from flask import Blueprint, request, jsonify
import sqlite3, os, json, time
from ai_classifier import classify, CATEGORIES

ROOT = os.path.dirname(__file__)
DB_PATH = os.path.join(ROOT, "gschool.db")

ai = Blueprint("ai", __name__, url_prefix="/api/ai")

def _db():
    return sqlite3.connect(DB_PATH)

def ensure_schema():
    with _db() as conn:
        cur = conn.cursor()
        # Tables
        cur.execute("""CREATE TABLE IF NOT EXISTS categories(
            name TEXT PRIMARY KEY,
            blocked INTEGER DEFAULT 0,
            block_url TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS category_schedules(
            name TEXT PRIMARY KEY,
            schedule_json TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS settings(
            k TEXT PRIMARY KEY,
            v TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT,
            user_id TEXT,
            role TEXT,
            text TEXT,
            ts INTEGER
        )""")
        conn.commit()

        # Seed categories if any missing
        cur.execute("SELECT name FROM categories")
        existing = {r[0] for r in cur.fetchall()}
        for c in CATEGORIES:
            if c not in existing:
                cur.execute("INSERT OR IGNORE INTO categories(name, blocked, block_url) VALUES(?,?,?)", (c, 0, None))
        conn.commit()


def _is_schedule_active(sched, now_ts=None):
    """
    Simple helper to decide if a schedule is currently "active".
    Sched format:
      {
        "enabled": bool,
        "start": "HH:MM",   # optional, default "00:00"
        "end": "HH:MM",     # optional, default "23:59"
        "weekdays_only": bool
      }
    """
    if not isinstance(sched, dict):
        return False
    if not sched.get("enabled"):
        return False

    if now_ts is None:
        now_ts = time.time()
    lt = time.localtime(now_ts)

    # Optional weekday restriction (Mon=0 .. Sun=6)
    if sched.get("weekdays_only") and lt.tm_wday >= 5:
        return False

    def _parse_hhmm(val, default_h, default_m):
        if not val:
            return default_h, default_m
        try:
            parts = str(val).split(":", 1)
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            h = max(0, min(23, h))
            m = max(0, min(59, m))
            return h, m
        except Exception:
            return default_h, default_m

    sh, sm = _parse_hhmm(sched.get("start"), 0, 0)
    eh, em = _parse_hhmm(sched.get("end"), 23, 59)

    cur_minutes = lt.tm_hour * 60 + lt.tm_min
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em

    if start_minutes == end_minutes:
        # Degenerate case: treat as always off
        return False

    if start_minutes < end_minutes:
        # Normal same-day window
        return start_minutes <= cur_minutes < end_minutes
    else:
        # Window wraps past midnight (e.g. 22:00–06:00)
        return not (end_minutes <= cur_minutes < start_minutes)

def get_setting(key, default=None):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row and row[0] else default

def set_setting(key, value):
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (key, json.dumps(value)))
        conn.commit()


@ai.route("/categories", methods=["GET", "POST"])
def categories():
    """
    GET: return all AI categories plus any attached schedules.
    POST: update a category's blocked/block_url and/or schedule.

    Schedules are stored in the category_schedules table as JSON, with
    a shape like:
      {
        "enabled": bool,
        "start": "HH:MM",
        "end": "HH:MM",
        "weekdays_only": bool
      }
    """
    ensure_schema()
    with _db() as conn:
        cur = conn.cursor()

        if request.method == "POST":
            body = request.json or {}
            name = body.get("name")
            if not name:
                return jsonify({"ok": False, "error": "name required"}), 400

            blocked = body.get("blocked")
            block_url_present = "block_url" in body
            block_url = body.get("block_url")
            schedule = body.get("schedule")

            # Ensure base row exists
            cur.execute(
                "INSERT OR IGNORE INTO categories(name, blocked, block_url) VALUES(?,?,?)",
                (name, 0, None),
            )

            # Only update fields that were explicitly provided
            if blocked is not None:
                cur.execute(
                    "UPDATE categories SET blocked=? WHERE name=?",
                    (1 if blocked else 0, name),
                )
            if block_url_present:
                cur.execute(
                    "UPDATE categories SET block_url=? WHERE name=?",
                    (block_url, name),
                )

            # Upsert schedule if provided
            if schedule is not None:
                cur.execute(
                    "INSERT OR REPLACE INTO category_schedules(name, schedule_json) VALUES(?,?)",
                    (name, json.dumps(schedule)),
                )

            conn.commit()
            return jsonify({"ok": True})

        # Auto-add any missing categories silently
        cur.execute("SELECT name FROM categories")
        existing = {r[0] for r in cur.fetchall()}
        for c in CATEGORIES:
            if c not in existing:
                cur.execute(
                    "INSERT OR IGNORE INTO categories(name, blocked, block_url) VALUES(?,?,?)",
                    (c, 0, None),
                )
        conn.commit()

        # Return categories with schedules included
        cur.execute("SELECT name, blocked, block_url FROM categories ORDER BY name")
        rows = []
        for (n, b, u) in cur.fetchall():
            cur.execute(
                "SELECT schedule_json FROM category_schedules WHERE name=?",
                (n,),
            )
            srow = cur.fetchone()
            schedule = None
            if srow and srow[0]:
                try:
                    schedule = json.loads(srow[0])
                except Exception:
                    schedule = None
            rows.append(
                {"name": n, "blocked": bool(b), "block_url": u, "schedule": schedule}
            )

        return jsonify({"ok": True, "categories": rows})


@ai.route("/classify", methods=["POST"])
def api_classify():
    """
    Classify a URL using the AI classifier and decide whether to block it.
    This respects both:
      - Per-category "blocked" flags
      - Optional time-based schedules for each category, and for the
        special "Global Block All" category.
    """
    ensure_schema()
    body = request.json or {}
    url = body.get("url") or ""
    html = body.get("html")
    result = classify(url, html)

    # --- Load settings ---
    default_redirect = get_setting(
        "blocked_redirect",
        "https://blocked.gdistrict.org/Gschool%20block",
    )

    with _db() as conn:
        cur = conn.cursor()

        # Get global allowlist (unchanged behavior)
        cur.execute("CREATE TABLE IF NOT EXISTS overrides (k TEXT PRIMARY KEY, v TEXT)")
        conn.commit()
        cur.execute("SELECT v FROM overrides WHERE k='allowlist'")
        row = cur.fetchone()
        allowlist = json.loads(row[0]) if row and row[0] else []

        # Base category flags
        cur.execute(
            "SELECT blocked FROM categories WHERE name=?",
            ("Global Block All",),
        )
        row = cur.fetchone()
        global_block_on = bool(row and row[0])

        cur.execute(
            "SELECT blocked, block_url FROM categories WHERE name=?",
            (result["category"],),
        )
        row = cur.fetchone()
        cat_blocked = bool(row[0]) if row else False
        cat_block_url = row[1] if row else None

        # --- Apply schedule overrides, if configured ---
        # Global Block All schedule
        cur.execute(
            "SELECT schedule_json FROM category_schedules WHERE name=?",
            ("Global Block All",),
        )
        srow = cur.fetchone()
        if srow and srow[0]:
            try:
                sched = json.loads(srow[0])
            except Exception:
                sched = None
            if sched:
                active = _is_schedule_active(sched)
                # When a schedule exists, it controls whether Global Block All is on.
                global_block_on = bool(active)

        # Per-category schedule
        cur.execute(
            "SELECT schedule_json FROM category_schedules WHERE name=?",
            (result["category"],),
        )
        srow = cur.fetchone()
        if srow and srow[0]:
            try:
                sched = json.loads(srow[0])
            except Exception:
                sched = None
            if sched:
                active = _is_schedule_active(sched)
                # When a schedule exists, it controls whether this category is blocked.
                cat_blocked = bool(active)

    # --- Handle Global Block All Mode (unchanged, except schedule support) ---
    allowed_domains = ["blocked.gdistrict.org"]
    if global_block_on:
        # Check if URL is in allowlist or allowed domains
        allowed = any(a.lower() in url.lower() for a in allowlist + allowed_domains)
        if not allowed:
            return jsonify(
                {
                    "ok": True,
                    "url": url,
                    "result": result,
                    "blocked": True,
                    "block_url": default_redirect,
                }
            )

    # --- Normal AI blocking with (maybe) scheduled flag ---
    blocked = cat_blocked
    final_block_url = cat_block_url or default_redirect

    return jsonify(
        {
            "ok": True,
            "url": url,
            "result": result,
            "blocked": blocked,
            "block_url": final_block_url,
        }
    )

@ai.route("/chat/send", methods=["POST"])
def chat_send():
    ensure_schema()
    b = request.json or {}
    room = b.get("room") or "*"
    user_id = b.get("user_id") or "unknown"
    role = b.get("role") or "student"
    text = (b.get("text") or "").strip()[:1000]
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    ts = int(time.time() * 1000)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO chat_messages(room,user_id,role,text,ts) VALUES(?,?,?,?,?)",
                    (room, user_id, role, text, ts))
        conn.commit()
    return jsonify({"ok": True, "ts": ts})

@ai.route("/chat/poll", methods=["GET"])
def chat_poll():
    ensure_schema()
    room = request.args.get("room", "*")
    since = int(request.args.get("since", "0") or 0)
    with _db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT user_id, role, text, ts FROM chat_messages WHERE room=? AND ts>? ORDER BY ts ASC",
                    (room, since))
        rows = [{"user_id": u, "role": r, "text": t, "ts": ts} for (u, r, t, ts) in cur.fetchall()]
    return jsonify({"ok": True, "messages": rows})
