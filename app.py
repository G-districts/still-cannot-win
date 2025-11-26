# =========================
# G-SCHOOLS CONNECT BACKEND
# =========================

import os, json, time, sqlite3, traceback, uuid, re
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

from flask import (
    Flask, request, jsonify, render_template,
    session, redirect, url_for, send_from_directory
)
from flask_cors import CORS

ROOT = os.path.dirname(__file__)
DATA_PATH = os.path.join(ROOT, "data.json")
DB_PATH = os.path.join(ROOT, "gschool.db")
SCENES_PATH = os.path.join(ROOT, "scenes.json")

app = Flask(__name__, static_url_path="/static", static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
CORS(app, supports_credentials=True)

# =========================
# Data & Database
# =========================

def db():
    """Open sqlite connection."""
    con = sqlite3.connect(DB_PATH)
    return con

def _init_db():
    """Create tables if missing."""
    con = db()
    cur = con.cursor()
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
    con.commit()
    con.close()

_init_db()

def get_setting(k, default=None):
    try:
        con = db()
        cur = con.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        row = cur.fetchone()
        con.close()
        if row:
            return json.loads(row[0])
    except Exception:
        traceback.print_exc()
    return default

def set_setting(k, v):
    try:
        con = db()
        cur = con.cursor()
        cur.execute("REPLACE INTO settings (k,v) VALUES (?,?)", (k, json.dumps(v)))
        con.commit()
        con.close()
    except Exception:
        traceback.print_exc()

def _default_data():
    return {
        "classes": {
            "period1": {
                "name": "Period 1",
                "active": True,
                "focus_mode": False,
                "paused": False,
                "allowlist": [],
                "teacher_blocks": []
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

def load_data():
    if not os.path.exists(DATA_PATH):
        d = _default_data()
        save_data(d)
        return d
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        d = _default_data()
        save_data(d)
        return d

def save_data(d):
    try:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        traceback.print_exc()

def ensure_keys(d):
    if "classes" not in d:
        d["classes"] = _default_data()["classes"]
    if "categories" not in d:
        d["categories"] = {}
    if "settings" not in d:
        d["settings"] = {"blocked_redirect": "https://blocked.gdistrict.org/Gschool%20block"}
    if "announcements" not in d:
        d["announcements"] = ""
    if "history" not in d:
        d["history"] = {}
    if "student_overrides" not in d:
        d["student_overrides"] = {}
    if "pending_per_student" not in d:
        d["pending_per_student"] = {}
    return d

# =========================
# Auth helpers
# =========================

def current_user():
    u = session.get("user")
    if not u:
        return None
    return u

def require_admin(fn):
    def wrapper(*a, **k):
        u = current_user()
        if not u or u.get("role") != "admin":
            return redirect(url_for("login_page"))
        return fn(*a, **k)
    wrapper.__name__ = fn.__name__
    return wrapper

# =========================
# Basic Pages
# =========================

@app.route("/")
def index():
    u = current_user()
    if not u:
        return redirect(url_for("login_page"))
    if u["role"] == "admin":
        return redirect(url_for("admin_page"))
    if u["role"] == "teacher":
        return redirect(url_for("teacher_page"))
    return redirect(url_for("teacher_page"))

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/admin")
def admin_page():
    u = current_user()
    if not u or u["role"] != "admin":
        return redirect(url_for("login_page"))
    return render_template("admin.html", data=load_data(), user=u)

@app.route("/teacher")
def teacher_page():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return redirect(url_for("login_page"))
    return render_template("teacher.html", data=load_data(), user=u)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login_page"))

# =========================
# Auth
# =========================

@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.json or request.form
    email = (body.get("email") or "").strip().lower()
    pw = body.get("password") or ""
    con = db(); cur = con.cursor()
    cur.execute("SELECT email,role FROM users WHERE email=? AND password=?", (email, pw))
    row = cur.fetchone()
    con.close()
    if row:
        session["user"] = {"email": row[0], "role": row[1]}
        return jsonify({"ok": True, "role": row[1]})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401

# =========================
# Core Data & Settings
# =========================

@app.route("/api/data")
def api_data():
    """Compatibility wrapper used by teacher.html's loadData()."""
    d = ensure_keys(load_data())
    cls = d["classes"].get("period1", {})
    return jsonify({
        "ok": True,
        "classes": d["classes"],
        "categories": d["categories"],
        "settings": d["settings"],
        "announcement": d.get("announcements", ""),
        "class": cls
    })

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

# =========================
# Categories & AI classify
# =========================

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

# =========================
# Scenes & Policy
# =========================

def _load_scenes():
    if not os.path.exists(SCENES_PATH):
        return {"allowed": [], "blocked": [], "current": None}
    try:
        with open(SCENES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        traceback.print_exc()
        return {"allowed": [], "blocked": [], "current": None}

def _save_scenes(store):
    try:
        with open(SCENES_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    except Exception:
        traceback.print_exc()

@app.route("/api/scenes", methods=["GET"])
def api_scenes_list():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    store = _load_scenes()
    return jsonify({"ok": True, "data": store})

@app.route("/api/scenes/save", methods=["POST"])
def api_scenes_save():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    store = _load_scenes()
    b = request.json or {}
    bucket = b.get("bucket") or "allowed"
    scene = b.get("scene") or {}
    if bucket not in ("allowed", "blocked"):
        return jsonify({"ok": False, "error": "bad bucket"}), 400
    if "id" not in scene:
        scene["id"] = str(uuid.uuid4())
    lst = store.get(bucket, [])
    new_list = [s for s in lst if str(s.get("id")) != str(scene["id"])]
    new_list.append(scene)
    store[bucket] = new_list
    _save_scenes(store)
    return jsonify({"ok": True, "scene": scene})

@app.route("/api/scenes/delete", methods=["POST"])
def api_scenes_delete():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    store = _load_scenes()
    b = request.json or {}
    sid = b.get("id")
    if not sid:
        return jsonify({"ok": False, "error": "missing id"}), 400
    for bucket in ("allowed", "blocked"):
        store[bucket] = [s for s in store.get(bucket, []) if str(s.get("id")) != str(sid)]
    if store.get("current") and str(store["current"].get("id")) == str(sid):
        store["current"] = None
    _save_scenes(store)
    return jsonify({"ok": True})

@app.route("/api/scenes/apply", methods=["POST"])
def api_scenes_apply():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    store = _load_scenes()
    b = request.json or {}
    sid = b.get("id")
    bucket = b.get("bucket") or "allowed"
    if bucket not in ("allowed", "blocked"):
        return jsonify({"ok": False, "error": "bad bucket"}), 400
    found = None
    for s in store.get(bucket, []):
        if str(s.get("id")) == str(sid):
            found = s
            break
    if not found:
        return jsonify({"ok": False, "error": "no scene"}), 404
    store["current"] = {"id": found["id"], "bucket": bucket}
    _save_scenes(store)
    return jsonify({"ok": True, "current": store["current"]})

@app.route("/api/scenes/clear", methods=["POST"])
def api_scenes_clear():
    u = current_user()
    if not u or u["role"] not in ("teacher", "admin"):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    store = _load_scenes()
    store["current"] = None
    _save_scenes(store)
    return jsonify({"ok": True})

@app.route("/api/policy", methods=["POST"])
def api_policy():
    b = request.json or {}
    student = (b.get("student") or "").strip()
    d = ensure_keys(load_data())
    cls = d["classes"]["period1"]

    # Base flags
    focus = bool(cls.get("focus_mode", False))
    paused = bool(cls.get("paused", False))

    # Per-student overrides
    ov = d.get("student_overrides", {}).get(student, {}) if student else {}
    focus = bool(ov.get("focus_mode", focus))
    paused = bool(ov.get("paused", paused))

    # deliver any per-student pending commands (one-shot)
    pending = d.get("pending_per_student", {}).get(student, []) if student else []
    if student and student in d.get("pending_per_student", {}):
        d["pending_per_student"].pop(student, None)
        save_data(d)

    # Scene merge logic
    store = _load_scenes()
    current = store.get("current") or None

    # Start with class-level lists
    allowlist = list(cls.get("allowlist", []))
    teacher_blocks = list(cls.get("teacher_blocks", []))

    if current:
        scene_obj = None
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
        "blocked_redirect": d.get("settings", {}).get(
            "blocked_redirect",
            "https://blocked.gdistrict.org/Gschool%20block"
        ),
        "categories": d.get("categories", {}),
        "focus_mode": bool(focus),
        "paused": bool(paused),
        "announcement": d.get("announcements", ""),
        "class": {
            "id": "period1",
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

# =========================
# Bypass endpoint
# =========================

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

# =========================
# Health
# =========================

@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
