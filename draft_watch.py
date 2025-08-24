#!/usr/bin/env python3
"""
MTGA Draft Watcher
- Tails Player.log in real time
- Detects draft start, packs, and picks
- Enriches each pick with GIHWR from a local CSV
- Writes tidy Parquet outputs: draft_session, draft_pick, draft_pack_card
"""

import os
import re
def _looks_like_uuid(s: str) -> bool:
  return bool(re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", s))
import sys
import json
import time
import uuid
import queue
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import argparse

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DEBUG = str(os.environ.get("DRAFT_DEBUG", "0")).lower() in {"1","true","yes","on"}
MISC = str(os.environ.get("DRAFT_MISC", "0" if not DEBUG else "1")).lower() in {"1","true","yes","on"}
# Verbose sniffer for draft/pack lines (for regex tuning)
DRAFT_SNIFF = str(os.environ.get("DRAFT_SNIFF", "")).lower() in {"1","true","yes","on"} or DEBUG
# Live mode: flush CSV/snapshots after each offer/pick
LIVE = str(os.environ.get("DRAFT_LIVE", "1")).lower() in {"1","true","yes","on"}

def _trunc(s: str, n: int = 180) -> str:
  # Convert to single line for cleaner logs
  s = s.strip().replace("\n", " ")
  # When DEBUG is on, do not truncate any log lines (full fidelity for debugging)
  if DEBUG:
    return s
  # Otherwise, keep logs compact to avoid megabyte-scale files during normal runs
  return s if len(s) <= n else s[: n - 1] + "…"

def _log(msg: str):
  ts = datetime.now(timezone.utc).isoformat()
  line = f"[{ts}] {msg}"
  print(line)
  try:
    (OUT_DIR / "draft_watch.log").parent.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "draft_watch.log").open("a", encoding="utf-8") as f:
      f.write(line + "\n")
  except Exception:
    pass

# -------------------------
# 1) Config
# -------------------------
REPO_ROOT = Path(__file__).resolve().parent
# Default to repo-local logs/Player.log unless overridden by MTGA_PLAYER_LOG or --input
LOG_PATH = Path(os.environ.get("MTGA_PLAYER_LOG", str(REPO_ROOT / "logs" / "Player.log"))).expanduser()
OUT_DIR = Path(os.environ.get("DRAFT_OUT_DIR", "./draft_out")).resolve()
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Expect a CSV you prepare per set+format, columns: card_name,gihwr
# Example file name: EOE.PremierDraft.gihwr.csv
GIHWR_INDEX_PATH = os.environ.get("GIHWR_INDEX_PATH", None)
if not GIHWR_INDEX_PATH:
  _candidate = Path("log samples/eoe_card_ratings.csv")
  if _candidate.exists():
    GIHWR_INDEX_PATH = str(_candidate.resolve())
GIHWR_DEFAULT = None  # or a float like 0.5 if you want fallback

# Optional separate ratings file for offered cards (e.g., per-set consolidated ratings)
OFFER_WR_PATH = os.environ.get("OFFER_WR_PATH")
if not OFFER_WR_PATH:
  # If a common default exists, prefer it; otherwise fall back to GIHWR_INDEX_PATH
  candidate = Path("log samples/eoe_card_ratings.csv")
  OFFER_WR_PATH = str(candidate.resolve()) if candidate.exists() else GIHWR_INDEX_PATH

# Optional MTGA GRP id → card-name map (needed when logs list numeric PackCards)
CARD_MAP_PATH = os.environ.get("CARD_MAP_PATH")
if not CARD_MAP_PATH:
  _grp_candidate = Path("log samples/mtga_card_id.csv")
  if _grp_candidate.exists():
    CARD_MAP_PATH = str(_grp_candidate.resolve())
