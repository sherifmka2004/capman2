#!/usr/bin/env bash
# capman2 one-command installer
#   curl -sSL https://raw.githubusercontent.com/sherifmka2004/capman2/main/install.sh | bash
# or, in a cloned repo:
#   ./install.sh
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
say() { echo -e "${GREEN}▸${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err() { echo -e "${RED}✗${NC} $*" >&2; }

REPO="${CAPMAN_REPO:-https://github.com/sherifmka2004/capman2.git}"
IS_MACOS=false
[[ "$(uname)" == "Darwin" ]] && IS_MACOS=true

# If CAPMAN_DIR not set and we're already running from inside the repo, use it in place
if [ -z "${CAPMAN_DIR:-}" ] && [ -f "${BASH_SOURCE[0]%/*}/pyproject.toml" ]; then
  INSTALL_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
  say "Running from existing repo at $INSTALL_DIR"
else
  INSTALL_DIR="${CAPMAN_DIR:-$HOME/capman2}"
fi

# 1. uv (Python package manager)
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
# Ensure uv is on PATH for this session even if it was already installed
export PATH="$HOME/.local/bin:$PATH"
say "uv: $(uv --version)"

# 2. tesseract for OCR (optional but recommended)
if ! command -v tesseract >/dev/null 2>&1; then
  warn "tesseract not found — OCR on screenshots will be disabled."
  if $IS_MACOS; then
    warn "  Install with:  brew install tesseract"
  else
    warn "  Install with:  sudo apt-get install -y tesseract-ocr"
  fi
fi

# 3. Clone or update repo
if [ -d "$INSTALL_DIR/.git" ]; then
  say "Updating existing capman2 at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  say "Cloning capman2 to $INSTALL_DIR..."
  git clone "$REPO" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# 4. Install Python deps
say "Resolving and installing Python dependencies (uv sync)..."
uv sync --quiet

# 5. Install global wrapper
WRAPPER_TARGET=""
for candidate in /usr/local/bin "$HOME/.local/bin"; do
  if [ -d "$candidate" ] && [ -w "$candidate" ]; then
    WRAPPER_TARGET="$candidate/capman"
    break
  fi
done
if [ -z "$WRAPPER_TARGET" ] && command -v sudo >/dev/null 2>&1; then
  WRAPPER_TARGET="/usr/local/bin/capman"
  say "Installing global 'capman' command (sudo required)..."
  sudo tee "$WRAPPER_TARGET" >/dev/null <<EOF
#!/bin/bash
export PATH="\$HOME/.local/bin:/usr/local/bin:\$PATH"
cd "$INSTALL_DIR" && exec uv run capman "\$@"
EOF
  sudo chmod +x "$WRAPPER_TARGET"
else
  cat > "$WRAPPER_TARGET" <<EOF
#!/bin/bash
export PATH="\$HOME/.local/bin:/usr/local/bin:\$PATH"
cd "$INSTALL_DIR" && exec uv run capman "\$@"
EOF
  chmod +x "$WRAPPER_TARGET"
fi
say "Global command installed: $WRAPPER_TARGET"

# 6. Shell hook (real-time command capture with exit codes, durations, CWD)
HOOK_LINE="source $INSTALL_DIR/shell/capman-init.sh"
HOOK_MARKER="# capman2 shell hook"
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  [ -f "$rc" ] || continue
  if grep -Fq "$HOOK_MARKER" "$rc"; then
    say "Shell hook already present in $rc — skipping"
  else
    {
      echo ""
      echo "$HOOK_MARKER"
      echo "$HOOK_LINE"
    } >> "$rc"
    say "Installed shell hook into $rc"
  fi
done
say "Shell hook will activate on next shell start (or run: source ~/.zshrc)"

# 7. macOS: auto-start LaunchAgent + permissions
if $IS_MACOS; then
  say "macOS detected — setting up auto-start LaunchAgent..."

  LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
  PLIST="$LAUNCH_AGENTS_DIR/com.capman.daemon.plist"
  LOG_DIR="$HOME/.capman"
  mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

  # Resolve the actual Python binary uv will use (needed for TCC/permissions)
  PYTHON_BIN=$(uv run python -c "import sys; print(sys.executable)")
  say "Python binary: $PYTHON_BIN"

  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.capman.daemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>$WRAPPER_TARGET</string>
        <string>start</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/capman-daemon.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/capman-daemon.err</string>
</dict>
</plist>
EOF

  # Unload existing agent if running, then load the new one
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load -w "$PLIST"
  say "LaunchAgent loaded — capman will now auto-start on every login"

  # Trigger the Automation (System Events) permission dialog
  say "Requesting macOS Automation permission for window sensor..."
  "$PYTHON_BIN" -c \
    "import subprocess; subprocess.run(['osascript','-e','tell app \"System Events\" to get name of first process'], check=False)" \
    2>/dev/null || true

  echo ""
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}  macOS permissions required (one-time setup):${NC}"
  echo ""
  echo -e "  ${CYAN}1. Accessibility${NC} (keyboard + mouse sensors):"
  echo "     System Settings → Privacy & Security → Accessibility → +"
  echo "     Then press ⌘⇧G and paste:"
  echo -e "     ${CYAN}$PYTHON_BIN${NC}"
  echo ""
  echo -e "  ${CYAN}2. Automation → System Events${NC} (window sensor):"
  echo "     A permission dialog may have just appeared — click OK."
  echo "     If not: System Settings → Privacy & Security → Automation"
  echo "     → find python3.12 → enable System Events"
  echo ""
  echo "  After granting, restart the daemon:"
  echo -e "  ${CYAN}launchctl unload ~/Library/LaunchAgents/com.capman.daemon.plist && launchctl load -w ~/Library/LaunchAgents/com.capman.daemon.plist${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
