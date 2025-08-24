# MTG Draft Dashboard

An MTG Arena draft watcher + lightweight UI to visualize the current pack, suggestions, and recent picks. It tails your Arena `Player.log`, writes tidy tables to disk, and serves a small dashboard you can run locally.

## Overview
- Watcher (`draft_watch.py`): Tails `logs/Player.log` (symlink to Arena log) and writes CSV/Parquet to `draft_out/` plus live snapshots `latest_offers.csv` and `recent_picks.csv`.
- Suggester (`draft_suggester.py`): Writes scored offers and `latest_suggestion.json` to `draft_suggestions_out/`.
- UI (`frontend/ui_server.py`): Flask app serving `/` and JSON APIs. Use `?dir=watch|suggest` to switch between `draft_out` and `draft_suggestions_out`.
- Simulator (`tools/mock_draft_feed.py`): Appends Arena-like draft lines to `logs/Player.log` every 25s by default for local testing.
- Consistency: `/api/consistency` and `tools/check_ui_consistency.py` verify suggestion/offers/picks alignment. The UI shows a small OK/Mismatch badge.

## Install
```bash
python3 -m pip install -r requirements.txt
./tools/setup_player_log_symlink.sh   # creates logs/Player.log -> ~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
```

## Quick Start (Live Watch)
```bash
# 1) Start the watcher (writes to draft_out/)
./runlog.sh

# 2) Start the UI
python3 frontend/ui_server.py

# 3) Open the dashboard
open http://localhost:5000/?dir=watch
```

## Suggester View
```bash
./runlog.sh suggest               # writes to draft_suggestions_out/
python3 frontend/ui_server.py
open http://localhost:5000/?dir=suggest
```

## Offline Demo (Fixtures)
```bash
UI_DATA_DIR=frontend/fixtures/demo_1 python3 frontend/ui_server.py
open http://localhost:5000    # shows Example Mapper highlighted
```

## Simulator Feed
```bash
# Default: 25s per pick, writes to logs/Player.log
python3 tools/mock_draft_feed.py --picks 12

# Fast test feed
python3 tools/mock_draft_feed.py --interval 0.25 --picks 6

# In parallel (any of the above):
./runlog.sh
python3 frontend/ui_server.py
open http://localhost:5000/?dir=watch
```

## Consistency Checks
```bash
# CLI (auto-selects latest dir)
python3 tools/check_ui_consistency.py

# Explicit selection
python3 tools/check_ui_consistency.py --dir suggest
python3 tools/check_ui_consistency.py --base frontend/fixtures/demo_1

# API
curl -s "http://localhost:5000/api/consistency?dir=watch" | jq
```

## UI API Endpoints
- `GET /api/offers?dir=watch|suggest` — rows from `latest_offers_scored.csv` (or `latest_offers.csv` fallback)
- `GET /api/picks?dir=…` — rows from `recent_picks.csv`
- `GET /api/suggestion?dir=…` — data from `latest_suggestion.json` (or log fallback)
- `GET /api/consistency?dir=…` — alignment checks + `ok` boolean

## Data Locations
- Watcher outputs: `draft_out/` (CSV/Parquet) + `latest_offers.csv`, `recent_picks.csv`
- Suggester outputs: `draft_suggestions_out/` — `latest_offers_scored.csv`, `latest_suggestion.json`
- UI selection: `?dir=watch|suggest` or `UI_DATA_DIR=/path/to/data`

## Environment Variables (optional)
- `MTGA_PLAYER_LOG` — defaults to repo `logs/Player.log` (symlink to mac path)
- `DRAFT_OUT_DIR`, `DRAFT_OUT_DIR_SUGG` — override output directories
- `GIHWR_INDEX_PATH` — ratings CSV (e.g., `log samples/eoe_card_ratings.csv`)
- `CARD_MAP_PATH` — GRP id → card-name map (e.g., `log samples/mtga_card_id.csv`)
- `DRAFT_LIVE=1` — flush snapshots live (default)

## Tests
```bash
# Install pytest if needed
python3 -m pip install pytest

# Replay (fast, deterministic)
pytest -q tests/test_watch_replay_mock_feed.py -k replay

# Live tail (optional)
RUN_TAIL=1 pytest -q tests/test_watch_replay_mock_feed.py -k tail

# Consistency API against fixture
pytest -q tests/test_ui_consistency.py
```

## Makefile Targets
```bash
make install        # pip install -r requirements.txt
make setup-log      # create logs/Player.log symlink to macOS Arena log
make watch          # run watcher (writes draft_out)
make suggest        # run suggester (writes draft_suggestions_out)
make ui             # run UI server
make demo           # run UI against fixtures demo_1
make feed           # run simulator (25s default, override FEED_INTERVAL/PICKS)
make check          # run CLI consistency checker
make test           # run all tests (requires pytest)
make test-replay    # replay integration test only
make test-tail      # live-tail integration (RUN_TAIL=1)
```

---

Notes
- On macOS, `./tools/setup_player_log_symlink.sh` creates a symlink so `logs/Player.log` points at `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`. This keeps the app portable without hard-coding user paths.
- You can override any default path with environment variables shown above.

