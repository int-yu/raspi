#!/usr/bin/env python3
"""
Draw a simple test pattern directly to a Linux framebuffer such as /dev/fb0.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np


def framebuffer_info(fbdev: str) -> tuple[int, int, int]:
    fb_name = Path(fbdev).name
    sysfs = Path("/sys/class/graphics") / fb_name
    width, height = [
        int(part)
        for part in (sysfs / "virtual_size").read_text(encoding="ascii").strip().split(",")
    ]
    bpp = int((sysfs / "bits_per_pixel").read_text(encoding="ascii").strip())
    return width, height, bpp


def bgr_to_framebuffer_payload(image_bgr: np.ndarray, bpp: int) -> bytes:
    if bpp == 16:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        r = (rgb[:, :, 0].astype(np.uint16) >> 3) & 0x1F
        g = (rgb[:, :, 1].astype(np.uint16) >> 2) & 0x3F
        b = (rgb[:, :, 2].astype(np.uint16) >> 3) & 0x1F
        return (((r << 11) | (g << 5) | b).astype("<u2")).tobytes()
    if bpp == 32:
        alpha = np.full((*image_bgr.shape[:2], 1), 255, dtype=np.uint8)
        return np.concatenate((image_bgr, alpha), axis=2).tobytes()
    raise SystemExit(f"Unsupported framebuffer depth: {bpp} bpp")


def make_pattern(width: int, height: int) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    colors = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 0, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
        (255, 255, 255),
        (0, 0, 0),
    ]
    stripe_w = max(1, width // len(colors))
    for idx, color in enumerate(colors):
        x0 = idx * stripe_w
        x1 = width if idx == len(colors) - 1 else (idx + 1) * stripe_w
        image[:, x0:x1] = color

    cv2.rectangle(image, (0, 0), (width - 1, height - 1), (255, 255, 255), 2)
    cv2.putText(
        image,
        f"LCD {width}x{height}",
        (20, max(40, height // 2 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 0, 0),
        3,
        cv2.LINE_AA,
    )
    cv2.putText(
        image,
        "MPI3501 / XPT2046",
        (20, max(75, height // 2 + 30)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fbdev", default="/dev/fb0")
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()

    width, height, bpp = framebuffer_info(args.fbdev)
    image = make_pattern(width, height)
    payload = bgr_to_framebuffer_payload(image, bpp)
    with open(args.fbdev, "r+b", buffering=0) as fb:
        fb.seek(0)
        fb.write(payload)

    print(f"Wrote LCD test pattern to {args.fbdev}: {width}x{height}, {bpp}bpp")
    if args.duration > 0:
        time.sleep(args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
