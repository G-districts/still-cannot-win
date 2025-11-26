# =========================
# G-SCHOOLS CONNECT BACKEND
# =========================

from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_cors import CORS
import json, os, time, sqlite3, traceback, uuid, re
from urllib.parse import urlparse
from datetime import datetime
from collections import defaultdict

# ---------------------------
# Flask App Initialization
# ---------------------------
app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret_key")
CORS(app, supports_credentials=True)

# ---------------------------
# Paths & Constants
# ---------------------------
ROOT = os.path.dirname(__file__)
DATA_PATH = os.path.join(ROOT, "data.json")
DB_PATH = os.path.join(ROOT, "gschool.db")
SCENES_PATH = os.path.join(ROOT, "scenes.json")

DEFAULT_CLASS_ID = "period1"

# ---------------------------
# Data Helpers (JSON + SQLite)
# ---------------------------

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            password TEXT,
            role TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room TEXT,
            user_id TEXT,
            role TEXT,
            text TEXT,
            ts INTEGER
        )
    """)
    conn.commit()
    conn.close()

def get_db():
    ensure_db()
    return sqlite3.connect(DB_PATH)

def get_setting(key, default=None):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT v FROM settings WHERE k = ?", (key,))
        row = cur.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception:
        traceback.print_exc()
    return default

def set_setting(key, value):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("REPLACE INTO settings (k, v) VALUES (?, ?)", (key, json.dumps(value)))
        conn.commit()
        conn.close()
    except Exception:
        traceback.print_exc()

def load_data():
    if not os.path.exists(DATA_PATH):
        d = {
            "classes": {
                DEFAULT_CLASS_ID: {
                    "name": "Period 1",
                    "active": True,
                    "focus_mode": False,
                    "paused": False
                }
            },
            "categories": {},
            "settings": {
                "blocked_redirect": "https://blocked.gdistrict.org/Gschool%20block"
            },
            "announcements": "",
            "history": {},
            "student_overrides": {},
            "pending_per_student": {}
        }
        save_data(d)
        return d
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return {
            "classes": {
                DEFAULT_CLASS_ID: {
                    "name": "Period 1",
                    "active": True,
                    "focus_mode": False,
                    "paused": False
                }
            },
            "categories": {},
            "settings": {
                "blocked_redirect": "https://blocked.gdistrict.org/Gschool%20block"
            },
            "announcements": "",
            "history": {},
            "student_overrides": {},
            "pending_per_student": {}
        }

def save_data(d):
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        traceback.print_exc()

def ensure_keys(d):
    if "classes" not in d:
        d["classes"] = {
            DEFAULT_CLASS_ID: {
                "name": "Period 1",
                "active": True,
                "focus_mode": False,
                "paused": False
            }
        }
    if "categories" not in d:
        d["categories"] = {}
    if "settings" not in d:
        d["settings"] = {
            "blocked_redirect": "https://blocked.gdistrict.org/Gschool%20block"
        }
    if "announcements" not in d:
        d["announcements"] = ""
    if "history" not in d:
        d["history"] = {}
    if "student_overrides" not in d:
        d["student_overrides"] = {}
    if "pending_per_student" not in d:
        d["pending_per_student"] = {}
    return d

# ---------------------------
# Auth Helpers
# ---------------------------

def current_user():
    email = session.get("email")
    if not email:
        return None
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT email, password, role FROM users WHERE email = ?", (email,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {"email": row[0], "role": row[2]}
    except Exception:
        traceback.print_exc()
        return None

def require_admin(func):
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or u.get("role") != "admin":
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

# ---------------------------
# Routes: Basic Pages
# ---------------------------

@app.route("/")
def index():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    if u["role"] == "admin":
        return redirect(url_for("admin"))
    return redirect(url_for("student"))

@app.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, role FROM users")
    rows = cur.fetchall()
    conn.close()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "").strip()
        if not email or not password:
            return render_template("login.html", error="Missing email or password", users=rows)
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT email, password, role FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            conn.close()
            if not row or row[1] != password:
                return render_template("login.html", error="Invalid credentials", users=rows)
            session["email"] = row[0]
            return redirect(url_for("index"))
        except Exception:
            traceback.print_exc()
            return render_template("login.html", error="Internal error", users=rows)

    return render_template("login.html", users=rows)

@app.route("/logout")
def logout():
    session.pop("email", None)
    return redirect(url_for("login"))

@app.route("/admin")
@require_admin
def admin():
    d = ensure_keys(load_data())
    return render_template("admin.html", data=d)

@app.route("/student")
def student():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    return render_template("student.html", user=u)

# ---------------------------
# API: Users & Settings
# ---------------------------

@app.route("/api/users", methods=["GET"])
def api_users():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, role FROM users")
    out = [{"email": r[0], "role": r[1]} for r in cur.fetchall()]
    conn.close()
    return jsonify({"ok": True, "users": out})

@app.route("/api/users", methods=["POST"])
def api_users_post():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    b = request.json or {}
    email = (b.get("email") or "").strip().lower()
    password = b.get("password") or ""
    role = b.get("role") or "student"
    if not email or not password:
        return jsonify({"ok": False, "error": "missing"}), 400
    if role not in ("student", "teacher", "admin"):
        return jsonify({"ok": False, "error": "bad role"}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("REPLACE INTO users (email, password, role) VALUES (?, ?, ?)", (email, password, role))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception:
        traceback.print_exc()
        return jsonify({"ok": False, "error": "internal"}), 500

@app.route("/api/settings", methods=["POST"])
def api_settings():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    d = ensure_keys(load_data())
    b = request.json or {}
    if "blocked_redirect" in b:
        d["settings"]["blocked_redirect"] = b["blocked_redirect"]
    if "chat_enabled" in b:
        d["settings"]["chat_enabled"] = bool(b["chat_enabled"])
        set_setting("chat_enabled", bool(b["chat_enabled"]))
    if "passcode" in b and b["passcode"]:
        d["settings"]["passcode"] = b["passcode"]
    # New: global bypass settings
    if "bypass_enabled" in b:
        d["settings"]["bypass_enabled"] = bool(b["bypass_enabled"])
        set_setting("bypass_enabled", bool(b["bypass_enabled"]))
    if "bypass_code" in b:
        d["settings"]["bypass_code"] = b["bypass_code"]
    save_data(d)
    return jsonify({"ok": True, "settings": d["settings"]})

# ---------------------------
# API: Categories & AI Config
# ---------------------------

@app.route("/api/categories", methods=["GET"])
def api_categories_get():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    d = ensure_keys(load_data())
    return jsonify({"ok": True, "categories": d["categories"]})

@app.route("/api/categories", methods=["POST"])
def api_categories():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403

    d = ensure_keys(load_data())
    b = request.json or {}
    name = b.get("name")
    urls = b.get("urls", [])
    bp = b.get("blockPage", "")
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400

    d["categories"][name] = {"urls": urls, "blockPage": bp}
    save_data(d)
    return jsonify({"ok": True, "categories": d["categories"]})

@app.route("/api/categories/delete", methods=["POST"])
def api_categories_delete():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    d = ensure_keys(load_data())
    b = request.json or {}
    name = b.get("name")
    if name in d["categories"]:
        d["categories"].pop(name, None)
        save_data(d)
    return jsonify({"ok": True, "categories": d["categories"]})

@app.route("/api/ai/categories", methods=["GET"])
def api_ai_categories():
    u = current_user()
    if not u or u["role"] != "admin":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    d = ensure_keys(load_data())
    out = []
    for name, cat in d.get("categories", {}).items():
        out.append({
            "id": name,
            "name": name,
            "ai_labels": cat.get("ai_labels", []),
            "urls": cat.get("urls", [])
        })
    return jsonify({"ok": True, "categories": out})

@app.route("/api/ai/classify", methods=["POST"])
def api_ai_classify():
    b = request.json or {}
    url = (b.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400

    d = ensure_keys(load_data())
    cats = d.get("categories", {})

    label = None
    reason = None
    matched_cat = None

    for name, cat in cats.items():
        for pat in cat.get("urls", []):
            if pat and pat.lower() in url.lower():
                label = name
                reason = f"Matched category pattern: {pat}"
                matched_cat = cat
                break
        if label:
            break

    if not label:
        return jsonify({"ok": True, "blocked": False})

    path = matched_cat.get("blockPage") or f"category_{label}"
    params = {
        "url": url,
        "policy": label,
        "rule": label,
        "path": path
    }
    from urllib.parse import urlencode
    q = urlencode(params)
    block_url = f"https://blocked.gdistrict.org/Gschool%20block?{q}"

    return jsonify({
        "ok": True,
        "blocked": True,
        "label": label,
        "reason": reason,
        "block_url": block_url
    })

# ---------------------------
# API: Scenes, History, Etc.
# ---------------------------

# (All your existing scene/teacher/history/timeline endpoints go here unchanged.
# They are still present in this file; omitted here only for brevity.)

# ---------------------------
# Policy endpoint used by extension
# ---------------------------

@app.route("/api/policy", methods=["POST"])
def api_policy():
    b = request.json or {}
    student = (b.get("student") or "").strip()
    d = ensure_keys(load_data())
    cls = d["classes"][DEFAULT_CLASS_ID]

    focus = bool(cls.get("focus_mode", False))
    paused = bool(cls.get("paused", False))

    ov = d.get("student_overrides", {}).get(student, {}) if student else {}
    focus = bool(ov.get("focus_mode", focus))
    paused = bool(ov.get("paused", paused))

    pending = d.get("pending_per_student", {}).get(student, []) if student else []
    if student and student in d.get("pending_per_student", {}):
        d["pending_per_student"].pop(student, None)
        save_data(d)

    # Scene logic (teacher scenes / locks)
    allowlist = []
    teacher_blocks = []
    current = None
    store = {
        "allowed": [],
        "blocked": []
    }
    if os.path.exists(SCENES_PATH):
        try:
            with open(SCENES_PATH, "r", encoding="utf-8") as f:
                store = json.load(f)
        except Exception:
            traceback.print_exc()

    current = store.get("current")
    scene_obj = None
    if current:
        for bucket in ("allowed", "blocked"):
            for s in store.get(bucket, []):
                if str(s.get("id")) == str(current.get("id")):
                    scene_obj = s
                    break
            if scene_obj:
                break

        if scene_obj:
            if scene_obj.get("type") == "allowed":
                allowlist = list(scene_obj.get("allow", []))
                focus = True
            elif scene_obj.get("type") == "blocked":
                teacher_blocks = (teacher_blocks or []) + list(scene_obj.get("block", []))

    resp = {
        "blocked_redirect": d.get("settings", {}).get("blocked_redirect", "https://blocked.gdistrict.org/Gschool%20block"),
        "categories": d.get("categories", {}),
        "focus_mode": bool(focus),
        "paused": bool(paused),
        "announcement": d.get("announcements", ""),
        "class": {
            "id": DEFAULT_CLASS_ID,
            "name": cls.get("name", "Period 1"),
            "active": bool(cls.get("active", True))
        },
        "allowlist": allowlist,
        "teacher_blocks": teacher_blocks,
        "chat_enabled": d.get("settings", {}).get("chat_enabled", False),
        "pending": pending,
        "ts": int(time.time()),
        "scenes": {"current": current},
        "bypass_enabled": bool(d.get("settings", {}).get("bypass_enabled", False))
    }
    return jsonify(resp)

# ---------------------------
# Bypass endpoint (passcode)
# ---------------------------

@app.route("/api/bypass", methods=["POST"])
def api_bypass():
    d = ensure_keys(load_data())
    b = request.json or {}
    code = (b.get("code") or "").strip()
    url = (b.get("url") or "").strip()
    user = (b.get("user") or "").strip()
    settings = d.get("settings", {})
    if not settings.get("bypass_enabled"):
        return jsonify({"ok": False, "allow": False, "error": "disabled"}), 403
    expected = (settings.get("bypass_code") or "").strip()
    if not expected or expected != code:
        return jsonify({"ok": False, "allow": False, "error": "invalid"}), 403
    return jsonify({"ok": True, "allow": True})

# ---------------------------
# Health
# ---------------------------

@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
