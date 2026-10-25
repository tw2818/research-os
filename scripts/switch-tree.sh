#!/usr/bin/env bash
# Point a Hermes profile at a Research OS state tree.
#
# The pointer file belongs to the installation, and it is what every
# part of Research OS reads first: the plugin's hooks, the tool, the
# CLI and the renderer. So changing which tree a profile works on is
# one line in one file, and this script is that line done safely.
#
#   ./switch-tree.sh                 # show where each profile points
#   ./switch-tree.sh production      # default profile -> production tree
#   ./switch-tree.sh test            # default profile -> test tree
#
# The test profile always points at the test tree; it exists for that
# one purpose.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROD_TREE="$HERMES_HOME/research-os"
TEST_TREE="$HOME/research-test/state"

show() {
    for prof in "$HERMES_HOME" "$HERMES_HOME/profiles/research-test"; do
        [ -d "$prof" ] || continue
        name="$(basename "$prof")"
        [ "$prof" = "$HERMES_HOME" ] && name="default"
        ptr="$prof/research-os-home"
        if [ -f "$ptr" ]; then
            echo "  $name -> $(cat "$ptr")"
        else
            echo "  $name -> (no pointer; the default tree)"
        fi
    done
}

case "${1:-show}" in
    show)
        echo "pointers:"
        show
        ;;
    production)
        echo "$PROD_TREE" > "$HERMES_HOME/research-os-home"
        echo "default profile -> $PROD_TREE"
        show
        ;;
    test)
        echo "$TEST_TREE" > "$HERMES_HOME/research-os-home"
        echo "default profile -> $TEST_TREE"
        show
        ;;
    *)
        echo "usage: $0 [show|production|test]" >&2
        exit 2
        ;;
esac
