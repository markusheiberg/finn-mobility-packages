#!/bin/bash
set -e

SCRIPT="${SCRIPT:-finn}"   # finn | blocket
GCS_BUCKET="${GCS_BUCKET}" # required: e.g. my-bucket

if [ -z "$GCS_BUCKET" ]; then
  echo "ERROR: GCS_BUCKET env var is required"
  exit 1
fi

if [ "$SCRIPT" = "blocket" ]; then
  python3 blocket_mobility_packages_requests.py
  FILES="blocket_mobility_packages_summary.csv blocket_mobility_debug.csv"
else
  python3 finn_mobility_packages_requests.py
  FILES="finn_mobility_packages_summary.csv finn_mobility_debug.csv"
fi

for f in $FILES; do
  if [ -f "$f" ]; then
    gsutil cp "$f" "gs://${GCS_BUCKET}/$f"
    echo "Uploaded $f to gs://${GCS_BUCKET}/$f"
  fi
done
