#!/usr/bin/env bash
# Grok + JEV research desk — one-command installer (macOS / Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/achupradeep3050/grok-jev-research-desk/main/install.sh | bash
#
# Options (pass after `bash -s --` when piping):
#   --dir <path>     install location           (default: ~/grok-jev-research-desk)
#   --no-prompt      do not ask for keys; edit .env yourself later
#   --service        install a background service (launchd on macOS, systemd --user on Linux)
#   --branch <name>  git branch                 (default: main)
set -euo pipefail

REPO="https://github.com/achupradeep3050/grok-jev-research-desk.git"
DIR="${HOME}/grok-jev-research-desk"
BRANCH="main"
PROMPT=1
SERVICE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --no-prompt) PROMPT=0; shift ;;
    --service) SERVICE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done

say()  { printf '\033[1;33m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# 1. prerequisites ------------------------------------------------------------
command -v git >/dev/null || die "git is required"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || die "Python 3.9+ is required"
ok "using $($PY --version)"

# 2. code ---------------------------------------------------------------------
if [ -f "./pyproject.toml" ] && grep -q 'name = "jevdesk"' ./pyproject.toml 2>/dev/null; then
  DIR="$(pwd)"; say "installing from the current checkout: $DIR"
elif [ -d "$DIR/.git" ]; then
  say "updating $DIR"; git -C "$DIR" pull --ff-only
else
  say "cloning into $DIR"; git clone --depth 1 --branch "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

# 3. virtualenv + package -----------------------------------------------------
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dev]"
ok "installed jevdesk $(.venv/bin/jevdesk --version)"

# 4. configuration --------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  ok "created .env (mode 600)"
fi

set_env() {  # set_env KEY VALUE  — replaces or appends, never echoes the value
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  grep -v "^${key}=" .env > "$tmp" || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" .env; chmod 600 .env
}

if [ "$PROMPT" = 1 ] && [ -r /dev/tty ]; then
  echo
  say "Configuration (press Enter to skip any item — everything is optional)"
  read -r -s -p "  xAI API key for Grok (https://console.x.ai): " XAI </dev/tty; echo
  [ -n "$XAI" ] && set_env XAI_API_KEY "$XAI"
  read -r -s -p "  Telegram bot token from @BotFather: " TGT </dev/tty; echo
  [ -n "$TGT" ] && set_env TELEGRAM_BOT_TOKEN "$TGT"
  read -r -p "  Your numeric Telegram user id (ask @userinfobot): " TGID </dev/tty
  [ -n "$TGID" ] && set_env TELEGRAM_OPERATOR_IDS "$TGID"
  read -r -p "  Risk per trade in USD [100]: " RISK </dev/tty
  [ -n "$RISK" ] && set_env RISK_PER_TRADE_USD "$RISK"
  unset XAI TGT
fi

# 5. verification ---------------------------------------------------------------
say "offline self-test"
.venv/bin/jevdesk selftest
say "unit tests"
.venv/bin/python -m pytest -q
say "connectivity check"
.venv/bin/jevdesk doctor || say "doctor reported problems — fix .env, then run: .venv/bin/jevdesk doctor"

# 6. optional background service ----------------------------------------------
if [ "$SERVICE" = 1 ]; then
  if [ "$(uname)" = "Darwin" ]; then
    PLIST="$HOME/Library/LaunchAgents/io.github.jevdesk.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$DIR/data"
    sed -e "s#__DIR__#$DIR#g" deploy/io.github.jevdesk.plist > "$PLIST"
    launchctl bootout "gui/$(id -u)/io.github.jevdesk" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    ok "launchd service io.github.jevdesk started (logs: $DIR/data/desk.log)"
  else
    UNIT="$HOME/.config/systemd/user/jevdesk.service"
    mkdir -p "$(dirname "$UNIT")"
    sed -e "s#__DIR__#$DIR#g" deploy/jevdesk.service > "$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable --now jevdesk.service
    ok "systemd user service jevdesk started (journalctl --user -u jevdesk -f)"
  fi
fi

echo
ok "done. Next:"
echo "   cd $DIR"
echo "   .venv/bin/jevdesk eval gold          # one research pass"
echo "   .venv/bin/jevdesk eval gold --llm    # same, with Grok on every stage"
echo "   .venv/bin/jevdesk run                # start the desk (Telegram cards)"
