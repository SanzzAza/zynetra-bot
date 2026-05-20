import os
import time
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
HOST = os.getenv("HOST", "zynetra.duckdns.org")
ISP_NAME = os.getenv("ISP_NAME", "Zynetra Network")
TUTORIAL_URL = os.getenv("TUTORIAL_URL", "https://youtube.com")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
TRIAL_SCRIPT = os.getenv("TRIAL_SCRIPT", "/etc/zivpn/zivpn_trial.sh")

TZ = ZoneInfo("Asia/Jakarta")
DB_PATH = "/opt/zivpn-bot/zynetra.db"
BACKUP_DIR = "/opt/zivpn-bot/backups"

TRIAL_MINUTES = 30
COOLDOWN_SECONDS = 30

cooldown = {}
broadcast_mode = set()


def now_wib():
    return datetime.now(TZ)


def fmt_time(dt):
    return dt.astimezone(TZ).strftime("%d-%m-%Y %H:%M:%S WIB")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS trials (
        telegram_id INTEGER PRIMARY KEY,
        vpn_user TEXT,
        vpn_pass TEXT,
        expired_at INTEGER,
        created_at INTEGER
    )
    """)
    conn.commit()
    return conn


def save_user(user):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)",
        (
            user.id,
            user.username or "",
            user.first_name or "",
            fmt_time(now_wib()),
        ),
    )
    conn.commit()
    conn.close()


def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton("🚀 Buat Trial 30 Menit", callback_data="trial")],
        [
            InlineKeyboardButton("👤 Status Trial", callback_data="status"),
            InlineKeyboardButton("🌐 Server", callback_data="server"),
        ],
        [InlineKeyboardButton("📘 Tutorial", url=TUTORIAL_URL)],
    ]

    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")])

    return InlineKeyboardMarkup(buttons)


def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Statistik", callback_data="admin_stats"),
            InlineKeyboardButton("🌐 Server", callback_data="server"),
        ],
        [
            InlineKeyboardButton("💾 Backup", callback_data="admin_backup"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🧾 Logs", callback_data="admin_logs"),
            InlineKeyboardButton("🔙 Kembali", callback_data="home"),
        ],
    ])


def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=15).strip()
    except Exception as e:
        return str(e)


def server_status_text():
    uptime = run_cmd("uptime -p")
    ram = run_cmd("free -m | awk 'NR==2{printf \"%s/%s MB\", $3,$2}'")
    disk = run_cmd("df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    cpu = run_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print 100-$8 \"%\"}'")
    date_now = fmt_time(now_wib())

    return f"""┏━━〔 ZYNETRA SERVER 〕━━┓
┃ 🟢 Status : Online
┃ 🌐 Host   : {HOST}
┃ 🏢 ISP    : {ISP_NAME}
┃ 🕒 Jam    : {date_now}
┃ ⚙️ CPU    : {cpu}
┃ 💾 RAM    : {ram}
┃ 💽 Disk   : {disk}
┃ ⏱ Uptime : {uptime}
┗━━━━━━━━━━━━━━━━━━┛"""


def get_trial(user_id):
    conn = db()
    row = conn.execute(
        "SELECT vpn_user, vpn_pass, expired_at, created_at FROM trials WHERE telegram_id=?",
        (user_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    vpn_user, vpn_pass, expired_at, created_at = row

    if expired_at <= int(time.time()):
        return None

    return {
        "vpn_user": vpn_user,
        "vpn_pass": vpn_pass,
        "expired_at": expired_at,
        "created_at": created_at,
    }


def create_trial(user):
    old = get_trial(user.id)
    if old:
        return old, False

    vpn_user = f"trial{user.id}"
    vpn_pass = f"zyn{str(user.id)[-5:]}{int(time.time()) % 1000}"
    expired_at = int(time.time()) + TRIAL_MINUTES * 60

    if os.path.exists(TRIAL_SCRIPT):
        cmd = f"bash {TRIAL_SCRIPT} {vpn_user} {vpn_pass} {TRIAL_MINUTES}"
        run_cmd(cmd)

    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO trials VALUES (?, ?, ?, ?, ?)",
        (user.id, vpn_user, vpn_pass, expired_at, int(time.time())),
    )
    conn.commit()
    conn.close()

    return get_trial(user.id), True


def trial_text(data, created_new=True):
    exp = datetime.fromtimestamp(data["expired_at"], TZ)

    title = "AKUN TRIAL BERHASIL DIBUAT" if created_new else "AKUN TRIAL MASIH AKTIF"

    return f"""┏━━〔 {title} 〕━━┓
┃ 🌐 Host : {HOST}
┃ 🔐 User : {data["vpn_user"]}
┃ 🔑 Pass : {data["vpn_pass"]}
┃ ⏳ Masa : {TRIAL_MINUTES} Menit
┃ 📅 Exp  : {fmt_time(exp)}
┗━━━━━━━━━━━━━━━━━━┛

