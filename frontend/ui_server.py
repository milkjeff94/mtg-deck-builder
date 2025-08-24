#!/usr/bin/env python3
import os, time
from pathlib import Path
from flask import Flask, jsonify, render_template, request
import pandas as pd

app = Flask(__name__, template_folder="templates", static_folder="static")
REPO_ROOT = Path(__file__).resolve().parents[1]  # repo root (mtg-deck-builder)
DIR_WATCH = REPO_ROOT / "draft_out"
DIR_SUGG  = REPO_ROOT / "draft_suggestions_out"

# Optional environment override for data directory
UI_DATA_DIR = os.environ.get("UI_DATA_DIR")

# Sanity-check: print where we’re reading from
print("[ui] ui_server starting…", flush=True)
print(f"[ui] __file__={__file__}", flush=True)
print(f"[ui] REPO_ROOT={REPO_ROOT}", flush=True)
print(f"[ui] DIR_WATCH={DIR_WATCH}", flush=True)
print(f"[ui] DIR_SUGG={DIR_SUGG}", flush=True)
if UI_DATA_DIR:
    print(f"[ui] UI_DATA_DIR override={UI_DATA_DIR}", flush=True)


def _dir_mtime(p: Path) -> float:
    try:
        ts = 0.0
        for ext in ("*.csv", "*.parquet"):
            for f in p.glob(ext):
                try:
                    ts = max(ts, f.stat().st_mtime)
                except Exception:
                    pass
        return ts
    except Exception:
        return 0.0

def _latest_base_dir() -> Path:
    # 1) Explicit override via env
    if UI_DATA_DIR:
        p = Path(UI_DATA_DIR).expanduser().resolve()
        return p

    # 2) Pick the most recently updated directory by any table file
    candidates = []
    for p in [DIR_SUGG, DIR_WATCH]:
        if p.exists():
            mtime = _dir_mtime(p)
            # Prefer newest non-empty directory (has at least one table file)
            nonempty = any(p.glob("*.csv")) or any(p.glob("*.parquet"))
            candidates.append((mtime, nonempty, p))
    if not candidates:
        return DIR_SUGG

    # Prefer the dir with the latest mtime; if mtimes tie or are zero, prefer a non-empty one
    candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return candidates[0][2]


# Helper: Try to read as CSV first, then as Parquet if CSV not found, fallback to empty DataFrame
def _read_table(base: Path, name: str) -> pd.DataFrame:
    csv_path = base / f"{name}.csv"
    parquet_path = base / f"{name}.parquet"
    if csv_path.exists():
        try:
            return pd.read_csv(csv_path)
        except Exception:
            pass
    if parquet_path.exists():
        try:
            return pd.read_parquet(parquet_path)
        except Exception:
            pass
    return pd.DataFrame()

def _load_tables(base=None):
    # Allow request-time override: /api/offers?dir=watch or ?dir=suggest
    try:
        req_dir = request.args.get("dir") if request else None  # request may not exist in CLI
    except RuntimeError:
        req_dir = None  # no request context
    if not base and req_dir:
        if req_dir.lower().startswith("sug"):
            base = DIR_SUGG
        elif req_dir.lower().startswith("wat") or req_dir.lower().startswith("out"):
            base = DIR_WATCH
    base = base or _latest_base_dir()
    picks = _read_table(base, "draft_pick")
    packs = _read_table(base, "draft_pack_card")
    sess  = _read_table(base, "draft_session")
    return sess, picks, packs

@app.route("/")
def index():
    return render_template("index.html", base_dir=str(_latest_base_dir()))

@app.route("/api/offers")
def api_offers():
    base = _latest_base_dir()
    _, _, packs = _load_tables(base)
    return jsonify({
        "base_dir": str(base),
        "rows": packs.tail(30).to_dict(orient="records")
    })

@app.route("/api/picks")
def api_picks():
    base = _latest_base_dir()
    _, picks, _ = _load_tables(base)
    return jsonify({
        "base_dir": str(base),
        "rows": picks.tail(30).to_dict(orient="records")
    })

if __name__ == "__main__":
    app.run(port=5050, debug=True, threaded=True)