# -------------------------
# 3b) GRP id → card name map
# -------------------------
class GrpIdMap:
  def __init__(self):
    self.id_to_name: Dict[int, str] = {}

  def load_csv(self, path: str):
    try:
      df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8")
    except Exception:
      df = pd.read_csv(path, dtype=str, encoding_errors="ignore")
    df.columns = [normalize_header(c) for c in df.columns]

    id_candidates = ["grp_id", "grpid", "id", "grp"]
    name_candidates = ["card_name", "name", "card", "title"]

    id_col = next((c for c in id_candidates if c in df.columns), None)
    name_col = next((c for c in name_candidates if c in df.columns), None)
    if not id_col or not name_col:
      detected = ", ".join(df.columns.tolist())
      raise ValueError(f"CARD MAP CSV missing id/name columns. Detected columns: {detected}")

    mapping = {}
    for _, row in df.iterrows():
      try:
        gid = int(str(row.get(id_col, "").strip()))
      except Exception:
        continue
      nm = str(row.get(name_col, "")).strip()
      if gid and nm:
        mapping[gid] = nm
    if not mapping:
      raise ValueError("CARD MAP parsed but no usable rows found.")
    self.id_to_name = mapping

  def get(self, gid: int) -> Optional[str]:
    return self.id_to_name.get(gid)

# -------------------------
# 2) Simple card normalizer
# -------------------------
def norm_card(name: str) -> str:
  if not name:
    return ""
  return re.sub(r"\s+", " ", name.strip().upper())

# -------------------------
# 3) GIHWR cache
# -------------------------

def normalize_header(h: str) -> str:
  # Normalize CSV header strings from a variety of exports.
  if h is None:
    h = ""
  h = str(h)
  # Remove BOM and non‑breaking spaces
  h = h.replace("\ufeff", "").replace("\xa0", " ")
  # Lowercase and strip
  h = h.strip().lower()
  # Remove common punctuation, including smart quotes
  h = re.sub(r"[\%\(\)\[\]\.\"\'\,\u2018\u2019\u201c\u201d]", "", h)
  # Convert any leading '#' to 'num_'
  h = re.sub(r"^#+", "num_", h)
  # Collapse whitespace to single underscores
  h = re.sub(r"\s+", " ", h)
  h = h.replace(" ", "_")
  return h

def parse_wr(v) -> Optional[float]:
  if v is None:
    return None
  s = str(v).strip()
  if s == "" or s.lower() in {"na", "null", "none"}:
    return None
  # Remove percent sign and commas
  s = s.replace("%", "").replace(",", "")
  try:
    x = float(s)
  except Exception:
    return None
  # If looks like percentage (e.g., 56.7), convert to 0-1 scale
  if x > 1.0:
    x = x / 100.0
  # clamp to [0,1]
  if x < 0.0:
    x = 0.0
  if x > 1.0:
    x = 1.0
  return x

class GihwrIndex:
  def __init__(self):
    self.by_key: Dict[str, float] = {}

  def load_csv(self, path: str):
    # Try flexible parsing, auto-detect separator and encoding
    try:
      df = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8")
    except Exception:
      # fallback if pandas sniffer fails
      df = pd.read_csv(path, dtype=str, encoding_errors="ignore")

    # Normalize column names to robust keys (force assignment avoids edge cases)
    df.columns = [normalize_header(c) for c in df.columns]

    # Accept a variety of reasonable header names (normalized to lowercase snake_case)
    name_candidates = [
      "card_name", "name", "card", "cardname", "mtg_card", "card_title"
    ]
    wr_candidates = [
      "gihwr", "gih_wr", "gih_win_rate", "gih_winrate", "expected_win_rate",
      "expected_winrate", "wr", "win_rate", "winrate", "gihwr_percent"
    ]

    name_col = next((c for c in name_candidates if c in df.columns), None)
    wr_col = next((c for c in wr_candidates if c in df.columns), None)

    if not name_col or not wr_col:
      detected = ", ".join(df.columns.tolist())
      raise ValueError(
        f"GIHWR CSV missing required columns. Looking for one of "
        f"{name_candidates} and one of {wr_candidates}. Detected columns: {detected}"
      )

    # Build lookup map
    local = {}
    for _, row in df.iterrows():
      nm = norm_card(row.get(name_col, ""))
      if not nm:
        continue
      wr = parse_wr(row.get(wr_col))
      if wr is None:
        continue
      local[nm] = float(wr)

    if not local:
      raise ValueError("GIHWR CSV parsed but no rows with usable values were found.")

    self.by_key = local

  def get(self, card_name: str) -> Optional[float]:
    return self.by_key.get(norm_card(card_name), GIHWR_DEFAULT)

