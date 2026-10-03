#!/usr/bin/env python3
"""Prepare explicit causal model fields one clock at a time; no silent partial corpus.

Validation frame preparation is locked behind the full pipeline freeze, even though its
raw observation files may already have been built. Execution/label fields remain separate.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import polars as pl
import lifecycle_study as ls
from lifecycle_models import PRICE, FULL, TAPE, TARGETS


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--which",choices=("discovery","validation"),default="discovery")
    a=ap.parse_args()
    root=a.data_root/"harvest01/lifecycle/v2"
    days=ls.discovery_days(a.data_root) if a.which=="discovery" else ls.validation_days(a.data_root,allow=True)
    folder=root/"frames"; folder.mkdir(exist_ok=True)
    paths=[]
    for clock in (540,560,569,571):
        frame=ls.load_panel(days,a.data_root,clocks=(clock,))
        identifiers=["day","clock","rank","ticker","t","entry_et","filled_asof"]
        fields=identifiers+sorted((set(PRICE)|set(FULL)|set(TAPE)|set(TARGETS))-set(identifiers))
        frame=frame.select([c for c in fields if c in frame.columns])
        p=folder/f"{a.which}_{clock}.parquet"
        frame.write_parquet(p)
        print(f"prepared {a.which} clock{clock} rows={frame.height} fields={frame.width}",flush=True)
        paths.append(p)
        del frame
    dest=root/f"{a.which}_model.parquet"
    pl.scan_parquet(paths).sink_parquet(dest)
    print(f"prepared complete {a.which} model input: {dest}",flush=True)


if __name__=="__main__":
    main()
