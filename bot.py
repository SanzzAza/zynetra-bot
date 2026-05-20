#!/usr/bin/env python3
# =====================================================
# ZYNETRA ziVPN Bot - Single VPS Edition
# =====================================================

import os
import time
import html
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
HOST = os.getenv("HOST", "your-server.duckdns.org").strip()
ISP_NAME = os.getenv("ISP_NAME", "ZYNETRA SERVER").strip()
SERVER_NAME = os.getenv("SERVER_NAME", "ID-BIZNET-1").strip()
TUTORIAL_URL = os.getenv("TUTORIAL_URL", "https://youtu.be/").strip()
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
TRIAL_SCRIPT = os.getenv("TRIAL_SCRIPT", "/etc/zivpn/zivpn_trial.sh").strip()
ZIVPN_SERVICE = os.getenv("ZIVPN_SERVICE", "zivpn").strip()
BOT_DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "/opt/zivpn-bot"))
USERS_DB = BOT_DATA_DIR / "users.txt"
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "10"))

BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB.touch(exist_ok=True)
_cooldown: dict[int, float] = {}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("/var/log/zynetra_bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger("zynetra-bot")


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def is_admin_id(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def user_key(user_id: int) -> str:
    return f"tg_{user_id}"


def register_user(user) -> None:
    line_id = str(user.id)
    rows = USERS_DB.read_text(errors="ignore").splitlines()
    existing = {r.split("|", 1)[0] for r in rows if r.strip()}
    if line_id not in existing:
        username = user.username or "-"
        name = user.full_name or user.first_name or "-"
        with USERS_DB.open("a", encoding="utf-8") as f:
            f.write(f"{user.id}|{username}|{name}|{int(time.time())}\n")


def all_user_ids() -> list[int]:
    ids: list[int] = []
    for row in USERS_DB.read_text(errors="ignore").splitlines():
        try:
            ids.append(int(row.split("|", 1)[0]))
        except Exception:
            pass
    return sorted(set(ids))


def cooldown_left(user_id: int) -> float:
    last = _cooldown.get(user_id, 0)
    return max(0.0, COOLDOWN_SECONDS - (time.time() - last))


def set_cooldown(user_id: int) -> None:
    _cooldown[user_id] = time.time()


def run_script(action: str, arg: str = "") -> str:
    if not os.path.isfile(TRIAL_SCRIPT):
        return "ERROR|Script trial tidak ditemukan."
    cmd = ["bash", TRIAL_SCRIPT, action]
    if arg:
        cmd.append(arg)
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=35)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if p.returncode != 0 and not out:
            return f"ERROR|{err or 'Script error'}"
        return out or "ERROR|Script tidak mengembalikan output."
    except subprocess.TimeoutExpired:
        return "ERROR|Timeout saat proses akun."
    except Exception as e:
        logger.exception("run_script error")
        return f"ERROR|{e}"


def service_status() -> str:
    try:
        r = subprocess.run(["systemctl", "is-active", ZIVPN_SERVICE], text=True, capture_output=True, timeout=5)
        return "🟢 Online" if r.stdout.strip() == "active" else "🔴 Offline"
    except Exception:
        return "⚪ Unknown"


def server_metrics() -> dict[str, str]:
    disk = shutil.disk_usage("/")
    disk_pct = round((disk.used / disk.total) * 100)

    mem_pct = "?"
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        data = {x.split(":")[0]: int(x.split()[1]) for x in meminfo if x}
        total = data.get("MemTotal", 1)
        available = data.get("MemAvailable", 0)
        mem_pct = str(round(((total - available) / total) * 100))
    except Exception:
        pass

    load = "?"
    try:
        load = os.getloadavg()[0]
        load = f"{load:.2f}"
    except Exception:
        pass

    uptime = "?"
    try:
        seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        uptime = f"{days}d {hours}h"
    except Exception:
        pass

    return {"disk": f"{disk_pct}%", "ram": f"{mem_pct}%", "load": load, "uptime": uptime}


def main_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🚀 Ambil Trial 1 Jam", callback_data="trial")],
        [InlineKeyboardButton("🔍 Cek Trial", callback_data="status"), InlineKeyboardButton("📡 Server", callback_data="server")],
        [InlineKeyboardButton("📘 Tutorial", url=TUTORIAL_URL)],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistik", callback_data="admin_stats"), InlineKeyboardButton("📋 Trial Aktif", callback_data="admin_list")],
        [InlineKeyboardButton("🧹 Cleanup", callback_data="admin_cleanup"), InlineKeyboardButton("💾 Backup", callback_data="admin_backup")],
        [InlineKeyboardButton("⬅️ Menu Utama", callback_data="home")],
    ])