# -------------------------
# 4) Draft state machine
# -------------------------
class DraftState:
  # Explicit annotations for Pylance/mypy
  active: bool
  draft_id: Optional[str]
  expansion: Optional[str]
  event_type: Optional[str]
  format: Optional[str]
  arena_run_id: Optional[str]
  pack_number: int
  pick_number: int
  offered_cards: List[str]
  started_at: Optional[str]

  def __init__(self):
    self.active = False
    self.draft_id = None
    self.expansion = None
    self.event_type = None
    self.format = None
    self.arena_run_id = None
    self.pack_number = 0
    self.pick_number = 0
    self.offered_cards = []
    self.started_at = None

  def reset(self):
    self.__init__()

# -------------------------
# 5) Storage writer
# -------------------------
class Storage:
  def __init__(self, out_dir: Path):
    self.out_dir = out_dir
    self._sess_rows = []
    self._pick_rows = []
    self._offer_rows = []

  def record_session_start(self, st: DraftState):
    self._sess_rows.append({
      "draft_id": st.draft_id,
      "arena_run_id": st.arena_run_id,
      "expansion": st.expansion,
      "event_type": st.event_type,
      "format": st.format,
      "started_at": st.started_at,
    })

  def record_offer(self, st: DraftState, names: List[str], wrs: List[Optional[float]]):
    for name, wr in zip(names, wrs):
      self._offer_rows.append({
        "draft_id": st.draft_id,
        "pack_number": st.pack_number,
        "pick_number": st.pick_number,
        "card_name": name,
        "card_name_norm": norm_card(name),
        "offered_card_gihwr": wr,
        "is_picked": 0,
      })

  def record_pick(self, st: DraftState, picked_card: str, gihwr: Optional[float], ts: datetime):
    self._pick_rows.append({
      "draft_id": st.draft_id,
      "arena_run_id": st.arena_run_id,
      "expansion": st.expansion,
      "event_type": st.event_type,
      "format": st.format,
      "pack_number": st.pack_number,
      "pick_number": st.pick_number,
      "picked_card": picked_card,
      "picked_card_norm": norm_card(picked_card),
      "picked_card_gihwr": gihwr,
      "ts": ts.isoformat(),
    })
    # Update existing offer rows for this pack/pick, and also append an offer snapshot if needed
    picked_norm = norm_card(picked_card)
    updated_any = False
    for row in reversed(self._offer_rows):
      if (row.get("draft_id") == st.draft_id and
          row.get("pack_number") == st.pack_number and
          row.get("pick_number") == st.pick_number):
        if row.get("card_name_norm") == picked_norm:
          row["is_picked"] = 1
          updated_any = True
    # If no offers were recorded earlier (edge case), at least record the picked as an offer row
    if not updated_any:
      self._offer_rows.append({
        "draft_id": st.draft_id,
        "pack_number": st.pack_number,
        "pick_number": st.pick_number,
        "card_name": picked_card,
        "card_name_norm": picked_norm,
        "offered_card_gihwr": gihwr,
        "is_picked": 1,
      })

  def flush_parquet(self):
    if self._sess_rows:
      df = pd.DataFrame(self._sess_rows).drop_duplicates(subset=["draft_id"])
      pq.write_table(pa.Table.from_pandas(df), self.out_dir / "draft_session.parquet", compression="zstd")
    if self._pick_rows:
      df = pd.DataFrame(self._pick_rows)
      pq.write_table(pa.Table.from_pandas(df), self.out_dir / "draft_pick.parquet", compression="zstd")
    if self._offer_rows:
      df = pd.DataFrame(self._offer_rows)
      pq.write_table(pa.Table.from_pandas(df), self.out_dir / "draft_pack_card.parquet", compression="zstd")

  def _write_csv(self, df: pd.DataFrame, path: Path):
    try:
      df.to_csv(path, index=False)
    except Exception:
      pass

  def flush_csv(self):
    # Mirror parquet outputs as CSV for easy viewing while drafting
    if self._sess_rows:
      df = pd.DataFrame(self._sess_rows).drop_duplicates(subset=["draft_id"])
      self._write_csv(df, self.out_dir / "draft_session.csv")
    if self._pick_rows:
      df = pd.DataFrame(self._pick_rows)
      self._write_csv(df, self.out_dir / "draft_pick.csv")
    if self._offer_rows:
      df = pd.DataFrame(self._offer_rows)
      self._write_csv(df, self.out_dir / "draft_pack_card.csv")

  def flush_snapshots(self, st: "DraftState"):
    """Write small, live-updating snapshots for the current view (latest pack & recent picks)."""
    try:
      offers_df = pd.DataFrame(self._offer_rows)
      picks_df  = pd.DataFrame(self._pick_rows)
      if not offers_df.empty:
        # Latest pack/pick snapshot
        cols = [c for c in ["draft_id","pack_number","pick_number","card_name","offered_card_gihwr","is_picked"] if c in offers_df.columns]
        latest = offers_df
        if {"pack_number","pick_number"}.issubset(offers_df.columns):
          latest = offers_df.sort_values(["draft_id","pack_number","pick_number"]).groupby(["draft_id","pack_number","pick_number"], as_index=False).apply(lambda g: g).reset_index(drop=True)
          latest = latest[(latest["draft_id"]==st.draft_id) & (latest["pack_number"]==st.pack_number) & (latest["pick_number"]==st.pick_number)]
        if not latest.empty:
          latest = latest[cols] if cols else latest
          self._write_csv(latest, self.out_dir / "latest_offers.csv")
      if not picks_df.empty:
        cols = [c for c in ["draft_id","pack_number","pick_number","picked_card","picked_card_gihwr","ts"] if c in picks_df.columns]
        tail = picks_df.sort_values(["draft_id","pack_number","pick_number"]).tail(12)
        tail = tail[cols] if cols else tail
        self._write_csv(tail, self.out_dir / "recent_picks.csv")
    except Exception:
      pass

