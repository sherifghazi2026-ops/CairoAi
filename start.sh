#!/data/data/com.termux/files/usr/bin/bash
cd ~/CairoAi
echo "🎬 CairoAi Starter"
echo ""

termux-wake-lock 2>/dev/null
echo "✅ Wake Lock"

if pgrep -f "web_app.py" > /dev/null; then
    echo "✅ web_app.py شغال"
else
    nohup python3 web_app.py > ~/webapp.log 2>&1 &
    echo $! > ~/webapp.pid
    disown
    sleep 4
    echo "✅ web_app.py شغال"
fi

pkill -f autossh 2>/dev/null
pkill -f "ssh.*localhost.run" 2>/dev/null
sleep 2

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
disown
sleep 12

LINK=$(grep -o 'https://[a-z0-9]*\.lhr\.life' ~/tunnel.log | tail -1)
echo ""
echo "═══════════════════════════════"
echo "🌐 اللينك: $LINK"
echo "🔐 الباسورد: ghazi"
echo "👤 Users: Sharo / Nona / Youssif"
echo "═══════════════════════════════"