📘 Tutorial:
{TUTORIAL_URL}"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    text = f"""╔══════════════════╗
║    ZYNETRA VPN    ║
╚══════════════════╝

Halo {user.first_name or "user"} 👋

🚀 Trial otomatis
⏳ Durasi trial: {TRIAL_MINUTES} menit
🌐 Server: {HOST}
🏢 ISP: {ISP_NAME}

Pilih menu di bawah 👇"""

    await update.message.reply_text(text, reply_markup=main_menu(user.id))


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Lu bukan admin mas.")
        return

    await update.message.reply_text(
        "┏━━〔 ADMIN PANEL 〕━━┓\n┃ 🛠 ZYNETRA CONTROL\n┗━━━━━━━━━━━━━━━━━━┛",
        reply_markup=admin_menu(),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user
    save_user(user)
    data = q.data

    if data == "home":
        await q.edit_message_text(
            "╔══════════════════╗\n║    ZYNETRA VPN    ║\n╚══════════════════╝\n\nPilih menu di bawah 👇",
            reply_markup=main_menu(user.id),
        )

    elif data == "trial":
        last = cooldown.get(user.id, 0)
        if time.time() - last < COOLDOWN_SECONDS:
            await q.edit_message_text("⏳ Jangan spam mas, coba lagi bentar.", reply_markup=main_menu(user.id))
            return

        cooldown[user.id] = time.time()
        trial, created = create_trial(user)
        await q.edit_message_text(trial_text(trial, created), reply_markup=main_menu(user.id))

    elif data == "status":
        trial = get_trial(user.id)
        if not trial:
            await q.edit_message_text(
                "❌ Lu belum punya trial aktif mas.\n\nKlik tombol trial buat bikin akun 30 menit.",
                reply_markup=main_menu(user.id),
            )
        else:
            await q.edit_message_text(trial_text(trial, False), reply_markup=main_menu(user.id))

    elif data == "server":
        await q.edit_message_text(server_status_text(), reply_markup=main_menu(user.id))

    elif data == "admin":
        if not is_admin(user.id):
            await q.edit_message_text("❌ Lu bukan admin mas.")
            return
        await q.edit_message_text(
            "┏━━〔 ADMIN PANEL 〕━━┓\n┃ 🛠 ZYNETRA CONTROL\n┗━━━━━━━━━━━━━━━━━━┛",
            reply_markup=admin_menu(),
        )

    elif data == "admin_stats":
        if not is_admin(user.id):
            return

        conn = db()
        total_user = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_trial = conn.execute(
            "SELECT COUNT(*) FROM trials WHERE expired_at > ?",
            (int(time.time()),),
        ).fetchone()[0]
        total_trial = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        conn.close()

        text = f"""┏━━〔 STATISTIK BOT 〕━━┓
┃ 👥 Total User  : {total_user}
┃ 🚀 Total Trial : {total_trial}
┃ 🟢 Trial Aktif : {active_trial}
┃ ⏳ Durasi      : {TRIAL_MINUTES} menit
┃ 🕒 Jam         : {fmt_time(now_wib())}
┗━━━━━━━━━━━━━━━━━━┛"""

        await q.edit_message_text(text, reply_markup=admin_menu())

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

        logs = run_cmd("tail -n 40 /var/log/zynetra_bot.log")
        if len(logs) > 3500:
            logs = logs[-3500:]

        await q.edit_message_text(f"🧾 LOG TERAKHIR:\n\n{logs}", reply_markup=admin_menu())

    elif data == "admin_broadcast":
        if not is_admin(user.id):
            return

        broadcast_mode.add(user.id)
        await q.edit_message_text(
            "📢 Kirim pesan broadcast sekarang.\n\nContoh:\nMaintenance jam 02:00 WIB",
            reply_markup=admin_menu(),
        )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if user.id in broadcast_mode:
        broadcast_mode.remove(user.id)

        if not is_admin(user.id):
            return

        msg = update.message.text

        conn = db()
        users = conn.execute("SELECT telegram_id FROM users").fetchall()
        conn.close()

        success = 0
        failed = 0

        for (uid,) in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 INFO ZYNETRA\n\n{msg}")
                success += 1
            except Exception:
                failed += 1

        await update.message.reply_text(f"✅ Broadcast selesai.\n\nBerhasil: {success}\nGagal: {failed}")
    else:
        await update.message.reply_text("Ketik /start mas 😎")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN kosong. Isi .env dulu mas.")

    os.makedirs("/opt/zivpn-bot", exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    db().close()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("ZYNETRA BOT RUNNING...")
    app.run_polling()


if __name__ == "__main__":
    main()
