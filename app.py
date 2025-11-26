# =========================
# G-SCHOOLS CONNECT BACKEND
# =========================
import os
import time
import random
import string
import hashlib
import json
from functools import wraps

from flask import Flask, request, jsonify, render_template, redirect, make_response, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash

# Config
ROOT = os.path.dirname(__file__)
DATA_PATH = os.path.join(ROOT, "data.json")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_API_URL = os.environ.get("AI_API_URL", "https://api.openai.com/v1/chat/completions")
APP_BASE = os.environ.get("APP_BASE", "https://gschool.gdistrict.org")
DEBUG = bool(os.environ.get("DEBUG", ""))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "devkey")

# ---------------
# Helper functions
# ---------------

def load_data():
    if not os.path.exists(DATA_PATH):
        return {}
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(d):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

def ensure_keys(d):
    if "users" not in d:
        d["users"] = {}
    if "classes" not in d:
        d["classes"] = {}
    if "scenes" not in d:
        d["scenes"] = {}
    if "categories" not in d:
        d["categories"] = {}
    if "settings" not in d:
        d["settings"] = {}
    if "tokens" not in d:
        d["tokens"] = {}
    if "announcements" not in d:
        d["announcements"] = ""
    return d

def get_cookie_token():
    return request.cookies.get("gstoken") or ""

def current_user():
    t = get_cookie_token()
    if not t:
        return None
    d = ensure_keys(load_data())
    toks = d.get("tokens", {})
    info = toks.get(t)
    if not info:
        return None
    if info.get("exp", 0) < int(time.time()):
        return None
    uid = info.get("user")
    return d.get("users", {}).get(uid)

def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or u.get("role") != "admin":
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

def rand_id(n=12):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))

def set_setting(k, v):
    # optional: also mirror some settings into environment or an in-memory cache if needed
    pass

# -----------------
# Basic web pages
# -----------------

@app.route("/")
def index():
    u = current_user()
    if not u:
        return redirect("/login")
    if u["role"] == "admin":
        return redirect("/admin")
    return redirect("/student")

@app.route("/login")
def login():
    return render_template("login.html", client_id=CLIENT_ID, base=APP_BASE)

@app.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.set_cookie("gstoken", "", expires=0)
    return resp

@app.route("/admin")
@require_admin
def admin():
    d = ensure_keys(load_data())
    return render_template("admin.html", data=d)

@app.route("/student")
def student():
    u = current_user()
    if not u:
        return redirect("/login")
    return render_template("student.html", user=u)

# ----------
# Auth / API
# ----------

@app.route("/api/google_signin", methods=["POST"])
def api_google_signin():
    body = request.json or {}
    email = (body.get("email") or "").strip().lower()
    name = body.get("name") or ""
    if not email:
        return jsonify({"ok": False, "error": "no email"}), 400

    d = ensure_keys(load_data())
    users = d["users"]
    u = users.get(email)
    if not u:
        # create student by default, admin must promote later
        u = {
            "id": email,
            "email": email,
            "name": name or email,
            "role": "student",
            "created": int(time.time())
        }
        users[email] = u
        save_data(d)

    toks = d.get("tokens", {})
    tok = rand_id(32)
    toks[tok] = {"user": email, "exp": int(time.time()) + 86400 * 7}
    d["tokens"] = toks
    save_data(d)

    resp = jsonify({"ok": True})
    resp.set_cookie("gstoken", tok, max_age=86400 * 7, httponly=True, samesite="Lax")
    return resp

# ----------------------
# Admin: Users & Classes
# ----------------------

@app.route("/api/users", methods=["GET"])
@require_admin
def api_users():
    d = ensure_keys(load_data())
    return jsonify({"ok": True, "users": list(d["users"].values())})

@app.route("/api/users", methods=["POST"])
@require_admin
def api_users_post():
    d = ensure_keys(load_data())
    b = request.json or {}
    email = (b.get("email") or "").strip().lower()
    name = b.get("name") or ""
    role = b.get("role") or "student"
    if not email:
        return jsonify({"ok": False, "error": "missing email"}), 400
    if role not in ("student", "admin", "teacher"):
        return jsonify({"ok": False, "error": "bad role"}), 400
    u = d["users"].get(email) or {
        "id": email,
        "email": email,
        "created": int(time.time())
    }
    u["name"] = name or email
    u["role"] = role
    d["users"][email] = u
    save_data(d)
    return jsonify({"ok": True, "user": u})

@app.route("/api/classes", methods=["GET"])
@require_admin
def api_classes():
    d = ensure_keys(load_data())
    return jsonify({"ok": True, "classes": d["classes"]})

@app.route("/api/classes", methods=["POST"])
@require_admin
def api_classes_post():
    d = ensure_keys(load_data())
    b = request.json or {}
    cid = b.get("id") or rand_id(6)
    name = b.get("name") or cid
    students = b.get("students") or []
    teacher = b.get("teacher") or ""
    cls = {
        "id": cid,
        "name": name,
        "students": students,
        "teacher": teacher,
        "active": bool(b.get("active", True))
    }
    d["classes"][cid] = cls
    save_data(d)
    return jsonify({"ok": True, "class": cls})

# -------------
# Scenes / SEB
# -------------

@app.route("/api/scenes", methods=["GET"])
@require_admin
def api_scenes():
    d = ensure_keys(load_data())
    return jsonify({"ok": True, "scenes": d.get("scenes", {})})

