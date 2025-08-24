

#!/usr/bin/env bash
set -euo pipefail

# --- Config ---
# Path to Player.log used by watcher. Default to repo-local logs/Player.log
export MTGA_PLAYER_LOG="${MTGA_PLAYER_LOG:-$(pwd)/logs/Player.log}"
# Ensure directory exists
mkdir -p "$(dirname "$MTGA_PLAYER_LOG")"
# If running on macOS and the repo link doesn't exist yet, auto-symlink to the system Player.log
if [[ "$(uname -s)" == "Darwin" ]] && [[ ! -e "$MTGA_PLAYER_LOG" ]]; then
  MAC_LOG="$HOME/Library/Logs/Wizards Of The Coast/MTGA/Player.log"
  if [[ -e "$MAC_LOG" ]]; then
    ln -s "$MAC_LOG" "$MTGA_PLAYER_LOG" || true
  fi
fi
# Touch the path to ensure an attach target (if it's a symlink, this updates the target)
touch "$MTGA_PLAYER_LOG"

# Ratings file (Name + GIH WR). Defaults to per-set CSV in repo
export GIHWR_INDEX_PATH="${GIHWR_INDEX_PATH:-$(pwd)/log samples/eoe_card_ratings.csv}"

# Optional: GRP id → name map improves detection when logs show numeric PackCards
export CARD_MAP_PATH="${CARD_MAP_PATH:-$(pwd)/log samples/mtga_card_id.csv}"

# Where outputs will be written
export DRAFT_OUT_DIR="${DRAFT_OUT_DIR:-$(pwd)/draft_out}"
mkdir -p "$DRAFT_OUT_DIR"


 # Verbose/debug (optional)
export DRAFT_DEBUG="${DRAFT_DEBUG:-0}"

# Miscellaneous non-table logs (init/scene/etc.). Default off unless explicitly enabled.
export DRAFT_MISC="${DRAFT_MISC:-0}"

# Live CSV/parquet snapshots on/off
export DRAFT_LIVE="${DRAFT_LIVE:-1}"

# --- Mode ---
# Use: ./runlog.sh            → watcher
#      ./runlog.sh suggest    → suggester
MODE="${1:-watch}"
# If a mode arg was supplied, drop it so the rest (e.g., --replay) pass through
if [[ "$#" -gt 0 ]]; then
  shift
fi
if [[ "$MODE" == "suggest" || "${SUGGEST:-0}" == "1" ]]; then
  echo "[runner] starting draft_suggester.py"
  # Suggester outputs to its own dir
  export DRAFT_OUT_DIR="${DRAFT_OUT_DIR_SUGG:-$(pwd)/draft_suggestions_out}"
  mkdir -p "$DRAFT_OUT_DIR"
  exec python3 draft_suggester.py "$@"
else
  echo "[runner] starting draft_watch.py"
  exec python3 draft_watch.py "$@"
fi
