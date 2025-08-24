#!/usr/bin/env python3
"""
check_ui_consistency.py

Runs the same consistency checks as the UI API against a chosen data directory.

Usage examples:
  # Auto-pick most recent between draft_suggestions_out and draft_out
  python3 tools/check_ui_consistency.py

  # Force suggest/watch dirs
  python3 tools/check_ui_consistency.py --dir suggest
  python3 tools/check_ui_consistency.py --dir watch

  # Explicit base path or via env UI_DATA_DIR
  python3 tools/check_ui_consistency.py --base frontend/fixtures/demo_1
  UI_DATA_DIR=frontend/fixtures/demo_1 python3 tools/check_ui_consistency.py

Exits 0 on success, 1 on mismatch or insufficient data.
"""

import argparse
import json
import os
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
DIR_WATCH = REPO_ROOT / "draft_out"
DIR_SUGG = REPO_ROOT / "draft_suggestions_out"


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
    env = os.environ.get("UI_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    candidates = []
    for p in [DIR_SUGG, DIR_WATCH]:
        if p.exists():
            mtime = _dir_mtime(p)
            nonempty = any(p.glob("*.csv")) or any(p.glob("*.parquet"))
            candidates.append((mtime, nonempty, p))
    if not candidates:
        return DIR_SUGG
    candidates.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return candidates[0][2]


def _read_snapshot(base: Path, filename: str) -> pd.DataFrame:
    path = base / filename
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()


def _norm(x):
    return x.strip().lower() if isinstance(x, str) else x


def run_checks(base: Path):
    # Suggestion
    sugg_card = None
    js = base / "latest_suggestion.json"
    if js.exists():
        try:
            with js.open("r", encoding="utf-8") as f:
                data = json.load(f)
            sugg_card = data.get("suggested_card") or data.get("card")
        except Exception:
            pass

    # Offers (scored)
    offers = _read_snapshot(base, "latest_offers_scored.csv")
    picked_card = None
    if not offers.empty and "is_picked" in offers.columns and "card_name" in offers.columns:
        try:
            isp = offers["is_picked"]
            try:
                isp = isp.astype(int)
            except Exception:
                isp = isp.astype(str)
            candidates = offers[isp == 1]
            if not candidates.empty:
                if "score" in candidates.columns:
                    candidates = candidates.sort_values("score", ascending=False)
                elif "offered_card_gihwr" in candidates.columns:
                    candidates = candidates.sort_values("offered_card_gihwr", ascending=False)
                row = candidates.iloc[0]
                picked_card = str(row.get("card_name"))
        except Exception:
            pass

    # Recent picks
    picks = _read_snapshot(base, "recent_picks.csv")
    recent_pick = None
    if not picks.empty and "picked_card" in picks.columns:
        try:
            if "ts" in picks.columns:
                picks = picks.sort_values("ts")
            recent_pick = str(picks.iloc[-1]["picked_card"]) if len(picks) else None
        except Exception:
            pass

    # Checks
    checks = {}
    # 1) Suggestion vs offers picked
    c1 = {"ok": None, "expected": picked_card, "actual": sugg_card}
    if sugg_card is None or picked_card is None:
        reason = []
        if sugg_card is None:
            reason.append("missing suggestion")
        if picked_card is None:
            reason.append("no picked row in offers")
        c1["reason"] = ", ".join(reason) if reason else "insufficient data"
    else:
        c1["ok"] = _norm(sugg_card) == _norm(picked_card)
    checks["suggestion_vs_offers"] = c1

    # 2) Recent picks vs offers picked
    c2 = {"ok": None, "expected": picked_card, "actual": recent_pick}
    if recent_pick is None or picked_card is None:
        reason = []
        if recent_pick is None:
            reason.append("missing recent picks")
        if picked_card is None:
            reason.append("no picked row in offers")
        c2["reason"] = ", ".join(reason) if reason else "insufficient data"
    else:
        c2["ok"] = _norm(recent_pick) == _norm(picked_card)
    checks["recent_picks_vs_offers"] = c2

    oks = [c["ok"] for c in checks.values() if isinstance(c.get("ok"), bool)]
    overall_ok = all(oks) if oks else False
    return overall_ok, checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", choices=["suggest", "sug", "watch", "wat", "out", "draft"], help="Pick built-in data dir")
    ap.add_argument("--base", help="Explicit base directory path")
    args = ap.parse_args()

    base: Path
    if args.base:
        base = Path(args.base).expanduser().resolve()
    elif args.dir:
        key = args.dir.lower()
        if key.startswith("sug"):
            base = DIR_SUGG
        else:
            base = DIR_WATCH
    else:
        base = _latest_base_dir()

    ok, checks = run_checks(base)
    print(f"Base: {base}")
    for name, c in checks.items():
        status = "OK" if c.get("ok") else ("SKIPPED" if c.get("ok") is None else "MISMATCH")
        line = f"- {name}: {status}"
        if c.get("expected") is not None or c.get("actual") is not None:
            line += f" (expected='{c.get('expected')}', actual='{c.get('actual')}')"
        if c.get("reason"):
            line += f" — {c['reason']}"
        print(line)
    if ok:
        print("Overall: OK")
        raise SystemExit(0)
    else:
        print("Overall: MISMATCH or insufficient data")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

