"""Download one month of mito0o852/OHLCV-1m from Hugging Face Hub."""

import argparse
from pathlib import Path
from huggingface_hub import hf_hub_download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument(
        "--out", type=Path, default=Path("data"),
        help="Output directory (default: data/)",
    )
    args = parser.parse_args()

    filename = f"ohlcv_{args.year:04d}-{args.month:02d}.parquet"
    args.out.mkdir(parents=True, exist_ok=True)

    dest = args.out / filename
    if dest.exists():
        print(f"Already exists: {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
        return

    print(f"Downloading mito0o852/OHLCV-1m → {dest} ...")
    # hf_hub_download preserves remote dir structure, so we download
    # to parent and rename. Simpler: download to cache then copy.
    cached = hf_hub_download(
        repo_id="mito0o852/OHLCV-1m",
        filename=f"data/{filename}",
        repo_type="dataset",
    )
    import shutil
    shutil.copy2(cached, dest)
    size_mb = dest.stat().st_size / 1e6
    print(f"Done: {dest} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()