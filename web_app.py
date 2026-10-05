#!/usr/bin/env python3
"""
CairoAi Web v7.0 - Multi-User AI Video Generator
- 3 مستخدمين: Sharo, Nona, Youssif
- باسورد واحد: ghazi
- كل مستخدم يشوف فيديوهاته بس
- 6 Spaces + Custom
- v7 auto-fallback بين الحسابات
"""
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, flash, jsonify, redirect, render_template_string,
                   request, send_file, session, url_for)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from cairoai import (
    get_spaces_for_user,
    extract_reset_time,
    SPACES,
    generate_video,
    generate_video_multi,
    get_account_by_id,
    get_active_account,
    get_available_accounts,
    load_config,
    save_config,
    check_quota,
    test_token,
    update_quota,
)

# ═══════════════════════════════════════════════════════════════════════
#  App Setup
# ═══════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

_boot_cfg = load_config()
app.secret_key = _boot_cfg.get("auth", {}).get("session_secret", "cairoai-v7-fallback-secret")
app.permanent_session_lifetime = timedelta(days=7)


@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


OUTPUT_DIR = Path("outputs")
UPLOAD_DIR = Path("uploads")
INPUT_DIR = Path("inputs")
for d in [OUTPUT_DIR, UPLOAD_DIR, INPUT_DIR]:
    d.mkdir(exist_ok=True)

ALLOWED_IMG = {"png", "jpg", "jpeg", "webp"}

def _load_users():
    """اقرأ users من config.json"""
    cfg = load_config()
    users = cfg.get("users", [])
    if not users:
        # fallback
        users = [
            {"id": "sharo", "name": "Sharo", "display": "Sharo", "accounts": []},
            {"id": "nona", "name": "Nona", "display": "Nona", "accounts": []},
            {"id": "youssif", "name": "Youssif", "display": "Youssif", "accounts": []},
        ]
    return users

USERS = _load_users()
USER_IDS = {u["id"] for u in USERS}

# ═══════════════════════════════════════════════════════════════════════
#  Auth
# ═══════════════════════════════════════════════════════════════════════
_login_attempts = {}
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


def _is_rate_limited(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def _record_attempt(ip):
    _login_attempts.setdefault(ip, []).append(time.time())


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"success": False, "error": "unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def current_user_id():
    return session.get("user_id")


def current_user_name():
    return session.get("user_name", "?")


def get_user_accounts(user_id):
    """حسابات المستخدم (IDs)"""
    users = load_config().get("users", [])
    for u in users:
        if u.get("id") == user_id:
            return u.get("accounts", [])
    return []


def get_all_spaces_for_user(user_id):
    """SPACES الافتراضية + custom_spaces للمستخدم"""
    all_spaces = dict(SPACES)
    cfg = load_config()
    for u in cfg.get("users", []):
        if u.get("id") == user_id:
            for csp in u.get("custom_spaces", []):
                key = csp.get("key")
                if key:
                    all_spaces[key] = {
                        "id": csp.get("id", ""),
                        "name": csp.get("name", "Custom"),
                        "type": "custom",
                        "endpoint": csp.get("endpoint", "/video"),
                        "needs_image": csp.get("needs_image", True),
                        "max_duration": 10,
                        "default_duration": 2,
                    }
            break
    return all_spaces


def get_hidden_spaces(user_id):
    """الـ spaces الافتراضية المحذوفة للمستخدم"""
    from cairoai import SPACES as _ALL
    cfg = load_config()
    hidden = {}
    for u in cfg.get("users", []):
        if u.get("id") == user_id:
            for skey in u.get("hidden_default_spaces", []):
                if skey in _ALL:
                    hidden[skey] = _ALL[skey]
            break
    return hidden


def get_user_custom_spaces(user_id):
    """custom spaces الخاصة بمستخدم"""
    cfg = load_config()
    for u in cfg.get("users", []):
        if u.get("id") == user_id:
            return u.get("custom_spaces", [])
    return []


def get_user_account_objects(user_id):
    """كائنات الحسابات كاملة للمستخدم"""
    cfg = load_config()
    ids = set(get_user_accounts(user_id))
    return [a for a in cfg.get("accounts", []) if a["id"] in ids]


def save_user_accounts(user_id, account_ids):
    """حفظ حساب المستخدم"""
    cfg = load_config()
    for u in cfg.get("users", []):
        if u.get("id") == user_id:
            u["accounts"] = account_ids
            break
    save_config(cfg)


def make_thumbnail(video_path, thumb_path):
    """استخراج frame من الفيديو كـ thumbnail"""
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-ss", "00:00:01",   # عند الثانية 1
                "-vframes", "1",       # إطار واحد
                "-vf", "scale=320:-1", # عرض 320 px
                "-q:v", "5",           # جودة متوسطة
                str(thumb_path),
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            print(f"⚠️ ffmpeg فشل: {result.stderr.decode()[:200]}")
            return False
        return True
    except FileNotFoundError:
        print("⚠️ ffmpeg مش مثبت")
        return False
    except Exception as e:
        print(f"⚠️ thumbnail فشل: {e}")
        return False


def user_dir(user_id):
    d = OUTPUT_DIR / user_id
    d.mkdir(exist_ok=True)
    return d


def allowed_file(fn):
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_IMG


def save_upload(file, prefix, user_id):
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename):
        return None
    fname = f"{user_id}_{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.png"
    path = UPLOAD_DIR / fname
    file.save(str(path))
    return path


