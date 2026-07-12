#!/usr/bin/env python3
"""
OpenCV-only real-time digit recognition for a Raspberry Pi CSI camera.

The default pipeline is tuned for black digits on a white background:
threshold -> contour candidates -> OpenCV KNN classifier trained from synthetic
0-9 templates -> display and command-line output.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from picamera2 import Picamera2

try:
    import serial
except ImportError:  # pyserial is only needed when --serial-device is used.
    serial = None


MODEL_SIZE = 32
TEMPLATE_CANVAS = 96
FONTS = [
    cv2.FONT_HERSHEY_SIMPLEX,
    cv2.FONT_HERSHEY_PLAIN,
    cv2.FONT_HERSHEY_DUPLEX,
    cv2.FONT_HERSHEY_COMPLEX,
    cv2.FONT_HERSHEY_TRIPLEX,
]


@dataclass
class Detection:
    digit: str
    distance: float
    x: int
    y: int
    w: int
    h: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenCV-only CSI camera digit recognition.")
    parser.add_argument("--width", type=int, default=640, help="Camera width.")
    parser.add_argument("--height", type=int, default=480, help="Camera height.")
    parser.add_argument(
        "--threshold",
        choices=("adaptive", "otsu"),
        default="adaptive",
        help="Threshold method for black digits on white background.",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=35,
        help="Odd adaptive-threshold block size. Larger handles slower lighting changes.",
    )
    parser.add_argument(
        "--c",
        type=int,
        default=11,
        help="Adaptive-threshold constant. Larger values keep thinner strokes.",
    )
    parser.add_argument("--min-height", type=int, default=24, help="Minimum digit height in pixels.")
    parser.add_argument("--min-width", type=int, default=6, help="Minimum digit width in pixels.")
    parser.add_argument(
        "--max-height",
        type=int,
        default=180,
        help="Maximum digit height in pixels. Increase this if the digit is very close.",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=160,
        help="Maximum digit width in pixels. Increase this if the digit is very close.",
    )
    parser.add_argument("--min-area", type=int, default=80, help="Minimum contour area.")
    parser.add_argument("--max-distance", type=float, default=0.30, help="Reject KNN matches above this distance.")
    parser.add_argument(
        "--target-confidence-weight",
        type=float,
        default=0.35,
        help=(
            "How much classifier confidence affects the single displayed target. "
            "The target is primarily chosen by distance to screen center; lower KNN distance means higher confidence."
        ),
    )
    parser.add_argument("--k", type=int, default=3, help="KNN neighbor count.")
    parser.add_argument(
        "--roi",
        default="",
        help="Optional crop as x,y,w,h. Use this if the digits are in a fixed screen area.",
    )
    parser.add_argument("--debug", action="store_true", help="Show threshold and candidate windows.")
    parser.add_argument("--no-window", action="store_true", help="Do not open OpenCV display windows.")
    parser.add_argument(
        "--fbdev",
        default="",
        help="Write the annotated frame directly to a Linux framebuffer, e.g. /dev/fb0.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Exit after this many seconds. Use 0 to run until q/Ctrl+C.",
    )
    parser.add_argument(
        "--image",
        default="",
        help="Process one image instead of opening the camera. Useful for tuning.",
    )
    parser.add_argument(
        "--save-debug",
        action="store_true",
        help="Save the latest annotated frame and threshold image when pressing s.",
    )
    parser.add_argument(
        "--serial-device",
        default="",
        help="UART device for STM32 output, for example /dev/serial0. Disabled by default.",
    )
    parser.add_argument("--serial-baud", type=int, default=115200, help="UART baud rate.")
    parser.add_argument(
        "--serial-interval",
        type=float,
        default=0.10,
        help="Minimum seconds between UART messages.",
    )
    parser.add_argument(
        "--no-serial-empty",
        action="store_true",
        help="Do not send -1,0,0 when no digit is detected.",
    )
    parser.add_argument(
        "--invert-serial-y",
        action="store_true",
        help="Use math-style Y offset, positive above center. Default is OpenCV Y, positive below center.",
    )
    return parser.parse_args()


def parse_roi(raw: str) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    try:
        parts = [int(part.strip()) for part in raw.split(",")]
    except ValueError as exc:
        raise SystemExit("--roi must be x,y,w,h") from exc
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise SystemExit("--roi must be x,y,w,h with positive width and height")
    return tuple(parts)  # type: ignore[return-value]


def ensure_odd(value: int) -> int:
    value = max(3, value)
    return value if value % 2 else value + 1


def center_digit(binary: np.ndarray) -> np.ndarray:
    """Crop foreground and center it on a fixed black canvas."""
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((MODEL_SIZE, MODEL_SIZE), dtype=np.uint8)

    x, y, w, h = cv2.boundingRect(points)
    crop = binary[y : y + h, x : x + w]
    scale = min((MODEL_SIZE - 8) / max(w, 1), (MODEL_SIZE - 8) / max(h, 1))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((MODEL_SIZE, MODEL_SIZE), dtype=np.uint8)
    x0 = (MODEL_SIZE - new_w) // 2
    y0 = (MODEL_SIZE - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def make_hog() -> cv2.HOGDescriptor:
    return cv2.HOGDescriptor(
        (MODEL_SIZE, MODEL_SIZE),
        (16, 16),
        (8, 8),
        (8, 8),
        9,
    )


def feature_from_binary(binary: np.ndarray, hog: cv2.HOGDescriptor) -> np.ndarray:
    centered = center_digit(binary)
    centered = cv2.GaussianBlur(centered, (3, 3), 0)
    feature = hog.compute(centered).reshape(1, -1).astype(np.float32)
    norm = np.linalg.norm(feature)
    if norm > 0:
        feature /= norm
    return feature


def render_template(digit: int, font: int, scale: float, thickness: int, angle: float, shift: tuple[int, int]) -> np.ndarray:
    image = np.zeros((TEMPLATE_CANVAS, TEMPLATE_CANVAS), dtype=np.uint8)
    text = str(digit)
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    x = (TEMPLATE_CANVAS - tw) // 2 + shift[0]
    y = (TEMPLATE_CANVAS + th) // 2 + shift[1]
    cv2.putText(image, text, (x, y), font, scale, 255, thickness, cv2.LINE_AA)

    if angle:
        matrix = cv2.getRotationMatrix2D((TEMPLATE_CANVAS / 2, TEMPLATE_CANVAS / 2), angle, 1.0)
        image = cv2.warpAffine(image, matrix, (TEMPLATE_CANVAS, TEMPLATE_CANVAS), borderValue=0)

    _, image = cv2.threshold(image, 40, 255, cv2.THRESH_BINARY)
    return image


def train_knn(k: int) -> tuple[cv2.ml_KNearest, cv2.HOGDescriptor]:
    hog = make_hog()
    samples: list[np.ndarray] = []
    labels: list[int] = []
    scales = (1.7, 2.1, 2.5)
    thicknesses = (2, 3, 4)
    angles = (-8.0, -4.0, 0.0, 4.0, 8.0)
    shifts = ((0, 0), (-4, 0), (4, 0), (0, -4), (0, 4))
    morphs = ("none", "dilate", "erode")
    kernel = np.ones((2, 2), np.uint8)

    for digit in range(10):
        for font in FONTS:
            for scale in scales:
                for thickness in thicknesses:
                    for angle in angles:
                        for shift in shifts:
                            image = render_template(digit, font, scale, thickness, angle, shift)
                            for morph in morphs:
                                variant = image
                                if morph == "dilate":
                                    variant = cv2.dilate(image, kernel, iterations=1)
                                elif morph == "erode":
                                    variant = cv2.erode(image, kernel, iterations=1)
                                samples.append(feature_from_binary(variant, hog))
                                labels.append(digit)

    train_data = np.vstack(samples).astype(np.float32)
    responses = np.array(labels, dtype=np.float32).reshape(-1, 1)
    knn = cv2.ml.KNearest_create()
    knn.setDefaultK(k)
    knn.setIsClassifier(True)
    knn.train(train_data, cv2.ml.ROW_SAMPLE, responses)
    return knn, hog


def threshold_frame(gray: np.ndarray, method: str, block_size: int, c_value: int) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    if method == "otsu":
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        binary = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            ensure_odd(block_size),
            c_value,
        )

    open_kernel = np.ones((2, 2), np.uint8)
    close_kernel = np.ones((3, 3), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, open_kernel, iterations=1)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_kernel, iterations=1)
    return binary


def split_wide_candidate(binary: np.ndarray, x: int, y: int, w: int, h: int) -> list[tuple[int, int, int, int]]:
    if w / max(h, 1) < 1.15:
        return [(x, y, w, h)]

    roi = binary[y : y + h, x : x + w]
    projection = np.count_nonzero(roi, axis=0)
    min_gap = max(1, h // 18)
    is_gap = projection <= min_gap
    segments: list[tuple[int, int]] = []
    start: int | None = None

    for idx, gap in enumerate(is_gap):
        if not gap and start is None:
            start = idx
        elif gap and start is not None:
            if idx - start >= max(4, h // 8):
                segments.append((start, idx))
            start = None
    if start is not None and w - start >= max(4, h // 8):
        segments.append((start, w))

    boxes: list[tuple[int, int, int, int]] = []
    for sx, ex in segments:
        sub = roi[:, sx:ex]
        pts = cv2.findNonZero(sub)
        if pts is None:
            continue
        bx, by, bw, bh = cv2.boundingRect(pts)
        boxes.append((x + sx + bx, y + by, bw, bh))

    return boxes if len(boxes) >= 2 else [(x, y, w, h)]


def candidate_boxes(binary: np.ndarray, args: argparse.Namespace) -> list[tuple[int, int, int, int]]:
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[int, int, int, int]] = []
    frame_h, frame_w = binary.shape[:2]

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)
        if h < args.min_height or w < args.min_width or area < args.min_area:
            continue
        if h > args.max_height or w > args.max_width:
            continue
        if h > frame_h * 0.9 or w > frame_w * 0.9:
            continue

        aspect = w / max(h, 1)
        if aspect < 0.10 or aspect > 8.0:
            continue

        for box in split_wide_candidate(binary, x, y, w, h):
            bx, by, bw, bh = box
            if (
                bh >= args.min_height
                and bw >= args.min_width
                and bh <= args.max_height
                and bw <= args.max_width
            ):
                boxes.append(box)

    boxes.sort(key=lambda b: (b[1] // 45, b[0]))
    return boxes


def classify_candidate(
    binary: np.ndarray,
    box: tuple[int, int, int, int],
    knn: cv2.ml_KNearest,
    hog: cv2.HOGDescriptor,
    args: argparse.Namespace,
) -> Detection | None:
    x, y, w, h = box
    pad = max(3, int(round(max(w, h) * 0.12)))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(binary.shape[1], x + w + pad)
    y1 = min(binary.shape[0], y + h + pad)
    roi = binary[y0:y1, x0:x1]

    feature = feature_from_binary(roi, hog)
    _, result, _, distances = knn.findNearest(feature, args.k)
    digit = str(int(result[0, 0]))
    distance = float(np.mean(distances[0])) if distances.size else 0.0
    if distance > args.max_distance:
        return None
    return Detection(digit, distance, x, y, w, h)


def detect_digits(
    frame_bgr: np.ndarray,
    knn: cv2.ml_KNearest,
    hog: cv2.HOGDescriptor,
    args: argparse.Namespace,
    roi: tuple[int, int, int, int] | None,
) -> tuple[list[Detection], np.ndarray]:
    if roi:
        rx, ry, rw, rh = roi
        work = frame_bgr[ry : ry + rh, rx : rx + rw]
    else:
        rx = ry = 0
        work = frame_bgr

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    binary = threshold_frame(gray, args.threshold, args.block_size, args.c)
    detections: list[Detection] = []

    for box in candidate_boxes(binary, args):
        det = classify_candidate(binary, box, knn, hog, args)
        if det is None:
            continue
        detections.append(
            Detection(det.digit, det.distance, det.x + rx, det.y + ry, det.w, det.h)
        )

    detections.sort(key=lambda d: (d.y // 45, d.x))
    return detections, binary


def center_offset(det: Detection, frame_shape: tuple[int, ...]) -> tuple[int, int, float]:
    frame_h, frame_w = frame_shape[:2]
    target_x = det.x + det.w / 2.0
    target_y = det.y + det.h / 2.0
    dx = int(round(target_x - frame_w / 2.0))
    dy = int(round(target_y - frame_h / 2.0))
    center_distance = float(np.hypot(dx, dy))
    return dx, dy, center_distance


def target_detection(
    detections: list[Detection],
    frame_shape: tuple[int, ...],
    args: argparse.Namespace,
) -> Detection | None:
    if not detections:
        return None

    frame_h, frame_w = frame_shape[:2]
    half_diag = max(1.0, float(np.hypot(frame_w / 2.0, frame_h / 2.0)))
    max_distance = max(1e-6, float(args.max_distance))
    confidence_weight = max(0.0, float(args.target_confidence_weight))

    def score(det: Detection) -> tuple[float, float, float]:
        _, _, center_distance = center_offset(det, frame_shape)
        center_score = center_distance / half_diag
        confidence_score = det.distance / max_distance
        return (center_score + confidence_weight * confidence_score, center_score, det.distance)

    return min(detections, key=score)


def draw_results(
    frame_bgr: np.ndarray,
    target: Detection | None,
    roi: tuple[int, int, int, int] | None,
    fps: float | None = None,
) -> None:
    if roi:
        rx, ry, rw, rh = roi
        cv2.rectangle(frame_bgr, (rx, ry), (rx + rw, ry + rh), (255, 180, 0), 2)

    frame_h, frame_w = frame_bgr.shape[:2]
    cv2.drawMarker(
        frame_bgr,
        (frame_w // 2, frame_h // 2),
        (0, 180, 255),
        cv2.MARKER_CROSS,
        24,
        2,
        cv2.LINE_AA,
    )

    fps_text = f"FPS: {fps:.1f}" if fps is not None and fps > 0 else "FPS: -"
    cv2.putText(
        frame_bgr,
        fps_text,
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 180, 255),
        2,
        cv2.LINE_AA,
    )

    if target is None:
        cv2.putText(
            frame_bgr,
            "-",
            (12, 96),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.0,
            (0, 0, 255),
            4,
            cv2.LINE_AA,
        )
        return

    cv2.rectangle(frame_bgr, (target.x, target.y), (target.x + target.w, target.y + target.h), (0, 255, 0), 3)
    cv2.putText(
        frame_bgr,
        target.digit,
        (12, 96),
        cv2.FONT_HERSHEY_SIMPLEX,
        2.2,
        (0, 255, 0),
        5,
        cv2.LINE_AA,
    )

    label_y = max(24, target.y - 10)
    cv2.putText(
        frame_bgr,
        target.digit,
        (target.x, label_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )


class SerialOutput:
    def __init__(self, args: argparse.Namespace) -> None:
        self.enabled = bool(args.serial_device)
        self.interval = max(0.02, float(args.serial_interval))
        self.send_empty = not args.no_serial_empty
        self.invert_y = bool(args.invert_serial_y)
        self.last_sent_at = 0.0

        self.port = None
        if not self.enabled:
            return

        if serial is None:
            raise SystemExit("pyserial is not installed. Install it with: python -m pip install pyserial")

        self.port = serial.serial_for_url(
            args.serial_device,
            baudrate=args.serial_baud,
            timeout=0,
            write_timeout=0.1,
        )
        print(f"Serial output enabled: {args.serial_device} @ {args.serial_baud}")
        print("UART protocol: digit,dx,dy\\n ; no detection is -1,0,0")

    def close(self) -> None:
        if self.port is not None:
            self.port.close()

    def maybe_send(self, target: Detection | None, frame_shape: tuple[int, ...]) -> None:
        if not self.enabled or self.port is None:
            return

        now = time.monotonic()
        if now - self.last_sent_at < self.interval:
            return

        if target is None:
            if not self.send_empty:
                return
            digit, dx, dy = -1, 0, 0
        else:
            digit = int(target.digit)
            dx, dy, _ = center_offset(target, frame_shape)
            if self.invert_y:
                dy = -dy

        line = f"{digit},{dx},{dy}\n"
        self.port.write(line.encode("ascii"))
        self.last_sent_at = now


class FramebufferDisplay:
    def __init__(self, fbdev: str) -> None:
        self.fbdev = fbdev
        fb_name = Path(fbdev).name
        sysfs = Path("/sys/class/graphics") / fb_name
        size_text = (sysfs / "virtual_size").read_text(encoding="ascii").strip()
        bpp_text = (sysfs / "bits_per_pixel").read_text(encoding="ascii").strip()
        self.width, self.height = [int(part) for part in size_text.split(",")]
        self.bpp = int(bpp_text)
        if self.bpp not in (16, 32):
            raise SystemExit(f"Unsupported framebuffer depth: {self.bpp} bpp")
        self.frame_bytes = self.width * self.height * (self.bpp // 8)
        self.handle = open(fbdev, "r+b", buffering=0)
        print(f"Framebuffer output enabled: {fbdev} {self.width}x{self.height} {self.bpp}bpp")

    def close(self) -> None:
        self.handle.close()

    def show(self, frame_bgr: np.ndarray) -> None:
        resized = cv2.resize(frame_bgr, (self.width, self.height), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        if self.bpp == 16:
            r = (rgb[:, :, 0].astype(np.uint16) >> 3) & 0x1F
            g = (rgb[:, :, 1].astype(np.uint16) >> 2) & 0x3F
            b = (rgb[:, :, 2].astype(np.uint16) >> 3) & 0x1F
            packed = ((r << 11) | (g << 5) | b).astype("<u2")
            payload = packed.tobytes()
        else:
            alpha = np.full((self.height, self.width, 1), 255, dtype=np.uint8)
            bgra = np.concatenate((resized, alpha), axis=2)
            payload = bgra.tobytes()

        if len(payload) != self.frame_bytes:
            raise RuntimeError("Framebuffer payload size mismatch")
        self.handle.seek(0)
        self.handle.write(payload)


def print_target(target: Detection | None, frame_shape: tuple[int, ...], last_printed: str) -> str:
    if target is None:
        return ""

    dx, dy, center_distance = center_offset(target, frame_shape)
    status = f"{target.digit}:{dx}:{dy}:{target.distance:.2f}"
    if status != last_printed:
        print(
            f"[{time.strftime('%H:%M:%S')}] target: {target.digit} "
            f"dx={dx} dy={dy} center={center_distance:.1f}px distance={target.distance:.2f}",
            flush=True,
        )
        return status
    return last_printed


def process_image(path: str, knn: cv2.ml_KNearest, hog: cv2.HOGDescriptor, args: argparse.Namespace) -> int:
    frame = cv2.imread(path)
    if frame is None:
        print(f"Could not read image: {path}")
        return 1

    roi = parse_roi(args.roi)
    detections, binary = detect_digits(frame, knn, hog, args, roi)
    target = target_detection(detections, frame.shape, args)
    draw_results(frame, target, roi)
    print_target(target, frame.shape, "")

    out_path = Path(path).with_name(Path(path).stem + "_opencv_digits.jpg")
    cv2.imwrite(str(out_path), frame)
    if args.save_debug:
        cv2.imwrite(str(Path(path).with_name(Path(path).stem + "_threshold.jpg")), binary)
    print(f"Saved annotated image: {out_path}")
    return 0


def run_camera(knn: cv2.ml_KNearest, hog: cv2.HOGDescriptor, args: argparse.Namespace) -> int:
    roi = parse_roi(args.roi)
    show_window = not args.no_window
    serial_output = SerialOutput(args)
    fb_display = FramebufferDisplay(args.fbdev) if args.fbdev else None

    print("Starting CSI camera...")
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"format": "RGB888", "size": (args.width, args.height)}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(1.2)

    started_at = time.monotonic()
    last_frame_at = started_at
    fps_ema: float | None = None
    last_printed = ""
    binary = np.zeros((args.height, args.width), dtype=np.uint8)

    print("OpenCV digit recognition is running. Press q to quit, s to save debug images.")
    print(
        f"Options: {args.width}x{args.height}, threshold={args.threshold}, "
        f"min_height={args.min_height}, max_distance={args.max_distance:.2f}"
    )

    try:
        while True:
            frame_rgb = picam2.capture_array()
            now = time.monotonic()
            frame_dt = now - last_frame_at
            last_frame_at = now
            if frame_dt > 0:
                instant_fps = 1.0 / frame_dt
                fps_ema = instant_fps if fps_ema is None else (0.85 * fps_ema + 0.15 * instant_fps)

            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
            detections, binary = detect_digits(frame_bgr, knn, hog, args, roi)
            target = target_detection(detections, frame_bgr.shape, args)
            last_printed = print_target(target, frame_bgr.shape, last_printed)
            serial_output.maybe_send(target, frame_bgr.shape)
            draw_results(frame_bgr, target, roi, fps_ema)
            if fb_display is not None:
                fb_display.show(frame_bgr)

            if show_window:
                cv2.imshow("OpenCV digit recognition - press q to quit", frame_bgr)
                if args.debug:
                    cv2.imshow("OpenCV threshold", binary)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s") and args.save_debug:
                    cv2.imwrite("/home/intyu/Desktop/opencv_digits_frame.jpg", frame_bgr)
                    cv2.imwrite("/home/intyu/Desktop/opencv_digits_threshold.jpg", binary)
                    print("Saved debug images to /home/intyu/Desktop", flush=True)
            else:
                time.sleep(0.03)

            if args.duration > 0 and time.monotonic() - started_at >= args.duration:
                print(f"Reached duration limit: {args.duration:.1f}s")
                break
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        if show_window:
            cv2.destroyAllWindows()
        if fb_display is not None:
            fb_display.close()
        serial_output.close()
        picam2.stop()
        print("Camera stopped.")

    return 0


def main() -> int:
    args = parse_args()
    print("Training OpenCV digit classifier...")
    knn, hog = train_knn(args.k)
    print("Classifier ready.")

    if args.image:
        return process_image(args.image, knn, hog, args)
    return run_camera(knn, hog, args)


if __name__ == "__main__":
    raise SystemExit(main())
