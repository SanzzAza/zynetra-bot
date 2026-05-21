#!/bin/bash
set -e

BOT_FILE="${1:-/root/zynetra-bot/bot.py}"

if [ ! -f "$BOT_FILE" ]; then
  echo "❌ File bot.py tidak ketemu: $BOT_FILE"
  exit 1
fi

cp "$BOT_FILE" "$BOT_FILE.bak.$(date +%s)"

python3 - "$BOT_FILE" <<'PY'
import sys
from pathlib import Path

p = Path(sys.argv[1])
s = p.read_text()

if "import json" not in s:
    s = s.replace("import asyncio\n", "import asyncio\nimport json\n")
if "import base64" not in s:
    s = s.replace("import json\n", "import json\nimport base64\n")

block = '''
def make_zivpn_link(vpn_user, vpn_pass):
    payload = {
        "v": 2,
        "ps": f"ZYNETRA-{vpn_user}",
        "add": HOST,
        "port": "5667",
        "id": vpn_pass,
        "user": vpn_user,
        "net": "udp",
        "type": "zivpn",
        "host": "",
        "path": "/",
        "tls": "tls",
        "server": SERVER_NAME,
        "isp": ISP_NAME,
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    encoded = base64.b64encode(raw).decode()
    return f"zivpn://{encoded}"


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Khusus admin mas.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("🔗 Format:\\n/link username password\\n\\nContoh:\\n/link andi premABC123")
        return

    vpn_user = safe_vpn_name(context.args[0]) if "safe_vpn_name" in globals() else context.args[0].strip()
    vpn_pass = context.args[1].strip()

    if not vpn_user or not vpn_pass:
        await update.message.reply_text("❌ Username/password kosong mas.")
        return

    link = make_zivpn_link(vpn_user, vpn_pass)
    await update.message.reply_text(f"🔗 Link ziVPN:\\n`{link}`", parse_mode="Markdown")
'''

if "def link_cmd" not in s:
    if "def last_trial_seconds" in s:
        s = s.replace("def last_trial_seconds", block + "\n\ndef last_trial_seconds", 1)
    elif "async def help_cmd" in s:
        s = s.replace("async def help_cmd", block + "\n\nasync def help_cmd", 1)
    else:
        s += "\n" + block

handler_line = '    app.add_handler(CommandHandler("link", link_cmd))\n'
if 'CommandHandler("link"' not in s:
    if 'app.add_handler(CommandHandler("premium", premium_cmd))' in s:
        s = s.replace(
            '    app.add_handler(CommandHandler("premium", premium_cmd))\n',
            '    app.add_handler(CommandHandler("premium", premium_cmd))\n' + handler_line,
            1
        )
    elif 'app.add_handler(CommandHandler("help", help_cmd))' in s:
        s = s.replace(
            '    app.add_handler(CommandHandler("help", help_cmd))\n',
            '    app.add_handler(CommandHandler("help", help_cmd))\n' + handler_line,
            1
        )
    else:
        print("⚠️ Handler main app tidak ketemu, function link_cmd sudah ditambah tapi handler belum otomatis.")

p.write_text(s)
print("✅ PATCH OK:", p)
PY

grep -n 'link_cmd\|CommandHandler("link"' "$BOT_FILE"
