#!/usr/bin/env python3
import argparse
import pandas as pd
from pathlib import Path

CANDIDATE_ID_COLS = ["grpId", "grpid", "id", "mtga_id", "card_id"]
CANDIDATE_NAME_COLS = ["name", "cardName", "title", "card_name", "Card Name"]

def pick_col(df, candidates):
    cols = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in cols:
            return cols[c.lower()]
    raise ValueError(f"Could not find any of columns {candidates} in {list(df.columns)}")

def main():
    ap = argparse.ArgumentParser(description="Convert mtga_card_id.csv to grpId,name map")
    ap.add_argument("--card-csv", required=True, help="Path to mtga_card_id.csv")
    ap.add_argument("--set-filter", default=None, help="Optional set code to filter to, e.g. EOE or TDM")
    ap.add_argument("--out", required=True, help="Output CSV path, e.g. data/card_map_eoe.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.card_csv)
    id_col = pick_col(df, CANDIDATE_ID_COLS)
    name_col = pick_col(df, CANDIDATE_NAME_COLS)

    # Optional set filter if your file has a set column
    if args.set_filter:
        for maybe in ["set", "Set", "set_code", "code", "expansion"]:
            if maybe in df.columns:
                before = len(df)
                df = df[df[maybe].astype(str).str.upper() == args.set_filter.upper()]
                print(f"[info] set filter {args.set_filter}: {before} → {len(df)} rows")
                break

    out = (
        df[[id_col, name_col]]
        .rename(columns={id_col: "grpId", name_col: "name"})
        .dropna(subset=["grpId", "name"])
        .drop_duplicates(subset=["grpId"])
    )
    # sanitize
    out["grpId"] = out["grpId"].astype(int)
    out["name"] = out["name"].astype(str).str.strip()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"[ok] wrote {args.out} with {len(out)} rows")

if __name__ == "__main__":
    main()