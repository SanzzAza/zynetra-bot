import os
import time
import shutil
import sqlite3
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

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


def run_cmd(cmd):
    try:
        return subprocess.check_output(
            cmd,
            shell=True,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=20
        ).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()
    except Exception as e:
        return f"ERROR|{e}"


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
        expired_text TEXT,
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
        [InlineKeyboardButton("🚀 Ambil Trial 30 Menit", callback_data="trial")],
        [
            InlineKeyboardButton("🔍 Cek Trial", callback_data="status"),
            InlineKeyboardButton("📡 Server", callback_data="server"),
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
            InlineKeyboardButton("📡 Server", callback_data="server"),
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


def server_status_text():
    uptime = run_cmd("uptime -p")
    ram = run_cmd("free -m | awk 'NR==2{printf \"%s/%s MB\", $3,$2}'")
    disk = run_cmd("df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'")
    cpu = run_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print 100-$8 \"%\"}'")

    return f"""┏━━〔 ZYNETRA SERVER 〕━━┓
┃ 🟢 Status : Online
┃ 🌐 Host   : {HOST}
┃ 🏢 ISP    : {ISP_NAME}
┃ 🕒 Jam    : {fmt_time(now_wib())}
┃ ⚙️ CPU    : {cpu}
┃ 💾 RAM    : {ram}
┃ 💽 Disk   : {disk}
┃ ⏱ Uptime : {uptime}
┗━━━━━━━━━━━━━━━━━━┛"""


def get_trial_from_script(vpn_user):
    out = run_cmd(f"bash {TRIAL_SCRIPT} status {vpn_user}")
    if out.startswith("AKTIF|"):
        parts = out.split("|")
        return {
            "vpn_user": vpn_user,
            "vpn_pass": parts[1],
            "left": parts[2],
        }
    return None


def create_trial_from_script(vpn_user):
    out = run_cmd(f"bash {TRIAL_SCRIPT} create {vpn_user}")

    if out.startswith("BERHASIL|") or out.startswith("SUDAH_ADA|"):
        parts = out.split("|")
        vpn_pass = parts[1]
        expired_text = parts[2] if len(parts) > 2 else "30 menit"

        return {
            "vpn_user": vpn_user,
            "vpn_pass": vpn_pass,
            "expired_text": expired_text,
            "raw": out,
        }, True

    return {
        "error": out
    }, False


def save_trial(telegram_id, vpn_user, vpn_pass, expired_text):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO trials VALUES (?, ?, ?, ?, ?)",
        (telegram_id, vpn_user, vpn_pass, expired_text, int(time.time())),
    )
    conn.commit()
    conn.close()


def trial_text(data, created=True):
    title = "AKUN TRIAL BERHASIL DIBUAT" if created else "AKUN TRIAL MASIH AKTIF"

    return f"""┏━━〔 {title} 〕━━┓
┃ 🌐 Host : {HOST}
┃ 🔐 User : {data["vpn_user"]}
┃ 🔑 Pass : {data["vpn_pass"]}
┃ ⏳ Masa : {TRIAL_MINUTES} Menit
┃ 📅 Exp  : {data.get("expired_text", data.get("left", "-"))}
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

📌 Ketentuan:
• 1 akun trial per user aktif
• Trial tidak bisa diperpanjang
• Akun otomatis expired sesuai waktu

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

    vpn_user = f"trial{user.id}"

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

        result, ok = create_trial_from_script(vpn_user)

        if not ok:
            await q.edit_message_text(
                f"❌ Gagal buat trial mas.\n\nError:\n{result['error']}",
                reply_markup=main_menu(user.id),
            )
            return

        save_trial(user.id, result["vpn_user"], result["vpn_pass"], result["expired_text"])
        await q.edit_message_text(trial_text(result, True), reply_markup=main_menu(user.id))

    elif data == "status":
        result = get_trial_from_script(vpn_user)

        if not result:
            await q.edit_message_text(
                "❌ Lu belum punya trial aktif mas.\n\nKlik tombol trial buat bikin akun 30 menit.",
                reply_markup=main_menu(user.id),
            )
            return

        await q.edit_message_text(trial_text(result, False), reply_markup=main_menu(user.id))

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
        total_trial = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        conn.close()

        active_list = run_cmd(f"bash {TRIAL_SCRIPT} list")

        text = f"""┏━━〔 STATISTIK BOT 〕━━┓
┃ 👥 Total User  : {total_user}
┃ 🚀 Total Trial : {total_trial}
┃ ⏳ Durasi      : {TRIAL_MINUTES} menit
┃ 🕒 Jam         : {fmt_time(now_wib())}
┗━━━━━━━━━━━━━━━━━━┛

{active_list}"""

        await q.edit_message_text(text[:3900], reply_markup=admin_menu())

    elif data == "admin_backup":
        if not is_admin(user.id):
            return

        os.makedirs(BACKUP_DIR, exist_ok=True)
        filename = f"{BACKUP_DIR}/zynetra_backup_{int(time.time())}.db"
        shutil.copy(DB_PATH, filename)

        await context.bot.send_document(
            chat_id=user.id,
            document=open(filename, "rb"),
            caption="💾 Backup database Zynetra"
        )
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
