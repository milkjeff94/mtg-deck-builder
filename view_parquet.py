#!/usr/bin/env python3
"""
view_parquet.py — quick inspector for draft_out/ and draft_suggestions_out/ Parquet logs.

Examples:
  # Basic summary of watcher outputs
  python view_parquet.py

  # Look at suggester outputs instead of watcher
  python view_parquet.py --suggest

  # Show Pack 1 Pick 1 (sorted by WR desc)
  python view_parquet.py --p1p1

  # Show a specific pack/pick (e.g., Pack 2 Pick 3)
  python view_parquet.py --pack 2 --pick 3

  # Show last 10 picks
  python view_parquet.py --last-picks 10

  # Filter by a particular draft id (copy from draft_watch.log)
  python view_parquet.py --draft-id <uuid>

  # Export CSVs next to the Parquet files
  python view_parquet.py --export-csv

  # Point at a different output directory
  python view_parquet.py --dir ./some/other/out
"""

import argparse
from pathlib import Path
from typing import Optional
import pandas as pd
import time
import os
import traceback


def _read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    # Try pyarrow engine first (usually fastest), fallback if necessary
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception:
        return pd.read_parquet(path)


def _load_tables(base_dir: Path):
    picks = _read_parquet(base_dir / "draft_pick.parquet") if (base_dir / "draft_pick.parquet").exists() else pd.DataFrame()
    packs = _read_parquet(base_dir / "draft_pack_card.parquet") if (base_dir / "draft_pack_card.parquet").exists() else pd.DataFrame()
    sess  = _read_parquet(base_dir / "draft_session.parquet") if (base_dir / "draft_session.parquet").exists() else pd.DataFrame()
    return sess, picks, packs


def _print_df(df: pd.DataFrame, cols: Optional[list] = None, title: Optional[str] = None, max_rows: int = 30):
    if title:
        print(f"\n=== {title} ===")
    if df.empty:
        print("(no rows)")
        return
    if cols:
        for c in cols:
            if c not in df.columns:
                raise SystemExit(f"Requested column '{c}' not found. Available: {list(df.columns)}")
        df = df[cols]
    with pd.option_context('display.max_rows', max_rows, 'display.max_columns', 200, 'display.width', 200):
        print(df)


