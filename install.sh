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
INSTALL_DIR="${CAPMAN_DIR:-$HOME/capman2}"

# 1. uv (Python package manager)
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
say "uv: $(uv --version)"

# 2. tesseract for OCR (optional but recommended)
if ! command -v tesseract >/dev/null 2>&1; then
  warn "tesseract not found — OCR on screenshots will be disabled."
  warn "  Linux:  sudo apt-get install -y tesseract-ocr"
  warn "  macOS:  brew install tesseract"
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

# 5. Install global wrapper at /usr/local/bin/capman (if writable)
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
cd "$INSTALL_DIR" && exec uv run capman "\$@"
EOF
  sudo chmod +x "$WRAPPER_TARGET"
else
  cat > "$WRAPPER_TARGET" <<EOF
#!/bin/bash
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
say "Shell hook will activate on next shell start (or run: source ~/.bashrc)"

# 7. LLM API key
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

# 8. Done
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  capman2 installed.${NC}  Run ${CYAN}capman start${NC} to begin capturing."
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Commands:"
echo "    ${CYAN}capman start${NC}     start the capture daemon"
echo "    ${CYAN}capman stop${NC}      stop it"
echo "    ${CYAN}capman status${NC}    see captured event/session counts"
echo "    ${CYAN}capman query <q>${NC} semantic search over captured knowledge"
echo ""
echo "  Web chat & API:    ${CYAN}http://localhost:7331${NC}"
echo "  Browser extension: ${CYAN}$INSTALL_DIR/browser-extension/${NC} (load unpacked in Chrome)"
echo ""
