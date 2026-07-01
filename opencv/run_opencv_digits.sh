#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source /home/intyu/env/bin/activate
exec python "$SCRIPT_DIR/opencv_digits_csi.py" "$@"
