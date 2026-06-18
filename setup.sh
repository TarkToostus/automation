#!/usr/bin/env bash
# Tark Automation — one-shot onboarding installer (macOS / Linux).
#
# Installs Claude Code (native binary, NO Node / Xcode / Homebrew), downloads the
# tark_cli, verifies Python, and configures your PAT. Run it with:
#
#   curl -fsSL https://raw.githubusercontent.com/TarkToostus/automation/main/setup.sh | bash
#
# Safe to re-run — every step is idempotent.

set -u
RAW="https://raw.githubusercontent.com/TarkToostus/automation/main"
DEST="${TARK_HOME:-$HOME/tark-automation}"
BIN="$HOME/bin"

say()  { printf '\n\033[1;36m> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[OK]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARNING]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Claude Code (native installer — bundles its own runtime, no Node needed)
# ---------------------------------------------------------------------------
say "Checking Claude Code"
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code already installed ($(command -v claude))"
else
  say "Installing Claude Code (native — no Node, no Xcode, no Homebrew)"
  if curl -fsSL https://claude.ai/install.sh | bash; then
    ok "Claude Code installed"
  else
    err "Claude Code install failed. Open https://claude.ai/code in a browser instead (Path A)."
  fi
fi

# ---------------------------------------------------------------------------
# 2. Fetch the automation files (curl, NOT git — no git dependency)
# ---------------------------------------------------------------------------
say "Downloading tark_cli into $DEST"
mkdir -p "$DEST" "$BIN"
for f in tark_cli.py sales_followup.py README.md ONBOARDING.md; do
  if curl -fsSL "$RAW/$f" -o "$DEST/$f"; then
    ok "downloaded $f"
  else
    warn "could not download $f (skipping)"
  fi
done
chmod +x "$DEST/tark_cli.py" "$DEST/sales_followup.py" 2>/dev/null || true

# Bundle the research skill + workflow guide so Claude Code "just knows" the flow.
mkdir -p "$DEST/.claude/commands"
curl -fsSL "$RAW/.claude/commands/research-customer.md" -o "$DEST/.claude/commands/research-customer.md" 2>/dev/null \
  && ok "bundled /research-customer skill" || warn "skill not bundled (older repo)"
curl -fsSL "$RAW/.claude/CLAUDE.md" -o "$DEST/CLAUDE.md" 2>/dev/null || true

# symlink onto PATH as `tark_cli`
ln -sf "$DEST/tark_cli.py" "$BIN/tark_cli" 2>/dev/null && ok "linked tark_cli -> $BIN/tark_cli"
case ":$PATH:" in
  *":$BIN:"*) : ;;
  *) warn "$BIN is not on your PATH. Add this to ~/.zshrc:  export PATH=\"\$HOME/bin:\$PATH\"" ;;
esac

# ---------------------------------------------------------------------------
# 3. Python 3 — required to run tark_cli (stdlib only, no pip install)
#    On a fresh Mac /usr/bin/python3 is a stub that triggers the Xcode prompt.
#    We do NOT use Xcode/brew — point the user at the python.org .pkg instead.
# ---------------------------------------------------------------------------
say "Checking Python 3"
if python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' >/dev/null 2>&1; then
  ok "Python 3 present ($(python3 --version 2>&1))"
else
  warn "Python 3 not available."
  if [ "$(uname)" = "Darwin" ]; then
    echo "  Install it WITHOUT Xcode/Homebrew — download the official installer (double-click, no terminal):"
    echo "    https://www.python.org/downloads/macos/"
    echo "  Then re-run this script. (Or just use Path A — claude.ai/code — which has Python built in.)"
  else
    echo "  Install python3 via your distro, e.g.  sudo apt-get install -y python3"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Configure PAT + deployment URL
#    Read from the controlling terminal (/dev/tty), NOT stdin — under the
#    documented `curl ... | bash` invocation stdin is the pipe, so a plain
#    `[ -t 0 ]` test is false and the prompts would silently never run.
#    PAT is read with -s (no echo) so it never lands in terminal scrollback.
# ---------------------------------------------------------------------------
say "Configuring your Tark connection"
# pick a readable terminal: stdin if it's a tty, else the controlling /dev/tty
TTY=""
if [ -t 0 ]; then TTY=/dev/stdin; elif [ -e /dev/tty ]; then TTY=/dev/tty; fi
if [ -n "$TTY" ] && command -v python3 >/dev/null 2>&1; then
  printf 'Tark deployment URL (e.g. https://ennetavhooldus.tarktoostus.ee): '; read -r URL < "$TTY" 2>/dev/null || true
  printf 'Your PAT (tark_pat_..., hidden as you type): '; read -rs PAT < "$TTY" 2>/dev/null || true; printf '\n'
  if [ -n "${URL:-}" ] && [ -n "${PAT:-}" ]; then
    python3 "$DEST/tark_cli.py" config set url "$URL"  >/dev/null 2>&1
    python3 "$DEST/tark_cli.py" config set pat "$PAT"  >/dev/null 2>&1
    ok "saved to ~/.config/tark/config.json (chmod 600)"
    say "Verifying connection"
    python3 "$DEST/tark_cli.py" tasks >/dev/null 2>&1 \
      && ok "Connected — tark_cli can reach your deployment." \
      || warn "Could not reach the API. Check the URL/PAT and re-run: tark_cli config"
  else
    warn "Skipped (blank input). Configure later with:  tark_cli config set url <URL> ; tark_cli config set pat <PAT>"
  fi
else
  warn "Non-interactive run — configure later with:"
  echo "    tark_cli config set url https://YOUR-DEPLOYMENT.tarktoostus.ee"
  echo "    tark_cli config set pat tark_pat_xxxxxxxxxxxxx"
fi

# ---------------------------------------------------------------------------
say "Done"
cat <<EOF

Next:
  1. cd $DEST
  2. claude                       # open Claude Code here
  3. In Claude, try:
        /research-customer https://www.hekotek.com
        Create a Tark lead for that company.

Full guide: $DEST/ONBOARDING.md
EOF