fi

# 8. LLM API key
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  warn "No LLM API key found in environment."
  echo ""
  echo "  capman captures activity even without a key, but won't analyze sessions."
  echo "  To enable analysis, set one of these in your shell rc file:"
  echo ""
  echo "    export OPENROUTER_API_KEY=sk-or-v1-..."
  echo "    export ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
fi

# 9. Done
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if $IS_MACOS; then
  echo -e "${GREEN}  capman2 installed and running.${NC}  Open ${CYAN}http://localhost:7331${NC} to explore."
else
  echo -e "${GREEN}  capman2 installed.${NC}  Run ${CYAN}capman start${NC} to begin capturing."
fi
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Commands:"
echo "    ${CYAN}capman start${NC}     start the capture daemon"
echo "    ${CYAN}capman stop${NC}      stop it"
echo "    ${CYAN}capman status${NC}    see captured event/session counts"
echo "    ${CYAN}capman storage${NC}   see disk usage broken down by component"
echo "    ${CYAN}capman query <q>${NC} semantic search over captured knowledge"
echo ""
echo "  Web chat & API:    ${CYAN}http://localhost:7331${NC}"
echo "  Browser extension: ${CYAN}$INSTALL_DIR/browser-extension/${NC} (load unpacked in Chrome)"
echo ""
echo "  Optional — deep file monitoring (captures file opens/reads + responsible process; needs root):"
echo "    set ${CYAN}deep_monitor${NC} under [sensors.filesystem] in config/default.toml"
if $IS_MACOS; then
  echo "    macOS backend: eslogger (needs Full Disk Access — see ${CYAN}docs/FILE_MONITORING.md${NC})"
  echo "    ${CYAN}sudo $PYTHON_BIN $INSTALL_DIR/tools/capman-fsmon/fsmon.py --backend eslogger${NC}"
else
  echo "    Linux backend: fanotify (or auditd/eBPF — see ${CYAN}docs/FILE_MONITORING.md${NC})"
  echo "    ${CYAN}sudo python3 $INSTALL_DIR/tools/capman-fsmon/fsmon.py --backend auto${NC}"
fi
echo ""