# -------------------------
# Helper: replay a log file through the watcher
# -------------------------
def replay_file(path: Path, watcher: "DraftWatcher", tail_lines: int | None = None):
  _log(f"replay: reading {path}")
  if not path.exists():
    print(f"Replay file not found: {path}", file=sys.stderr)
    sys.exit(2)
  with path.open("r", encoding="utf-8", errors="ignore") as f:
    if tail_lines is None:
      for line in f:
        watcher._handle_line(line.rstrip("\n"))
    else:
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

# -------------------------
# 6) Log tailer
# -------------------------
class Tailer(threading.Thread):
  def __init__(self, path: Path, q: queue.Queue):
    super().__init__(daemon=True)
    self.path = path
    self.q = q
    self._stop = threading.Event()

  def run(self):
    attached_logged = False
    while not self._stop.is_set():
      try:
        with self.path.open("r", encoding="utf-8", errors="ignore") as f:
          f.seek(0, os.SEEK_END)
          if not attached_logged:
            _log("tail attached to Player.log")
            attached_logged = True
          while not self._stop.is_set():
            line = f.readline()
            if not line:
              time.sleep(0.10)
              continue
            self.q.put(line.rstrip("\n"))
      except FileNotFoundError:
        time.sleep(1.0)

  def stop(self):
    self._stop.set()

# -------------------------
# 7) Regexes for Arena signals
# Adjust these to match your Player.log.
# -------------------------
RE_DRAFT_START = re.compile(
  r"(DraftSession|Event_JoinDraft|Draft\.Start|Join Draft|EnterDraft).*?(?P<format>PremierDraft|TraditionalDraft|QuickDraft)? .*?(?P<expansion>[A-Z0-9]{3})?.*?(?P<run>[A-F0-9\-]{8,})?",
  re.IGNORECASE,
)

