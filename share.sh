#!/data/data/com.termux/files/usr/bin/bash

LINK=$(grep -o 'https://[a-z0-9]*\.lhr\.life' ~/tunnel.log 2>/dev/null | tail -1)

if [ -z "$LINK" ]; then
    echo "❌ مفيش لينك. شغّل: ~/CairoAi/start.sh"
    exit 1
fi

cat << MSG

🎬 **CairoAi — مولّد الفيديو بالذكاء الاصطناعي**

🌐 اللينك: $LINK
🔐 الباسورد: ghazi
👤 المستخدمون: Sharo / Nona / Youssif

📝 **الاستخدام:**
1. افتح اللينك
2. اختر اسمك + الباسورد
3. ارفع صورة + اكتب وصف الحركة
4. اضغط 🎬 توليد
5. استنى 1-5 دقايق

⚠️ لو اللينك مش شغال، قوللي.

MSG