def start_text(user) -> str:
    return (
        "┏━━━〔 <b>ZYNETRA VPN</b> 〕━━━┓\n"
        "┃ 🚀 Trial ziVPN Otomatis\n"
        "┃ ⚡ Cepat • Simple • Auto Expired\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"👋 Halo <b>{esc(user.first_name or 'User')}</b>\n"
        f"🌐 Server: <code>{esc(SERVER_NAME)}</code>\n"
        f"📡 Host: <code>{esc(HOST)}</code>\n\n"
        "📌 <b>Ketentuan Trial</b>\n"
        "• 1 user hanya bisa punya 1 trial aktif\n"
        "• Masa aktif 1 jam\n"
        "• Akun otomatis expired\n\n"
        "Pilih menu di bawah 👇"
    )


def format_trial(password: str, expire: str) -> str:
    return (
        "┏━━━〔 <b>TRIAL AKTIF</b> 〕━━━┓\n"
        f"┃ 🌐 Host : <code>{esc(HOST)}</code>\n"
        f"┃ 🔑 Pass : <code>{esc(password)}</code>\n"
        f"┃ 📡 ISP  : {esc(ISP_NAME)}\n"
        f"┃ ⏳ Exp  : {esc(expire)}\n"
        "┗━━━━━━━━━━━━━━━━━━┛\n\n"
        "⚠️ Jangan share password ke orang lain.\n"
        f"📘 Tutorial: {esc(TUTORIAL_URL)}"
    )


async def notify_admins(app: Application, text: str) -> None:
    for admin_id in ADMIN_IDS:
        if admin_id:
            try:
                await app.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning("notif admin gagal %s: %s", admin_id, e)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    await update.message.reply_text(start_text(user), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))


async def trial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await do_trial(update, context, update.effective_user, False)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    await do_status(update, context, update.effective_user, False)


async def server_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_server_status(update, context, False)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    register_user(user)
    data = q.data

    if data == "home":
        await q.message.edit_text(start_text(user), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))
    elif data == "trial":
        await do_trial(update, context, user, True)
    elif data == "status":
        await do_status(update, context, user, True)
    elif data == "server":
        await send_server_status(update, context, True)
    elif data == "admin":
        if not is_admin_id(user.id):
            await q.message.reply_text("❌ Kamu bukan admin.")
            return
        await q.message.edit_text("👑 <b>Admin Panel ZYNETRA</b>\n\nPilih menu admin:", parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
    elif data.startswith("admin_"):
        if not is_admin_id(user.id):
            await q.message.reply_text("❌ Kamu bukan admin.")
            return
        await handle_admin_callback(update, context, data)


async def do_trial(update: Update, context: ContextTypes.DEFAULT_TYPE, user, is_callback: bool):
    left = cooldown_left(user.id)
    reply = update.callback_query.message.reply_text if is_callback else update.message.reply_text
    if left > 0:
        await reply(f"⏱ Tunggu <b>{left:.0f} detik</b> dulu mas.", parse_mode=ParseMode.HTML)
        return
    set_cooldown(user.id)

    msg = await reply("⏳ Membuat akun trial...")
    result = run_script("create", user_key(user.id))
    parts = result.split("|")
    status = parts[0] if parts else "ERROR"

    if status == "BERHASIL":
        password = parts[1] if len(parts) > 1 else "-"
        expire = parts[2] if len(parts) > 2 else "1 jam"
        await msg.edit_text(format_trial(password, expire), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))
        await notify_admins(context.application, f"🆕 <b>Trial Baru</b>\n👤 @{esc(user.username or user.first_name)} (<code>{user.id}</code>)\n🔑 <code>{esc(password)}</code>\n⏳ {esc(expire)}")
    elif status == "SUDAH_ADA":
        password = parts[1] if len(parts) > 1 else "-"
        sisa = parts[2] if len(parts) > 2 else "?"
        await msg.edit_text(format_trial(password, f"Sisa {sisa}"), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))
    else:
        err = parts[1] if len(parts) > 1 else result
        await msg.edit_text(f"❌ <b>Gagal membuat trial</b>\n\n<code>{esc(err)}</code>", parse_mode=ParseMode.HTML)


