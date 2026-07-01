#!/usr/bin/env bash
set -euo pipefail

NO_REBOOT=0
for arg in "$@"; do
  case "$arg" in
    --no-reboot) NO_REBOOT=1 ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: $0 [--no-reboot]"
      exit 2
      ;;
  esac
done

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi

BOOT_DIR="/boot/firmware"
if [ ! -f "$BOOT_DIR/config.txt" ]; then
  BOOT_DIR="/boot"
fi

stamp="$(date +%Y%m%d-%H%M%S)"
cp -a "$BOOT_DIR/config.txt" "$BOOT_DIR/config.txt.before-hdmi-$stamp"
cp -a "$BOOT_DIR/cmdline.txt" "$BOOT_DIR/cmdline.txt.before-hdmi-$stamp"

sed -i '/^# LCD35 MPI3501/,/^# End LCD35 MPI3501/d' "$BOOT_DIR/config.txt"
sed -i \
  -e 's/^#dtoverlay=vc4-kms-v3d/dtoverlay=vc4-kms-v3d/' \
  -e 's/^#disable_fw_kms_setup=1/disable_fw_kms_setup=1/' \
  "$BOOT_DIR/config.txt"

grep -q '^dtoverlay=vc4-kms-v3d' "$BOOT_DIR/config.txt" || echo 'dtoverlay=vc4-kms-v3d' >> "$BOOT_DIR/config.txt"
grep -q '^disable_fw_kms_setup=1' "$BOOT_DIR/config.txt" || echo 'disable_fw_kms_setup=1' >> "$BOOT_DIR/config.txt"
grep -q '^display_auto_detect=1' "$BOOT_DIR/config.txt" || echo 'display_auto_detect=1' >> "$BOOT_DIR/config.txt"

rm -f /etc/X11/xorg.conf.d/10-lcd35-fbdev.conf
cat > /etc/X11/xorg.conf.d/99-v3d.conf <<'EOF'
Section "OutputClass"
  Identifier "vc4"
  MatchDriver "vc4"
  Driver "modesetting"
  Option "PrimaryGPU" "true"
EndSection
EOF

rm -f /etc/systemd/system/lightdm.service.d/10-fbdev-no-dri.conf

python3 - <<'PY'
from pathlib import Path

path = Path("/etc/lightdm/lightdm.conf")
text = path.read_text(encoding="utf-8")

def set_key(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines()
    out = []
    in_section = False
    seen_section = False
    written = False
    header = f"[{section}]"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not written:
                out.append(f"{key}={value}")
                written = True
            in_section = stripped == header
            seen_section = seen_section or in_section
        if in_section and (stripped == key or stripped.startswith(key + "=") or stripped.startswith("#" + key + "=")):
            if not written:
                out.append(f"{key}={value}")
                written = True
            continue
        out.append(line)
    if in_section and not written:
        out.append(f"{key}={value}")
    if not seen_section:
        out.extend(["", header, f"{key}={value}"])
    return "\n".join(out) + "\n"

settings = {
    "LightDM": {
        "start-default-seat": "true",
        "logind-check-graphical": "true",
    },
    "Seat:*": {
        "greeter-session": "pi-greeter-labwc",
        "user-session": "rpd-labwc",
        "autologin-user": "intyu",
        "autologin-session": "rpd-labwc",
    },
}
for section, pairs in settings.items():
    for key, value in pairs.items():
        text = set_key(text, section, key, value)
path.write_text(text, encoding="utf-8")
PY

systemctl daemon-reload
systemctl set-default graphical.target
systemctl disable --now lcd-show-desktop.service 2>/dev/null || true
systemctl disable --now vncserver-x11-serviced.service 2>/dev/null || true
systemctl enable wayvnc.service wayvnc-control.service 2>/dev/null || true
systemctl enable lightdm.service

sync
echo "HDMI/KMS mode configured. VNC will use WayVNC after reboot."
if [ "$NO_REBOOT" -eq 0 ]; then
  echo "Rebooting now..."
  reboot
fi
