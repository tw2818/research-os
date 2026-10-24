#!/usr/bin/env bash
# Deploy the Research OS plugin into a Hermes plugin directory.
#
#   ./scripts/deploy.sh [hermes_home]     # default: ~/.hermes
#
# The plugin is loaded by Hermes as a top-level module (plugin.yaml + __init__.py
# side by side), so the package modules are copied flat. A `research_os/`
# package copy is kept for the repo's own test suite.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${1:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/research-os"

echo "repo:    $REPO"
echo "target:  $PLUGIN_DIR"

mkdir -p "$PLUGIN_DIR"
cp "$REPO/plugin.yaml" "$PLUGIN_DIR/"
cp "$REPO/research_os/__init__.py" "$PLUGIN_DIR/"
for f in state classify protocol validate; do
  cp "$REPO/research_os/$f.py" "$PLUGIN_DIR/"
done
find "$PLUGIN_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "installed:"
ls -1 "$PLUGIN_DIR"

echo
echo "next: hermes plugins enable research-os   (then restart the gateway)"
