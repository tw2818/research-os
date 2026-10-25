#!/usr/bin/env bash
# One-shot installer for the Research OS Hermes plugin.
#
#   curl -fsSL https://raw.githubusercontent.com/tw2818/research-os/main/install.sh | bash
#   bash install.sh --vault /path/to/obsidian/vault
#
# Safe to re-run: it copies files (never appends blindly), initialises the
# state tree, and leaves the plugin disabled until you enable it.
set -euo pipefail

REPO="${RESEARCH_OS_REPO:-https://github.com/tw2818/research-os}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
VAULT=""
ENABLE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --vault)  VAULT="$2"; shift 2 ;;
    --repo)   REPO="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    --home)   HERMES_HOME="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

PLUGIN_DIR="$HERMES_HOME/plugins/research-os"
SRC=""

echo "==> Hermes home: $HERMES_HOME"

# ---------------------------------------------------------------- get source
if [ -d "$(dirname "${BASH_SOURCE[0]}")/research_os" ]; then
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  echo "==> Using local source: $SRC"
else
  TMP="$(mktemp -d)"
  echo "==> Cloning $REPO"
  git clone --depth 1 "$REPO" "$TMP/research-os" >/dev/null 2>&1 || {
    echo "!! clone failed. Clone manually and re-run with --home <hermes_home>." >&2
    exit 1
  }
  SRC="$TMP/research-os"
fi

# ---------------------------------------------------------------- copy files
echo "==> Installing plugin into $PLUGIN_DIR"
mkdir -p "$PLUGIN_DIR"
cp "$SRC/plugin.yaml" "$PLUGIN_DIR/"
# Every module in the package, so adding one never needs this script edited.
cp "$SRC"/research_os/*.py "$PLUGIN_DIR/"
find "$PLUGIN_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- state tree
STATE_HOME="$HERMES_HOME/research-os"
export RESEARCH_OS_HOME="$STATE_HOME"
echo "==> Initialising state at $STATE_HOME"
python3 "$SRC/scripts/osctl.py" init ${VAULT:+--vault "$VAULT"} >/dev/null

# Keep the CLI and the renderer next to the state, so they stay available
# after the temporary clone is gone. The modules go with them: the CLI has to
# work from this directory, not from the plugin directory it was written in.
# `__init__.py` is left out — it would turn the directory into a package and
# break the flat imports the CLI falls back to.
mkdir -p "$STATE_HOME/bin"
cp "$SRC/scripts/osctl.py" "$STATE_HOME/bin/osctl.py"
cp "$SRC/scripts/render_obsidian.py" "$STATE_HOME/bin/render_obsidian.py"
# Every module in the package, not a hand-written list. A module that
# the CLI imports but the installer did not copy is an import error
# that only appears on someone else's machine.
#
# `__init__.py` is left out - it would turn the directory into a package
# and break the flat imports the CLI falls back to.
for _mod in "$SRC"/research_os/*.py; do
  [ "$(basename "$_mod")" = "__init__.py" ] && continue
  cp "$_mod" "$STATE_HOME/bin/"
done
find "$STATE_HOME/bin" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------- enable
if command -v hermes >/dev/null 2>&1; then
  if [ "$ENABLE" = "1" ]; then
    echo "==> Enabling plugin"
    HERMES_HOME="$HERMES_HOME" hermes plugins enable research-os || true
  else
    echo "==> Plugin copied but not enabled. Run:"
    echo "    HERMES_HOME=$HERMES_HOME hermes plugins enable research-os"
  fi
  echo "==> Restart the gateway to load the tool:  hermes gateway restart"
else
  echo "!! 'hermes' not on PATH — plugin copied but not enabled."
fi

echo
echo "Done. Next:"
echo "  1. restart the gateway:   hermes gateway restart"
echo "  2. ask a research question — the agent opens research mode itself"
echo "  3. list topics:           RESEARCH_OS_HOME='$STATE_HOME' python3 '$STATE_HOME/bin/osctl.py' topics"
echo "  4. render the Obsidian notes:  RESEARCH_OS_HOME='$STATE_HOME' python3 '$STATE_HOME/bin/render_obsidian.py' --all"
