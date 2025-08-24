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


def _resolve_base_dir() -> Path:
    """Resolve the base directory for data based on request args or recency.

    Supports query parameter `dir`:
      - `sug`, `suggest`, `suggestions` -> draft_suggestions_out
      - `wat`, `watch`, `out`, `draft`  -> draft_out
    Falls back to the most recently updated directory.
    """
    try:
        req_dir = request.args.get("dir") if request else None
    except RuntimeError:
        req_dir = None

    if req_dir:
        key = req_dir.strip().lower()
        if key.startswith("sug"):
            return DIR_SUGG
        if key.startswith("wat") or key.startswith("out") or key.startswith("dra"):
            return DIR_WATCH
    return _latest_base_dir()


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


def _read_snapshot(base: Path, filename: str) -> pd.DataFrame:
    """Read a single CSV snapshot (e.g. latest_offers.csv)."""
    path = base / filename
    if path.exists():
        try:
            return pd.read_csv(path)
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
    base = _resolve_base_dir()
    return render_template("index.html", base_dir=str(base))

@app.route("/api/offers")
def api_offers():
    base = _resolve_base_dir()
    # Prefer scored snapshot, then lightweight offers snapshot
    scored = _read_snapshot(base, "latest_offers_scored.csv")
    snap = _read_snapshot(base, "latest_offers.csv") if scored.empty else scored
    rows = snap.to_dict(orient="records") if not snap.empty else []
    return jsonify({"base_dir": str(base), "rows": rows})

@app.route("/api/picks")
def api_picks():
    base = _resolve_base_dir()
    snap = _read_snapshot(base, "recent_picks.csv")
    rows = snap.to_dict(orient="records") if not snap.empty else []
    return jsonify({"base_dir": str(base), "rows": rows})


@app.route("/api/suggestion")
def api_suggestion():
    base = _resolve_base_dir()
    # Prefer machine-readable suggestion
    js = base / "latest_suggestion.json"
    if js.exists():
        try:
            import json
            with js.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data["base_dir"] = str(base)
            return jsonify(data)
        except Exception:
            pass
    # Fallback: parse last suggestion line from log
    log_path = base / "draft_watch.log"
    if log_path.exists():
        try:
            import re
            rx = re.compile(r"suggested P(\d+)P(\d+): (.+?) \(GIHWR=([0-9\.]+|None), score=([0-9\.]+)\)")
            last = None
            with log_path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = rx.search(line)
                    if m:
                        last = m
            if last:
                return jsonify({
                    "base_dir": str(base),
                    "pack_number": int(last.group(1)),
                    "pick_number": int(last.group(2)),
                    "suggested_card": last.group(3),
                    "gihwr": None if last.group(4) == "None" else float(last.group(4)),
                    "score": float(last.group(5)),
                })
        except Exception:
            pass
    return jsonify({"base_dir": str(base), "error": "no suggestion available"}), 404


@app.route("/api/consistency")
def api_consistency():
    """Verify suggestion/offers/picks alignment.

    - latest_suggestion.json.suggested_card matches card with is_picked=1 in latest_offers_scored.csv
    - If present, recent_picks.csv last picked_card matches the same picked card
    """
    base = _resolve_base_dir()

    result = {
        "base_dir": str(base),
        "ok": False,
        "checks": {},
    }

    # Load suggestion (JSON)
    sugg_card = None
    js = base / "latest_suggestion.json"
    if js.exists():
        try:
            import json
            with js.open("r", encoding="utf-8") as f:
                data = json.load(f)
            sugg_card = data.get("suggested_card") or data.get("card")
        except Exception:
            pass

    # Load offers (CSV)
    offers = _read_snapshot(base, "latest_offers_scored.csv")
    picked_card = None
    if not offers.empty and "is_picked" in offers.columns and "card_name" in offers.columns:
        try:
            # Normalize is_picked to ints (0/1) when possible
            isp = offers["is_picked"]
            try:
                isp = isp.astype(int)
            except Exception:
                isp = isp.astype(str)
            candidates = offers[isp == 1]
            if candidates.empty:
                # No explicit picked rows; leave picked_card=None
                pass
            else:
                # If multiple, prefer highest score then GIHWR
                if "score" in candidates.columns:
                    candidates = candidates.sort_values("score", ascending=False)
                elif "offered_card_gihwr" in candidates.columns:
                    candidates = candidates.sort_values("offered_card_gihwr", ascending=False)
                row = candidates.iloc[0]
                picked_card = str(row.get("card_name")) if row is not None else None
        except Exception:
            pass

    # Load recent picks (CSV)
    picks = _read_snapshot(base, "recent_picks.csv")
    recent_pick = None
    if not picks.empty and "picked_card" in picks.columns:
        try:
            if "ts" in picks.columns:
                # ISO timestamps sort correctly as strings
                picks_sorted = picks.sort_values("ts")
            else:
                picks_sorted = picks
            recent_pick = str(picks_sorted.iloc[-1]["picked_card"]) if len(picks_sorted) else None
        except Exception:
            pass

    def norm(x):
        return x.strip().lower() if isinstance(x, str) else x

    # Check 1: suggestion vs offers picked
    check1 = {"ok": None, "expected": picked_card, "actual": sugg_card}
    if sugg_card is None or picked_card is None:
        reason = []
        if sugg_card is None:
            reason.append("missing suggestion")
        if picked_card is None:
            reason.append("no picked row in offers")
        check1["reason"] = ", ".join(reason) if reason else "insufficient data"
    else:
        check1["ok"] = norm(sugg_card) == norm(picked_card)
    result["checks"]["suggestion_vs_offers"] = check1

    # Check 2: recent picks vs offers picked
    check2 = {"ok": None, "expected": picked_card, "actual": recent_pick}
    if recent_pick is None or picked_card is None:
        reason = []
        if recent_pick is None:
            reason.append("missing recent picks")
        if picked_card is None:
            reason.append("no picked row in offers")
        check2["reason"] = ", ".join(reason) if reason else "insufficient data"
    else:
        check2["ok"] = norm(recent_pick) == norm(picked_card)
    result["checks"]["recent_picks_vs_offers"] = check2

    # Overall OK logic: if any check ran (ok is True/False), require all True.
    oks = [c["ok"] for c in result["checks"].values() if isinstance(c.get("ok"), bool)]
    if oks:
        result["ok"] = all(oks)
    else:
        result["ok"] = False
        result["reason"] = "insufficient_data"

    return jsonify(result)

if __name__ == "__main__":
    app.run(port=5000, debug=True, threaded=True)
