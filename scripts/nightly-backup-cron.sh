#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${HOME}/.config/pharmacy-backup/env"

if [[ ! -r "$CONFIG_FILE" ]]; then
  echo "ERROR: private pharmacy backup config is missing or unreadable" >&2
  exit 1
fi

# This private file must contain trusted shell variable assignments only.
# shellcheck source=/dev/null
source "$CONFIG_FILE"

: "${PHARMACY_OFFSITE_REMOTE:?off-site remote is not configured}"
: "${PHARMACY_RCLONE_BIN:?rclone path is not configured}"

export PHARMACY_OFFSITE_REMOTE PHARMACY_RCLONE_BIN

exec "$ROOT_DIR/scripts/nightly-backup.sh" "$@"
