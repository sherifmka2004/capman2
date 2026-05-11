# capman2 shell integration — real-time command capture
#
# Source this in your shell rc file:
#   echo 'source ~/capman2/shell/capman-init.sh' >> ~/.bashrc
#   echo 'source ~/capman2/shell/capman-init.sh' >> ~/.zshrc
#
# Optional: override the daemon URL
#   export CAPMAN_API=http://192.168.51.80:7331
#
# Captures, per command:
#   - command text
#   - working directory at time of execution
#   - exit code
#   - duration in milliseconds
#   - hostname, user, tty, shell
#   - timestamp
#
# Sends async via Python (background, never blocks the prompt).
# Silently skips if capman daemon is unreachable.

# Idempotent: only set up once
if [ -n "$CAPMAN_HOOK_LOADED" ]; then
  return 0 2>/dev/null || true
fi
export CAPMAN_HOOK_LOADED=1

CAPMAN_API="${CAPMAN_API:-http://localhost:7331}"

# ----------------------------------------------------------------------
# Universal sender (uses python3 for safe JSON encoding)
# ----------------------------------------------------------------------
_capman_send() {
  local cmd="$1" exit_code="$2" duration_ms="$3" cwd="$4"
  command -v python3 >/dev/null 2>&1 || return 0
  (
    CAPMAN_API="$CAPMAN_API" \
    CMD="$cmd" \
    CWD="$cwd" \
    EXIT="$exit_code" \
    DUR="$duration_ms" \
    SHELL_NAME="$1__shell" \
    python3 - <<'PYEOF' >/dev/null 2>&1 &
import json, os, socket, urllib.request

shell = "zsh" if os.environ.get("ZSH_VERSION") else ("bash" if os.environ.get("BASH_VERSION") else os.path.basename(os.environ.get("SHELL", "shell")))

payload = {
    "command":     os.environ["CMD"],
    "cwd":         os.environ["CWD"],
    "shell":       shell,
    "exit_code":   int(os.environ["EXIT"]),
    "duration_ms": int(os.environ["DUR"]),
    "hostname":    socket.gethostname(),
    "user":        os.environ.get("USER", ""),
    "term":        os.environ.get("TERM", ""),
    "term_program": os.environ.get("TERM_PROGRAM", ""),
    "ssh":         bool(os.environ.get("SSH_CONNECTION")),
}
body = json.dumps({
    "type":         "shell_command",
    "app":          "terminal",
    "window_title": shell,
    "payload":      payload,
    "sensor_id":    "shell_hook",
}).encode("utf-8")
try:
    req = urllib.request.Request(
        os.environ["CAPMAN_API"] + "/events",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=2).read()
except Exception:
    pass
PYEOF
  ) &
  disown 2>/dev/null || true
}

# ----------------------------------------------------------------------
# bash hook (uses DEBUG trap + PROMPT_COMMAND)
# ----------------------------------------------------------------------
if [ -n "$BASH_VERSION" ]; then

  _capman_preexec_bash() {
    # Skip the trap calls that PROMPT_COMMAND itself triggers
    [ -n "$COMP_LINE" ] && return
    [ "$BASH_COMMAND" = "$PROMPT_COMMAND" ] && return
    [[ "$BASH_COMMAND" == _capman_* ]] && return
    CAPMAN_START_MS=$(date +%s%3N 2>/dev/null || echo 0)
    CAPMAN_CMD_PRE="$BASH_COMMAND"
    CAPMAN_CWD_PRE="$PWD"
  }

  _capman_postcmd_bash() {
    local exit_code=$?
    # Pull latest command from history (most reliable source)
    local cmd
    cmd=$(HISTTIMEFORMAT='' history 1 2>/dev/null | sed 's/^[ ]*[0-9]*[ ]*//')
    [ -z "$cmd" ] && return
    [ "$cmd" = "$CAPMAN_LAST_BASH" ] && return
    CAPMAN_LAST_BASH="$cmd"
    local dur=0
    if [ -n "$CAPMAN_START_MS" ] && [ "$CAPMAN_START_MS" != "0" ]; then
      dur=$(( $(date +%s%3N 2>/dev/null || echo 0) - CAPMAN_START_MS ))
    fi
    _capman_send "$cmd" "$exit_code" "$dur" "${CAPMAN_CWD_PRE:-$PWD}"
    unset CAPMAN_START_MS CAPMAN_CMD_PRE CAPMAN_CWD_PRE
  }

  trap '_capman_preexec_bash' DEBUG
  PROMPT_COMMAND="_capman_postcmd_bash${PROMPT_COMMAND:+;$PROMPT_COMMAND}"

# ----------------------------------------------------------------------
# zsh hook (preexec + precmd via add-zsh-hook)
# ----------------------------------------------------------------------
elif [ -n "$ZSH_VERSION" ]; then

  autoload -Uz add-zsh-hook 2>/dev/null

  _capman_preexec_zsh() {
    CAPMAN_CMD_ZSH="$1"
    CAPMAN_CWD_ZSH="$PWD"
    CAPMAN_START_ZSH=$EPOCHSECONDS
  }

  _capman_precmd_zsh() {
    local exit_code=$?
    if [ -n "$CAPMAN_CMD_ZSH" ]; then
      local dur=$(( (EPOCHSECONDS - CAPMAN_START_ZSH) * 1000 ))
      _capman_send "$CAPMAN_CMD_ZSH" "$exit_code" "$dur" "$CAPMAN_CWD_ZSH"
      CAPMAN_CMD_ZSH=""
    fi
  }

  add-zsh-hook preexec _capman_preexec_zsh
  add-zsh-hook precmd _capman_precmd_zsh

fi

# ----------------------------------------------------------------------
# capman command-line helper: post any custom event from a script
#   capman-event <type> '<json-payload>'
# Example:
#   capman-event note_taken '{"text": "remember to refactor X"}'
# ----------------------------------------------------------------------
capman-event() {
  local etype="$1" body="${2:-{}}"
  python3 - <<PYEOF >/dev/null 2>&1 &
import json, os, urllib.request
data = json.dumps({"type": "$etype", "app": "shell", "payload": $body, "sensor_id": "shell_hook"}).encode()
try:
    req = urllib.request.Request("$CAPMAN_API/events", data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2).read()
except Exception:
    pass
PYEOF
  disown 2>/dev/null || true
}
