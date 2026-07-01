#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LCD_SCRIPT="/home/intyu/Desktop/LCD驱动/install_lcd35_mpi3501.sh"
if [ ! -f "$LCD_SCRIPT" ]; then
  LCD_SCRIPT="$SCRIPT_DIR/../LCD驱动/install_lcd35_mpi3501.sh"
fi
if [ ! -f "$LCD_SCRIPT" ]; then
  echo "Cannot find install_lcd35_mpi3501.sh"
  exit 1
fi

exec bash "$LCD_SCRIPT" --no-apt "$@"
