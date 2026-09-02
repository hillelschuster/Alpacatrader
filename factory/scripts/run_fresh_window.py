"""One-command fresh-window chain: certify -> featurize -> frozen eval, per month.

Certify output goes to factory/artifacts/certification_2026-04..08 (fresh data, no reuse
of the burned 2025 months' certification logs as evidence).

Usage:
  uv run --no-project --with polars --with numpy --with lightgbm --with tzdata \
    python factory/scripts/run_fresh_window.py --month 2026-04
"""
import argparse
import subprocess
import sys

def run(cmd):
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}")


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--month", required=True)
    a.add_argument("--skip-certify", action="store_true")
    args = a.parse_args()
    prev = {"2026-04": ["2026-03"], "2026-05": ["2026-04"], "2026-06": ["2026-05"],
            "2026-07": ["2026-06"], "2026-08": ["2026-07"]}[args.month]
    if not args.skip_certify:
        run([sys.executable, "factory/scripts/certify_month.py",
             "--files"] + [f"data/clean_ohlcv_{m}.parquet" for m in prev + [args.month]] +
            ["--month", args.month])
    run([sys.executable, "factory/scripts/build_features.py", "--month", args.month])


if __name__ == "__main__":
    main()
