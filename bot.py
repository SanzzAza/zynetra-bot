import os
import time
import shutil
import sqlite3
import logging
import subprocess
import asyncio
import json
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

load_dotenv()

BOT_TOKEN   = os.getenv("BOT_TOKEN", "")
HOST        = os.getenv("HOST", "zynetra.duckdns.org")
SERVER_NAME = os.getenv("SERVER_NAME", "SG-1")
ISP_NAME    = os.getenv("ISP_NAME", "Zynetra Network")
TUTORIAL_URL = os.getenv("TUTORIAL_URL", "https://youtube.com")
ADMIN_IDS   = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
TRIAL_SCRIPT = os.getenv("TRIAL_SCRIPT", "/etc/zivpn/zivpn_trial.sh")

# Setting default untuk config/link ziVPN
VPN_PORT    = os.getenv("VPN_PORT", "5667")
VPN_SNI     = os.getenv("VPN_SNI", "m.facebook.com")
VPN_MTU     = os.getenv("VPN_MTU", "1300")
VPN_DNS     = os.getenv("VPN_DNS", "1.1.1.1")

TZ = ZoneInfo("Asia/Jakarta")
DB_PATH    = "/opt/zivpn-bot/zynetra.db"
BACKUP_DIR = "/opt/zivpn-bot/backups"
LOG_FILE   = "/var/log/zynetra_bot.log"

TRIAL_MINUTES      = 30
DAILY_LIMIT_SECONDS = 24 * 60 * 60
COOLDOWN_SECONDS   = 30
REMINDER_SECONDS   = 5 * 60
CLEANUP_INTERVAL   = 10 * 60  # jalanin cleanup tiap 10 menit

cooldown       = {}
broadcast_mode = set()
premium_mode   = set()
reminder_sent  = set()

# ── Logging ──────────────────────────────────────────────────────────────────
os.makedirs("/var/log", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_wib():
    return datetime.now(TZ)

def fmt_time(dt):
    return dt.astimezone(TZ).strftime("%d-%m-%Y %H:%M:%S WIB")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=20).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()
    except Exception as e:
        return f"ERROR|{e}"


# ── Database ──────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT
    )""")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trials (
        telegram_id INTEGER PRIMARY KEY,
        vpn_user TEXT,
        vpn_pass TEXT,
        expired_text TEXT,
        created_at INTEGER
    )""")
    conn.commit()
    return conn

def save_user(user):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)",
        (user.id, user.username or "", user.first_name or "", fmt_time(now_wib())),
    )
    conn.commit()
    conn.close()


# ── Menu ──────────────────────────────────────────────────────────────────────
def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton("🚀 Ambil Trial 30 Menit", callback_data="trial")],
        [InlineKeyboardButton("🔍 Cek Trial", callback_data="status"), InlineKeyboardButton("📡 Server", callback_data="server")],
        [InlineKeyboardButton("📘 Tutorial", url=TUTORIAL_URL)],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(buttons)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistik", callback_data="admin_stats"), InlineKeyboardButton("📡 Server", callback_data="server")],
        [InlineKeyboardButton("👑 Create Premium", callback_data="admin_premium"), InlineKeyboardButton("📋 List Premium", callback_data="admin_premium_list")],
        [InlineKeyboardButton("💾 Backup", callback_data="admin_backup"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🧾 Logs", callback_data="admin_logs"), InlineKeyboardButton("🔙 Kembali", callback_data="home")],
    ])


# ── Server info ───────────────────────────────────────────────────────────────
def server_status_text():
    uptime = run_cmd("uptime -p")
    ram    = run_cmd("free -m | awk 'NR==2{printf \"%s/%s MB\", $3,$2}'")
    disk   = run_cmd("df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}' ")
    cpu    = run_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print 100-$8 \"%\"}'")
    return f"""┏━━〔 ZYNETRA SERVER 〕━━┓
┃ 🟢 Status : Online
┃ 🌐 Host   : {HOST}
┃ 🧭 Server : {SERVER_NAME}
┃ 🏢 ISP    : {ISP_NAME}
┃ 🕒 Jam    : {fmt_time(now_wib())}
┃ ⚙️ CPU    : {cpu}
┃ 💾 RAM    : {ram}
┃ 💽 Disk   : {disk}
┃ ⏱ Uptime : {uptime}
┗━━━━━━━━━━━━━━━━━━┛"""


# ── Trial logic ───────────────────────────────────────────────────────────────
def vpn_user_for(user_id):
    return f"trial{user_id}"

