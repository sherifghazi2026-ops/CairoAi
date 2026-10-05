#!/usr/bin/env python3
"""
CairoAi v7 - Multi-Space AI Video Generator
يدعم 6 Spaces من Hugging Face + إدارة حسابات متعددة + CLI متقدم
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from gradio_client import Client, handle_file
except ImportError:
    print("❌ gradio_client مش متثبت. شغّل: pip3 install gradio_client")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════
VERSION = "7.0"
CONFIG_PATH = Path("config.json")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR = Path("uploads")
INPUT_DIR = Path("inputs")

for d in (OUTPUT_DIR, UPLOAD_DIR, INPUT_DIR):
    d.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
#  Spaces Registry
# ═══════════════════════════════════════════════════════════════════════
SPACES = {
    "ltx_distilled": {
        "id": "Lightricks/ltx-video-distilled",
        "name": "⚡ LTX Video Distilled",
        "type": "ltx_distilled",
        "endpoint": "/image_to_video",
        "modes": ["text-to-video", "image-to-video", "video-to-video"],
        "needs_image": True,
        "max_duration": 10,
        "default_duration": 2,
        "description": "الأسرع والأكثر مرونة (3 modes)",
    },
    "wan_fast": {
        "id": "multimodalart/wan2-1-fast",
        "name": "⚡ Wan 2.1 Fast",
        "type": "wan_fast",
        "endpoint": "/generate_video",
        "modes": ["image-to-video"],
        "needs_image": True,
        "max_duration": 8,
        "default_duration": 2,
        "description": "جودة Wan + سرعة عالية",
    },
    "ltx_turbo": {
        "id": "alexnasa/ltx-2-TURBO",
        "name": "🎞️ LTX-2 TURBO",
        "type": "ltx_turbo",
        "endpoint": "/generate_video",
        "modes": ["image-to-video"],
        "needs_image": True,
        "needs_end_frame": True,
        "max_duration": 5,
        "default_duration": 5,
        "description": "First-Last frame + Camera LoRA",
    },
    "zeroscope": {
        "id": "hysts/zeroscope-v2",
        "name": "🎬 Zeroscope V2",
        "type": "zeroscope",
        "endpoint": "/run",
        "modes": ["text-to-video"],
        "needs_image": False,
        "max_duration": 3,
        "default_duration": 3,
        "description": "نص فقط → فيديو",
    },
    "svd": {
        "id": "multimodalart/stable-video-diffusion",
        "name": "🎞️ Stable Video Diffusion",
        "type": "svd",
        "endpoint": "/video",
        "modes": ["image-to-video"],
        "needs_image": True,
        "max_duration": 4,
        "default_duration": 4,
        "description": "صورة → فيديو (حركة خفيفة)",
    },
    "cogvideo": {
        "id": "THUDM/CogVideoX-5B",
        "name": "🎬 CogVideoX-5B",
        "type": "cogvideo",
        "endpoint": "/generate",
        "modes": ["image-to-video"],
        "needs_image": True,
        "max_duration": 6,
        "default_duration": 6,
        "description": "جودة عالية (6 ثواني ثابتة)",
    },
}


# ═══════════════════════════════════════════════════════════════════════
#  Config Layer
# ═══════════════════════════════════════════════════════════════════════
def load_config():
    """قراءة config.json — يدعم ENV var للـ Render"""
    # 1) من ENV (للـ Render)
    env_json = os.environ.get("CONFIG_JSON", "").strip()
    if env_json:
        try:
            return json.loads(env_json)
        except Exception as e:
            print(f"⚠️ فشل قراءة CONFIG_JSON: {e}")
    
    # 2) من الملف (للتشغيل المحلي)
    if not CONFIG_PATH.exists():
        return {
            "accounts": [],
            "active_account_id": None,
            "spaces": {},
            "custom_spaces": {},
            "auth": {},
        }
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ فشل قراءة config.json: {e}")
        return {}


def save_config(cfg):
    """حفظ config.json — يتحطّل لو ENV active"""
    # لو على Render (env var) → ما نكتبش
    if os.environ.get("CONFIG_JSON", "").strip():
        # في الذاكرة بس — التعديلات مش بتتحفظ
        # (محتاج DB في المستقبل)
        return
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_active_account(cfg):
    """الحساب النشط الحالي"""
    active_id = cfg.get("active_account_id")
    if not active_id:
        return None
    for acc in cfg.get("accounts", []):
        if acc.get("id") == active_id:
            return acc
    return None


def get_account_by_id(cfg, account_id):
    """بحث عن حساب بالـ id"""
    for acc in cfg.get("accounts", []):
        if acc.get("id") == account_id:
            return acc
    return None


def get_available_accounts(cfg):
    """
    كل الحسابات اللي عندها quota متبقية، مرتبة بالأكثر متبقي.
    الحسابات اللي مافحصتش بعد تُعتبر متاحة.
    """
    accounts = []
    for acc in cfg.get("accounts", []):
        q = acc.get("quota")
        if q is None:
            # مافحصش — نعتبره متاح
            accounts.append(acc)
        elif q.get("remaining_runs", 0) > 0 and q.get("remaining_seconds", 0) > 0:
            accounts.append(acc)

    accounts.sort(
        key=lambda a: (a.get("quota") or {}).get("remaining_seconds", 9999),
        reverse=True,
    )
    return accounts


def update_quota(cfg, account_id, used_seconds=15):
    """تحديث الـ quota بعد استخدام"""
    for acc in cfg.get("accounts", []):
        if acc.get("id") == account_id:
            if acc.get("quota") is None:
                acc["quota"] = {
                    "total_seconds": 300,
                    "used_seconds": 0,
                    "remaining_seconds": 300,
                    "total_runs": 8,
                    "used_runs": 0,
                    "remaining_runs": 8,
                }
            q = acc["quota"]
            q["used_seconds"] = q.get("used_seconds", 0) + used_seconds
            q["remaining_seconds"] = max(0, q.get("remaining_seconds", 300) - used_seconds)
            q["used_runs"] = q.get("used_runs", 0) + 1
            q["remaining_runs"] = max(0, q.get("remaining_runs", 8) - 1)
            acc["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            break
    save_config(cfg)


# ═══════════════════════════════════════════════════════════════════════
#  Space Helpers
# ═══════════════════════════════════════════════════════════════════════
def get_spaces_for_user(cfg, user_id=None):
    """
    يرجّع SPACES مع تطبيق overrides من المستخدم.
    """
    spaces = {k: dict(v) for k, v in SPACES.items()}
    
    if not user_id:
        return spaces
    
    # نلاقي المستخدم
    user = None
    for u in cfg.get("users", []):
        if u.get("id") == user_id:
            user = u
            break
    if not user:
        return spaces
    
    overrides = user.get("space_overrides", {})
    
    for sp_key, override in overrides.items():
        if sp_key not in spaces:
            continue
        supports_img = override.get("supports_img", True)
        supports_txt = override.get("supports_txt", False)
        
        # نبني modes جديدة
        modes = []
        if supports_img:
            modes.append("image-to-video")
        if supports_txt:
            modes.append("text-to-video")
        
        spaces[sp_key]["modes"] = modes
        spaces[sp_key]["needs_image"] = supports_img
    
    return spaces


def get_user_custom_spaces(cfg, user_id=None):
    """يرجّع custom_spaces لمستخدم معين (أو كل الموجودين لو None)"""
    if user_id:
        for u in cfg.get("users", []):
            if u.get("id") == user_id:
                return u.get("custom_spaces", [])
        return []
    # كل custom
    all_custom = []
    for u in cfg.get("users", []):
        for sp in u.get("custom_spaces", []):
            all_custom.append(sp)
    return all_custom


def extract_reset_time(error_msg):
    """يستخرج ثواني الانتظار من رسالة HF quota"""
    if not error_msg:
        return None
    import re
    # نمط: "Try again in 1:31:08"
    m = re.search(r'Try again in (\d+):(\d+):(\d+)', error_msg)
    if m:
        h, mn, s = map(int, m.groups())
        return h * 3600 + mn * 60 + s
    # نمط: "Try again in 5 minutes" أو "in 2 hours"
    m = re.search(r'Try again in (\d+)\s*(hour|hr|h)', error_msg)
    if m:
        return int(m.group(1)) * 3600
    m = re.search(r'Try again in (\d+)\s*(minute|min|m)', error_msg)
    if m:
        return int(m.group(1)) * 60
    return None


def get_space(space_key):
    """معلومات Space من الـ registry"""
    if space_key not in SPACES:
        raise ValueError(f"Space غير معروف: {space_key}. متاح: {list(SPACES.keys())}")
    return SPACES[space_key]


def resolve_space(space_key_or_id, cfg=None, user_id=None):
    """يقبل key (زي ltx_distilled) أو id (Lightricks/...) أو custom من user"""
    # 1) في SPACES الافتراضية
    if space_key_or_id in SPACES:
        return SPACES[space_key_or_id]
    
    # 2) في الافتراضية (بـ id)
    for key, sp in SPACES.items():
        if sp["id"] == space_key_or_id:
            return sp
    
    # 3) في custom_spaces (لو cfg متاح)
    if cfg is None:
        cfg = load_config()
    
    custom_list = get_user_custom_spaces(cfg, user_id) if user_id else get_user_custom_spaces(cfg)
    for csp in custom_list:
        if csp.get("key") == space_key_or_id or csp.get("id") == space_key_or_id:
            return {
                "id": csp.get("id", ""),
                "name": csp.get("name", csp.get("id", "Custom")),
                "type": "custom",
                "endpoint": csp.get("endpoint", "/video"),
                "modes": ["image-to-video", "text-to-video"],
                "needs_image": csp.get("needs_image", True),
                "max_duration": 10,
                "default_duration": 2,
                "description": "Space مخصص",
            }
    
    # 4) fallback: id عادي
    if "/" in space_key_or_id:
        return {
            "id": space_key_or_id,
            "name": f"Custom: {space_key_or_id}",
            "type": "custom",
            "endpoint": "/video",
            "modes": ["image-to-video"],
            "needs_image": True,
            "max_duration": 5,
            "default_duration": 2,
            "description": "Space مخصص",
        }
    raise ValueError(f"Space غير معروف: {space_key_or_id}")


def check_quota(token):
    """فحص quota من HF API"""
    try:
        import requests
        r = requests.get(
            "https://huggingface.co/api/spaces/zero-gpu/quota",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def test_token(token):
    """التحقق من صلاحية التوكن"""
    try:
        import requests
        r = requests.get(
            "https://huggingface.co/api/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return True, r.json().get("name", "unknown")
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)[:100]


# ═══════════════════════════════════════════════════════════════════════
#  Video Extraction Helpers
# ═══════════════════════════════════════════════════════════════════════
def _extract_video(result):
    """استخراج مسار الفيديو من نتيجة gradio_client (أشكال متعددة)"""
    if result is None:
        return None
    if isinstance(result, dict):
        for key in ("video", "path", "value", "url"):
            if key in result and result[key]:
                return _extract_video(result[key])
        return None
    if isinstance(result, (list, tuple)):
        for item in result:
            p = _extract_video(item)
            if p:
                return p
        return None
    if isinstance(result, str):
        return result
    return None


# ═══════════════════════════════════════════════════════════════════════
#  Per-Space Callers
# ═══════════════════════════════════════════════════════════════════════
def _call_ltx_distilled(client, img1, img2, prompt, duration, height, width, seed):
    """Lightricks/ltx-video-distilled"""
    result = client.predict(
        prompt,
        "worst quality, inconsistent motion, blurry, jittery, distorted",
        handle_file(str(img1)),
        None,  # input_video
        width,
        height,
        "image-to-video",
        duration,   # duration_ui
        max(9, int(duration * 8)),  # ui_frames_to_use
        seed,
        True,       # randomize_seed
        1,          # ui_guidance_scale
        True,       # improve_texture_flag
        api_name="/image_to_video",
    )
    return _extract_video(result)


def _call_wan_fast(client, img1, img2, prompt, duration, height, width, seed):
    """multimodalart/wan2-1-fast"""
    result = client.predict(
        handle_file(str(img1)),
        prompt,
        width,
        height,
        "worst quality, blurry, distorted, static, watermark",
        duration,   # duration_seconds
        1.0,        # guidance_scale
        4,          # steps
        seed,
        True,       # randomize_seed
        api_name="/generate_video",
    )
    return _extract_video(result)


def _call_ltx_turbo(client, img1, img2, prompt, duration, height, width, seed):
    """alexnasa/ltx-2-TURBO"""
    result = client.predict(
        handle_file(str(img1)),
        handle_file(str(img2)) if img2 else handle_file(str(img1)),
        prompt,
        None,           # input_video
        "Image-to-Video",
        False,          # enhance_prompt
        seed,
        True,           # randomize_seed
        height,
        width,
        "No LoRA",
        None,           # audio_path
        api_name="/generate_video",
    )
    return _extract_video(result)


def _call_zeroscope(client, img1, img2, prompt, duration, height, width, seed):
    """hysts/zeroscope-v2 (text-to-video)"""
    result = client.predict(
        prompt,
        seed,
        max(24, int(duration * 8)),  # num_frames
        25,                          # num_inference_steps
        api_name="/run",
    )
    return _extract_video(result)


def _call_svd(client, img1, img2, prompt, duration, height, width, seed):
    """multimodalart/stable-video-diffusion"""
    result = client.predict(
        handle_file(str(img1)),
        seed,
        True,       # randomize_seed
        127,        # motion_bucket_id
        6,          # fps_id
        api_name="/video",
    )
    return _extract_video(result)


def _call_cogvideo(client, img1, img2, prompt, duration, height, width, seed):
    """THUDM/CogVideoX-5B"""
    result = client.predict(
        prompt,
        handle_file(str(img1)),
        None,       # video_input
        0.8,        # video_strength
        seed,
        False,      # scale_status
        False,      # rife_status
        api_name="/generate",
    )
    return _extract_video(result)


SPACE_CALLERS = {
    "ltx_distilled": _call_ltx_distilled,
    "wan_fast": _call_wan_fast,
    "ltx_turbo": _call_ltx_turbo,
    "zeroscope": _call_zeroscope,
    "svd": _call_svd,
    "cogvideo": _call_cogvideo,
}


# ═══════════════════════════════════════════════════════════════════════
#  Core Generation
# ═══════════════════════════════════════════════════════════════════════
def generate_with_account(
    space_info, account, image_path=None, end_frame_path=None,
    prompt="", duration=None, aspect="9:16", seed=42,
    timeout=900.0,
):
    """
    توليد فيديو مع حساب واحد.
    Returns: (video_path, error_message)
    """
    space_type = space_info["type"]
    space_id = space_info["id"]
    endpoint = space_info["endpoint"]

    # duration
    max_dur = space_info.get("max_duration", 5)
    default_dur = space_info.get("default_duration", 2)
    if duration is None:
        duration = default_dur
    if duration > max_dur:
        print(f"⚠️ duration={duration}s > max {max_dur}s → هنستخدم {max_dur}s")
        duration = max_dur

    # aspect
    if aspect == "9:16":
        width, height = 512, 896  # عمودي: 512×896

    elif aspect == "16:9":
        width, height = 896, 512  # أفقي: 896×512

    elif aspect == "1:1":
        width, height = 640, 640

    token = account["token"]
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGINGFACEHUB_API_TOKEN"] = token

    try:
        client = Client(
            space_id,
            token=token,
            httpx_kwargs={"timeout": timeout},
            verbose=False,
        )

        caller = SPACE_CALLERS.get(space_type)
        if not caller:
            return None, f"مفيش caller لـ space_type={space_type}"

        video = caller(client, image_path, end_frame_path, prompt,
                       duration, height, width, seed)

        if not video or not Path(video).exists():
            return None, f"مفيش فيديو في النتيجة: {video!r}"

        return video, None

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:250]}"


def generate_video(
    space,
    image=None,
    end_frame=None,
    prompt="",
    duration=None,
    aspect="9:16",
    account=None,
    strict=False,
    output_name=None,
    timeout=900.0,
):
    """
    توليد فيديو مع fallback تلقائي بين الحسابات.
    
    Args:
        space: space key (زي "ltx_distilled") أو id (زي "Lightricks/...")
        image: مسار الصورة (path)
        end_frame: مسار الصورة الأخيرة (للـ ltx_turbo)
        prompt: نص الوصف
        duration: المدة بالثواني
        aspect: نسبة الأبعاد ("9:16" | "16:9" | "1:1")
        account: id حساب معين (اختياري)
        strict: لو True، يرفض duration > max بدل ما يقصّ
        output_name: اسم الملف الناتج (اختياري)
        timeout: timeout للاتصال
    
    Returns: dict {success, video_path, size_mb, account_used, space, error}
    """
    cfg = load_config()
    space_info = resolve_space(space, cfg)

    # duration strict check
    max_dur = space_info.get("max_duration", 5)
    if duration and duration > max_dur and strict:
        return {
            "success": False,
            "error": f"Duration {duration}s > max {max_dur}s (strict mode)",
        }

    # image check
    img_path = None
    if space_info.get("needs_image"):
        if not image:
            return {"success": False, "error": "الصورة مطلوبة"}
        img_path = Path(image)
        if not img_path.exists():
            return {"success": False, "error": f"الصورة غير موجودة: {img_path}"}

    end_path = Path(end_frame) if end_frame else None

    # prompt check
    prompt = (prompt or "").strip()
    if not prompt and space_info.get("needs_image"):
        # default prompt
        prompt = "make this image come alive, cinematic motion, smooth animation"

    # output name
    if not output_name:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"{space_info['type']}_{ts}.mp4"
    output_path = OUTPUT_DIR / output_name

    # accounts to try
    if account:
        acc = get_account_by_id(cfg, account)
        if not acc:
            return {"success": False, "error": f"حساب غير موجود: {account}"}
        accounts_to_try = [acc]
    else:
        accounts_to_try = get_available_accounts(cfg)
        if not accounts_to_try:
            active = get_active_account(cfg)
            if active:
                accounts_to_try = [active]

    if not accounts_to_try:
        return {"success": False, "error": "مفيش أي حساب متاح"}

    # try each account
    last_error = None
    for idx, acc in enumerate(accounts_to_try, 1):
        print(f"\n🎯 [{idx}/{len(accounts_to_try)}] {acc.get('name', '?')}")
        print(f"   Token: {acc['token'][:15]}...{acc['token'][-4:]}")
        print(f"   Space: {space_info['id']} ({space_info['endpoint']})")

        # ═══ فحص HF live قبل المحاولة ═══
        try:
            _rq = check_quota(acc["token"])
            if _rq:
                _rem = _rq.get("current", 0)
                if _rem < 30:
                    print(f"   HF live: {_rem:.0f}s فقط - نتخطاه")
                    for _a in cfg["accounts"]:
                        if _a["id"] == acc["id"]:
                            _a["quota"] = {
                                "total_seconds": _rq.get("base", 300),
                                "used_seconds": max(0, _rq.get("base", 300) - _rem),
                                "remaining_seconds": _rem,
                                "total_runs": _rq.get("runs", {}).get("limit", 8),
                                "used_runs": _rq.get("runs", {}).get("used", 0),
                                "remaining_runs": _rq.get("runs", {}).get("remaining", 8),
                            }
                            _a["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                            break
                    save_config(cfg)
                    continue
                print(f"   HF live: {_rem:.0f}s متبقي")
        except Exception as _e:
            print(f"   فحص HF فشل: {_e}")

        video, err = generate_with_account(
            space_info, acc, img_path, end_path, prompt,
            duration, aspect, timeout=timeout,
        )

        if video:
            shutil.copy(video, output_path)
            size_mb = output_path.stat().st_size / 1024 / 1024

            # update quota + set as active
            actual_duration = duration or space_info.get("default_duration", 2)
            update_quota(cfg, acc["id"], used_seconds=actual_duration)
            cfg["active_account_id"] = acc["id"]
            save_config(cfg)

            return {
                "success": True,
                "video_path": str(output_path),
                "size_mb": round(size_mb, 2),
                "account_used": acc.get("name", "?"),
                "space": space_info["id"],
                "duration": actual_duration,
            }
        else:
            last_error = err
            print(f"   ❌ فشل: {err}")

    return {
        "success": False,
        "error": f"فشل كل الحسابات. آخر خطأ: {last_error}",
    }


# ═══════════════════════════════════════════════════════════════════════
#  CLI Commands
# ═══════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════
#  Multi-Space Generation
# ═══════════════════════════════════════════════════════════════════════
def generate_video_multi(
    spaces=None,
    account_ids=None,
    mode="img2video",
    image=None,
    end_frame=None,
    prompt="",
    duration=None,
    aspect="9:16",
    sleep_between=10,
    output_name=None,
    timeout=900.0,
):
    """Try each space with each account, with sleep between attempts.
    
    Args:
        account_ids: list of account IDs to use (None = all available)
        mode: img2video | text2video (filters spaces)
    """
    # فلترة الـ Spaces حسب mode
    if spaces is None:
        if mode == "text2video":
            spaces = ["ltx_distilled", "zeroscope"]
        else:
            spaces = ["ltx_distilled", "wan_fast", "svd", "cogvideo"]
    else:
        # لو المستخدم حدد spaces + mode، نفلترهم حسب modes
        allowed = set()
        for sk in spaces:
            try:
                si = resolve_space(sk)
                modes = si.get("modes", [])
                needs_img = si.get("needs_image", True)
                
                # يدعم text2video لو modes فيها text-to-video أو needs_image=False
                supports_txt = ("text-to-video" in modes) or (not needs_img)
                # يدعم img2video لو needs_image=True أو modes فيها image-to-video
                supports_img = needs_img or ("image-to-video" in modes)
                
                if mode == "text2video" and not supports_txt:
                    continue
                if mode == "img2video" and not supports_img:
                    continue
                allowed.add(sk)
            except Exception:
                continue
        spaces = [s for s in spaces if s in allowed]
        if not spaces:
            return {"success": False, "error": f"No spaces compatible with mode={mode}"}

    cfg = load_config()
    
    # حسابات المستخدم أو الكل
    if account_ids:
        accounts = []
        for aid in account_ids:
            acc = get_account_by_id(cfg, aid)
            if acc:
                accounts.append(acc)
        if not accounts:
            return {"success": False, "error": "No valid accounts in account_ids"}
    else:
        accounts = get_available_accounts(cfg)
        if not accounts:
            active = get_active_account(cfg)
            if active:
                accounts = [active]
    
    if not accounts:
        return {"success": False, "error": "No accounts"}

    total = len(spaces) * len(accounts)
    counter = 0
    last_error = None

    print()
    print(f"Multi-Space: {len(spaces)} spaces x {len(accounts)} accounts = {total} tries")
    print(f"Spaces: {', '.join(spaces)}")
    print(f"Sleep: {sleep_between}s")
    print()

    for space_key in spaces:
        try:
            space_info = resolve_space(space_key)
        except Exception as e:
            print(f"Skip {space_key}: {e}")
            continue

        print()
        print("=" * 70)
        print(f"Space: {space_info['id']}")
        print("=" * 70)

        for acc in accounts:
            counter += 1
            print()
            print(f"[{counter}/{total}] {space_key} + {acc.get('name', '?')}")

            # ═══ فحص reset_at: لو في المستقبل، تخطاه ═══
            if acc.get("reset_at"):
                try:
                    _reset_dt = datetime.strptime(acc["reset_at"], "%Y-%m-%d %H:%M")
                    if _reset_dt > datetime.now():
                        _mins = int((_reset_dt - datetime.now()).total_seconds() / 60)
                        _h = _mins // 60
                        _m = _mins % 60
                        print(f"   ⏰ reset at {acc['reset_at']} (~{_h}h {_m}m) - skip")
                        last_error = f"{acc.get('name', '?')}: reset pending (~{_h}h {_m}m)"
                        if counter < total:
                            time.sleep(sleep_between)
                        continue
                except Exception:
                    pass

            try:
                _rq = check_quota(acc["token"])
                if _rq:
                    _rem = _rq.get("current", 0)
                    if _rem < 30:
                        print(f"   HF live: {_rem:.0f}s only - skip")
                        last_error = f"{acc.get('name', '?')}: quota 0s ({_rem:.0f}s)"
                        for _a in cfg["accounts"]:
                            if _a["id"] == acc["id"]:
                                _a["quota"] = {
                                    "total_seconds": _rq.get("base", 300),
                                    "used_seconds": max(0, _rq.get("base", 300) - _rem),
                                    "remaining_seconds": _rem,
                                    "total_runs": _rq.get("runs", {}).get("limit", 8),
                                    "used_runs": _rq.get("runs", {}).get("used", 0),
                                    "remaining_runs": _rq.get("runs", {}).get("remaining", 8),
                                }
                                _a["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                                break
                        save_config(cfg)
                        if counter < total:
                            print(f"   Sleep {sleep_between}s...")
                            time.sleep(sleep_between)
                        continue
                    print(f"   HF live: {_rem:.0f}s left")
            except Exception as _e:
                print(f"   HF check failed: {_e}")

            img_path = Path(image) if image else None
            end_path = Path(end_frame) if end_frame else None

            video, err = generate_with_account(
                space_info, acc, img_path, end_path,
                prompt, duration, aspect, timeout=timeout,
            )

            if video:
                if not output_name:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    output_name = f"{space_info['type']}_{ts}.mp4"
                output_path = OUTPUT_DIR / output_name
                shutil.copy(video, output_path)
                size_mb = output_path.stat().st_size / 1024 / 1024

                actual_duration = duration or space_info.get("default_duration", 2)
                update_quota(cfg, acc["id"], used_seconds=actual_duration)
                cfg["active_account_id"] = acc["id"]
                cfg.setdefault("spaces", {})
                cfg["spaces"]["last_success"] = space_key
                save_config(cfg)

                print()
                print("=" * 70)
                print(f"SUCCESS!")
                print(f"   Space:    {space_info['id']}")
                print(f"   Account:  {acc.get('name', '?')}")
                print(f"   Try:      [{counter}/{total}]")
                print("=" * 70)

                return {
                    "success": True,
                    "video_path": str(output_path),
                    "size_mb": round(size_mb, 2),
                    "account_used": acc.get("name", "?"),
                    "space": space_info["id"],
                    "space_key": space_key,
                    "duration": actual_duration,
                    "attempt": counter,
                    "total": total,
                }
            else:
                last_error = err
                # ═══ استخرج وقت التجديد من رسالة الخطأ ═══
                wait_secs = extract_reset_time(err)
                if wait_secs:
                    # (removed local import)
                    reset_dt = datetime.now() + timedelta(seconds=wait_secs)
                    for _a in cfg.get("accounts", []):
                        if _a["id"] == acc["id"]:
                            _a["reset_at"] = reset_dt.strftime("%Y-%m-%d %H:%M")
                            _a["reset_in_seconds"] = wait_secs
                            # حدّث الـ quota = 0
                            if _a.get("quota"):
                                _a["quota"]["remaining_seconds"] = 0
                                _a["quota"]["remaining_runs"] = 0
                            break
                    save_config(cfg)
                    h = wait_secs // 3600
                    mn = (wait_secs % 3600) // 60
                    print(f"   FAILED: quota reset in {h}h {mn}m")
                else:
                    print(f"   FAILED: {err[:100]}")
                if counter < total:
                    print(f"   Sleep {sleep_between}s...")
                    time.sleep(sleep_between)

    return {
        "success": False,
        "error": f"All failed. Last: {last_error}",
        "attempts": counter,
    }


def cmd_list_spaces():
    """اعرض كل الـ Spaces المتاحة"""
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + f"CairoAi v{VERSION} — Spaces".center(78) + "║")
    print("╚" + "═" * 78 + "╝")
    print()
    for key, sp in SPACES.items():
        print(f"  🔹 {key}")
        print(f"     ID:       {sp['id']}")
        print(f"     Name:     {sp['name']}")
        print(f"     Modes:    {', '.join(sp['modes'])}")
        print(f"     Max:      {sp['max_duration']}s")
        print(f"     Default:  {sp['default_duration']}s")
        print(f"     Desc:     {sp['description']}")
        print()
    print("💡 للاستخدام: --space <key>")
    print()


def cmd_list_accounts():
    """اعرض كل الحسابات"""
    cfg = load_config()
    accounts = cfg.get("accounts", [])
    active_id = cfg.get("active_account_id")

    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + f"CairoAi v{VERSION} — Accounts ({len(accounts)})".center(78) + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    if not accounts:
        print("  ⚠️ مفيش حسابات. ضيف من web_app أو عدّل config.json")
        print()
        return

    for i, acc in enumerate(accounts, 1):
        marker = "🟢" if acc.get("id") == active_id else "⚪"
        q = acc.get("quota") or {}
        print(f"  {marker} {i}. {acc.get('name', '?')}")
        print(f"     ID:      {acc.get('id', '?')}")
        print(f"     Token:   {acc['token'][:20]}...{acc['token'][-4:]}")
        if q:
            print(f"     Quota:   {q.get('remaining_runs', '?')}/{q.get('total_runs', '?')} runs | "
                  f"{q.get('remaining_seconds', '?')}s")
        else:
            print(f"     Quota:   (مافحصش)")
        print()


def cmd_check_quota():
    """فحص quota لكل الحسابات من HF API + حفظ في config.json"""
    cfg = load_config()
    accounts = cfg.get("accounts", [])

    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + f"CairoAi v{VERSION} — Quota Check".center(78) + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    seen_tokens = set()
    updated = 0
    for i, acc in enumerate(accounts, 1):
        token = acc["token"]
        print(f"  {i}. {acc.get('name', '?')}")
        print(f"     Token: {token[:20]}...{token[-4:]}")

        if token in seen_tokens:
            print(f"     ⚠️  توكن مكرر!")
            print()
            continue
        seen_tokens.add(token)

        ok, info = test_token(token)
        if not ok:
            print(f"     ❌ التوكن غير صالح: {info}")
            print()
            continue

        q = check_quota(token)
        if q:
            current = q.get("current", 0)
            base = q.get("base", 300)
            runs = q.get("runs", {})
            print(f"     ✅ HF User: {info}")
            print(f"     🎬 GPU:     {base - current:.1f}s / {base}s | متبقي {current:.1f}s")
            print(f"     🔄 Runs:    {runs.get('used', 0)}/{runs.get('limit', 8)} | "
                  f"متبقي {runs.get('remaining', 8)}")
            print(f"     ⏰ Reset:   {q.get('resetsAt', '?')}")
            
            # ═══ جديد: احفظ في config.json + وقت التجديد ═══
            reset_at = q.get("resetsAt") or q.get("resets_at") or None
            for a in cfg["accounts"]:
                if a["id"] == acc["id"]:
                    a["quota"] = {
                        "total_seconds": base,
                        "used_seconds": max(0, base - current),
                        "remaining_seconds": current,
                        "total_runs": runs.get("limit", 8),
                        "used_runs": runs.get("used", 0),
                        "remaining_runs": runs.get("remaining", 8),
                    }
                    a["reset_at"] = reset_at
                    a["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    updated += 1
                    break
        else:
            print(f"     ✅ HF User: {info}")
            print(f"     ⚠️  فشل جلب quota")
        print()
    
    if updated > 0:
        save_config(cfg)
        print(f"💾 تم حفظ {updated} حساب في config.json")
        print()


def cmd_generate(args):
    """توليد فيديو"""
    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + f"CairoAi v{VERSION} — Generation".center(78) + "║")
    print("╚" + "═" * 78 + "╝")

    if getattr(args, "multi", False):
        spaces = None
        if getattr(args, "spaces", None):
            spaces = [s.strip() for s in args.spaces.split(",") if s.strip()]
        account_ids = None
        if getattr(args, "accounts", None):
            account_ids = [s.strip() for s in args.accounts.split(",") if s.strip()]
        result = generate_video_multi(
            spaces=spaces,
            account_ids=account_ids,
            mode=getattr(args, "mode", "img2video"),
            image=args.image,
            end_frame=getattr(args, "end_frame", None),
            prompt=args.prompt,
            duration=args.duration,
            aspect=args.aspect,
            sleep_between=getattr(args, "sleep", 10),
            output_name=args.output,
        )
    else:
        result = generate_video(
            space=args.space,
            image=args.image,
            end_frame=getattr(args, "end_frame", None),
            prompt=args.prompt,
            duration=args.duration,
            aspect=args.aspect,
            account=args.account,
            strict=args.strict,
            output_name=args.output,
        )

    print()
    print("=" * 80)
    if result.get("success"):
        print(f"✅ نجح")
        print(f"   🎬 الفيديو:  {result['video_path']}")
        print(f"   📦 الحجم:     {result['size_mb']} MB")
        print(f"   ⏱️  المدة:     {result.get('duration', '?')}s")
        print(f"   👤 الحساب:   {result['account_used']}")
        print(f"   🚀 Space:    {result['space']}")
        print("=" * 80)
        return 0
    else:
        print(f"❌ فشل")
        print(f"   {result.get('error', '?')}")
        print("=" * 80)
        return 1


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════
def build_parser():
    parser = argparse.ArgumentParser(
        prog="cairoai",
        description=f"CairoAi v{VERSION} — Multi-Space AI Video Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  # اعرض الـ Spaces
  python3 cairoai.py --list-spaces

  # اعرض الحسابات
  python3 cairoai.py --list-accounts

  # افحص quota
  python3 cairoai.py --check-quota

  # ولّد فيديو
  python3 cairoai.py --space ltx_distilled \\
                     --image inputs/logo.png \\
                     --prompt "cinematic glow, light sweep" \\
                     --duration 5

  # بحساب معين
  python3 cairoai.py --space wan_fast --image x.png \\
                     --prompt "..." --account acc_xxx
""",
    )
    parser.add_argument("--version", action="version", version=f"CairoAi v{VERSION}")

    # Actions
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-spaces", action="store_true", help="اعرض كل الـ Spaces")
    group.add_argument("--list-accounts", action="store_true", help="اعرض كل الحسابات")
    group.add_argument("--check-quota", action="store_true", help="افحص quota")

    # Generate args
    parser.add_argument("--space", "-s", help="Space key أو id")
    parser.add_argument("--image", "-i", help="مسار الصورة")
    parser.add_argument("--end-frame", help="مسار الصورة الأخيرة (ltx_turbo)")
    parser.add_argument("--prompt", "-p", help="نص الوصف")
    parser.add_argument("--duration", "-d", type=int, help="المدة بالثواني")
    parser.add_argument("--aspect", "-a", default="9:16",
                        choices=["9:16", "16:9", "1:1"], help="نسبة الأبعاد")
    parser.add_argument("--account", help="id الحساب (اختياري)")
    parser.add_argument("--strict", action="store_true",
                        help="رفض duration > max بدل القص")
    parser.add_argument("--output", "-o", help="اسم الملف الناتج")

    # Multi-Space
    parser.add_argument("--multi", action="store_true",
                        help="جرّب كل space مع كل account")
    parser.add_argument("--spaces",
                        help="قائمة spaces مفصولة بفاصلة")
    parser.add_argument("--sleep", type=int, default=10,
                        help="ثواني انتظار بين المحاولات (default: 10)")
    parser.add_argument("--mode", default="img2video",
                        choices=["img2video", "text2video"],
                        help="نوع التوليد (default: img2video)")
    parser.add_argument("--accounts",
                        help="قائمة account IDs مفصولة بفاصلة")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Actions
    if args.list_spaces:
        cmd_list_spaces()
        return 0
    if args.list_accounts:
        cmd_list_accounts()
        return 0
    if args.check_quota:
        cmd_check_quota()
        return 0

    # Generate
    if args.space or getattr(args, "multi", False):
        # في text2video، الـ prompt بس كافي
        mode = getattr(args, "mode", "img2video")
        if mode == "text2video":
            if not args.prompt:
                parser.error("text2video محتاج --prompt")
        else:
            if not args.prompt and not args.image:
                parser.error("img2video محتاج --prompt أو --image")
        return cmd_generate(args)

    # No args → help
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