# Try to capture any JSON-ish array following common draft/pack markers
RE_PACK_CARDS = re.compile(r"(Draft\.?Pack|Draft_Offer|CurrentPack|PackCards|CardsInPack|Booster).*?(\[[^\]]+\])", re.IGNORECASE)
# Fallback: extract repeated CardName:"..." occurrences on a line (seen in some GreToClientEvent payloads)
RE_CARDNAME_KV = re.compile(r"CardName\"\s*:\s*\"([^\"]+)\"")

#
# Arena commonly logs PackCards as a comma-separated GRP id string
RE_PACK_IDS = re.compile(r"PackCards\":\"(?P<ids>\d+(?:,\d+)*)\"")

# Your pick sometimes comes as a chosen card name or id; cover more Arena variants
RE_MAKE_PICK = re.compile(
  r"(Draft\.MakePick|DraftPick|EventPlayerDraftMakePick|PlayerDraftMakePick|Event\.PlayerDraft\.MakePick)",
  re.IGNORECASE,
)

# Also catch explicit key-value card names on pick lines
RE_PICK_NAME_KV = re.compile(r"\"(?:CardName|cardName|PickedCard|chosenCard)\"\s*:\s*\"([^\"]+)\"")

# Pick ids can be logged under different keys and as arrays or singletons
RE_PICK_IDS = re.compile(r"\"(?:GrpIds|PickedGrpIds|PickGrpIds)\"\s*:\s*\[(?P<ids>[0-9, ]+)\]", re.IGNORECASE)
RE_PICK_ID_SINGLE = re.compile(r"\"(?:GrpId|PickedGrpId|PickGrpId)\"\s*:\s*(?P<id>\d+)", re.IGNORECASE)

# Scene change into the Draft UI (useful to flip state.active on)
RE_SCENE_TO_DRAFT = re.compile(r"toSceneName\":\"Draft\"", re.IGNORECASE)
# Draft.Notify carries SelfPick/SelfPack; use it to set counters
RE_NOTIFY = re.compile(r"Draft\.Notify.*?\"SelfPick\":(?P<pick>\d+),\"SelfPack\":(?P<pack>\d+)")

# Bump pack/pick when a new pack is presented
RE_NEXT_PICK = re.compile(r"(NextPack|PickNumber\s*:\s*(?P<pick>\d+)|PackNumber\s*:\s*(?P<pack>\d+))", re.IGNORECASE)

# -------------------------
# Misc notable events to confirm the tail is alive
RE_MISC_EVENTS = [
  (re.compile(r"(Initializing|Initialize|Init Game|UnityCrossThreadLogger)", re.IGNORECASE), "init"),
  (re.compile(r"(Event\.Join|Event_Join|JoinEvent)", re.IGNORECASE), "event_join"),
  (re.compile(r"(Match\.Start|BeginMatch)", re.IGNORECASE), "match_start"),
  (re.compile(r"(Match\.End|EndMatch|FinalResult)", re.IGNORECASE), "match_end"),
  (re.compile(r"(Deck\.Submit|SubmitDeck)", re.IGNORECASE), "deck_submit"),
  (re.compile(r"(Scene|LoadScene|SceneMgr)", re.IGNORECASE), "scene"),
]