def get_trial_from_script(vpn_user):
    out = run_cmd(f"bash {TRIAL_SCRIPT} status {vpn_user}")
    if out.startswith("AKTIF|"):
        parts = out.split("|")
        return {"vpn_user": vpn_user, "vpn_pass": parts[1], "left": parts[2], "raw": out}
    return None

def create_trial_from_script(vpn_user):
    out = run_cmd(f"bash {TRIAL_SCRIPT} create {vpn_user}")
    if out.startswith("BERHASIL|") or out.startswith("SUDAH_ADA|"):
        parts = out.split("|")
        return {"vpn_user": vpn_user, "vpn_pass": parts[1], "expired_text": parts[2] if len(parts) > 2 else "30 menit", "raw": out}, True
    return {"error": out}, False

def safe_vpn_name(name: str) -> str:
    clean = "".join(c for c in name.strip() if c.isalnum() or c in ("_", "-"))
    return clean[:32]


# ── ziVPN config/link generator ───────────────────────────────────────────────
def make_zivpn_payload(vpn_user: str, vpn_pass: str, port: str = None, sni: str = None):
    """
    Payload dibuat DINAMIS.
    Jadi user/pass beda = link pasti beda.
    Catatan: ini base64 JSON custom. Kalau app ZIVPN butuh encrypted .ziv asli,
    link ini bisa dipakai sebagai format share internal/manual fallback.
    """
    port = str(port or VPN_PORT)
    sni = str(sni or VPN_SNI)

    return {
        "v": 2,
        "name": f"ZYNETRA-{vpn_user}",
        "ps": f"ZYNETRA-{vpn_user}",
        "server": SERVER_NAME,
        "isp": ISP_NAME,
        "add": HOST,
        "host": HOST,
        "port": port,
        "type": "zivpn",
        "net": "udp",
        "obfs": "zivpn",
        "user": vpn_user,
        "id": vpn_pass,
        "password": vpn_pass,
        "pass": vpn_pass,
        "sni": sni,
        "tls": "tls",
        "mtu": str(VPN_MTU),
        "dns": str(VPN_DNS),
    }


def make_zivpn_link(vpn_user: str, vpn_pass: str, port: str = None, sni: str = None) -> str:
    payload = make_zivpn_payload(vpn_user, vpn_pass, port, sni)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"zivpn://{encoded}"


def config_link_text(vpn_user: str, vpn_pass: str, title: str = "ZYNETRA CONFIG", port: str = None, sni: str = None) -> str:
    port = str(port or VPN_PORT)
    sni = str(sni or VPN_SNI)
    link = make_zivpn_link(vpn_user, vpn_pass, port, sni)

    return f"""┏━━〔 {title} 〕━━┓
┃ 🌐 Host : {HOST}
┃ 🔌 Port : {port}
┃ 📡 SNI  : {sni}
┃ 📦 MTU  : {VPN_MTU}
┃ 🌍 DNS  : {VPN_DNS}
┃ 👤 User : {vpn_user}
┃ 🔑 Pass : {vpn_pass}
┗━━━━━━━━━━━━━━━━━━┛

🔗 Link ziVPN:
{link}"""


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Khusus admin mas.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "🔗 Format:\\n"
            "/link username password [port] [sni]\\n\\n"
            "Contoh:\\n"
            "/link andi premABC123\\n"
            "/link sanzz premNFGO3mGj 5667 m.facebook.com"
        )
        return

    vpn_user = safe_vpn_name(context.args[0])
    vpn_pass = context.args[1].strip()
    port = context.args[2].strip() if len(context.args) >= 3 else VPN_PORT
    sni = context.args[3].strip() if len(context.args) >= 4 else VPN_SNI

    if not vpn_user or not vpn_pass:
        await update.message.reply_text("❌ Username/password kosong mas.")
        return

    await update.message.reply_text(config_link_text(vpn_user, vpn_pass, "ZYNETRA LINK", port, sni))


async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Khusus admin mas.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "📦 Format:\\n"
            "/config username password [port] [sni]\\n\\n"
            "Contoh:\\n"
            "/config andi premABC123"
        )
        return

    vpn_user = safe_vpn_name(context.args[0])
    vpn_pass = context.args[1].strip()
    port = context.args[2].strip() if len(context.args) >= 3 else VPN_PORT
    sni = context.args[3].strip() if len(context.args) >= 4 else VPN_SNI

    await update.message.reply_text(config_link_text(vpn_user, vpn_pass, "ZYNETRA CONFIG", port, sni))


