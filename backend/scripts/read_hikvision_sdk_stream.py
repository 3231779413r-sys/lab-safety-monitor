from __future__ import annotations

import argparse
import hashlib
import re
import socket
import sys
import time
from pathlib import Path

import cv2

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.hikvision_sdk_source import (
    HikvisionSdkError,
    HikvisionSdkFrameSource,
)

DEFAULT_SDK_PORT = 8000
DEFAULT_SDK_DIR = (
    ROOT_DIR / "HCNetSDKV6.1.11.5_build20251204_linux64_ZH" / "库文件"
)

try:
    from backend.app.core.config import settings as project_settings
except Exception:
    project_settings = None


def default_sdk_port() -> int:
    if project_settings is None:
        return DEFAULT_SDK_PORT
    return int(getattr(project_settings, "HIKVISION_SDK_PORT", DEFAULT_SDK_PORT))


def default_sdk_dir() -> str:
    if project_settings is None:
        return str(DEFAULT_SDK_DIR)
    return str(getattr(project_settings, "HIKVISION_SDK_DIR", DEFAULT_SDK_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read frames from a Hikvision camera through the same HCNetSDK path used by the project."
    )
    parser.add_argument("--host", required=True, help="Camera IP address or hostname.")
    parser.add_argument(
        "--port",
        type=int,
        default=default_sdk_port(),
        help="SDK login port, usually 8000.",
    )
    parser.add_argument("--username", required=True, help="Camera username.")
    parser.add_argument("--password", required=True, help="Camera password.")
    parser.add_argument("--channel", type=int, default=1, help="Camera channel number.")
    parser.add_argument(
        "--stream-type",
        choices=["main", "sub"],
        default="sub",
        help="Preview stream type.",
    )
    parser.add_argument(
        "--sdk-dir",
        default=default_sdk_dir(),
        help="Directory containing the Hikvision SDK shared libraries.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=100,
        help="Number of read attempts before exiting.",
    )
    parser.add_argument(
        "--read-timeout",
        type=float,
        default=1.0,
        help="Seconds to wait for a frame on each read.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.04,
        help="Seconds to sleep between reads.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the live video in an OpenCV window. Press q to quit.",
    )
    parser.add_argument(
        "--window-name",
        default="Hikvision SDK Live",
        help="Window title used with --show.",
    )
    return parser.parse_args()


def frame_digest(frame) -> str:
    return hashlib.sha1(frame.tobytes()).hexdigest()[:12]


def sdk_error_hint(message: str) -> str | None:
    match = re.search(r"failed:\s*(\d+)", message)
    if not match:
        return None
    code = int(match.group(1))
    hints = {
        1: "Username or password is incorrect.",
        2: "The account does not have permission.",
        3: "SDK was not initialized correctly.",
        4: "Channel number is invalid.",
        5: "Device is overloaded.",
        6: "SDK and device protocol versions do not match.",
        7: "Cannot connect to the device SDK service. Check IP, SDK port, routing, firewall, and whether the camera exposes port 8000.",
        8: "Failed to send data to the device.",
        9: "Failed to receive data from the device.",
        10: "Receiving data from the device timed out.",
    }
    return hints.get(code)


def preflight_connect(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"tcp connect ok to {host}:{port}"
    except Exception as exc:
        return False, f"tcp connect failed to {host}:{port}: {exc}"


def main() -> int:
    args = parse_args()
    source = HikvisionSdkFrameSource(
        sdk_dir=args.sdk_dir,
        host=args.host,
        username=args.username,
        password=args.password,
        port=args.port,
        channel=args.channel,
        stream_type=args.stream_type,
        link_mode=0,
    )

    last_digest: str | None = None
    changed_count = 0
    repeated_count = 0

    try:
        ok, message = preflight_connect(args.host, args.port, timeout=3.0)
        print(f"[PREFLIGHT] {message}")
        if not ok:
            return 1

        source.open()
        print(
            f"[OPENED] host={args.host} port={args.port} channel={args.channel} stream={args.stream_type}"
        )

        for index in range(1, args.frames + 1):
            frame = source.read_latest_frame(timeout=args.read_timeout)
            if frame is None:
                print(f"[READ {index}] no frame")
            else:
                digest = frame_digest(frame)
                changed = digest != last_digest
                if changed:
                    changed_count += 1
                else:
                    repeated_count += 1
                height, width = frame.shape[:2]
                print(
                    f"[READ {index}] ok size={width}x{height} digest={digest} changed={'yes' if changed else 'no'}"
                )
                last_digest = digest
                if args.show:
                    cv2.imshow(args.window_name, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        print("[STOP] window closed by user input")
                        break
            if index < args.frames:
                time.sleep(max(0.0, args.interval))

    except HikvisionSdkError as exc:
        print(f"[FAIL] {exc}")
        hint = sdk_error_hint(str(exc))
        if hint:
            print(f"[HINT] {hint}")
        return 1
    except KeyboardInterrupt:
        print("[STOP] interrupted")
        return 130
    except Exception as exc:
        print(f"[FAIL] Unexpected error: {exc}")
        return 1
    finally:
        source.close()
        if args.show:
            cv2.destroyAllWindows()

    print(
        f"[DONE] reads={args.frames} changed={changed_count} repeated={repeated_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