# -------------------------
# 8) Orchestrator
# -------------------------
class DraftWatcher:
  def __init__(self, log_path: Path, gihwr_index: GihwrIndex, storage: Storage, offer_index: Optional[GihwrIndex] = None, idmap: Optional[GrpIdMap] = None):
    self.log_path = log_path
    self.gihwr = gihwr_index
    self.storage = storage
    self.state = DraftState()
    self.q = queue.Queue()
    self.tailer = Tailer(log_path, self.q)
    self.lines_read = 0
    self.last_event = "idle"
    self._last_heartbeat = time.time()
    self.offer_index = offer_index
    self.idmap = idmap

  def start(self):
    self.tailer.start()
    _log(f"tailing {self.log_path}")
    _log("ready: waiting for draft events (Draft.Start / DraftSession)")
    try:
      while True:
        line = self.q.get()
        if DEBUG and "draft" in line.lower():
          _log("DBG:" + _trunc(line))
        self.lines_read += 1
        now = time.time()
        if DEBUG and now - self._last_heartbeat >= 5.0:
          _log(f"heartbeat: lines_read={self.lines_read} last_event={self.last_event} active={self.state.active}")
          self._last_heartbeat = now
        self._handle_line(line)
    except KeyboardInterrupt:
      _log("stopping...")
    finally:
      self.tailer.stop()
      self.storage.flush_parquet()
      self.storage.flush_csv()
      self.storage.flush_snapshots(self.state)

  def _handle_line(self, line: str):
    # 0) log miscellaneous non-draft events so you can see the tail is alive
    if MISC:
      for rx, tag in RE_MISC_EVENTS:
        if rx.search(line):
          self.last_event = tag
          _log(f"{tag}: " + _trunc(line))
          break

    # 0b) flip active on entering Draft scene
    if RE_SCENE_TO_DRAFT.search(line) and not self.state.active:
      self.state.active = True
      if not self.state.draft_id:
        self.state.draft_id = str(uuid.uuid4())
      self.state.started_at = self.state.started_at or datetime.now(timezone.utc).isoformat()
      self.last_event = "scene_draft"
      if DEBUG or MISC:
        _log("scene: entered Draft; watcher active")

    # 0c) keep pack/pick counters in sync from Draft.Notify
    m_notify = RE_NOTIFY.search(line)
    if m_notify:
      try:
        self.state.pick_number = int(m_notify.group("pick"))
        self.state.pack_number = int(m_notify.group("pack"))
      except Exception:
        pass

    # 1) detect draft start
    m = RE_DRAFT_START.search(line)
    if m:
      self.state = DraftState()
      self.state.active = True
      self.state.draft_id = str(uuid.uuid4())
      self.state.format = m.group("format") if m.groupdict().get("format") else None
      self.state.expansion = m.group("expansion") if m.groupdict().get("expansion") else None
      self.state.arena_run_id = m.group("run") if m.groupdict().get("run") else None
      self.state.started_at = datetime.now(timezone.utc).isoformat()
      self.state.pack_number = 1
      self.state.pick_number = 0
      self.storage.record_session_start(self.state)
      self.last_event = "draft_start"
      _log(f"draft start {self.state.draft_id} {self.state.format} {self.state.expansion}")
      return

    if not self.state.active:
      return

    # 2) update pack/pick counters opportunistically
    bump = False
    for mnp in RE_NEXT_PICK.finditer(line):
      gd = mnp.groupdict()
      if gd.get("pick"):
        try:
          self.state.pick_number = int(gd["pick"])
          bump = True
        except Exception:
          pass
      if gd.get("pack"):
        try:
          self.state.pack_number = int(gd["pack"])
          bump = True
        except Exception:
          pass
    if bump and DRAFT_SNIFF:
      _log(f"sniff: counters pack={self.state.pack_number} pick={self.state.pick_number}")

    # 3) detect current pack offers (names or grp ids)
    names: List[str] = []

    # 3a) explicit CardName occurrences
    cardname_hits = RE_CARDNAME_KV.findall(line)
    if cardname_hits:
      names = [n.strip() for n in cardname_hits if n.strip()]

    # 3b) JSON-ish array after Draft.Pack / PackCards, etc.
    if not names:
      m_pack = RE_PACK_CARDS.search(line)
      if m_pack:
        payload = m_pack.group(2)
        # Try parse as JSON array of strings or objects
        try:
          arr = json.loads(payload)
          if isinstance(arr, list) and arr:
            if isinstance(arr[0], str):
              # Could be names or raw grp id strings
              tmp = []
              for item in arr:
                s = str(item).strip()
                if s.isdigit():
                  # map grp id to name
                  gid = int(s)
                  tmp.append(self.idmap.get(gid) if self.idmap else s)
                else:
                  tmp.append(s)
              names = [t for t in tmp if t]
            elif isinstance(arr[0], dict):
              # Look for CardName keys
              for obj in arr:
                nm = obj.get("CardName") or obj.get("cardName") or obj.get("name")
                if nm:
                  names.append(str(nm).strip())
        except Exception:
          # Fallback: treat as comma-separated numbers
          ids = [s.strip() for s in payload.strip("[]").split(",") if s.strip()]
          gids = []
          for s in ids:
            try:
              gids.append(int(s))
            except Exception:
              pass
          if gids:
            if self.idmap:
              names = [self.idmap.get(g) or str(g) for g in gids]
            else:
              names = [str(g) for g in gids]

    # 3c) Arena often logs PackCards as a simple comma-separated grp id string
    if not names:
      m_ids = RE_PACK_IDS.search(line)
      if m_ids:
        raw_ids = [s.strip() for s in m_ids.group("ids").split(",") if s.strip()]
        gids = []
        for s in raw_ids:
          try:
            gids.append(int(s))
          except Exception:
            continue
        if gids:
          if self.idmap:
            names = [self.idmap.get(g) or str(g) for g in gids]
          else:
            names = [str(g) for g in gids]

    # If we collected an offer, record it
    if names:
      wrs = [(self.gihwr.get(n) if self.gihwr else None) for n in names]
      self.storage.record_offer(self.state, names, wrs)
      self.state.offered_cards = names[:]
      self.last_event = "offer"
      if self.state.pick_number == 0:
        self.state.pick_number = 1
      _log(
        "available P{p}P{k}: {n} cards | ".format(p=self.state.pack_number, k=self.state.pick_number, n=len(names))
        + ", ".join([f"{n}={w if w is not None else '?'}" for n, w in zip(names, wrs)])
      )
      if LIVE:
        try:
          if hasattr(self.storage, "flush_parquet"):
            self.storage.flush_parquet()
          if hasattr(self.storage, "flush_csv"):
            self.storage.flush_csv()
          if hasattr(self.storage, "flush_snapshots"):
            self.storage.flush_snapshots(self.state)
        except Exception as e:
          _log(f"live flush error: {e}")
      # do not return; the same line may also carry the chosen pick

    # 4) detect the actual pick (prefer GRP ids like EventPlayerDraftMakePick payloads)
    picked_name: Optional[str] = None
    picked_gid: Optional[int] = None

    # 4a) ID-style pick (array form) — e.g., "GrpIds":[12345]
    m_pid = RE_PICK_IDS.search(line)
    if m_pid:
      ids = [s.strip() for s in m_pid.group("ids").split(",") if s.strip()]
      if ids:
        try:
          picked_gid = int(ids[0])
          picked_name = self.idmap.get(picked_gid) if self.idmap else str(picked_gid)
          if DEBUG:
            _log(f"DBG: pick ids={ids} → gid={picked_gid} name={picked_name}")
        except Exception:
          picked_gid = None
          picked_name = None

    # 4b) ID-style pick (single id) — e.g., "GrpId":12345
    if picked_name is None:
      m_pid1 = RE_PICK_ID_SINGLE.search(line)
      if m_pid1:
        try:
          picked_gid = int(m_pid1.group("id"))
          picked_name = self.idmap.get(picked_gid) if self.idmap else str(picked_gid)
          if DEBUG:
            _log(f"DBG: pick id(single)={picked_gid} → name={picked_name}")
        except Exception:
          picked_gid = None
          picked_name = None

    # 4c) Explicit key-value card name on the pick line
    # Only accept this if it matches a currently offered card (to avoid UUID/garbage tokens).
    if picked_name is None:
      m_pkv = RE_PICK_NAME_KV.search(line)
      if m_pkv:
        raw = m_pkv.group(1).strip()
        if raw:
          candidate = raw
          # Validate against the last known offers to avoid false positives
          offered_norms = {norm_card(n) for n in (self.state.offered_cards or [])}
          if offered_norms and norm_card(candidate) in offered_norms:
            picked_name = candidate
            if DEBUG:
              _log(f"DBG: pick by name matched offered set → {picked_name}")
          else:
            if DEBUG:
              _log(f"DBG: ignored pick name not in offers: {candidate}")

    # 4d) As a last resort, only proceed if the line is a MakePick AND we have a plausible name/id.
    m_pick = RE_MAKE_PICK.search(line)
    if m_pick and picked_name is None:
      if DEBUG:
        _log("DBG: MakePick seen without parsable GrpIds/name — skipping to avoid bad data")
      # Do not guess; wait for a line with GrpIds or a name present in offers
      return

    # Safety: ignore UUID/hex blobs or obvious non-card tokens that sometimes appear on MakePick lines
    if picked_name:
      token = picked_name.strip().strip('"').strip("{}[]")
      if token.isdigit() or _looks_like_uuid(token) or (re.fullmatch(r"[0-9A-Fa-f\-]{20,}", token) and " " not in token):
        if DEBUG:
          _log(f"DBG: ignored suspicious pick token: {token}")
        picked_name = None

    if picked_name:
      # If counters weren't established yet, set sane defaults
      if self.state.pack_number <= 0:
        self.state.pack_number = 1
      if self.state.pick_number <= 0:
        self.state.pick_number = 1

      wr = self.gihwr.get(picked_name) if self.gihwr else None
      ts = datetime.now(timezone.utc)
      self.storage.record_pick(self.state, picked_name, wr, ts)
      self.last_event = "pick"
      _log(f"picked P{self.state.pack_number}P{self.state.pick_number}: {picked_name}  GIHWR={wr}")
      # clear current offers and advance pick counter heuristically
      self.state.offered_cards = []
      self.state.pick_number = max(1, self.state.pick_number) + 1

      if LIVE:
        try:
          if hasattr(self.storage, "flush_parquet"):
            self.storage.flush_parquet()
          if hasattr(self.storage, "flush_csv"):
            self.storage.flush_csv()
          if hasattr(self.storage, "flush_snapshots"):
            self.storage.flush_snapshots(self.state)
        except Exception as e:
          _log(f"live flush error: {e}")