def create_premium_from_script(vpn_user, days):
    vpn_user = safe_vpn_name(vpn_user)
    if not vpn_user:
        return {"error": "Username kosong / tidak valid"}, False
    if not str(days).isdigit() or int(days) < 1:
        return {"error": "Durasi hari harus angka minimal 1"}, False
    out = run_cmd(f"bash {TRIAL_SCRIPT} premium {vpn_user} {int(days)}")
    if out.startswith("BERHASIL|") or out.startswith("SUDAH_ADA|"):
        parts = out.split("|")
        return {
            "vpn_user": vpn_user,
            "vpn_pass": parts[1],
            "expired_text": parts[2] if len(parts) > 2 else "-",
            "days": parts[3] if len(parts) > 3 else str(days),
            "raw": out,
        }, True
    return {"error": out}, False

def last_trial_seconds(user_id):
    conn = db()
    row = conn.execute("SELECT created_at FROM trials WHERE telegram_id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return int(time.time()) - int(row[0])

def save_trial(telegram_id, vpn_user, vpn_pass, expired_text):
    conn = db()
    conn.execute("INSERT OR REPLACE INTO trials VALUES (?, ?, ?, ?, ?)", (telegram_id, vpn_user, vpn_pass, expired_text, int(time.time())))
    conn.commit()
    conn.close()

def trial_today_count():
    today_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM trials WHERE created_at >= ?", (int(today_start),)).fetchone()[0]
    conn.close()
    return count

def trial_text(data, created=True):
    title = "AKUN TRIAL BERHASIL DIBUAT" if created else "AKUN TRIAL MASIH AKTIF"
    return f"""╔══════════════════╗
║    ZYNETRA VPN    ║
╚══════════════════╝

┏━━〔 {title} 〕━━┓
┃ 🌐 Host : {HOST}
┃ 🧭 Server : {SERVER_NAME}
┃ 🔐 User : {data['vpn_user']}
┃ 🔑 Pass : {data['vpn_pass']}
┃ ⏳ Masa : {TRIAL_MINUTES} Menit
┃ 📅 Exp/Sisa : {data.get('expired_text', data.get('left', '-'))}
┗━━━━━━━━━━━━━━━━━━┛

🔗 Link ziVPN:
{make_zivpn_link(data['vpn_user'], data['vpn_pass'])}

🔗 Link ziVPN:
{make_zivpn_link(data['vpn_user'], data['vpn_pass'])}

📘 Tutorial:
{TUTORIAL_URL}"""

def premium_text(data, created=True):
    title = "AKUN PREMIUM BERHASIL DIBUAT" if created else "AKUN PREMIUM MASIH AKTIF"
    return f"""╔══════════════════╗
║    ZYNETRA VPN    ║
╚══════════════════╝

┏━━〔 {title} 〕━━┓
┃ 🌐 Host : {HOST}
┃ 🧭 Server : {SERVER_NAME}
┃ 👑 Paket : Premium {data.get('days', '-')} Hari
┃ 🔐 User : {data['vpn_user']}
┃ 🔑 Pass : {data['vpn_pass']}
┃ 📅 Exp/Sisa : {data.get('expired_text', data.get('left', '-'))}
┗━━━━━━━━━━━━━━━━━━┛

📘 Tutorial:
{TUTORIAL_URL}"""


# ── Notifikasi & reminder ──────────────────────────────────────────────────────
async def notify_admins(context: ContextTypes.DEFAULT_TYPE, user, trial):
    username = f"@{user.username}" if user.username else "-"
    text = f"""🚀 NEW TRIAL

👤 User : {username}
🆔 ID : {user.id}
🌐 Server : {SERVER_NAME}
🔐 VPN User : {trial['vpn_user']}
⏳ Durasi : {TRIAL_MINUTES} Menit
🕒 Jam : {fmt_time(now_wib())}"""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            pass

async def send_expiry_reminder_later(context: ContextTypes.DEFAULT_TYPE, chat_id: int, vpn_user: str):
    key = f"{chat_id}:{vpn_user}"
    if key in reminder_sent:
        return
    reminder_sent.add(key)
    delay = max(10, (TRIAL_MINUTES * 60) - REMINDER_SECONDS)
    await asyncio.sleep(delay)
    active = get_trial_from_script(vpn_user)
    if active:
        try:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Trial lu tinggal sekitar 5 menit lagi mas.\n\nSiapin reconnect atau ambil paket premium kalau ada 😎")
        except Exception:
            pass
    # Bersihkan key setelah reminder selesai
    reminder_sent.discard(key)


# ── Auto cleanup job ──────────────────────────────────────────────────────────
async def auto_cleanup(context: ContextTypes.DEFAULT_TYPE):
    result = run_cmd(f"bash {TRIAL_SCRIPT} cleanup")
    logger.info(f"[auto_cleanup] {result}")


# ── Command handlers ───────────────────────────────────────────────────────────
WELCOME_TEXT = """╔══════════════════╗
║    ZYNETRA VPN    ║
╚══════════════════╝

Halo {name} 👋

🚀 Trial otomatis
⏳ Durasi trial: {minutes} menit
🌐 Host: {host}
🧭 Server: {server}
🏢 ISP: {isp}

📌 Ketentuan:
• 1 akun trial per 24 jam
• Trial tidak bisa diperpanjang
• Akun otomatis expired sesuai waktu

Pilih menu di bawah 👇"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    text = WELCOME_TEXT.format(
        name=user.first_name or "user",
        minutes=TRIAL_MINUTES,
        host=HOST,
        server=SERVER_NAME,
        isp=ISP_NAME,
    )
    await update.message.reply_text(text, reply_markup=main_menu(user.id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    text = WELCOME_TEXT.format(
        name=user.first_name or "user",
        minutes=TRIAL_MINUTES,
        host=HOST,
        server=SERVER_NAME,
        isp=ISP_NAME,
    )
    await update.message.reply_text(text, reply_markup=main_menu(user.id))

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Lu bukan admin mas.")
        return
    await update.message.reply_text("┏━━〔 ADMIN PANEL 〕━━┓\n┃ 🛠 ZYNETRA CONTROL\n┗━━━━━━━━━━━━━━━━━━┛", reply_markup=admin_menu())


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Lu bukan admin mas.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("👑 Format:\n/premium username hari\n\nContoh:\n/premium andi 30")
        return
    result, ok = create_premium_from_script(context.args[0], context.args[1])
    if not ok:
        await update.message.reply_text(f"❌ Gagal buat premium mas.\n\nError:\n{result['error']}")
        return
    await update.message.reply_text(premium_text(result, True))

# ── Callback handler ───────────────────────────────────────────────────────────
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    save_user(user)
    data = q.data
    vpn_user = vpn_user_for(user.id)

    if data == "home":
        await q.edit_message_text("╔══════════════════╗\n║    ZYNETRA VPN    ║\n╚══════════════════╝\n\nPilih menu di bawah 👇", reply_markup=main_menu(user.id))

    elif data == "trial":
        last = cooldown.get(user.id, 0)
        if time.time() - last < COOLDOWN_SECONDS:
            await q.edit_message_text("⏳ Jangan spam mas, coba lagi bentar.", reply_markup=main_menu(user.id))
            return
        cooldown[user.id] = time.time()

        active = get_trial_from_script(vpn_user)
        if active:
            await q.edit_message_text(trial_text(active, False), reply_markup=main_menu(user.id))
            return

        seconds = last_trial_seconds(user.id)
        if seconds is not None and seconds < DAILY_LIMIT_SECONDS:
            left = DAILY_LIMIT_SECONDS - seconds
            h = left // 3600
            m = (left % 3600) // 60
            await q.edit_message_text(f"❌ Trial harian sudah dipakai mas.\n\nCoba lagi dalam {h} jam {m} menit.", reply_markup=main_menu(user.id))
            return

        result, ok = create_trial_from_script(vpn_user)
        if not ok:
            logger.warning(f"Gagal buat trial untuk user {user.id}: {result.get('error')}")
            await q.edit_message_text(f"❌ Gagal buat trial mas.\n\nError:\n{result['error']}", reply_markup=main_menu(user.id))
            return

        save_trial(user.id, result["vpn_user"], result["vpn_pass"], result["expired_text"])
        logger.info(f"Trial dibuat: user={user.id} vpn_user={result['vpn_user']}")
        await notify_admins(context, user, result)
        # Fix: pakai context.application.create_task biar aman
        context.application.create_task(send_expiry_reminder_later(context, user.id, result["vpn_user"]))
        await q.edit_message_text(trial_text(result, True), reply_markup=main_menu(user.id))

    elif data == "status":
        result = get_trial_from_script(vpn_user)
        if not result:
            await q.edit_message_text("❌ Lu belum punya trial aktif mas.\n\nKlik tombol trial buat bikin akun 30 menit.", reply_markup=main_menu(user.id))
            return
        await q.edit_message_text(trial_text(result, False), reply_markup=main_menu(user.id))

    elif data == "server":
        await q.edit_message_text(server_status_text(), reply_markup=main_menu(user.id))

    elif data == "admin":
        if not is_admin(user.id):
            await q.edit_message_text("❌ Lu bukan admin mas.")
            return
        await q.edit_message_text("┏━━〔 ADMIN PANEL 〕━━┓\n┃ 🛠 ZYNETRA CONTROL\n┗━━━━━━━━━━━━━━━━━━┛", reply_markup=admin_menu())

    elif data == "admin_stats":
        if not is_admin(user.id):
            return
        conn = db()
        total_user  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_trial = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        conn.close()
        active_list = run_cmd(f"bash {TRIAL_SCRIPT} list")
        text = f"""┏━━〔 STATISTIK BOT 〕━━┓
┃ 👥 Total User   : {total_user}
┃ 🚀 Total Trial  : {total_trial}
┃ 📅 Trial Today  : {trial_today_count()}
┃ ⏳ Durasi       : {TRIAL_MINUTES} menit
┃ 🕒 Jam          : {fmt_time(now_wib())}
┗━━━━━━━━━━━━━━━━━━┛

{active_list}"""
        await q.edit_message_text(text[:3900], reply_markup=admin_menu())

    elif data == "admin_backup":
        if not is_admin(user.id):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        filename = f"{BACKUP_DIR}/zynetra_backup_{int(time.time())}.db"
        shutil.copy(DB_PATH, filename)
        await context.bot.send_document(chat_id=user.id, document=open(filename, "rb"), caption="💾 Backup database Zynetra")
        await q.edit_message_text("✅ Backup berhasil dikirim mas.", reply_markup=admin_menu())

    elif data == "admin_logs":
        if not is_admin(user.id):
            return
        logs = run_cmd(f"tail -n 40 {LOG_FILE}")
        if len(logs) > 3500:
            logs = logs[-3500:]
        await q.edit_message_text(f"🧾 LOG TERAKHIR:\n\n{logs}", reply_markup=admin_menu())

    elif data == "admin_broadcast":
        if not is_admin(user.id):
            return
        broadcast_mode.add(user.id)
        await q.edit_message_text("📢 Kirim pesan broadcast sekarang.\n\nContoh:\nMaintenance jam 02:00 WIB", reply_markup=admin_menu())


# ── Text handler ───────────────────────────────────────────────────────────────
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    if user.id in premium_mode:
        premium_mode.remove(user.id)
        if not is_admin(user.id):
            return
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Format salah mas.\n\nContoh: andi 30")
            return
        vpn_user = safe_vpn_name(parts[0])
        days = parts[1]
        result, ok = create_premium_from_script(vpn_user, days)
        if not ok:
            await update.message.reply_text(f"❌ Gagal buat premium mas.\n\nError:\n{result['error']}")
            return
        logger.info(f"Premium dibuat: admin={user.id} vpn_user={result['vpn_user']} days={result.get('days')}")
        await update.message.reply_text(premium_text(result, True), reply_markup=admin_menu())
        return

    if user.id in broadcast_mode:
        broadcast_mode.remove(user.id)
        if not is_admin(user.id):
            return
        msg = update.message.text
        conn = db()
        users = conn.execute("SELECT telegram_id FROM users").fetchall()
        conn.close()
        success = 0
        failed  = 0
        for (uid,) in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 INFO ZYNETRA\n\n{msg}")
                success += 1
            except Exception:
                failed += 1
        logger.info(f"Broadcast selesai: berhasil={success} gagal={failed}")
        await update.message.reply_text(f"✅ Broadcast selesai.\n\nBerhasil: {success}\nGagal: {failed}")
    else:
        await update.message.reply_text("Ketik /start mas 😎")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN kosong. Isi .env dulu mas.")
    if not ADMIN_IDS:
        logger.warning("⚠️  ADMIN_IDS kosong! Tidak ada yang bisa akses admin panel.")

    os.makedirs("/opt/zivpn-bot", exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    db().close()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(CommandHandler("config", config_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Auto cleanup tiap 10 menit
    app.job_queue.run_repeating(auto_cleanup, interval=CLEANUP_INTERVAL, first=60)

    logger.info("ZYNETRA BOT RUNNING...")
    app.run_polling()


if __name__ == "__main__":
    main()
