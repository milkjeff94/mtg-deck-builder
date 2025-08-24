#!/usr/bin/env python3
"""
mock_draft_feed.py
Appends Arena-like draft lines to a mock Player.log so you can test the watcher,
suggester, and UI without running MTGA. It emits an "offer" followed by a "pick"
every --interval seconds, advancing pack/pick counters.

Usage (3 terminals recommended):
  # Terminal A: start the mock feed (writes to mock Player.log)
  export MTGA_PLAYER_LOG="$(pwd)/mock/Player.log"
  python3 tools/mock_draft_feed.py --out "$MTGA_PLAYER_LOG" --interval 30

  # Terminal B: run the watcher or suggester pointed at the mock log
  ./runlog.sh suggest   # or: ./runlog.sh

  # Terminal C: run the UI
  python3 frontend/ui_server.py

Fast mode:
  python3 tools/mock_draft_feed.py --out "$MTGA_PLAYER_LOG" --interval 5 --picks 12
"""

import argparse, random, time, json
from datetime import datetime, timezone
from pathlib import Path

# A tiny pool of card names (roughly like ratings CSV names).
# It's fine if GIHWR lookup misses — the UI still shows rows.
CARD_POOL = [
  "GENE POLLINATOR",
  "DRIX FATEMAKER",
  "SELFCRAFT MECHAN",
  "ZEALOUS DISPLAY",
  "SEWER CROCODILE",
  "PORT PRIMER",
  "STEADFAST PALADIN",
  "MOON-BEAM ADEPT",
  "VOLTAIC SURGE",
  "SHORE STALKER",
  "RAVENOUS SQUIRREL",
  "DARING DEMOLITION",
]

def ts():
  return datetime.now(timezone.utc).isoformat()

def append_line(fp: Path, line: str):
  with fp.open("a", encoding="utf-8") as f:
    f.write(f"[{ts()}] {line}\n")

def make_offer(pack_no: int, pick_no: int):
  # Build a Draft.Pack line with a JSON array payload the watcher understands.
  offers = random.sample(CARD_POOL, k=min(8, len(CARD_POOL)))
  payload = json.dumps(offers)
  # Include Draft.Notify to set the pack/pick counters, and a scene change to enable watcher.active
  lines = [
    'Client.SceneChange {"fromSceneName":"TableDraftQueue","toSceneName":"Draft","initiator":"System","context":"HumanDraft"}',
    f'Draft.Notify {{ "SelfPick":{pick_no}, "SelfPack":{pack_no} }}',
    f'Draft.Pack {payload}'
  ]
  return offers, lines

def make_pick(offers):
  # Choose the first as the "picked" and log it by name so watcher can resolve without GRP ids.
  picked = offers[0]
  # Include multiple possible pick markers; watcher will accept the name if it matches an offered card.
  lines = [
    'EventPlayerDraftMakePick {"EventName":"PremierDraft_MOCK","Payload":{}}',
    f'{{"type":"DraftPick","PickedCard":"{picked}"}}'
  ]
  return picked, lines

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--out", required=True, help="Path to mock Player.log")
  ap.add_argument("--interval", type=float, default=30.0, help="Seconds between picks (offer→pick→next)")
  ap.add_argument("--picks", type=int, default=12, help="How many picks to simulate before exiting (0 = infinite)")
  args = ap.parse_args()

  out = Path(args.out).expanduser().resolve()
  out.parent.mkdir(parents=True, exist_ok=True)

  # Start-of-session prologue: emulate entering draft scene
  append_line(out, 'UnityCrossThreadLogger Init mock session')
  append_line(out, 'Event_Join {"Name":"PremierDraft_MOCK"}')
  append_line(out, 'Client.SceneChange {"fromSceneName":"None","toSceneName":"Draft","initiator":"System","context":"MockDraft"}')

  pack_no, pick_no = 1, 1
  total = 0
  while True:
    offers, offer_lines = make_offer(pack_no, pick_no)
    for ln in offer_lines:
      append_line(out, ln)
    # wait half interval before logging the pick
    time.sleep(max(0.0, args.interval * 0.5))

    picked, pick_lines = make_pick(offers)
    for ln in pick_lines:
      append_line(out, ln)

    total += 1
    pick_no += 1
    if pick_no > 15:
      pack_no += 1
      pick_no = 1
    if pack_no > 3:
      # End of draft; reset for convenience
      pack_no = 1
      pick_no = 1
      append_line(out, 'SceneLoader:BI_SceneChange("Draft","EventLanding")')

    # Exit if limit reached
    if args.picks and total >= args.picks:
      break

    time.sleep(max(0.0, args.interval * 0.5))

  append_line(out, "mock_draft_feed exiting (done)")
  print(f"[mock] wrote {total} picks into {out}")

if __name__ == "__main__":
  main()