# -------------------------
# 9) CLI entry point
# -------------------------
def main():
  parser = argparse.ArgumentParser(description="MTGA Draft Watcher")
  parser.add_argument("--replay", action="store_true", help="Process the input log once and exit")
  parser.add_argument("--input", dest="input_path", default=None, help="Path to Player.log (overrides MTGA_PLAYER_LOG)")
  parser.add_argument("--tail", dest="tail_lines", type=int, default=None, help="When replaying, only process the last N lines")
  args = parser.parse_args()

  log_path = Path(args.input_path).expanduser() if args.input_path else LOG_PATH

  if not log_path.exists():
    print(f"Player.log not found at {log_path}", file=sys.stderr)
    sys.exit(2)

  # Ratings can be optional; if missing we'll proceed without WR enrichment
  gihwr = None
  if GIHWR_INDEX_PATH and Path(GIHWR_INDEX_PATH).exists():
    try:
      gihwr = GihwrIndex()
      gihwr.load_csv(GIHWR_INDEX_PATH)
    except Exception as e:
      _log(f"GIHWR load failed; continuing without WR enrichment: {e}")
      gihwr = GihwrIndex()  # empty index
  else:
    _log("GIHWR_INDEX_PATH not set or file not found; continuing without WR enrichment")
    gihwr = GihwrIndex()  # empty index

  # Card map (grp id → name) is optional but helpful
  idmap = None
  if CARD_MAP_PATH and Path(CARD_MAP_PATH).exists():
    try:
      idmap = GrpIdMap()
      idmap.load_csv(CARD_MAP_PATH)
    except Exception as e:
      _log(f"CARD_MAP load failed; continuing without id map: {e}")
      idmap = None

  out_dir = OUT_DIR
  storage = Storage(out_dir)
  watcher = DraftWatcher(log_path, gihwr, storage, idmap=idmap)

  if args.replay:
    replay_file(log_path, watcher, tail_lines=args.tail_lines)
    return

  watcher.start()


if __name__ == "__main__":
  main()
