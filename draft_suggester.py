#!/usr/bin/env python3
"""
Draft Suggester
- Runs similar to draft_watch, but in addition to logging offers/picks,
  generates a suggested pick.
- Factors considered:
  * GIHWR (Game in Hand Win Rate) from ratings CSV
  * Color openness (preference for colors already seen/picked)
  * Mana curve (prefer smoother CMC distribution)
  * Creature vs non-creature balance
"""

import os
import sys
import time
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter, defaultdict

import pandas as pd

# Reuse helpers from draft_watch
from draft_watch import (
    DraftWatcher, GihwrIndex, GrpIdMap, Storage, LOG_PATH,
    GIHWR_INDEX_PATH, CARD_MAP_PATH, OFFER_WR_PATH, _log, norm_card, normalize_header,
    RE_PICK_NAME_KV,
)
def run_replay(path: Path, watcher: "SuggestingDraftWatcher", tail_lines: int | None = None):
    _log(f"replay: reading {path}")
    if not path.exists():
        print(f"Replay file not found: {path}", file=sys.stderr)
        sys.exit(2)
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        if tail_lines is None:
            for line in f:
                watcher._handle_line(line.rstrip("\n"))
        else:
            # Read only the last N lines efficiently
            from collections import deque
            for line in deque(f, maxlen=tail_lines):
                watcher._handle_line(line.rstrip("\n"))
    if hasattr(watcher.storage, "flush_parquet"):
        watcher.storage.flush_parquet()
    if hasattr(watcher.storage, "flush_csv"):
        watcher.storage.flush_csv()
    if hasattr(watcher.storage, "flush_snapshots"):
        watcher.storage.flush_snapshots(watcher.state)
        _log("replay: done; parquet flushed")
def load_card_meta(ratings_path: str, info_path: str | None = None) -> pd.DataFrame:
    """Return a DataFrame with columns: card_name, colors, cmc, types, card_name_norm.
    Tries `info_path` first (if present), else falls back to `ratings_path`.
    """
    def _read_csv(p: str) -> pd.DataFrame:
        try:
            df = pd.read_csv(p, sep=None, engine="python", dtype=str, encoding="utf-8")
        except Exception:
            df = pd.read_csv(p, dtype=str, encoding_errors="ignore")
        df.columns = [normalize_header(c) for c in df.columns]
        return df
    base_df = None
    if info_path and Path(info_path).exists():
        base_df = _read_csv(info_path)
    else:
        base_df = _read_csv(ratings_path)
    # Identify columns
    name_col = next((c for c in ["card_name","name","card","title"] if c in base_df.columns), None)
    color_col = next((c for c in ["colors","color","colour","color_identity","colour_identity"] if c in base_df.columns), None)
    cmc_col = next((c for c in ["cmc","mana_value","mv","mana_cost_value","converted_mana_cost"] if c in base_df.columns), None)
    type_col = next((c for c in ["types","type","type_line","card_type"] if c in base_df.columns), None)
    if not name_col:
        raise ValueError("Card metadata is missing a name column")
    out = pd.DataFrame()
    out["card_name"] = base_df[name_col].astype(str).fillna("")
    out["colors"] = base_df[color_col].astype(str) if color_col else ""
    if cmc_col:
        # coerce to numeric when possible
        out["cmc"] = pd.to_numeric(base_df[cmc_col], errors="coerce")
    else:
        out["cmc"] = None
    out["types"] = base_df[type_col].astype(str) if type_col else ""
    out["card_name_norm"] = out["card_name"].map(lambda x: norm_card(x))
    return out