def main():
    ap = argparse.ArgumentParser(description="Inspect MTGA draft parquet outputs")
    ap.add_argument("--suggest", action="store_true", help="Use draft_suggestions_out instead of draft_out")
    ap.add_argument("--dir", default=None, help="Explicit output directory (overrides --suggest)")
    ap.add_argument("--draft-id", dest="draft_id", default=None, help="Filter rows to a specific draft_id")
    ap.add_argument("--p1p1", action="store_true", help="Show Pack 1 Pick 1 offers sorted by WR")
    ap.add_argument("--pack", type=int, default=None, help="Show offers for this pack number (requires --pick)")
    ap.add_argument("--pick", type=int, default=None, help="Show offers for this pick number (requires --pack)")
    ap.add_argument("--last-picks", type=int, default=None, help="Show the last N picks")
    ap.add_argument("--export-csv", action="store_true", help="Write CSVs next to parquet files")
    ap.add_argument("--watch", action="store_true", help="Continuously refresh the view (reads parquet/csv every interval)")
    ap.add_argument("--interval", type=float, default=2.0, help="Seconds between refreshes in --watch mode")
    ap.add_argument("--no-clear", action="store_true", help="Do not clear the screen between refreshes (useful for debugging)")
    args = ap.parse_args()

    base_dir = None
    if args.dir:
        base_dir = Path(args.dir)
    else:
        base_dir = Path("./draft_suggestions_out" if args.suggest else "./draft_out")
    if not base_dir.exists():
        raise SystemExit(f"Output directory not found: {base_dir}")

    def render_once():
        if not args.no_clear:
            os.system('clear' if os.name != 'nt' else 'cls')
        try:
            sess, picks, packs = _load_tables(base_dir)
        except Exception as e:
            print(f"Using base_dir: {base_dir}")
            print("(no parquet readable yet) — if this is your first run, generate data via replay or live draft.")
            print(f"Error: {e}")
            if args.watch:
                return
            else:
                raise
        print(f"Using base_dir: {base_dir}")
        if not sess.empty:
            _print_df(sess.tail(5), title="Recent Draft Sessions (tail)")
        else:
            print("(no draft_session.parquet)")

        # Diagnostic: if all main tables are empty, show a hint if no parquet files exist
        if sess.empty and True:
            # Also check other tables to decide if we should show a hint
            picks_exists = (base_dir / "draft_pick.parquet").exists()
            packs_exists = (base_dir / "draft_pack_card.parquet").exists()
            if not picks_exists and not packs_exists:
                print("Hint: no parquet files yet. Try:\n  ./runlog.sh --replay --tail 20000 --input \"$MTGA_PLAYER_LOG\"\n  python3 view_parquet.py --dir ./draft_out --watch --no-clear")

        if args.export_csv:
            for name, df in [("draft_session", sess), ("draft_pick", picks), ("draft_pack_card", packs)]:
                if not df.empty:
                    out_csv = base_dir / f"{name}.csv"
                    df.to_csv(out_csv, index=False)
                    print(f"wrote {out_csv}")

        def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty:
                return df
            if args.draft_id and "draft_id" in df.columns:
                df = df[df["draft_id"] == args.draft_id]
            return df

        packs_f = _apply_filters(packs)
        picks_f = _apply_filters(picks)

        if args.p1p1:
            if packs_f.empty:
                print("(no pack data)")
            else:
                q = packs_f.query("pack_number == 1 and pick_number == 1") if set(["pack_number","pick_number"]).issubset(packs_f.columns) else packs_f
                if "offered_card_gihwr" in q.columns:
                    q = q.sort_values("offered_card_gihwr", ascending=False)
                _print_df(q, cols=[c for c in ["card_name","offered_card_gihwr","is_picked","pack_number","pick_number"] if c in q.columns], title="Available P1P1 (best→worst)")

        if args.pack is not None and args.pick is not None:
            if packs_f.empty:
                print("(no pack data)")
            else:
                q = packs_f.query("pack_number == @args.pack and pick_number == @args.pick") if set(["pack_number","pick_number"]).issubset(packs_f.columns) else packs_f
                if "offered_card_gihwr" in q.columns:
                    q = q.sort_values("offered_card_gihwr", ascending=False)
                _print_df(q, cols=[c for c in ["card_name","offered_card_gihwr","is_picked"] if c in q.columns], title=f"Available P{args.pack}P{args.pick} (best→worst)")

        if args.last_picks:
            if picks_f.empty:
                print("(no pick data)")
            else:
                _print_df(picks_f.tail(args.last_picks), cols=[c for c in ["pack_number","pick_number","picked_card","picked_card_gihwr","ts"] if c in picks_f.columns], title=f"Last {args.last_picks} picks")

        if not any([args.p1p1, args.pack is not None, args.last_picks]):
            if not picks_f.empty:
                _print_df(picks_f.tail(10), cols=[c for c in ["pack_number","pick_number","picked_card","picked_card_gihwr","ts"] if c in picks_f.columns], title="Picked (recent)")
            if not packs_f.empty:
                _print_df(packs_f.tail(10), cols=[c for c in ["pack_number","pick_number","card_name","offered_card_gihwr","is_picked"] if c in packs_f.columns], title="Available cards (recent)")

    if args.watch:
        try:
            while True:
                try:
                    render_once()
                except Exception as ex:
                    print("\n[viewer] error while rendering:")
                    traceback.print_exc()
                    # keep watching; transient file write races are common
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return
    else:
        render_once()