@app.route("/api/scenes", methods=["POST"])
@require_admin
def api_scenes_post():
    d = ensure_keys(load_data())
    b = request.json or {}
    sid = b.get("id") or rand_id(6)
    scene = {
        "id": sid,
        "name": b.get("name") or sid,
        "urls": b.get("urls") or [],
        "blocked": b.get("blocked") or [],
        "teacher": b.get("teacher") or "",
        "students": b.get("students") or [],
        "active": bool(b.get("active", False)),
        "focus_mode": bool(b.get("focus_mode", False))
    }
    d["scenes"][sid] = scene
    save_data(d)
    return jsonify({"ok": True, "scene": scene})

@app.route("/api/scenes/apply", methods=["POST"])
@require_admin
def api_scenes_apply():
    d = ensure_keys(load_data())
    b = request.json or {}
    sid = b.get("id")
    if not sid or sid not in d["scenes"]:
        return jsonify({"ok": False, "error": "no such scene"}), 400
    # "current" is just a single active scene id for now
    d["scenes"]["current"] = sid
    save_data(d)
    return jsonify({"ok": True, "current": sid})

@app.route("/api/scenes/clear", methods=["POST"])
@require_admin
def api_scenes_clear():
    d = ensure_keys(load_data())
    d["scenes"]["current"] = None
    save_data(d)
    return jsonify({"ok": True})

# -----------------
# Categories / AI / Policy
# -----------------

@app.route("/api/categories", methods=["GET"])
@require_admin
def api_categories():
    d = ensure_keys(load_data())
    return jsonify({"ok": True, "categories": d["categories"]})

@app.route("/api/categories", methods=["POST"])
@require_admin
def api_categories_post():
    d = ensure_keys(load_data())
    b = request.json or {}
    cid = b.get("id") or rand_id(6)
    name = b.get("name") or cid
    urls = b.get("urls") or []
    cat = {
        "id": cid,
        "name": name,
        "urls": urls,
        "path": b.get("path") or "",
        "ai_labels": b.get("ai_labels") or []
    }
    d["categories"][cid] = cat
    save_data(d)
    return jsonify({"ok": True, "category": cat})

@app.route("/api/ai/categories", methods=["GET"])
@require_admin
def api_ai_categories():
    d = ensure_keys(load_data())
    cats = []
    for cid, cat in d.get("categories", {}).items():
        cats.append({
            "id": cid,
            "name": cat.get("name", cid),
            "ai_labels": cat.get("ai_labels", [])
        })
    return jsonify({"ok": True, "categories": cats})

@app.route("/api/ai/classify", methods=["POST"])
def api_ai_classify():
    body = request.json or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "no url"}), 400

    d = ensure_keys(load_data())
    cats = d.get("categories", {})

    # Simple stub / placeholder classification based on patterns / labels
    # You can plug in OpenAI here using AI_API_URL / AI_API_KEY.
    label = None
    reason = None

    # Very simple: if URL matches any cat.urls, treat that as blocked by AI.
    for cid, cat in cats.items():
        for pat in cat.get("urls", []):
            if pat and pat.lower() in url.lower():
                label = cat.get("name", cid)
                reason = f"Matched category: {label}"
                break
        if label:
            break

    if not label:
        # For now, default to not blocked.
        return jsonify({"ok": True, "blocked": False})

    # Build a simple block_url that our extension will rewrite to the real block page.
    params = {
        "url": url,
        "policy": label,
        "rule": label,
        "path": cats.get(cid, {}).get("path", "")
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

# ----------------
# Policy endpoint
# ----------------

@app.route("/api/policy", methods=["GET"])
def api_policy():
    # Called by the extension to get current blocking config
    d = ensure_keys(load_data())
    u = current_user()
    # For now, we don't do per-user policy except teacher scenes & classes.

    # Scenes
    scenes = d.get("scenes", {})
    current = scenes.get("current")
    scene = scenes.get(current) if current else None

    focus = False
    teacher_blocks = []
    allowlist = []
    paused = False
    pending = []

    cls = {"name": "Period 1", "active": True}
    if scene:
        focus = bool(scene.get("focus_mode", False))
        teacher_blocks = scene.get("blocked", [])
        allowlist = scene.get("urls", [])

    resp = {
        "blocked_redirect": d.get("settings", {}).get("blocked_redirect", "https://blocked.gdistrict.org/Gschool%20block"),
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
        "bypass_enabled": bool(d.get("settings", {}).get("bypass_enabled", False)),
        "bypass_ttl_minutes": int(d.get("settings", {}).get("bypass_ttl_minutes", 10))
    }
    return jsonify(resp)

# --------------
# Global settings
# --------------

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
    if "bypass_ttl_minutes" in b:
        try:
            ttl = int(b["bypass_ttl_minutes"])
        except Exception:
            ttl = 10
        if ttl < 1:
            ttl = 1
        if ttl > 1440:
            ttl = 1440
        d["settings"]["bypass_ttl_minutes"] = ttl
    save_data(d)
    return jsonify({"ok": True, "settings": d["settings"]})

# --------------
# Bypass endpoint
# --------------

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
    # In the future, add per-URL persistence if desired.
    return jsonify({"ok": True, "allow": True})

# --------------
# Announcements
# --------------

@app.route("/api/announcement", methods=["POST"])
@require_admin
def api_announcement():
    d = ensure_keys(load_data())
    b = request.json or {}
    text = b.get("text") or ""
    d["announcements"] = text
    save_data(d)
    return jsonify({"ok": True})

# -----------------
# Static / assets
# -----------------

@app.route("/static/<path:path>")
def static_files(path):
    return send_from_directory(os.path.join(ROOT, "static"), path)

# --------------
# Health / debug
# --------------

@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
