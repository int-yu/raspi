#!/usr/bin/env bash
set -euo pipefail

NO_APT=0
NO_REBOOT=0
for arg in "$@"; do
  case "$arg" in
    --no-apt) NO_APT=1 ;;
    --no-reboot) NO_REBOOT=1 ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: $0 [--no-apt] [--no-reboot]"
      exit 2
      ;;
  esac
done

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LCD_DIR="$SCRIPT_DIR/LCD-show"
LCD_ARCHIVE="$SCRIPT_DIR/LCD-show.tar.gz"
BOOT_DIR="/boot/firmware"
if [ ! -f "$BOOT_DIR/config.txt" ]; then
  BOOT_DIR="/boot"
fi

if [ ! -d "$LCD_DIR" ]; then
  if [ ! -f "$LCD_ARCHIVE" ]; then
    echo "Missing LCD-show directory and LCD-show.tar.gz in $SCRIPT_DIR"
    exit 1
  fi
  tar -xzf "$LCD_ARCHIVE" -C "$SCRIPT_DIR"
fi

if [ ! -f "$LCD_DIR/usr/tft35a-overlay.dtb" ]; then
  echo "Missing tft35a overlay: $LCD_DIR/usr/tft35a-overlay.dtb"
  exit 1
fi

if [ "$NO_APT" -eq 0 ]; then
  apt-get update
  apt-get install -y \
    python3-venv python3-pip python3-opencv python3-picamera2 \
    python3-numpy python3-pil python3-serial \
    xserver-xorg-input-evdev xserver-xorg-video-fbturbo \
    realvnc-vnc-server
fi

if [ ! -d /home/intyu/env ]; then
  sudo -u intyu python3 -m venv --system-site-packages /home/intyu/env
fi

install -m 0644 "$LCD_DIR/usr/tft35a-overlay.dtb" "$BOOT_DIR/overlays/tft35a-overlay.dtb"
install -m 0644 "$LCD_DIR/usr/tft35a-overlay.dtb" "$BOOT_DIR/overlays/tft35a.dtbo"

stamp="$(date +%Y%m%d-%H%M%S)"
cp -a "$BOOT_DIR/config.txt" "$BOOT_DIR/config.txt.before-lcd35-$stamp"
cp -a "$BOOT_DIR/cmdline.txt" "$BOOT_DIR/cmdline.txt.before-lcd35-$stamp"

sed -i '/^# LCD35 MPI3501/,/^# End LCD35 MPI3501/d' "$BOOT_DIR/config.txt"
sed -i \
  -e 's/^dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d/' \
  -e 's/^disable_fw_kms_setup=1/#disable_fw_kms_setup=1/' \
  -e 's/^#dtparam=spi=on/dtparam=spi=on/' \
  -e 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/' \
  "$BOOT_DIR/config.txt"

grep -q '^dtparam=spi=on' "$BOOT_DIR/config.txt" || echo 'dtparam=spi=on' >> "$BOOT_DIR/config.txt"
grep -q '^dtparam=i2c_arm=on' "$BOOT_DIR/config.txt" || echo 'dtparam=i2c_arm=on' >> "$BOOT_DIR/config.txt"

cat >> "$BOOT_DIR/config.txt" <<'EOF'

# LCD35 MPI3501 3.5inch 480x320 XPT2046
hdmi_force_hotplug=1
dtparam=i2c_arm=on
dtparam=spi=on
enable_uart=1
dtoverlay=tft35a:rotate=90
hdmi_group=2
hdmi_mode=87
hdmi_cvt 480 320 60 6 0 0 0
hdmi_drive=2
# End LCD35 MPI3501
EOF

sed -i \
  -e 's/console=serial0,[0-9]\+ //' \
  -e 's/console=ttyAMA0,[0-9]\+ //' \
  -e 's/console=ttyS0,[0-9]\+ //' \
  "$BOOT_DIR/cmdline.txt"

mkdir -p /etc/X11/xorg.conf.d
install -m 0644 "$LCD_DIR/usr/99-calibration.conf-35-90" /etc/X11/xorg.conf.d/99-calibration.conf

rm -f /etc/X11/xorg.conf.d/99-v3d.conf
cat > /etc/X11/xorg.conf.d/10-lcd35-fbdev.conf <<'EOF'
Section "Device"
    Identifier "LCD35 framebuffer"
    Driver "fbturbo"
    Option "fbdev" "/dev/fb0"
    Option "SwapbuffersWait" "true"
EndSection

Section "Monitor"
    Identifier "LCD35 monitor"
EndSection

Section "Screen"
    Identifier "LCD35 screen"
    Device "LCD35 framebuffer"
    Monitor "LCD35 monitor"
    DefaultDepth 16
EndSection
EOF

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
        "logind-check-graphical": "false",
    },
    "Seat:*": {
        "greeter-session": "pi-greeter-x",
        "user-session": "rpd-x",
        "autologin-user": "intyu",
        "autologin-session": "rpd-x",
        "xserver-command": "X -s 0 -dpms",
    },
}
for section, pairs in settings.items():
    for key, value in pairs.items():
        text = set_key(text, section, key, value)
path.write_text(text, encoding="utf-8")
PY

mkdir -p /etc/systemd/system/lightdm.service.d
cat > /etc/systemd/system/lightdm.service.d/10-fbdev-no-dri.conf <<'EOF'
[Unit]
After=
After=systemd-user-sessions.service plymouth-quit.service
Wants=
EOF

cat > /etc/systemd/system/lcd-show-desktop.service <<'EOF'
[Unit]
Description=Switch LCD console to X11 desktop VT
After=lightdm.service
Requires=lightdm.service

[Service]
Type=oneshot
ExecStart=/usr/bin/chvt 7

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl set-default graphical.target
systemctl enable lightdm.service lcd-show-desktop.service
systemctl disable --now wayvnc.service wayvnc-control.service 2>/dev/null || true
systemctl enable vncserver-x11-serviced.service 2>/dev/null || true
systemctl disable --now serial-getty@serial0.service 2>/dev/null || true
systemctl disable --now serial-getty@ttyAMA10.service 2>/dev/null || true

sync
echo "LCD35 MPI3501 mode configured. VNC will mirror the 480x320 LCD on port 5900."
if [ "$NO_REBOOT" -eq 0 ]; then
  echo "Rebooting now..."
  reboot
fi
