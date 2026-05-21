#!/bin/bash
# =====================================================
# ZYNETRA ziVPN Trial Manager - Single VPS
# =====================================================

CONFIG_FILE="/etc/zivpn/config.json"
TRIAL_DB="/etc/zivpn/trial_users.db"
PREMIUM_DB="/etc/zivpn/premium_users.db"
LOG_FILE="/var/log/zynetra_trial.log"
LOCK_FILE="/tmp/zynetra_trial.lock"
TRIAL_DURATION="${TRIAL_DURATION:-1800}"
ZIVPN_SERVICE="${ZIVPN_SERVICE:-zivpn}"

mkdir -p "$(dirname "$TRIAL_DB")" /var/log
touch "$TRIAL_DB" "$PREMIUM_DB" "$LOG_FILE"

log(){ echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

need_jq(){ command -v jq >/dev/null 2>&1 || { echo "ERROR|jq belum terinstall"; exit 1; }; }

restart_zivpn(){ systemctl reload "$ZIVPN_SERVICE" 2>/dev/null || systemctl restart "$ZIVPN_SERVICE" 2>/dev/null || true; }

format_left(){
  local s="$1" h m sec
  h=$((s/3600)); m=$(((s%3600)/60)); sec=$((s%60))
  if [ "$h" -gt 0 ]; then echo "${h}j ${m}m"; else echo "${m}m ${sec}s"; fi
}

gen_pass(){ echo "trial$(tr -dc '0-9' </dev/urandom | head -c 5)"; }

ensure_config(){
  [ -f "$CONFIG_FILE" ] || { echo "ERROR|config.json tidak ditemukan di $CONFIG_FILE"; exit 1; }
  need_jq
  jq -e '.auth.config | type == "array"' "$CONFIG_FILE" >/dev/null 2>&1 || {
    echo "ERROR|Format config harus .auth.config berupa array password"
    exit 1
  }
}

add_pass(){
  local pass="$1"
  ensure_config
  cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
  jq --arg pass "$pass" 'if (.auth.config | index($pass)) then . else .auth.config += [$pass] end' "$CONFIG_FILE" > /tmp/zynetra_config.json
  mv /tmp/zynetra_config.json "$CONFIG_FILE"
  restart_zivpn
  log "ADD $pass"
}

remove_pass(){
  local pass="$1"
  ensure_config
  cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
  jq --arg pass "$pass" '.auth.config -= [$pass]' "$CONFIG_FILE" > /tmp/zynetra_config.json
  mv /tmp/zynetra_config.json "$CONFIG_FILE"
  restart_zivpn
  log "REMOVE $pass"
}

status_trial(){
  local user="$1" now left
  now=$(date +%s)
  while IFS='|' read -r u pass exp created; do
    [ -z "$u" ] && continue
    if [ "$u" = "$user" ] && [ "$now" -lt "$exp" ]; then
      left=$((exp-now))
      echo "AKTIF|$pass|$(format_left "$left")"
      return 0
    fi
  done < "$TRIAL_DB"
  echo "TIDAK_ADA"
}

cleanup_expired(){
  local now tmp removed=0 active=0 prem_removed=0 prem_active=0
  now=$(date +%s)
  tmp="/tmp/zynetra_trial_db.$$"
  : > "$tmp"
  while IFS='|' read -r user pass exp created; do
    [ -z "$user" ] && continue
    if [ "$now" -lt "$exp" ]; then
      echo "$user|$pass|$exp|$created" >> "$tmp"
      active=$((active+1))
    else
      remove_pass "$pass"
      removed=$((removed+1))
      log "EXPIRED $user $pass"
    fi
  done < "$TRIAL_DB"
  mv "$tmp" "$TRIAL_DB"

  tmp="/tmp/zynetra_premium_db.$$"
  : > "$tmp"
  while IFS='|' read -r user pass exp created days; do
    [ -z "$user" ] && continue
    if [ "$now" -lt "$exp" ]; then
      echo "$user|$pass|$exp|$created|$days" >> "$tmp"
      prem_active=$((prem_active+1))
    else
      remove_pass "$pass"
      prem_removed=$((prem_removed+1))
      log "PREMIUM_EXPIRED $user $pass"
    fi
  done < "$PREMIUM_DB"
  mv "$tmp" "$PREMIUM_DB"

  echo "CLEANUP|trial_removed=$removed|trial_active=$active|premium_removed=$prem_removed|premium_active=$prem_active"
}

create_trial(){
  local user="$1" now exp pass exp_readable old
  [ -z "$user" ] && { echo "ERROR|User kosong"; exit 1; }
  old=$(status_trial "$user")
  if [[ "$old" == AKTIF* ]]; then
    echo "SUDAH_ADA|$(echo "$old" | cut -d'|' -f2)|$(echo "$old" | cut -d'|' -f3)"
    return 0
  fi
  now=$(date +%s)
  exp=$((now + TRIAL_DURATION))
  pass=$(gen_pass)
  while grep -q "|$pass|" "$TRIAL_DB"; do pass=$(gen_pass); done
  add_pass "$pass"
  echo "$user|$pass|$exp|$now" >> "$TRIAL_DB"
  exp_readable=$(date -d "@$exp" '+%d %b %Y %H:%M')
  log "CREATE $user $pass $exp_readable"
  echo "BERHASIL|$pass|$exp_readable"
}

status_premium(){
  local user="$1" now left
  now=$(date +%s)
  while IFS='|' read -r u pass exp created days; do
    [ -z "$u" ] && continue
    if [ "$u" = "$user" ] && [ "$now" -lt "$exp" ]; then
      left=$((exp-now))
      echo "AKTIF|$pass|$(format_left "$left")|$days"
      return 0
    fi
  done < "$PREMIUM_DB"
  echo "TIDAK_ADA"
}

create_premium(){
  local user="$1" days="$2" now exp pass exp_readable old
  [ -z "$user" ] && { echo "ERROR|User kosong"; exit 1; }
  [[ "$days" =~ ^[0-9]+$ ]] || { echo "ERROR|Durasi hari harus angka"; exit 1; }
  [ "$days" -ge 1 ] || { echo "ERROR|Durasi minimal 1 hari"; exit 1; }

  old=$(status_premium "$user")
  if [[ "$old" == AKTIF* ]]; then
    echo "SUDAH_ADA|$(echo "$old" | cut -d'|' -f2)|$(echo "$old" | cut -d'|' -f3)|$(echo "$old" | cut -d'|' -f4)"
    return 0
  fi

  now=$(date +%s)
  exp=$((now + (days * 86400)))
  pass="prem$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8)"
  while grep -q "|$pass|" "$PREMIUM_DB" "$TRIAL_DB" 2>/dev/null; do
    pass="prem$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8)"
  done

  add_pass "$pass"
  echo "$user|$pass|$exp|$now|$days" >> "$PREMIUM_DB"
  exp_readable=$(date -d "@$exp" '+%d %b %Y %H:%M')
  log "PREMIUM_CREATE $user $pass ${days}hari $exp_readable"
  echo "BERHASIL|$pass|$exp_readable|$days"
}

list_premium(){
  local now count=0 left
  now=$(date +%s)
  echo "===== ZYNETRA PREMIUM AKTIF ====="
  while IFS='|' read -r user pass exp created days; do
    [ -z "$user" ] && continue
    if [ "$now" -lt "$exp" ]; then
      left=$((exp-now))
      echo "USER: $user | PASS: $pass | PAKET: ${days} hari | SISA: $(format_left "$left")"
      count=$((count+1))
    fi
  done < "$PREMIUM_DB"
  echo "TOTAL: $count"
}

remove_user(){
  local user="$1" pass=""
  [ -z "$user" ] && { echo "ERROR|User kosong"; exit 1; }
  pass=$(awk -F'|' -v u="$user" '$1==u{print $2; exit}' "$PREMIUM_DB" "$TRIAL_DB")
  [ -z "$pass" ] && { echo "ERROR|User tidak ditemukan"; exit 1; }
  remove_pass "$pass"
  sed -i "/^${user}|/d" "$PREMIUM_DB" "$TRIAL_DB"
  echo "OK|User $user dihapus"
}

list_trials(){
  local now count=0 left
  now=$(date +%s)
  echo "===== ZYNETRA TRIAL AKTIF ====="
  while IFS='|' read -r user pass exp created; do
    [ -z "$user" ] && continue
    if [ "$now" -lt "$exp" ]; then
      left=$((exp-now))
      echo "USER: $user | PASS: $pass | SISA: $(format_left "$left")"
      count=$((count+1))
    fi
  done < "$TRIAL_DB"
  echo "TOTAL: $count"
}

manual_remove(){
  local pass="$1"
  [ -z "$pass" ] && { echo "ERROR|Password kosong"; exit 1; }
  remove_pass "$pass"
  sed -i "/|${pass}|/d" "$TRIAL_DB"
  echo "OK|Password $pass dihapus"
}

(
  flock -x 9
  case "$1" in
    create) create_trial "$2" ;;
    status) status_trial "$2" ;;
    premium) create_premium "$2" "$3" ;;
    premium_status) status_premium "$2" ;;
    premium_list) list_premium ;;
    list) list_trials ;;
    cleanup) cleanup_expired ;;
    remove_user) remove_user "$2" ;;
    remove) manual_remove "$2" ;;
    *) echo "Usage: $0 {create <user>|status <user>|premium <user> <hari>|premium_status <user>|premium_list|list|cleanup|remove <password>|remove_user <user>}"; exit 1 ;;
  esac
) 9>"$LOCK_FILE"
