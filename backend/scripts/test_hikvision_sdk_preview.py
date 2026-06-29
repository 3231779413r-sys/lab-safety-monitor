from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.core.config import settings
from backend.app.services.hikvision_sdk_source import HikvisionSdkError, save_one_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Login to a Hikvision camera with HCNetSDK, decode preview stream, and save one JPG frame."
    )
    parser.add_argument("--host", required=True, help="Camera IP address or hostname.")
    parser.add_argument("--port", type=int, default=settings.HIKVISION_SDK_PORT, help="SDK login port, usually 8000.")
    parser.add_argument("--username", required=True, help="Camera username.")
    parser.add_argument("--password", required=True, help="Camera password.")
    parser.add_argument("--channel", type=int, default=1, help="Camera channel number.")
    parser.add_argument(
        "--stream-type",
        choices=["main", "sub"],
        default="sub",
        help="Preview stream type. Use sub for lower bandwidth.",
    )
    parser.add_argument(
        "--sdk-dir",
        default=str(settings.HIKVISION_SDK_DIR),
        help="Directory containing HCNetSDK.dll and PlayCtrl.dll.",
    )
    parser.add_argument(
        "--output",
        default=str(settings.DATA_DIR / "sdk_test_frame.jpg"),
        help="Output JPG path.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for a decoded frame.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = save_one_frame(
            sdk_dir=args.sdk_dir,
            host=args.host,
            username=args.username,
            password=args.password,
            output=args.output,
            port=args.port,
            channel=args.channel,
            stream_type=args.stream_type,
            timeout=args.timeout,
        )
    except HikvisionSdkError as exc:
        print(f"[FAIL] {exc}")
        return 1
    except Exception as exc:
        print(f"[FAIL] Unexpected error: {exc}")
        return 1

    print(f"[OK] Saved decoded Hikvision SDK frame to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
