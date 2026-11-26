#!/usr/bin/env bash
set -euo pipefail

REF="${TQD_REF:-v0.2.0}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

COMMANDS=(
  "uv tool install --python 3.12 --torch-backend=auto \"git+https://github.com/abm-dungtq/tqd-voicestudio@${REF}\""
  "\"\$(uv tool dir --bin)/tqd-voicestudio\" doctor"
  "\"\$(uv tool dir --bin)/tqd-voicestudio\" install"
)

if [ "$DRY_RUN" = "1" ]; then
  printf '%s\n' "${COMMANDS[@]}"
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Chưa có uv. Cài bằng: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

uv tool install --python 3.12 --torch-backend=auto "git+https://github.com/abm-dungtq/tqd-voicestudio@${REF}"
uv tool update-shell || true
EXE="$(uv tool dir --bin)/tqd-voicestudio"
"$EXE" doctor || echo "doctor báo lỗi; 'tqd-voicestudio install' sẽ tự dừng nếu lỗi chặn cài đặt." >&2
"$EXE" install