class DeckEvaluator:
    def __init__(self):
        self.picked_cards = []
        self.color_counts = Counter()
        self.cmc_counts = Counter()
        self.creature_count = 0
        self.noncreature_count = 0
        # Track color openness based on cards PASSED/SEEN in previous offers
        self.seen_color_counts = defaultdict(float)  # allow fractional weights per color
        self.seen_color_tokens_total = 0.0  # use float for fractional token weights
    def add_offer(self, card_names: list[str], meta_df: pd.DataFrame, pack_number: int = 1):
        """Update openness stats from a newly seen pack offer.
        We count color letters across all cards offered to approximate table openness,
        with Pack 1 weighted more heavily than Pack 2 or 3.
        """
        if not card_names:
            return
        # Weight signals differently by pack number (Pack1 strongest, Pack3 weakest)
        weight = 1.0
        if pack_number == 2:
            weight = 0.5
        elif pack_number == 3:
            weight = 0.25

        for name in card_names:
            norm = norm_card(name)
            row = meta_df.loc[meta_df["card_name_norm"] == norm]
            if row.empty:
                continue
            colors = str(row.iloc[0].get("colors", ""))
            for c in colors:
                if c.strip():
                    self.seen_color_counts[c] += weight
                    self.seen_color_tokens_total += weight

    def add_pick(self, card_name: str, meta_df: pd.DataFrame):
        """Update deck stats from a new pick using normalized metadata."""
        self.picked_cards.append(card_name)
        norm = norm_card(card_name)
        row = meta_df.loc[meta_df["card_name_norm"] == norm]
        if not row.empty:
            colors = row.iloc[0].get("colors", "")
            cmc = row.iloc[0].get("cmc", None)
            types = row.iloc[0].get("types", "")

            for c in str(colors):
                self.color_counts[c] += 1
            if cmc is not None and cmc != "":
                try:
                    self.cmc_counts[int(float(cmc))] += 1
                except Exception:
                    pass
            if "Creature" in str(types):
                self.creature_count += 1
            else:
                self.noncreature_count += 1

    def score_card(self, card_name: str, gihwr: float, meta_df: pd.DataFrame) -> float:
        """Produce a heuristic score for a card offer using normalized metadata."""
        score = gihwr or 0.5  # default baseline
        norm = norm_card(card_name)
        row = meta_df.loc[meta_df["card_name_norm"] == norm]
        if row.empty:
            return score

        colors = str(row.iloc[0].get("colors", ""))
        cmc = row.iloc[0].get("cmc", None)
        types = str(row.iloc[0].get("types", ""))

        # Color openness from PASSED cards: prefer colors that have appeared more in packs we've seen
        # Normalize by total color tokens observed to keep the bonus bounded.
        if self.seen_color_tokens_total > 0:
            for c in colors:
                share = self.seen_color_counts.get(c, 0) / float(self.seen_color_tokens_total)
                # Up to ~+0.20 per color if that color dominates passed cards
                score += 0.20 * share

        # Mana curve smoothing: prefer cmc values we are light on
        if cmc is not None and cmc != "":
            try:
                cmc_int = int(float(cmc))
                if self.cmc_counts[cmc_int] < 2:  # underrepresented curve slot
                    score += 0.1
            except Exception:
                pass

        # Creature/non-creature balance
        if "Creature" in types and self.creature_count < self.noncreature_count:
            score += 0.1
        elif "Creature" not in types and self.noncreature_count < self.creature_count:
            score += 0.05

        return score


class SuggestingDraftWatcher(DraftWatcher):
    def __init__(self, *args, meta_df: pd.DataFrame, **kwargs):
        super().__init__(*args, **kwargs)
        self.deck_eval = DeckEvaluator()
        self.meta_df = meta_df

    def _handle_line(self, line: str):
        prev_event = self.last_event
        super()._handle_line(line)

        # After a NEW offer, update openness stats from the pack and then suggest a pick
        if self.last_event == "offer" and prev_event != "offer":
            if self.state.offered_cards:
                self.deck_eval.add_offer(self.state.offered_cards, self.meta_df, pack_number=self.state.pack_number)
            self._suggest_pick()

        # After a pick, update deck evaluator
        if self.last_event == "pick" and self.state.offered_cards == []:
            if self.storage._pick_rows:
                last_pick = self.storage._pick_rows[-1]
                self.deck_eval.add_pick(last_pick["picked_card"], self.meta_df)

    def _suggest_pick(self):
        if not self.state.offered_cards:
            return
        suggestions = []
        for card in self.state.offered_cards:
            wr = self.gihwr.get(card)
            sc = self.deck_eval.score_card(card, wr or 0.5, self.meta_df)
            suggestions.append((card, wr, sc))
        suggestions.sort(key=lambda x: x[2], reverse=True)
        best = suggestions[0]
        _log(f"suggested P{self.state.pack_number}P{self.state.pick_number}: "
             f"{best[0]} (GIHWR={best[1]}, score={best[2]:.3f})")


def main():
    parser = argparse.ArgumentParser(description="MTGA Draft Suggester")
    parser.add_argument("--replay", action="store_true", help="Process the input log once and exit")
    parser.add_argument("--input", dest="input_path", default=None, help="Path to Player.log (overrides MTGA_PLAYER_LOG)")
    parser.add_argument("--tail", dest="tail_lines", type=int, default=None, help="When replaying, only process the last N lines")
    args = parser.parse_args()

    log_path = Path(args.input_path).expanduser() if args.input_path else LOG_PATH

    if not GIHWR_INDEX_PATH:
        print("Set GIHWR_INDEX_PATH to ratings CSV", file=sys.stderr)
        sys.exit(2)
    if not log_path.exists():
        print(f"Player.log not found at {log_path}", file=sys.stderr)
        sys.exit(2)

    _log("Draft Suggester starting...")

    # Load ratings
    gihwr = GihwrIndex()
    gihwr.load_csv(GIHWR_INDEX_PATH)

    # Load card map
    idmap = None
    if CARD_MAP_PATH and Path(CARD_MAP_PATH).exists():
        idmap = GrpIdMap()
        idmap.load_csv(CARD_MAP_PATH)

    # Load normalized card metadata (colors, cmc, types)
    try:
        meta_df = load_card_meta(GIHWR_INDEX_PATH, info_path=CARD_MAP_PATH)
    except Exception as e:
        _log(f"card-meta load failed; suggestions will use WR only: {e}")
        meta_df = pd.DataFrame({
            "card_name": [], "colors": [], "cmc": [], "types": [], "card_name_norm": []
        })

    out_dir = Path(os.environ.get("DRAFT_OUT_DIR", "./draft_suggestions_out"))
    storage = Storage(out_dir)
    watcher = SuggestingDraftWatcher(log_path, gihwr, storage, idmap=idmap, meta_df=meta_df)

    if args.replay:
        run_replay(log_path, watcher, tail_lines=args.tail_lines)
        return

    watcher.start()


if __name__ == "__main__":
    main()