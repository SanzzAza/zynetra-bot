#!/bin/bash
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
INSTALL_DIR="/etc/zivpn"
BOT_DIR="/opt/zivpn-bot"
SERVICE_FILE="/etc/systemd/system/zynetra-bot.service"

[ "$EUID" -eq 0 ] || { echo -e "${RED}Jalankan sebagai root: sudo bash install.sh${NC}"; exit 1; }

echo -e "${CYAN}╔══════════════════════════════╗\n║    ZYNETRA BOT INSTALLER     ║\n╚══════════════════════════════╝${NC}"

echo -e "${YELLOW}[1/6] Install dependency...${NC}"
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip jq curl unzip -qq

echo -e "${YELLOW}[2/6] Siapkan folder...${NC}"
mkdir -p "$INSTALL_DIR" "$BOT_DIR" /var/log

echo -e "${YELLOW}[3/6] Copy file...${NC}"
cp bot.py "$BOT_DIR/bot.py"
cp requirements.txt "$BOT_DIR/requirements.txt"
cp zivpn_trial.sh "$INSTALL_DIR/zivpn_trial.sh"
chmod +x "$INSTALL_DIR/zivpn_trial.sh"
touch "$INSTALL_DIR/trial_users.db" "$BOT_DIR/users.txt"

if [ ! -f "$BOT_DIR/.env" ]; then
  cp .env.example "$BOT_DIR/.env"
  echo -e "${RED}[!] .env dibuat. Wajib edit: nano $BOT_DIR/.env${NC}"
else
  echo -e "${GREEN}[✓] .env lama dipertahankan.${NC}"
fi

echo -e "${YELLOW}[4/6] Setup virtualenv...${NC}"
python3 -m venv "$BOT_DIR/venv"
"$BOT_DIR/venv/bin/pip" install -U pip -q
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt" -q

echo -e "${YELLOW}[5/6] Setup systemd...${NC}"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=ZYNETRA ziVPN Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/zynetra_bot.log
StandardError=append:/var/log/zynetra_bot.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable zynetra-bot >/dev/null

echo -e "${YELLOW}[6/6] Setup cleanup cron...${NC}"
CRON_JOB="*/10 * * * * bash /etc/zivpn/zivpn_trial.sh cleanup >> /var/log/zynetra_trial.log 2>&1"
( crontab -l 2>/dev/null | grep -v "zivpn_trial.sh cleanup"; echo "$CRON_JOB" ) | crontab -

echo -e "${GREEN}\n✅ Install selesai.${NC}"
echo -e "Edit env: ${YELLOW}nano $BOT_DIR/.env${NC}"
echo -e "Start:    ${YELLOW}systemctl restart zynetra-bot${NC}"
echo -e "Status:   ${YELLOW}systemctl status zynetra-bot${NC}"
echo -e "Log:      ${YELLOW}tail -f /var/log/zynetra_bot.log${NC}"
