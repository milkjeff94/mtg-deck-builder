#!/usr/bin/env bash
set -euo pipefail

# Creates a symlink at repo-local logs/Player.log pointing to the macOS MTGA Player.log
# This keeps code using a relative path while reading the real live log.

TARGET_DEFAULT="$HOME/Library/Logs/Wizards Of The Coast/MTGA/Player.log"
LINK_PATH="$(cd "$(dirname "$0")"/.. && pwd)/logs/Player.log"

FORCE=0
TARGET="$TARGET_DEFAULT"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --target) TARGET="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$LINK_PATH")"

if [[ -e "$LINK_PATH" && ! -L "$LINK_PATH" ]]; then
  if [[ "$FORCE" == "1" ]]; then
    mv -f "$LINK_PATH" "$LINK_PATH.bak.$(date +%s)"
  else
    echo "Refusing to overwrite existing file: $LINK_PATH" >&2
    echo "Pass --force to back it up and create the symlink, or remove it manually." >&2
    exit 1
  fi
fi

if [[ ! -e "$TARGET" ]]; then
  echo "Target does not exist: $TARGET" >&2
  echo "Ensure MTGA has created Player.log, or specify an alternate --target" >&2
  exit 1
fi

ln -snf "$TARGET" "$LINK_PATH"
echo "Created symlink: $LINK_PATH -> $TARGET"