def user_videos(user_id):
    d = OUTPUT_DIR / user_id
    if not d.exists():
        return []
    vids = []
    for f in sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        thumb_path = d / f"{f.name}.jpg"
        thumbnail = f"/thumb/{user_id}/{f.name}" if thumb_path.exists() else None
        vids.append({
            "name": f.name,
            "url": f"/video/{user_id}/{f.name}",
            "thumbnail": thumbnail,
            "size_mb": round(st.st_size / 1024 / 1024, 2),
            "date": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return vids


# ═══════════════════════════════════════════════════════════════════════
#  HTML
# ═══════════════════════════════════════════════════════════════════════
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CairoAi - تسجيل الدخول</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Cairo', sans-serif;
  background: linear-gradient(135deg, #0a0e1a 0%, #1a1040 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #e5e7eb;
  padding: 1rem;
}
.box {
  background: rgba(17, 24, 39, 0.95);
  padding: 2.5rem 2rem;
  border-radius: 20px;
  border: 1px solid #1f2937;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 25px 60px rgba(0, 0, 0, 0.5);
}
h1 {
  text-align: center;
  font-size: 2rem;
  background: linear-gradient(135deg, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 0.5rem;
}
.subtitle { text-align: center; color: #6b7280; font-size: 0.9rem; margin-bottom: 2rem; }
label { display: block; margin-bottom: 6px; color: #c4b5fd; font-weight: 700; font-size: 13px; }
select, input[type="password"] {
  width: 100%; padding: 0.9rem 1rem;
  background: #1f2937; border: 1px solid #374151;
  border-radius: 12px; color: #e5e7eb;
  font-size: 1rem; margin-bottom: 1rem;
  font-family: inherit;
}
select:focus, input:focus { outline: none; border-color: #8b5cf6; box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2); }
select option { background: #1f2937; color: #fff; }
button {
  width: 100%; padding: 0.9rem;
  background: linear-gradient(135deg, #3b82f6, #8b5cf6);
  color: white; border: none; border-radius: 12px;
  font-size: 1.05rem; font-weight: 700;
  cursor: pointer; transition: all 0.2s;
  font-family: inherit;
}
button:hover { opacity: 0.9; transform: translateY(-1px); }
.error {
  background: rgba(127, 29, 29, 0.4); color: #fca5a5;
  padding: 0.8rem; border-radius: 10px; margin-bottom: 1.2rem;
  text-align: center; font-size: 0.9rem;
  border: 1px solid rgba(220, 38, 38, 0.3);
}
.footer { text-align: center; margin-top: 1.5rem; color: #4b5563; font-size: 0.75rem; }
</style>
</head>
<body>
<div class="box">
  <h1>🎬 CairoAi</h1>
  <div class="subtitle">مولّد الفيديو بالذكاء الاصطناعي</div>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="POST" action="/login">
    <input type="hidden" name="next" value="{{ next }}">
    <label>اختر اسمك</label>
    <select name="user_id" required>
      <option value="">— اختر —</option>
      {% for u in users %}
      <option value="{{ u.id }}">{{ u.display }}</option>
      {% endfor %}
    </select>
    <label>كلمة المرور</label>
    <input type="password" name="password" placeholder="••••••" required autocomplete="current-password">
    <button type="submit">🔓 دخول</button>
  </form>
  <div class="footer">محمي بكلمة مرور • CairoAi v7.0</div>
</div>
</body>
</html>
"""


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CairoAi v7.0</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --primary: #6366f1;
  --secondary: #8b5cf6;
  --bg-dark: #0f172a;
  --card-bg: rgba(255, 255, 255, 0.07);
  --border: rgba(255, 255, 255, 0.12);
  --text-muted: #a5b4fc;
  --success: #10b981;
  --danger: #ef4444;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Cairo', sans-serif;
  background: linear-gradient(135deg, var(--bg-dark) 0%, #1e1b4b 50%, #312e81 100%);
  background-attachment: fixed;
  min-height: 100vh;
  color: #fff;
  padding: 20px 15px;
  line-height: 1.6;
}
.container { max-width: 900px; margin: 0 auto; }
.header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 25px;
  padding: 0 10px;
}
.header h1 {
  font-size: 28px; font-weight: 800;
  background: linear-gradient(135deg, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.header .user-area { display: flex; align-items: center; gap: 10px; }
.user-badge {
  background: rgba(139, 92, 246, 0.2);
  padding: 8px 16px; border-radius: 12px;
  font-weight: 700; font-size: 14px;
  border: 1px solid rgba(139, 92, 246, 0.4);
}
.logout-btn {
  color: #fca5a5; text-decoration: none;
  padding: 8px 14px; border-radius: 10px;
  font-weight: 700; font-size: 13px;
  border: 1px solid rgba(220, 38, 38, 0.3);
  transition: all 0.2s;
}
.logout-btn:hover { background: rgba(220, 38, 38, 0.15); }
.card {
  background: var(--card-bg);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 25px;
  margin-bottom: 20px;
}
.flash {
  padding: 14px 18px; border-radius: 12px;
  margin-bottom: 15px; font-weight: 700;
}
.flash.success { background: rgba(16, 185, 129, 0.15); border: 1px solid var(--success); color: #6ee7b7; }
.flash.error { background: rgba(239, 68, 68, 0.15); border: 1px solid var(--danger); color: #fca5a5; }
label { display: block; margin-bottom: 6px; font-weight: 700; color: #e2e8f0; font-size: 13px; }
select, textarea, input[type="text"], input[type="number"], input[type="file"] {
  width: 100%; padding: 12px 14px; border-radius: 12px;
  border: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.25);
  color: #fff; font-size: 14px; font-family: inherit;
  font-weight: 600; margin-bottom: 8px;
}
select:focus, textarea:focus, input:focus {
  outline: none; border-color: var(--secondary);
  box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2);
}
select option { background: var(--bg-dark); color: #fff; }
textarea { resize: vertical; min-height: 110px; line-height: 1.7; }
.tabs { display: flex; gap: 8px; margin-bottom: 20px; }
.tab {
  flex: 1; text-align: center; padding: 14px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  color: var(--text-muted);
  cursor: pointer; transition: all 0.3s;
  font-size: 15px; font-weight: 700;
}
.tab.active {
  background: linear-gradient(135deg, var(--primary), var(--secondary));
  color: #fff; border-color: transparent;
}
.mode-picker { display: flex; gap: 8px; margin-bottom: 15px; }
.mode-opt {
  flex: 1; text-align: center; padding: 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 2px solid var(--border);
  border-radius: 14px; cursor: pointer;
  font-weight: 700; font-size: 14px;
  transition: all 0.2s;
}
.mode-opt.active {
  border-color: var(--secondary);
  background: rgba(139, 92, 246, 0.15);
}
.mode-opt input { display: none; }
.row { display: flex; gap: 10px; }
.row > * { flex: 1; }
.examples { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
.example-btn {
  padding: 6px 12px; border-radius: 8px;
  background: rgba(139, 92, 246, 0.15);
  border: 1px solid rgba(139, 92, 246, 0.3);
  color: #c4b5fd; cursor: pointer;
  font-size: 12px; font-weight: 700;
  transition: all 0.2s;
  font-family: inherit;
}
.example-btn:hover { background: rgba(139, 92, 246, 0.3); }
.generate-btn {
  width: 100%; padding: 16px;
  border-radius: 16px; border: none;
  background: linear-gradient(135deg, var(--primary), var(--secondary));
  color: #fff; font-size: 17px; font-weight: 800;
  cursor: pointer; margin-top: 20px;
  font-family: inherit;
  box-shadow: 0 8px 25px rgba(99, 102, 241, 0.4);
  transition: all 0.3s;
}
.generate-btn:hover { transform: translateY(-2px); }
.generate-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.videos-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px;
  margin-top: 15px;
}
.video-card {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  transition: all 0.3s;
}
.video-card:hover { transform: translateY(-3px); border-color: var(--secondary); }
.video-card video {
  width: 100%; height: 180px;
  object-fit: cover; background: #000;
  display: block;
}
.video-card .info { padding: 12px; }
.video-card .filename {
  font-size: 11px; color: var(--text-muted);
  font-family: monospace;
  word-break: break-all;
  margin-bottom: 8px;
}
.video-card .meta {
  display: flex; justify-content: space-between;
  align-items: center; font-size: 11px;
  color: #94a3b8;
}
.video-card .download-btn {
  display: inline-block; padding: 5px 10px;
  background: rgba(16, 185, 129, 0.2);
  color: #6ee7b7; text-decoration: none;
  border-radius: 6px; font-weight: 700;
  font-size: 11px;
}
.empty-state {
  text-align: center; padding: 50px 20px;
  color: #94a3b8;
}
.empty-state .icon { font-size: 60px; margin-bottom: 15px; }
.loading-overlay {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(15, 23, 42, 0.95);
  backdrop-filter: blur(8px);
  display: none; flex-direction: column;
  justify-content: center; align-items: center;
  z-index: 1000;
}
.loading-overlay.active { display: flex; }
.spinner {
  border: 5px solid rgba(255, 255, 255, 0.1);
  border-top: 5px solid var(--secondary);
  border-radius: 50%; width: 70px; height: 70px;
  animation: spin 1s linear infinite;
  margin-bottom: 20px;
}
@keyframes spin { to { transform: rotate(360deg); } }
.loading-text { font-size: 18px; font-weight: 700; }
.tab-content { display: none; }
.tab-content.active { display: block; }
@media (max-width: 600px) {
  .header h1 { font-size: 22px; }
  .videos-grid { grid-template-columns: 1fr; }
}

        /* ═══ Accounts Section (v6.1 style) ═══ */
        .accounts-section {
          background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(139, 92, 246, 0.15));
          border: 1px solid rgba(139, 92, 246, 0.4);
        }
        .section-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 15px;
        }
        .section-header h3 { font-size: 16px; color: #c4b5fd; }
        .account-active {
          background: rgba(16, 185, 129, 0.1);
          border: 1px solid rgba(16, 185, 129, 0.4);
          border-radius: 14px;
          padding: 15px;
          margin-bottom: 20px;
        }
        .account-active .label { font-size: 12px; color: #94a3b8; margin-bottom: 8px; }
        .account-active .active-name {
          font-size: 16px; font-weight: 700; margin-bottom: 12px; color: #6ee7b7;
        }
        .accounts-list { display: flex; flex-direction: column; gap: 12px; margin-top: 15px; }
        .account-item {
          display: flex; align-items: center; gap: 10px;
          padding: 14px; border-radius: 14px;
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.12);
          transition: all 0.3s; flex-wrap: wrap;
        }
        .account-item.active {
          background: rgba(16, 185, 129, 0.15);
          border-color: rgba(16, 185, 129, 0.5);
          box-shadow: 0 0 20px rgba(16, 185, 129, 0.2);
        }
        .account-item .account-info { flex: 1; min-width: 200px; }
        .account-item .account-name { font-size: 14px; font-weight: 700; margin-bottom: 4px; }
        .account-item .account-preview {
          font-size: 11px; color: #a5b4fc; font-family: monospace; margin-top: 3px;
        }
        .account-item .account-quota {
          font-size: 11px; margin-top: 6px;
          display: flex; gap: 8px; flex-wrap: wrap;
        }
        .quota-bar { display: inline-block; padding: 3px 10px; border-radius: 8px; font-weight: 700; }
        .quota-ok { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }
        .quota-warn { background: rgba(245, 158, 11, 0.2); color: #fcd34d; }
        .quota-full { background: rgba(239, 68, 68, 0.2); color: #fca5a5; }
        .add-account-form {
          margin-top: 20px; padding: 15px;
          background: rgba(139, 92, 246, 0.08);
          border: 1px dashed rgba(139, 92, 246, 0.4);
          border-radius: 14px;
        }

    
        /* ═══ Account Cards ═══ */
        .acc-item-card {
          background: rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.12);
          border-radius: 14px;
          padding: 16px;
          margin-bottom: 4px;
          transition: all 0.3s;
        }
        .acc-item-card.active {
          background: rgba(16, 185, 129, 0.12);
          border: 2px solid rgba(16, 185, 129, 0.6);
          box-shadow: 0 0 25px rgba(16, 185, 129, 0.25);
        }
        .acc-item-header {
          display: flex;
          align-items: center;
          gap: 10px;
          margin-bottom: 10px;
        }
        .acc-icon { font-size: 18px; }
        .acc-email {
          font-size: 15px;
          font-weight: 700;
          color: #fff;
          word-break: break-all;
        }
        .acc-token-row {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          background: rgba(0, 0, 0, 0.25);
          border-radius: 8px;
          margin-bottom: 10px;
        }
        .acc-token-label { font-size: 14px; }
        .acc-token-value {
          font-family: monospace;
          font-size: 12px;
          color: #a5b4fc;
          word-break: break-all;
        }
        .acc-quota-row {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-bottom: 10px;
        }
        .acc-actions-row {
          display: flex;
          justify-content: flex-start;
          gap: 8px;
          margin-top: 8px;
        }
        .acc-divider {
          border: none;
          border-top: 1px dashed rgba(255, 255, 255, 0.15);
          margin: 12px 0;
        }

    
        .acc-token-form { margin: 0; }
        .acc-token-input {
          flex: 1;
          background: transparent;
          border: none;
          color: #a5b4fc;
          font-family: monospace;
          font-size: 12px;
          padding: 4px 6px;
          outline: none;
          min-width: 0;
          transition: all 0.2s;
          border-radius: 6px;
        }
        .acc-token-input:focus {
          background: rgba(0, 0, 0, 0.3);
          color: #fff;
          box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.4);
        }
        .acc-save-btn {
          padding: 6px 12px;
          font-size: 14px;
          border-radius: 8px;
          background: rgba(16, 185, 129, 0.2);
          color: #6ee7b7;
          border: 1px solid rgba(16, 185, 129, 0.4);
          cursor: pointer;
          transition: all 0.2s;
        }
        .acc-save-btn:hover {
          background: rgba(16, 185, 129, 0.4);
        }

    
        .acc-active-top {
          background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(16, 185, 129, 0.05));
          border: 2px solid rgba(16, 185, 129, 0.5);
          border-radius: 14px;
          padding: 16px;
          margin-bottom: 20px;
          box-shadow: 0 0 25px rgba(16, 185, 129, 0.15);
        }
        .acc-active-label {
          font-size: 12px;
          color: #94a3b8;
          margin-bottom: 6px;
        }
        .acc-active-name {
          font-size: 16px;
          font-weight: 800;
          color: #6ee7b7;
          margin-bottom: 14px;
        }
        .acc-active-token-label {
          font-size: 12px;
          color: #94a3b8;
          margin-bottom: 8px;
        }

    
        .acc-active-badge {
          background: rgba(16, 185, 129, 0.25);
          color: #6ee7b7;
          border: 1px solid rgba(16, 185, 129, 0.5);
          padding: 3px 10px;
          border-radius: 12px;
          font-size: 11px;
          font-weight: 700;
          margin-inline-start: auto;
        }

    
        .acc-status-box {
          padding: 12px 14px;
          border-radius: 12px;
          margin: 10px 0;
          border: 2px solid;
        }
        .acc-status-ready {
          background: rgba(16, 185, 129, 0.12);
          border-color: rgba(16, 185, 129, 0.5);
        }
        .acc-status-limited {
          background: rgba(245, 158, 11, 0.12);
          border-color: rgba(245, 158, 11, 0.5);
        }
        .acc-status-exhausted {
          background: rgba(239, 68, 68, 0.12);
          border-color: rgba(239, 68, 68, 0.5);
        }
        .acc-status-unknown {
          background: rgba(148, 163, 184, 0.12);
          border-color: rgba(148, 163, 184, 0.4);
        }
        .acc-status-title {
          font-size: 15px;
          font-weight: 800;
          margin-bottom: 4px;
        }
        .acc-status-ready .acc-status-title { color: #6ee7b7; }
        .acc-status-limited .acc-status-title { color: #fcd34d; }
        .acc-status-exhausted .acc-status-title { color: #fca5a5; }
        .acc-status-unknown .acc-status-title { color: #cbd5e1; }
        .acc-status-detail {
          font-size: 12px;
          color: #94a3b8;
        }

    </style>
</head>
<body>
<div class="loading-overlay" id="loadingOverlay">
  <div class="spinner"></div>
  <div class="loading-text">⏳ جاري توليد الفيديو...</div>
  <p style="margin-top:10px; color:#94a3b8; font-size:13px;">قد يستغرق 1-5 دقائق</p>
</div>

<div class="container">
  <div class="header">
    <h1>🎬 CairoAi</h1>
    <div class="user-area">
      <div class="user-badge">👤 {{ user_name }}</div>
      <button onclick="switchTab('settings')" class="logout-btn" style="cursor:pointer;background:none;border:1px solid rgba(139,92,246,0.4);color:#c4b5fd;padding:8px 14px;">⚙️</button>
      <a href="/logout" class="logout-btn">🚪 خروج</a>
    </div>
  </div>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
      <div class="flash {{ category }}">{{ message }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <div class="tabs">
    <div class="tab active" onclick="switchTab('generate')">🎬 توليد فيديو</div>
    <div class="tab" onclick="switchTab('my-videos')">📼 فيديوهاتي ({{ videos_count }})</div>
  </div>

  <div id="tab-generate" class="tab-content active">
    {% if error %}
    <div class="card">
      <div class="flash error">❌ {{ error }}</div>
    </div>
    {% endif %}

    {% if video_url %}
    <div class="card">
      <h2 style="text-align:center; margin-bottom:12px;">🎉 الفيديو جاهز!</h2>
      <video controls autoplay loop style="width:100%; border-radius:15px;">
        <source src="{{ video_url }}" type="video/mp4">
      </video>
      <div style="text-align:center; margin-top:12px;">
        <a href="{{ video_url }}" download class="download-btn" style="padding:10px 22px; font-size:14px;">⬇️ تنزيل</a>
      </div>
    </div>
    {% endif %}

    <form method="POST" enctype="multipart/form-data" id="genForm">
      <input type="hidden" name="action" value="generate">

      <div class="card">
        <label>🎯 نوع التوليد</label>
        <div class="mode-picker">
          <label class="mode-opt active" id="mode_img">
            <input type="radio" name="mode" value="img2video" checked onchange="setMode('img2video')">
            🖼️ صورة → فيديو
          </label>
          <label class="mode-opt" id="mode_text">
            <input type="radio" name="mode" value="text2video" onchange="setMode('text2video')">
            📝 نص → فيديو
          </label>
        </div>

        <label>🚀 Space</label>
        <select name="space">
          {% for key, sp in spaces.items() %}
          {% set _modes = sp.get('modes', []) %}
          <option value="{{ key }}"
                  data-modes="{{ ','.join(_modes) }}"
                  data-needs-img="{{ 1 if sp.needs_image else 0 }}"
                  {% if key == default_space %}selected{% endif %}>
            {{ sp.name }}{% if 'image-to-video' in _modes and 'text-to-video' in _modes %} 🖼️📝{% elif 'image-to-video' in _modes %} 🖼️{% elif 'text-to-video' in _modes %} 📝{% endif %}
          </option>
          {% endfor %}
        </select>

        <div class="row">
          <div>
            <label>⏱️ المدة (ثواني)</label>
            <input type="number" name="duration" value="4" min="1" max="10" step="1">
          </div>
          <div>
            <label>📐 الأبعاد</label>
            <select name="aspect">
              <option value="9:16">📱 عمودي (9:16)</option>
              <option value="16:9">🖥️ أفقي (16:9)</option>
              <option value="1:1">⬜ مربع (1:1)</option>
            </select>
          </div>
        </div>
      </div>

      <div class="card" id="imageCard">
        <label>🖼️ الصورة (اختياري)</label>
        <input type="file" name="image" accept="image/*">
        <p style="font-size:11px; color:#94a3b8; margin-top:4px;">
          ✅ PNG / JPG / JPEG / WEBP — حتى 50 MB
        </p>
      </div>

      <div class="card">
        <label>📝 وصف الحركة (Prompt)</label>
        <div class="examples">
          <button type="button" class="example-btn" onclick="addExample('cinematic')">🎬 سينمائي</button>
          <button type="button" class="example-btn" onclick="addExample('nature')">🌸 طبيعة</button>
          <button type="button" class="example-btn" onclick="addExample('portrait')">👤 شخص</button>
          <button type="button" class="example-btn" onclick="addExample('dynamic')">⚡ ديناميكي</button>
        </div>
        <textarea name="prompt" id="promptInput" placeholder="اكتب وصف الحركة اللي عايزها..." required></textarea>

        <button type="submit" class="generate-btn" id="submitBtn">
          🎬 توليد الفيديو
        </button>
      </div>
    </form>
  </div>

  <div id="tab-my-videos" class="tab-content">
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; flex-wrap:wrap; gap:10px;">
        <h2 style="margin:0;">📼 فيديوهاتي ({{ videos_count }})</h2>
        {% if videos %}
        <form method="POST" style="margin:0;" onsubmit="return confirm('⚠️ حذف كل الفيديوهات؟\n\nلا يمكن التراجع!');">
          <input type="hidden" name="action" value="delete_all_videos">
          <button type="submit" class="btn-sm btn-del" style="padding:8px 16px; font-size:13px;">
            🗑️ حذف الكل
          </button>
        </form>
        {% endif %}
      </div>
      {% if videos %}
      <div class="videos-grid">
        {% for v in videos %}
        <div class="video-card">
          <video preload="none" controls muted
                 poster="/thumb/{{ user_id }}/{{ v.name }}"
                 onclick="this.play()">
            <source src="{{ v.url }}" type="video/mp4">
          </video>
          <div class="info">
            <div class="filename">{{ v.name }}</div>
            <div class="meta">
              <span>📦 {{ v.size_mb }} MB</span>
              <span>🕐 {{ v.date }}</span>
            </div>
            <div style="margin-top:10px; display:flex; gap:6px;">
              <a href="{{ v.url }}" download class="download-btn" style="flex:1; text-align:center;">
                ⬇️ تحميل
              </a>
              <form method="POST" style="margin:0; flex:1;" onsubmit="return confirm('حذف {{ v.name }}؟');">
                <input type="hidden" name="action" value="delete_video">
                <input type="hidden" name="filename" value="{{ v.name }}">
                <button type="submit" class="btn-sm btn-del" style="width:100%; padding:8px; font-size:12px;">
                  🗑️ حذف
                </button>
              </form>
            </div>
          </div>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty-state">
        <div class="icon">🎬</div>
        <p>مافيش فيديوهات لحد الآن</p>
        <p style="font-size:13px; margin-top:8px;">ابدأ بـ توليد فيديو من التاب الأول</p>
      </div>
      {% endif %}
    </div>
  </div>

  <!-- TAB: Settings ⚙️ -->
  <div id="tab-settings" class="tab-content">
    <div class="card accounts-section">
      <div class="section-header">
        <h3>👥 إدارة الحسابات</h3>
        <span style="font-size:12px;color:#94a3b8;">{{ user_accounts|length }} حساب</span>
      </div>

      {% if user_accounts %}
<div class="accounts-list">
      {% for acc in user_accounts %}
        <!-- كل حساب في كارت -->
        <div class="acc-item-card {% if loop.first %}active{% endif %}">
          <div class="acc-item-header">
            <span class="acc-icon">{% if loop.first %}🟢{% else %}⚪{% endif %}</span>
            <span class="acc-email">{{ acc.name }}</span>
            {% if loop.first %}
            <span class="acc-active-badge">النشط حالياً</span>
            {% endif %}
          </div>
          
          <form method="POST" class="acc-token-form">
            <input type="hidden" name="action" value="update_token">
            <input type="hidden" name="account_id" value="{{ acc.id }}">
            <div class="acc-token-row">
              <span class="acc-token-label">🔐</span>
              <input type="text" name="new_token" value="{{ acc.token }}" 
                     class="acc-token-input" spellcheck="false">
              <button type="submit" class="btn btn-success acc-save-btn">💾</button>
            </div>
          </form>
          
          {% set r_runs = acc.quota.remaining_runs if acc.quota else 0 %}
          {% set r_secs = acc.quota.remaining_seconds if acc.quota else 0 %}
          {% set has_quota = acc.quota is not none %}
          
          {% if has_quota %}
            {% if r_runs >= 2 and r_secs >= 120 %}
              {% set status = 'ready' %}
            {% elif r_runs > 0 and r_secs > 0 %}
              {% set status = 'limited' %}
            {% else %}
              {% set status = 'exhausted' %}
            {% endif %}
          {% else %}
            {% set status = 'unknown' %}
          {% endif %}

          <div class="acc-status-box acc-status-{{ status }}">
            {% if status == 'ready' %}
              <div class="acc-status-title">🟢 جاهز للاستخدام</div>
              <div class="acc-status-detail">سيُستخدم للتوليد</div>
            {% elif status == 'limited' %}
              <div class="acc-status-title">🟡 رصيد محدود</div>
              <div class="acc-status-detail">يكفي لمحاولة واحدة تقريباً</div>
            {% elif status == 'exhausted' %}
              <div class="acc-status-title">🔴 خلص الرصيد</div>
              <div class="acc-status-detail">
                لن يولّد فيديوهات الآن
                {% if acc.reset_at %}
                  <br>⏰ يُجدَّد: {{ acc.reset_at }}
                {% else %}
                  <br>⏰ يُجدَّد خلال ~24 ساعة
                {% endif %}
              </div>
            {% else %}
              <div class="acc-status-title">⚪ لم يُفحص بعد</div>
              <div class="acc-status-detail">اضغط "🔍 فحص" لجلب الحالة</div>
            {% endif %}
          </div>

          {% if has_quota %}
          <div class="acc-quota-row">
            <span class="quota-bar {% if r_runs == 0 %}quota-full{% elif r_runs < 3 %}quota-warn{% else %}quota-ok{% endif %}">
              🎬 {{ r_runs }} / {{ acc.quota.total_runs }} طلب
            </span>
            <span class="quota-bar {% if r_secs == 0 %}quota-full{% elif r_secs < 60 %}quota-warn{% else %}quota-ok{% endif %}">
              ⏱️ {{ r_secs|round(0)|int }}s
            </span>
            {% if acc.last_check %}
            <span class="quota-bar" style="background: rgba(148,163,184,0.15); color: #94a3b8;">
              🕐 {{ acc.last_check }}
            </span>
            {% endif %}
          </div>
          {% endif %}
          
          <div class="acc-actions-row">
            {% if not loop.first %}
            <form method="POST" style="margin:0;">
              <input type="hidden" name="action" value="activate_account">
              <input type="hidden" name="account_id" value="{{ acc.id }}">
              <button type="submit" class="btn btn-primary" style="padding:8px 16px;font-size:13px;">⭐ تعيين</button>
            </form>
            {% endif %}
            
            <form method="POST" style="margin:0;">
              <input type="hidden" name="action" value="check_quota">
              <input type="hidden" name="account_id" value="{{ acc.id }}">
              <button type="submit" class="btn btn-warning" style="padding:8px 16px;font-size:13px;">🔍 فحص الـ quota</button>
            </form>
            
            <form method="POST" style="margin:0;" onsubmit="return confirm('هل أنت متأكد من حذف {{ acc.name }}؟');">
              <input type="hidden" name="action" value="delete_account">
              <input type="hidden" name="account_id" value="{{ acc.id }}">
              <button type="submit" class="btn btn-danger" style="padding:8px 16px;font-size:13px;">🗑️ حذف</button>
            </form>
          </div>
        </div>
        
        {% if not loop.last %}
        <hr class="acc-divider">
        {% endif %}
      {% endfor %}
      </div>
      {% else %}
      <p style="text-align:center;padding:20px;color:#94a3b8;">مافيش حسابات. ضيف حسابك.</p>
      {% endif %}

      <!-- إضافة حساب -->
      <div class="add-account-form" style="margin-top:15px;padding:15px;background:rgba(139,92,246,0.08);border:1px dashed rgba(139,92,246,0.4);border-radius:12px;">
        <h4 style="font-size:14px;color:#c4b5fd;margin-bottom:12px;">➕ إضافة حساب جديد</h4>
        <form method="POST">
          <input type="hidden" name="action" value="add_account">
          <input type="text" name="email" placeholder="الإيميل" required style="width:100%;margin-bottom:8px;">
          <input type="text" name="token" placeholder="hf_..." required style="width:100%;margin-bottom:8px;font-family:monospace;font-size:12px;">
          <button type="submit" class="btn btn-success" style="width:100%;">➕ إضافة واختبار</button>
        </form>
      </div>
    </div>

    <!-- ═══ قسم Spaces ═══ -->
    <div class="card" style="margin-top:20px;">
      <div class="section-header">
        <h3>🚀 Spaces المتاحة</h3>
      </div>

      <h4 style="font-size:13px;color:#c4b5fd;margin:10px 0 8px 0;">📦 الافتراضية (اضغط ✏️ للتعديل)</h4>
      <div class="accounts-list">
      {% for skey, sp in all_spaces_default.items() %}
      <div class="account-item" style="flex-direction:column;align-items:stretch;">
        <div style="display:flex;justify-content:space-between;align-items:center;width:100%;">
          <div class="account-info">
            <div class="account-name">⚡ {{ sp.name }}
              {% set _m = sp.get('modes', []) %}
              {% if 'image-to-video' in _m and 'text-to-video' in _m %} 🖼️📝
              {% elif 'image-to-video' in _m %} 🖼️
              {% elif 'text-to-video' in _m %} 📝
              {% else %} ❓{% endif %}
            </div>
            <div class="account-preview">{{ sp.id }}</div>
          </div>
          <div style="display:flex;gap:6px;">
            <button type="button" class="btn-sm btn-update" 
                    onclick="toggleSpaceEdit('{{ skey }}')"
                    style="padding:6px 12px;font-size:14px;">✏️</button>
            <form method="POST" style="margin:0;display:inline;" 
                  onsubmit="return confirm('حذف Space {{ sp.name }}؟')">
              <input type="hidden" name="action" value="hide_space">
              <input type="hidden" name="space_key" value="{{ skey }}">
              <button type="submit" class="btn-sm btn-del"
                      style="padding:6px 12px;font-size:14px;">🗑️</button>
            </form>
          </div>
        </div>
        
        <div id="space_edit_{{ skey }}" style="display:none;margin-top:12px;padding:12px;background:rgba(0,0,0,0.2);border-radius:8px;">
          <form method="POST" style="margin:0;">
            <input type="hidden" name="action" value="update_space_modes">
            <input type="hidden" name="space_key" value="{{ skey }}">
            <div style="display:flex;gap:20px;margin-bottom:10px;flex-wrap:wrap;">
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:normal;font-size:13px;">
                <input type="checkbox" name="supports_img" 
                       {% if 'image-to-video' in sp.get('modes', []) %}checked{% endif %}>
                🖼️ صورة → فيديو
              </label>
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:normal;font-size:13px;">
                <input type="checkbox" name="supports_txt"
                       {% if 'text-to-video' in sp.get('modes', []) %}checked{% endif %}>
                📝 نص → فيديو
              </label>
            </div>
            <button type="submit" class="btn-sm btn-update" style="width:100%;padding:8px;">💾 حفظ التعديلات</button>
          </form>
        </div>
      </div>
      {% endfor %}
      </div>

      <h4 style="font-size:13px;color:#c4b5fd;margin:15px 0 8px 0;">🚀 المخصصة ({{ user_custom_spaces|length }})</h4>
      {% if user_custom_spaces %}
      <div class="accounts-list">
      {% for csp in user_custom_spaces %}
      <div class="account-item">
        <div class="account-info">
          <div class="account-name">✏️ {{ csp.name }}
            {% if csp.supports_img and csp.supports_txt %} 🖼️📝
            {% elif csp.supports_img %} 🖼️
            {% else %} 📝{% endif %}
          </div>
          <div class="account-preview">{{ csp.id }} • {{ csp.endpoint }}</div>
        </div>
        <form method="POST" style="display:inline;" onsubmit="return confirm('حذف {{ csp.name }}؟');">
          <input type="hidden" name="action" value="delete_space">
          <input type="hidden" name="space_key" value="{{ csp.key }}">
          <button type="submit" class="btn btn-danger" style="padding:6px 12px;font-size:12px;">🗑️ حذف</button>
        </form>
      </div>
      {% endfor %}
      </div>
      {% else %}
      <p style="text-align:center;padding:15px;color:#94a3b8;font-size:13px;">مافيش Spaces مخصصة</p>
      {% endif %}

      <!-- إضافة Space -->
      <div class="add-account-form" style="margin-top:15px;padding:15px;background:rgba(139,92,246,0.08);border:1px dashed rgba(139,92,246,0.4);border-radius:12px;">
        <h4 style="font-size:14px;color:#c4b5fd;margin-bottom:12px;">➕ إضافة Space جديد</h4>
        <form method="POST">
          <input type="hidden" name="action" value="add_space">
          <input type="text" name="space_name" placeholder="الاسم (مثال: LTX Turbo)" required style="width:100%;margin-bottom:8px;">
          <input type="text" name="space_id" placeholder="owner/space-name" required style="width:100%;margin-bottom:8px;font-family:monospace;font-size:12px;">
          <input type="text" name="space_endpoint" placeholder="/generate_video" value="/video" style="width:100%;margin-bottom:8px;font-family:monospace;font-size:12px;">
          <div style="display:flex;gap:15px;flex-wrap:wrap;margin-bottom:10px;">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
              <input type="checkbox" name="supports_img"> 🖼️ صورة
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
              <input type="checkbox" name="supports_txt"> 📝 نص
            </label>
          </div>
          <button type="submit" class="btn btn-success" style="width:100%;">➕ إضافة Space</button>
        </form>
      </div>
    </div>
  </div>

  <!-- Hidden form for activation -->
  <form method="POST" id="activateForm" style="display:none;">
    <input type="hidden" name="action" value="activate_account">
    <input type="hidden" name="account_id" id="activateAccountId">
  </form>

<script>
const EXAMPLES = {
  cinematic: "Cinematic shot of ",
  nature: "Nature scene of ",
  portrait: "Portrait of ",
  dynamic: "Dynamic action of ",
};

function addExample(type) {
  const ta = document.getElementById('promptInput');
  ta.value = EXAMPLES[type] + ta.value.replace(/^(Cinematic shot of |Nature scene of |Portrait of |Dynamic action of )/i, '');
  ta.focus();
}

function toggleSpaceEdit(spaceKey) {
  const el = document.getElementById('space_edit_' + spaceKey);
  if (el) {
    el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
  }
}

function activateAccount(accountId) {
  document.getElementById('activateAccountId').value = accountId;
  document.getElementById('activateForm').submit();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', 
      (i === 0 && name === 'generate') ||
      (i === 1 && name === 'my-videos') ||
      (name === 'settings' && false)  // settings مش تاب
    );
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'my-videos') {
    document.querySelectorAll('.video-card video').forEach(v => v.load());
  }
}

function setMode(mode) {
  console.log('setMode called:', mode);
  document.querySelectorAll('.mode-opt').forEach(o => o.classList.remove('active'));
  document.getElementById(mode === 'img2video' ? 'mode_img' : 'mode_text').classList.add('active');
  
  const imgCard = document.getElementById('imageCard');
  if (imgCard) imgCard.style.display = (mode === 'img2video') ? 'block' : 'none';
  
  const spaceSelect = document.querySelector('select[name="space"]');
  if (!spaceSelect) return;
  
  const options = spaceSelect.querySelectorAll('option');
  const wantedMode = (mode === 'img2video') ? 'image-to-video' : 'text-to-video';
  
  let firstVisible = null;
  options.forEach(opt => {
    const modes = (opt.dataset.modes || '').split(',').map(s => s.trim());
    const visible = modes.includes(wantedMode);
    
    opt.style.display = visible ? '' : 'none';
    opt.disabled = !visible;
    if (visible && !firstVisible) firstVisible = opt;
  });
  
  if (firstVisible) spaceSelect.value = firstVisible.value;
}

document.getElementById('genForm').addEventListener('submit', function() {
  document.getElementById('submitBtn').disabled = true;
  document.getElementById('submitBtn').textContent = '⏳ جاري التوليد...';
  document.getElementById('loadingOverlay').classList.add('active');
});

window.addEventListener('DOMContentLoaded', function() {
  // ═══ فلترة الـ Spaces حسب الوضع الحالي ═══
  const checkedMode = document.querySelector('input[name="mode"]:checked');
  if (checkedMode && typeof setMode === 'function') {
    setTimeout(() => setMode(checkedMode.value), 100);
  }
  
  setTimeout(function() {
    document.querySelectorAll('.flash').forEach(el => {
      el.style.transition = 'opacity 0.5s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 500);
    });
  }, 5000);
});
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════════════════════
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template_string(LOGIN_HTML, users=USERS, error=None,
                                      next=request.args.get("next", "/"))

    ip = request.remote_addr or "unknown"
    if _is_rate_limited(ip):
        return render_template_string(
            LOGIN_HTML, users=USERS,
            error="محاولات كثيرة. استنى 5 دقايق.",
            next=request.form.get("next", "/"),
        ), 429

    user_id = request.form.get("user_id", "").strip()
    password = request.form.get("password", "")

    if user_id not in USER_IDS:
        return render_template_string(
            LOGIN_HTML, users=USERS,
            error="اختر اسمك من القائمة",
            next=request.form.get("next", "/"),
        ), 400

    cfg = load_config()
    stored_hash = cfg.get("auth", {}).get("password_hash", "")
    if not stored_hash:
        return render_template_string(
            LOGIN_HTML, users=USERS,
            error="مافيش كلمة مرور معدّة. شغّل setup_auth.py",
            next="/",
        ), 500

    if check_password_hash(stored_hash, password):
        session.permanent = True
        session["logged_in"] = True
        session["user_id"] = user_id
        session["user_name"] = next(u["name"] for u in USERS if u["id"] == user_id)
        _login_attempts.pop(ip, None)
        next_url = request.form.get("next", "/")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        return redirect(next_url)

    _record_attempt(ip)
    return render_template_string(
        LOGIN_HTML, users=USERS,
        error="كلمة المرور غلط",
        next=request.form.get("next", "/"),
    ), 401


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    user_id = current_user_id()
    user_name = current_user_name()

    error = None
    video_url = None

    if request.method == "POST":
        action = request.form.get("action", "generate")

        # ═══ تعديل modes لـ Space (افتراضي أو مخصص) ═══
        if action == "update_space_modes":
            sp_key = request.form.get("space_key", "")
            supports_img = request.form.get("supports_img") == "on"
            supports_txt = request.form.get("supports_txt") == "on"
            
            if not supports_img and not supports_txt:
                flash("اختر على الأقل نوع واحد", "error")
            else:
                cfg = load_config()
                # حفظ override في المستخدم
                for u in cfg.get("users", []):
                    if u.get("id") == user_id:
                        u.setdefault("space_overrides", {})
                        u["space_overrides"][sp_key] = {
                            "supports_img": supports_img,
                            "supports_txt": supports_txt,
                        }
                        save_config(cfg)
                        flash(f"تم تحديث Space: {sp_key}", "success")
                        break
            return redirect(url_for("index"))

        # ═══ إضافة Space مخصص ═══
        if action == "add_space":
            sp_name = request.form.get("space_name", "").strip()
            sp_id = request.form.get("space_id", "").strip()
            sp_endpoint = request.form.get("space_endpoint", "/video").strip()
            supports_img = request.form.get("supports_img") == "on"
            supports_txt = request.form.get("supports_txt") == "on"
            
            if not sp_name or "/" not in sp_id:
                flash("الاسم + Space ID مطلوبان (owner/space-name)", "error")
            elif not supports_img and not supports_txt:
                flash("اختر على الأقل نوع واحد (صورة أو نص)", "error")
            else:
                cfg = load_config()
                for u in cfg.get("users", []):
                    if u.get("id") == user_id:
                        u.setdefault("custom_spaces", [])
                        key = f"custom_{user_id}_{int(time.time())}"
                        u["custom_spaces"].append({
                            "key": key,
                            "name": sp_name,
                            "id": sp_id,
                            "endpoint": sp_endpoint or "/video",
                            "supports_img": supports_img,
                            "supports_txt": supports_txt,
                            "needs_image": supports_img,
                        })
                        save_config(cfg)
                        modes = []
                        if supports_img: modes.append("🖼️")
                        if supports_txt: modes.append("📝")
                        flash(f"تم إضافة Space: {sp_name} ({' '.join(modes)})", "success")
                        break
            return redirect(url_for("index"))

        # ═══ حذف Space مخصص ═══
        # ═══ حذف Space افتراضي ═══
        if action == "hide_space":
            sp_key = request.form.get("space_key", "").strip()
            if not sp_key:
                flash("مفيش space", "error")
            else:
                cfg = load_config()
                for u in cfg.get("users", []):
                    if u.get("id") == user_id:
                        hidden = u.setdefault("hidden_default_spaces", [])
                        if sp_key not in hidden:
                            hidden.append(sp_key)
                        save_config(cfg)
                        flash(f"تم حذف {sp_key}", "success")
                        break
            return redirect(url_for("index"))

        # ═══ استرجاع Space محذوف ═══
        if action == "enable_space":
            sp_key = request.form.get("space_key", "").strip()
            cfg = load_config()
            for u in cfg.get("users", []):
                if u.get("id") == user_id:
                    hidden = u.get("hidden_default_spaces", [])
                    if sp_key in hidden:
                        hidden.remove(sp_key)
                    save_config(cfg)
                    flash(f"تم استرجاع {sp_key}", "success")
                    break
            return redirect(url_for("index"))

        if action == "delete_space":
            sp_key = request.form.get("space_key", "")
            cfg = load_config()
            for u in cfg.get("users", []):
                if u.get("id") == user_id:
                    spaces = u.get("custom_spaces", [])
                    u["custom_spaces"] = [s for s in spaces if s.get("key") != sp_key]
                    save_config(cfg)
                    flash("تم حذف Space", "success")
                    break
            return redirect(url_for("index"))

        # ═══ تفعيل حساب (ينقل للأول في القائمة) ═══
        if action == "activate_account":
            aid = request.form.get("account_id", "")
            if aid not in get_user_accounts(user_id):
                flash("مش من حسابك", "error")
                return redirect(url_for("index"))
            cfg = load_config()
            for u in cfg.get("users", []):
                if u.get("id") == user_id:
                    accs = u.get("accounts", [])
                    if aid in accs:
                        accs.remove(aid)
                        accs.insert(0, aid)
                    break
            save_config(cfg)
            flash("تم التفعيل", "success")
            return redirect(url_for("index"))

        # ═══ إضافة حساب (خاص بالمستخدم) ═══
        if action == "add_account":
            email = request.form.get("email", "").strip()
            token = request.form.get("token", "").strip()
            if not email:
                flash("الإيميل مطلوب", "error")
            elif not token.startswith("hf_"):
                flash("التوكن لازم يبدأ بـ hf_", "error")
            else:
                ok, info = test_token(token)
                if not ok:
                    flash(f"التوكن غير صالح: {info}", "error")
                else:
                    cfg = load_config()
                    aid = f"acc_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
                    cfg.setdefault("accounts", []).append({
                        "id": aid, "name": email, "token": token,
                        "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "is_env": False, "quota": None, "last_check": None,
                    })
                    for u in cfg.get("users", []):
                        if u.get("id") == user_id:
                            u.setdefault("accounts", []).append(aid)
                            break
                    save_config(cfg)
                    flash(f"تم إضافة {email} (HF: {info})", "success")
            return redirect(url_for("index"))

        # ═══ حذف حساب ═══
        if action == "delete_account":
            aid = request.form.get("account_id", "")
            if aid not in get_user_accounts(user_id):
                flash("مش من حسابك", "error")
                return redirect(url_for("index"))
            cfg = load_config()
            cfg["accounts"] = [a for a in cfg.get("accounts", []) if a["id"] != aid]
            for u in cfg.get("users", []):
                if u.get("id") == user_id and aid in u.get("accounts", []):
                    u["accounts"].remove(aid)
                    break
            save_config(cfg)
            flash("تم حذف الحساب", "success")
            return redirect(url_for("index"))

        # ═══ تحديث التوكن ═══
        if action == "update_token":
            aid = request.form.get("account_id", "")
            new_token = request.form.get("new_token", "").strip()
            if aid not in get_user_accounts(user_id):
                flash("مش من حسابك", "error")
                return redirect(url_for("index"))
            if not new_token.startswith("hf_"):
                flash("التوكن لازم يبدأ بـ hf_", "error")
            else:
                cfg = load_config()
                for a in cfg.get("accounts", []):
                    if a["id"] == aid:
                        a["token"] = new_token
                        break
                save_config(cfg)
                flash("تم تحديث التوكن", "success")
            return redirect(url_for("index"))

        # ═══ فحص quota ═══
        if action == "check_quota":
            aid = request.form.get("account_id", "")
            if aid not in get_user_accounts(user_id):
                flash("مش من حسابك", "error")
                return redirect(url_for("index"))
            cfg = load_config()
            for a in cfg.get("accounts", []):
                if a["id"] == aid:
                    q = check_quota(a["token"])
                    if q:
                        cur = q.get("current", 0)
                        base = q.get("base", 300)
                        runs = q.get("runs", {})
                        a["quota"] = {
                            "total_seconds": base, "used_seconds": cur,
                            "remaining_seconds": max(0, base - cur),
                            "total_runs": runs.get("limit", 8),
                            "used_runs": runs.get("used", 0),
                            "remaining_runs": runs.get("remaining", 8),
                        }
                        a["reset_at"] = q.get("resetsAt") or q.get("resets_at")
                        a["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        save_config(cfg)
                        flash(f"{a['name']}: {a['quota']['remaining_seconds']:.0f}s", "success")
                    else:
                        flash(f"فشل فحص {a['name']}", "error")
                    break
            return redirect(url_for("index"))

        # ═══ حذف فيديو واحد ═══
        if action == "delete_video":
            filename = request.form.get("filename", "").strip()
            if not filename:
                flash("اسم الملف مطلوب", "error")
            else:
                safe = secure_filename(filename)
                video_path = OUTPUT_DIR / user_id / safe
                if video_path.exists() and video_path.is_file():
                    video_path.unlink()
                    print(f"[{user_name}] deleted video: {safe}")
                    flash(f"تم حذف {safe}", "success")
                else:
                    flash("الفيديو مش موجود", "error")
            return redirect(url_for("index"))

        # ═══ حذف كل الفيديوهات ═══
        if action == "delete_all_videos":
            user_vids_dir = OUTPUT_DIR / user_id
            if user_vids_dir.exists():
                count = 0
                for f in user_vids_dir.glob("*.mp4"):
                    try:
                        f.unlink()
                        count += 1
                    except Exception:
                        pass
                print(f"[{user_name}] deleted {count} videos")
                flash(f"تم حذف {count} فيديو", "success")
            else:
                flash("مافيش فيديوهات", "error")
            return redirect(url_for("index"))

        if action == "generate":
            try:
                mode = request.form.get("mode", "img2video")
                space_key = request.form.get("space", "ltx_distilled")
                duration = int(request.form.get("duration", 4))
                aspect = request.form.get("aspect", "9:16")
                prompt = request.form.get("prompt", "").strip()

                if not prompt:
                    raise Exception("الـ prompt مطلوب")

                # تحقق من توافق Space مع mode (باستخدام resolve_space اللي عنده modes)
                from cairoai import resolve_space as _resolve
                _sp = _resolve(space_key, load_config(), user_id)
                _modes = _sp.get("modes", [])
                _needs_img = _sp.get("needs_image", True)
                
                # يدعم النص لو modes فيها text-to-video أو needs_image=False
                _supports_txt = ("text-to-video" in _modes) or (not _needs_img)
                # يدعم الصورة لو needs_image=True أو modes فيها image-to-video
                _supports_img = _needs_img or ("image-to-video" in _modes)
                
                if mode == "text2video" and not _supports_txt:
                    raise Exception(f"Space {space_key} لا يدعم text2video. جرب space آخر.")
                if mode == "img2video" and not _supports_img:
                    raise Exception(f"Space {space_key} لا يدعم img2video. جرب space آخر.")

                img_path = None
                file = request.files.get("image")
                if file and file.filename:
                    img_path = save_upload(file, "gen", user_id)
                    if not img_path:
                        raise Exception("نوع الملف غير مدعوم")
                else:
                    if mode == "img2video":
                        raise Exception("❌ img2video يحتاج صورة. ارفع صورة أو اختر text2video.")

                print(f"[{user_name}] mode={mode}, space={space_key}, dur={duration}, aspect={aspect}")
                print(f"[{user_name}] prompt: {prompt[:80]}...")
                user_acc_ids = get_user_accounts(user_id)
                if not user_acc_ids:
                    raise Exception("مفيش حسابات معيّنة لك. روح ⚙️ وضيف حساب.")
                
                # ═══ استخدم الحساب المفعّل بس (الأول) ═══
                active_id = user_acc_ids[0]
                active_acc = get_account_by_id(load_config(), active_id)
                active_name = active_acc.get("name", "?") if active_acc else "?"
                print(f"[DEBUG] {user_name}: using active account = {active_name}")
                user_acc_ids = [active_id]

                result = generate_video_multi(
                    spaces=[space_key],
                    account_ids=user_acc_ids,
                    mode=mode,
                    image=str(img_path) if img_path else None,
                    prompt=prompt,
                    duration=duration,
                    aspect=aspect,
                    sleep_between=10,
                )

                if not result.get("success"):
                    raise Exception(result.get("error", "فشل غير معروف"))

                src = Path(result["video_path"])
                dst = user_dir(user_id) / f"{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                shutil.move(str(src), str(dst))

                # ═══ نعمل thumbnail ═══
                thumb_path = dst.parent / f"{dst.name}.jpg"
                make_thumbnail(dst, thumb_path)

                video_url = f"/video/{user_id}/{dst.name}"
                flash(f"تم التوليد بـ {result.get('account_used', '?')}", "success")

            except Exception as e:
                error = f"{type(e).__name__}: {str(e)[:300]}"
                print(f"[{user_name}] ERROR: {error}")
                import traceback
                traceback.print_exc()

    videos = user_videos(user_id)

    user_accounts_full = []
    cfg_now = load_config()
    for aid in get_user_accounts(user_id):
        for a in cfg_now.get("accounts", []):
            if a["id"] == aid:
                user_accounts_full.append(a)
                break

    return render_template_string(
        HTML_TEMPLATE,
        user_accounts=user_accounts_full,
        user_custom_spaces=get_user_custom_spaces(user_id),
        user_id=user_id,
        user_name=user_name,
        error=error,
        video_url=video_url,
        videos=videos,
        videos_count=len(videos),
        spaces=get_spaces_for_user(load_config(), user_id),
        all_spaces_default=get_spaces_for_user(load_config(), user_id),
        hidden_spaces=get_hidden_spaces(user_id),
        default_space="zeroscope",
    )


@app.route("/thumb/<user_id>/<filename>")
@login_required
def serve_thumb(user_id, filename):
    """يخدم thumbnail للفيديو"""
    if user_id != current_user_id():
        return "Forbidden", 403
    
    # نبني مسار الـ thumbnail: video.mp4 → video.mp4.jpg
    safe = secure_filename(filename)
    thumb_filename = f"{safe}.jpg"
    thumb_path = OUTPUT_DIR / user_id / thumb_filename
    
    # لو مش موجود → نحاول نعمله
    if not thumb_path.exists():
        video_path = OUTPUT_DIR / user_id / safe
        if video_path.exists():
            ok = make_thumbnail(video_path, thumb_path)
            if not ok:
                # فشل → SVG placeholder
                return svg_placeholder(), 200, {"Content-Type": "image/svg+xml"}
        else:
            return svg_placeholder(), 200, {"Content-Type": "image/svg+xml"}
    
    return send_file(str(thumb_path), mimetype="image/jpeg")


def svg_placeholder():
    """SVG placeholder للفيديوهات"""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#1a1a2e;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#312e81;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="320" height="180" fill="url(#bg)"/>
  <text x="160" y="80" fill="#a5b4fc" font-size="48" text-anchor="middle" dominant-baseline="middle">🎬</text>
  <text x="160" y="150" fill="#6b7280" font-size="12" font-family="Arial, sans-serif" text-anchor="middle">اضغط للتشغيل</text>
</svg>"""
    return svg


@app.route("/video/<user_id>/<filename>")
@login_required
def serve_video(user_id, filename):
    if user_id != current_user_id():
        return "Forbidden", 403
    safe = secure_filename(filename)
    video_path = OUTPUT_DIR / user_id / safe
    if not video_path.exists() or not video_path.is_file():
        return "Not found", 404
    return send_file(str(video_path), mimetype="video/mp4")


@app.route("/api/status")
@login_required
def api_status():
    cfg = load_config()
    accounts = get_available_accounts(cfg)
    return jsonify({
        "user": current_user_name(),
        "accounts_available": len(accounts),
        "videos_count": len(user_videos(current_user_id())),
    })


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print("=" * 60)
    print("  CairoAi v7.0 - Multi-User Web")
    print("=" * 60)
    print(f"  Users: {', '.join(u['name'] for u in USERS)}")
    print(f"  Port:  {port}")
    print(f"  URL:   http://0.0.0.0:{port}")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
