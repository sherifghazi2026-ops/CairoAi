#!/usr/bin/env python3
"""CairoAi - Setup password (run once)"""
import json
import secrets
import shutil
from pathlib import Path
from getpass import getpass

try:
    from werkzeug.security import generate_password_hash
except ImportError:
    print("❌ werkzeug مش متثبت")
    print("   شغّل: pip3 install werkzeug")
    raise SystemExit(1)

CONFIG_PATH = Path("config.json")


def main():
    print("🔐 CairoAi — إعداد كلمة المرور\n")

    if not CONFIG_PATH.exists():
        print(f"❌ مش لاقي {CONFIG_PATH}")
        raise SystemExit(1)

    # نسخة احتياطية قبل أي تعديل
    backup = CONFIG_PATH.with_suffix(
        f".json.bak.{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy(CONFIG_PATH, backup)
    print(f"📦 نسخة احتياطية: {backup}\n")

    pw = getpass("ادخل كلمة المرور الجديدة: ")
    pw2 = getpass("أكد كلمة المرور: ")

    if pw != pw2:
        print("❌ كلمتا المرور غير متطابقتين")
        raise SystemExit(1)

    if len(pw) < 6:
        print("❌ كلمة المرور قصيرة (6 أحرف على الأقل)")
        raise SystemExit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg["auth"] = {
        "password_hash": generate_password_hash(pw, method="pbkdf2:sha256"),
        "session_secret": secrets.token_hex(32),
    }

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    print("\n✅ تم حفظ كلمة المرور في config.json")
    print("⚠️  ماترفعش config.json على GitHub أبدًا!")


if __name__ == "__main__":
    main()
