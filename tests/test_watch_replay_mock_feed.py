import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


@pytest.mark.integration
def test_watch_replay_with_mock_feed(tmp_path: Path):
    """Generate a short mock draft log, replay it through the watcher, verify outputs.

    This uses tools/mock_draft_feed.py to write a small Player.log and then runs
    draft_watch.py in --replay mode to process past events. It does not require
    a live tail, so it is deterministic and quick.
    """

    repo = Path(__file__).resolve().parents[1]
    log_path = tmp_path / "Player.log"
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Create a tiny log with a few picks quickly
    feed = [sys.executable, str(repo / "tools" / "mock_draft_feed.py"), "--out", str(log_path), "--interval", "0.2", "--picks", "4"]
    subprocess.run(feed, check=True)

    # 2) Run watcher in replay mode over the generated log
    env = os.environ.copy()
    env["MTGA_PLAYER_LOG"] = str(log_path)
    env["DRAFT_OUT_DIR"] = str(out_dir)
    env.setdefault("DRAFT_LIVE", "1")
    # Optional enrichers when present in repo
    gihwr_csv = repo / "log samples" / "eoe_card_ratings.csv"
    if gihwr_csv.exists():
        env["GIHWR_INDEX_PATH"] = str(gihwr_csv)
    card_map = repo / "log samples" / "mtga_card_id.csv"
    if card_map.exists():
        env["CARD_MAP_PATH"] = str(card_map)

    watcher = [sys.executable, str(repo / "draft_watch.py"), "--replay", "--input", str(log_path)]
    subprocess.run(watcher, env=env, check=True)

    # 3) Validate outputs created
    # Main tables
    picks_csv = out_dir / "draft_pick.csv"
    offers_csv = out_dir / "draft_pack_card.csv"
    assert picks_csv.exists(), "draft_pick.csv not written"
    assert offers_csv.exists(), "draft_pack_card.csv not written"

    df_picks = pd.read_csv(picks_csv)
    df_offers = pd.read_csv(offers_csv)
    assert len(df_picks) >= 1
    assert len(df_offers) >= 1

    # Snapshots
    latest_offers = out_dir / "latest_offers.csv"
    recent_picks = out_dir / "recent_picks.csv"
    assert latest_offers.exists(), "latest_offers.csv not written"
    assert recent_picks.exists(), "recent_picks.csv not written"

    # Basic integrity: the last picked card appears as is_picked=1 in offers
    df_recent = pd.read_csv(recent_picks)
    assert not df_recent.empty
    last_pick = str(df_recent.iloc[-1]["picked_card"]) if "picked_card" in df_recent.columns else None
    df_latest = pd.read_csv(latest_offers)
    if "card_name" in df_latest.columns and "is_picked" in df_latest.columns and last_pick:
        picked_rows = df_latest[df_latest["is_picked"] == 1]
        if not picked_rows.empty:
            assert str(picked_rows.iloc[0]["card_name"]) == last_pick


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_TAIL", "0") != "1", reason="Set RUN_TAIL=1 to run live tail integration")
def test_watch_tail_with_mock_feed(tmp_path: Path):
    """Optional live-tail integration: run watcher in tail mode while feed appends.

    This test is skipped unless RUN_TAIL=1. It uses a very short interval to
    complete quickly and terminates the watcher after the feed finishes.
    """
    repo = Path(__file__).resolve().parents[1]
    log_path = tmp_path / "Player.log"
    out_dir = tmp_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["MTGA_PLAYER_LOG"] = str(log_path)
    env["DRAFT_OUT_DIR"] = str(out_dir)
    env.setdefault("DRAFT_LIVE", "1")

    # Start watcher (tail mode)
    watcher = subprocess.Popen([sys.executable, str(repo / "draft_watch.py")], env=env)

    try:
        # Start the mock feed with a quick interval
        feed = [sys.executable, str(repo / "tools" / "mock_draft_feed.py"), "--out", str(log_path), "--interval", "0.25", "--picks", "6"]
        subprocess.run(feed, check=True)

        # Give the watcher a moment to flush
        watcher.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        # Stop watcher after feed completes
        watcher.terminate()
        watcher.wait(timeout=5)

    # Validate that outputs exist
    assert (out_dir / "draft_pick.csv").exists()
    assert (out_dir / "latest_offers.csv").exists()

