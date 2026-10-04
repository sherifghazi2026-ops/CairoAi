#!/data/data/com.termux/files/usr/bin/bash

cd ~/CairoAi

echo "🎬 CairoAi Starter"
echo ""

# 1. Wake Lock
termux-wake-lock
echo "✅ Wake Lock"

# 2. web_app.py
if pgrep -f "web_app.py" > /dev/null; then
    echo "✅ web_app.py شغال"
else
    echo "🚀 شغّل web_app.py..."
    nohup python3 web_app.py > ~/webapp.log 2>&1 &
    echo $! > ~/webapp.pid
    sleep 3
    echo "✅ web_app.py شغال (PID: $(cat ~/webapp.pid))"
fi

# 3. autossh
if pgrep -f autossh > /dev/null; then
    echo "✅ autossh شغال"
else
    echo "🚀 شغّل autossh..."
    nohup autossh -M 0 \
        -o "ServerAliveInterval=15" \
        -o "ServerAliveCountMax=3" \
        -o "ExitOnForwardFailure=yes" \
        -o "StrictHostKeyChecking=no" \
        -o "TCPKeepAlive=yes" \
        -i ~/.ssh/id_ed25519 \
        -R 80:localhost:5000 \
        nokey@localhost.run > ~/tunnel.log 2>&1 &
    echo $! > ~/tunnel.pid
    sleep 5
    echo "✅ autossh شغال (PID: $(cat ~/tunnel.pid))"
fi

# 4. اللينك
LINK=$(grep -o 'https://[a-z0-9]*\.lhr\.life' ~/tunnel.log 2>/dev/null | tail -1)
echo ""
echo "═══════════════════════════════════════"
if [ -n "$LINK" ]; then
    echo "🌐 اللينك: $LINK"
else
    echo "⏳ اللينك لسه بيظهر... استنى شوية"
fi
echo "═══════════════════════════════════════"
echo ""
echo "✅ جاهز"
