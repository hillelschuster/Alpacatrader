set -x
for m in 04 08 09 10 11 12; do
  if [ ! -f "data/clean_ohlcv_2025-$m.parquet" ]; then
    PYTHONIOENCODING=utf-8 uv run --no-project --with polars --with tzdata python factory/scripts/clean_month.py --file "data/ohlcv_2025-$m.parquet" --out "data/clean_ohlcv_2025-$m.parquet"
  fi
done
# certify: month needs its own clean file; prior-month file appended for first-session prev_close
declare -A pairs=( ["08"]="07 08" ["09"]="08 09" ["10"]="09 10" ["11"]="10 11" ["12"]="11 12" ["04"]="04" )
for m in 08 09 10 11 12 04; do
  if [ ! -d "factory/artifacts/certification_2025-$m" ]; then
    files=""
    for f in ${pairs[$m]}; do files="$files data/clean_ohlcv_2025-$f.parquet"; done
    PYTHONIOENCODING=utf-8 uv run --no-project --with polars --with numpy --with tzdata --with yfinance python factory/scripts/certify_month.py --month "2025-$m" --files $files
  fi
done
echo CERT_CHAIN_DONE