async def do_status(update: Update, context: ContextTypes.DEFAULT_TYPE, user, is_callback: bool):
    reply = update.callback_query.message.reply_text if is_callback else update.message.reply_text
    result = run_script("status", user_key(user.id))
    parts = result.split("|")
    status = parts[0] if parts else "ERROR"

    if status == "AKTIF":
        password = parts[1] if len(parts) > 1 else "-"
        sisa = parts[2] if len(parts) > 2 else "?"
        await reply(format_trial(password, f"Sisa {sisa}"), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))
    elif status == "TIDAK_ADA":
        await reply("🔴 Kamu belum punya trial aktif.\n\nKlik tombol <b>Ambil Trial</b> dulu mas.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(user.id)))
    else:
        await reply(f"❌ Gagal cek status.\n<code>{esc(result)}</code>", parse_mode=ParseMode.HTML)


async def send_server_status(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool):
    m = server_metrics()
    text = (
        "┏━━━〔 <b>SERVER STATUS</b> 〕━━━┓\n"
        f"┃ 📡 Service : {service_status()}\n"
        f"┃ 🌐 Host    : <code>{esc(HOST)}</code>\n"
        f"┃ 💾 RAM     : {m['ram']}\n"
        f"┃ 🗄 Disk    : {m['disk']}\n"
        f"┃ ⚙️ Load    : {m['load']}\n"
        f"┃ ⏱ Uptime  : {m['uptime']}\n"
        "┗━━━━━━━━━━━━━━━━━━━━┛"
    )
    reply = update.callback_query.message.reply_text if is_callback else update.message.reply_text
    await reply(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(is_admin_id(update.effective_user.id)))


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    q = update.callback_query
    if data == "admin_stats":
        result = run_script("list")
        total_active = sum(1 for x in result.splitlines() if x.startswith("USER:"))
        total_users = len(all_user_ids())
        await q.message.edit_text(
            f"📊 <b>Statistik ZYNETRA</b>\n\n👥 User bot: <b>{total_users}</b>\n🚀 Trial aktif: <b>{total_active}</b>\n📡 Service: {service_status()}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard(),
        )
    elif data == "admin_list":
        result = run_script("list")
        await q.message.edit_text(f"📋 <b>Trial Aktif</b>\n\n<pre>{esc(result[:3500])}</pre>", parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
    elif data == "admin_cleanup":
        result = run_script("cleanup")
        await q.message.edit_text(f"🧹 <b>Cleanup selesai</b>\n\n<pre>{esc(result)}</pre>", parse_mode=ParseMode.HTML, reply_markup=admin_keyboard())
    elif data == "admin_backup":
        await send_backup(update, context)


async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    backup_path = Path(f"/tmp/zynetra-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt")
    chunks = []
    for path in ["/etc/zivpn/trial_users.db", "/etc/zivpn/config.json", str(USERS_DB)]:
        p = Path(path)
        chunks.append(f"\n===== {path} =====\n")
        chunks.append(p.read_text(errors="ignore") if p.exists() else "FILE TIDAK ADA\n")
    backup_path.write_text("".join(chunks), encoding="utf-8")
    await q.message.reply_document(document=backup_path.open("rb"), filename=backup_path.name, caption="💾 Backup ZYNETRA")
    await q.message.edit_text("✅ Backup dikirim.", reply_markup=admin_keyboard())


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_id(update.effective_user.id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    if not context.args:
        await update.message.reply_text("Contoh:\n<code>/broadcast Maintenance jam 23:00 WIB</code>", parse_mode=ParseMode.HTML)
        return
    text = " ".join(context.args)
    ok = fail = 0
    for uid in all_user_ids():
        try:
            await context.application.bot.send_message(uid, f"📢 <b>Info ZYNETRA</b>\n\n{esc(text)}", parse_mode=ParseMode.HTML)
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"✅ Broadcast selesai.\nTerkirim: {ok}\nGagal: {fail}")


async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_id(update.effective_user.id):
        await update.message.reply_text("❌ Kamu bukan admin.")
        return
    if not context.args:
        await update.message.reply_text("Contoh: <code>/remove trial12345</code>", parse_mode=ParseMode.HTML)
        return
    password = context.args[0]
    result = run_script("remove", password)
    await update.message.reply_text(f"<pre>{esc(result)}</pre>", parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin = "\n👑 Admin: /broadcast, /remove" if is_admin_id(update.effective_user.id) else ""
    await update.message.reply_text("📋 <b>Command</b>\n/start\n/trial\n/status\n/server" + admin, parse_mode=ParseMode.HTML)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN kosong. Isi file .env dulu.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("trial", trial_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("server", server_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("remove", admin_remove))
    app.add_handler(CallbackQueryHandler(callback))
    logger.info("ZYNETRA Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
