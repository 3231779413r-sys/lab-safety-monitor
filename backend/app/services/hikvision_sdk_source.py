from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..core.config import settings

logger = logging.getLogger(__name__)

_SDK_INIT_LOCK = threading.Lock()
_SDK_INITIALIZED = False

NET_DVR_SYSHEAD = 1
NET_DVR_STREAMDATA = 2
NET_DVR_GET_COMPRESSCFG_V30 = 1040
NET_DVR_SET_COMPRESSCFG_V30 = 1041
NET_DVR_GET_MULTI_STREAM_COMPRESSIONCFG = 3216
NET_DVR_SET_MULTI_STREAM_COMPRESSIONCFG = 3217
STREAM_ID_LEN = 32

SDK_FRAME_RATE_TO_FPS = {
    0: None,
    1: 1 / 16,
    2: 1 / 8,
    3: 1 / 4,
    4: 1 / 2,
    5: 1,
    6: 2,
    7: 4,
    8: 6,
    9: 8,
    10: 10,
    11: 12,
    12: 16,
    13: 20,
    14: 15,
    15: 18,
    16: 22,
    17: 25,
    18: 30,
    19: 35,
    20: 40,
    21: 45,
    22: 50,
    23: 55,
    24: 60,
    25: 3,
    26: 5,
    27: 7,
    28: 9,
    29: 100,
    30: 120,
    31: 24,
    32: 48,
    34: 75,
    35: 90,
    37: 150,
    38: 180,
    39: 200,
    40: 210,
}
FPS_TO_SDK_FRAME_RATE = {
    value: key for key, value in SDK_FRAME_RATE_TO_FPS.items() if value is not None
}

SDK_BITRATE_TO_KBPS = {
    15: 512,
    16: 640,
    17: 768,
    18: 896,
    19: 1024,
    20: 1280,
    21: 1536,
    22: 1792,
    23: 2048,
    24: 3072,
    25: 4096,
    26: 8192,
    27: 16384,
    31: 12288,
}
KBPS_TO_SDK_BITRATE = {value: key for key, value in SDK_BITRATE_TO_KBPS.items()}

SDK_ENCODING_TO_NAME = {
    1: "H.264",
    10: "H.265",
}
ENCODING_TO_SDK = {value: key for key, value in SDK_ENCODING_TO_NAME.items()}

SDK_RESOLUTION_TO_NAME = {
    1: "352x288",
    2: "176x144",
    3: "704x576",
    6: "320x240",
    16: "640x480",
    18: "800x600",
    19: "1280x720",
    27: "1920x1080",
    28: "2560x1920",
    39: "1920x1080",
    67: "1920x1080",
    70: "2560x1440",
    83: "3840x2160",
    202: "2688x1512",
    215: "1080x720",
    402: "960x432",
    438: "720x576",
    439: "704x576",
    499: "1024x576",
    616: "480x360",
}
RESOLUTION_TO_SDK = {
    "704x576": 3,
    "640x480": 16,
    "1280x720": 19,
    "1920x1080": 27,
    "2560x1440": 70,
    "2560x1920": 28,
}


if os.name == "nt":
    CALLBACK_TYPE = ctypes.WINFUNCTYPE
else:
    CALLBACK_TYPE = ctypes.CFUNCTYPE


def _sdk_hwnd_type():
    # Hikvision's Linux SDK documents HWND as an unsigned int instead of a pointer.
    return ctypes.c_void_p if os.name == "nt" else ctypes.c_uint


class NET_DVR_LOCAL_SDK_PATH(ctypes.Structure):
    _fields_ = [
        ("sPath", ctypes.c_char * 256),
        ("byRes", ctypes.c_byte * 128),
    ]


class NET_DVR_DEVICEINFO_V30(ctypes.Structure):
    _fields_ = [
        ("sSerialNumber", ctypes.c_byte * 48),
        ("byAlarmInPortNum", ctypes.c_byte),
        ("byAlarmOutPortNum", ctypes.c_byte),
        ("byDiskNum", ctypes.c_byte),
        ("byDVRType", ctypes.c_byte),
        ("byChanNum", ctypes.c_byte),
        ("byStartChan", ctypes.c_byte),
        ("byAudioChanNum", ctypes.c_byte),
        ("byIPChanNum", ctypes.c_byte),
        ("byZeroChanNum", ctypes.c_byte),
        ("byMainProto", ctypes.c_byte),
        ("bySubProto", ctypes.c_byte),
        ("bySupport", ctypes.c_byte),
        ("bySupport1", ctypes.c_byte),
        ("bySupport2", ctypes.c_byte),
        ("wDevType", ctypes.c_uint16),
        ("bySupport3", ctypes.c_byte),
        ("byMultiStreamProto", ctypes.c_byte),
        ("byStartDChan", ctypes.c_byte),
        ("byStartDTalkChan", ctypes.c_byte),
        ("byHighDChanNum", ctypes.c_byte),
        ("bySupport4", ctypes.c_byte),
        ("byLanguageType", ctypes.c_byte),
        ("byVoiceInChanNum", ctypes.c_byte),
        ("byStartVoiceInChanNo", ctypes.c_byte),
        ("bySupport5", ctypes.c_byte),
        ("bySupport6", ctypes.c_byte),
        ("byMirrorChanNum", ctypes.c_byte),
        ("wStartMirrorChanNo", ctypes.c_uint16),
        ("bySupport7", ctypes.c_byte),
        ("byRes2", ctypes.c_byte),
    ]


class NET_DVR_PREVIEWINFO(ctypes.Structure):
    _fields_ = [
        ("lChannel", ctypes.c_uint32),
        ("dwStreamType", ctypes.c_uint32),
        ("dwLinkMode", ctypes.c_uint32),
        ("hPlayWnd", _sdk_hwnd_type()),
        ("bBlocked", ctypes.c_uint32),
        ("bPassbackRecord", ctypes.c_uint32),
        ("byPreviewMode", ctypes.c_ubyte),
        ("byStreamID", ctypes.c_ubyte * 32),
        ("byProtoType", ctypes.c_ubyte),
        ("byRes1", ctypes.c_ubyte),
        ("byVideoCodingType", ctypes.c_ubyte),
        ("dwDisplayBufNum", ctypes.c_uint32),
        ("byNPQMode", ctypes.c_ubyte),
        ("byRecvMetaData", ctypes.c_ubyte),
        ("byDataType", ctypes.c_ubyte),
        ("byRes", ctypes.c_ubyte * 213),
    ]


class FRAME_INFO(ctypes.Structure):
    _fields_ = [
        ("nWidth", ctypes.c_uint32),
        ("nHeight", ctypes.c_uint32),
        ("nStamp", ctypes.c_uint32),
        ("nType", ctypes.c_uint32),
        ("nFrameRate", ctypes.c_uint32),
        ("dwFrameNum", ctypes.c_uint32),
    ]


class NET_DVR_COMPRESSION_INFO_V30(ctypes.Structure):
    _fields_ = [
        ("byStreamType", ctypes.c_ubyte),
        ("byResolution", ctypes.c_ubyte),
        ("byBitrateType", ctypes.c_ubyte),
        ("byPicQuality", ctypes.c_ubyte),
        ("dwVideoBitrate", ctypes.c_uint32),
        ("dwVideoFrameRate", ctypes.c_uint32),
        ("wIntervalFrameI", ctypes.c_uint16),
        ("byIntervalBPFrame", ctypes.c_ubyte),
        ("byres1", ctypes.c_ubyte),
        ("byVideoEncType", ctypes.c_ubyte),
        ("byAudioEncType", ctypes.c_ubyte),
        ("byVideoEncComplexity", ctypes.c_ubyte),
        ("byEnableSvc", ctypes.c_ubyte),
        ("byFormatType", ctypes.c_ubyte),
        ("byAudioBitRate", ctypes.c_ubyte),
        ("byStreamSmooth", ctypes.c_ubyte),
        ("byAudioSamplingRate", ctypes.c_ubyte),
        ("bySmartCodec", ctypes.c_ubyte),
        ("byDepthMapEnable", ctypes.c_ubyte),
        ("wAverageVideoBitrate", ctypes.c_uint16),
    ]


class NET_DVR_COMPRESSIONCFG_V30(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("struNormHighRecordPara", NET_DVR_COMPRESSION_INFO_V30),
        ("struRes", NET_DVR_COMPRESSION_INFO_V30),
        ("struEventRecordPara", NET_DVR_COMPRESSION_INFO_V30),
        ("struNetPara", NET_DVR_COMPRESSION_INFO_V30),
    ]


class NET_DVR_STREAM_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("byID", ctypes.c_ubyte * STREAM_ID_LEN),
        ("dwChannel", ctypes.c_uint32),
        ("byRes", ctypes.c_ubyte * 32),
    ]


class NET_DVR_MULTI_STREAM_COMPRESSIONCFG_COND(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("struStreamInfo", NET_DVR_STREAM_INFO),
        ("dwStreamType", ctypes.c_uint32),
        ("byRes", ctypes.c_ubyte * 32),
    ]


class NET_DVR_MULTI_STREAM_COMPRESSIONCFG(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("dwStreamType", ctypes.c_uint32),
        ("struStreamPara", NET_DVR_COMPRESSION_INFO_V30),
        ("dwResolution", ctypes.c_uint32),
        ("byRes", ctypes.c_ubyte * 76),
    ]


REAL_DATA_CALLBACK = CALLBACK_TYPE(
    None,
    ctypes.c_long,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_uint32,
    ctypes.c_void_p,
)

DECODE_CALLBACK = CALLBACK_TYPE(
    None,
    ctypes.c_long,
    ctypes.POINTER(ctypes.c_char),
    ctypes.c_long,
    ctypes.POINTER(FRAME_INFO),
    ctypes.c_void_p,
    ctypes.c_void_p,
)


class HikvisionSdkError(RuntimeError):
    pass


def _sdk_encoding() -> str:
    return "gbk" if os.name == "nt" else "utf-8"


def _prepend_env_path(name: str, path: Path) -> None:
    current = os.environ.get(name)
    path_str = str(path)
    if not current:
        os.environ[name] = path_str
        return
    entries = current.split(os.pathsep)
    if path_str not in entries:
        os.environ[name] = os.pathsep.join([path_str, current])


def _hcnet_library_name() -> str:
    return "HCNetSDK.dll" if os.name == "nt" else "libhcnetsdk.so"


def _playctrl_library_name() -> str:
    return "PlayCtrl.dll" if os.name == "nt" else "libPlayCtrl.so"


def _configure_and_init_hcnet_sdk(hcnet, sdk_dir: Path) -> None:
    """Initialize HCNetSDK once per process.

    HCNetSDK cleanup is process-global. Calling NET_DVR_Cleanup from a short-lived
    config request can interrupt an active preview stream, so this module keeps
    the SDK initialized for the backend process lifetime.
    """
    global _SDK_INITIALIZED
    with _SDK_INIT_LOCK:
        if _SDK_INITIALIZED:
            return

        sdk_path = NET_DVR_LOCAL_SDK_PATH()
        encoded_path = str(sdk_dir).encode(_sdk_encoding())
        sdk_path.sPath = encoded_path
        hcnet.NET_DVR_SetSDKInitCfg(2, ctypes.byref(sdk_path))

        if os.name == "nt":
            crypto_path = sdk_dir / "libcrypto-3-x64.dll"
            ssl_path = sdk_dir / "libssl-3-x64.dll"
        else:
            crypto_path = sdk_dir / "libcrypto.so.3"
            ssl_path = sdk_dir / "libssl.so.3"
        if crypto_path.exists():
            hcnet.NET_DVR_SetSDKInitCfg(
                3,
                ctypes.create_string_buffer(str(crypto_path).encode(_sdk_encoding())),
            )
        if ssl_path.exists():
            hcnet.NET_DVR_SetSDKInitCfg(
                4,
                ctypes.create_string_buffer(str(ssl_path).encode(_sdk_encoding())),
            )

        if not hcnet.NET_DVR_Init():
            error = int(hcnet.NET_DVR_GetLastError())
            raise HikvisionSdkError(f"NET_DVR_Init failed: {error}")

        log_dir = sdk_dir / "SdkLog_Python"
        log_dir.mkdir(exist_ok=True)
        hcnet.NET_DVR_SetLogToFile(3, str(log_dir).encode(_sdk_encoding()), False)
        _SDK_INITIALIZED = True


class HikvisionSdkFrameSource:
    """Headless Hikvision HCNetSDK preview source that exposes decoded BGR frames."""

    def __init__(
        self,
        sdk_dir: str | Path,
        host: str,
        username: str,
        password: str,
        port: int = 8000,
        channel: int = 1,
        stream_type: str = "sub",
        link_mode: int = 0,
    ):
        self.sdk_dir = Path(sdk_dir)
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port)
        self.channel = int(channel)
        self.stream_type = stream_type
        self.link_mode = int(link_mode)

        self._hcnet = None
        self._play = None
        self._play_port = ctypes.c_long(-1)
        self._user_id = -1
        self._real_handle = -1
        self._opened = False
        self._frame_ready = threading.Event()
        self._lock = threading.Lock()
        self._frame_condition = threading.Condition(self._lock)
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_counter = 0
        self._real_data_cb = None
        self._decode_cb = None
        self.device_info: Optional[NET_DVR_DEVICEINFO_V30] = None
        self._decode_min_interval_seconds = 1.0 / max(
            1,
            int(getattr(settings, "HIKVISION_DECODE_TARGET_FPS", 10)),
        )
        self._last_decoded_at_monotonic = 0.0

    def open(self) -> None:
        if self._opened:
            return
        self._load_libraries()
        self._init_sdk()
        self._login()
        self._start_preview()
        self._opened = True

    def read_latest_frame(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        if timeout > 0:
            self._frame_ready.wait(timeout)
        with self._lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    def wait_for_new_frame(
        self,
        last_frame_counter: int = 0,
        timeout: float = 1.0,
    ) -> tuple[Optional[np.ndarray], int]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._frame_condition:
            while self._frame_counter <= last_frame_counter:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_condition.wait(remaining)
            frame = self._latest_frame.copy() if self._latest_frame is not None else None
            return frame, self._frame_counter

    def wait_for_frame(self, timeout: float = 10.0) -> Optional[np.ndarray]:
        if not self._frame_ready.wait(timeout):
            return None
        return self.read_latest_frame(timeout=0)

    def close(self) -> None:
        try:
            if self._hcnet is not None and self._real_handle >= 0:
                self._hcnet.NET_DVR_StopRealPlay(self._real_handle)
        finally:
            self._real_handle = -1
        try:
            if self._play is not None and self._play_port.value >= 0:
                self._play.PlayM4_Stop(self._play_port)
                self._play.PlayM4_CloseStream(self._play_port)
                self._play.PlayM4_FreePort(self._play_port)
        finally:
            self._play_port = ctypes.c_long(-1)
        try:
            if self._hcnet is not None and self._user_id >= 0:
                self._hcnet.NET_DVR_Logout(self._user_id)
        finally:
            self._user_id = -1
        self._opened = False

    def _load_libraries(self) -> None:
        if not self.sdk_dir.exists():
            raise HikvisionSdkError(f"Hikvision SDK dir does not exist: {self.sdk_dir}")
        if os.name == "nt":
            os.add_dll_directory(str(self.sdk_dir))
        else:
            _prepend_env_path("LD_LIBRARY_PATH", self.sdk_dir)
            hcnet_com_dir = self.sdk_dir / "HCNetSDKCom"
            if hcnet_com_dir.exists():
                _prepend_env_path("LD_LIBRARY_PATH", hcnet_com_dir)
        self._hcnet = ctypes.CDLL(str(self.sdk_dir / _hcnet_library_name()))
        self._play = ctypes.CDLL(str(self.sdk_dir / _playctrl_library_name()))

    def _init_sdk(self) -> None:
        _configure_and_init_hcnet_sdk(self._hcnet, self.sdk_dir)
        if not self._play.PlayM4_GetPort(ctypes.byref(self._play_port)):
            raise HikvisionSdkError(f"PlayM4_GetPort failed: {self._last_play_error()}")

    def _login(self) -> None:
        device_info = NET_DVR_DEVICEINFO_V30()
        self._user_id = self._hcnet.NET_DVR_Login_V30(
            ctypes.create_string_buffer(self.host.encode()),
            self.port,
            ctypes.create_string_buffer(self.username.encode()),
            ctypes.create_string_buffer(self.password.encode()),
            ctypes.byref(device_info),
        )
        if self._user_id < 0:
            raise HikvisionSdkError(f"NET_DVR_Login_V30 failed: {self._last_hc_error()}")
        self.device_info = device_info

    def _start_preview(self) -> None:
        preview_info = NET_DVR_PREVIEWINFO()
        preview_info.hPlayWnd = 0
        preview_info.lChannel = self.channel
        preview_info.dwStreamType = 1 if self.stream_type.lower() == "sub" else 0
        preview_info.dwLinkMode = self.link_mode
        preview_info.bBlocked = 1
        preview_info.dwDisplayBufNum = 1

        self._decode_cb = DECODE_CALLBACK(self._decode_callback)
        self._real_data_cb = REAL_DATA_CALLBACK(self._real_data_callback)
        self._real_handle = self._hcnet.NET_DVR_RealPlay_V40(
            self._user_id,
            ctypes.byref(preview_info),
            self._real_data_cb,
            None,
        )
        if self._real_handle < 0:
            raise HikvisionSdkError(f"NET_DVR_RealPlay_V40 failed: {self._last_hc_error()}")

    def _real_data_callback(self, _handle, data_type, buffer, buffer_size, _user) -> None:
        if data_type == NET_DVR_SYSHEAD:
            self._play.PlayM4_SetStreamOpenMode(self._play_port, 0)
            if not self._play.PlayM4_OpenStream(self._play_port, buffer, buffer_size, 2 * 1024 * 1024):
                logger.warning("PlayM4_OpenStream failed: %s", self._last_play_error())
                return
            self._play.PlayM4_SetDecodeEngine(self._play_port, 0)
            self._play.PlayM4_SetDecCallBackExMend(self._play_port, self._decode_cb, None, 0, None)
            if not self._play.PlayM4_Play(self._play_port, None):
                logger.warning("PlayM4_Play failed: %s", self._last_play_error())
        elif data_type == NET_DVR_STREAMDATA:
            if not self._play.PlayM4_InputData(self._play_port, buffer, buffer_size):
                logger.debug("PlayM4_InputData failed: %s", self._last_play_error())

    def _decode_callback(self, port, frame_buffer, frame_size, frame_info, _user, _reserved) -> None:
        info = frame_info.contents
        if info.nWidth <= 0 or info.nHeight <= 0 or frame_size <= 0:
            return
        now_monotonic = time.monotonic()
        if (
            self._decode_min_interval_seconds > 0.0
            and now_monotonic - self._last_decoded_at_monotonic < self._decode_min_interval_seconds
        ):
            return
        raw = ctypes.string_at(frame_buffer, frame_size)
        if info.nType == 3:
            yuv = np.frombuffer(raw, dtype=np.uint8)
            expected = info.nWidth * info.nHeight * 3 // 2
            if yuv.size < expected:
                return
            yuv = yuv[:expected].reshape((info.nHeight * 3 // 2, info.nWidth))
            frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_YV12)
        else:
            frame = self._convert_with_playctrl(port, frame_buffer, frame_size, info)
            if frame is None:
                return
        self._last_decoded_at_monotonic = now_monotonic
        with self._frame_condition:
            self._latest_frame = frame
            self._frame_counter += 1
            self._frame_condition.notify_all()
        self._frame_ready.set()

    def _convert_with_playctrl(self, port, frame_buffer, frame_size, info) -> Optional[np.ndarray]:
        output_path = self.sdk_dir / "_latest_sdk_frame.jpg"
        ok = self._play.PlayM4_ConvertToJpegFile(
            frame_buffer,
            frame_size,
            info.nWidth,
            info.nHeight,
            info.nType,
            ctypes.c_char_p(str(output_path).encode(_sdk_encoding())),
        )
        if not ok:
            logger.debug("PlayM4_ConvertToJpegFile failed: %s", self._last_play_error(port))
            return None
        return cv2.imread(str(output_path))

    def _last_hc_error(self) -> int:
        return int(self._hcnet.NET_DVR_GetLastError()) if self._hcnet is not None else -1

    def _last_play_error(self, port: Optional[int] = None) -> int:
        if self._play is None:
            return -1
        play_port = self._play_port if port is None else ctypes.c_long(port)
        return int(self._play.PlayM4_GetLastError(play_port))


def save_one_frame(
    sdk_dir: str | Path,
    host: str,
    username: str,
    password: str,
    output: str | Path,
    port: int = 8000,
    channel: int = 1,
    stream_type: str = "sub",
    timeout: float = 10.0,
) -> Path:
    source = HikvisionSdkFrameSource(
        sdk_dir=sdk_dir,
        host=host,
        username=username,
        password=password,
        port=port,
        channel=channel,
        stream_type=stream_type,
    )
    try:
        source.open()
        frame = source.wait_for_frame(timeout)
        if frame is None:
            raise HikvisionSdkError(f"No decoded frame received within {timeout} seconds")
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), frame):
            raise HikvisionSdkError(f"Failed to write frame to {output_path}")
        return output_path
    finally:
        source.close()


class HikvisionSdkSession:
    """Short-lived SDK session for camera testing and configuration operations."""

    def __init__(
        self,
        sdk_dir: str | Path,
        host: str,
        username: str,
        password: str,
        port: int = 8000,
    ):
        self.sdk_dir = Path(sdk_dir)
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port)
        self._hcnet = None
        self._user_id = -1
        self.device_info: Optional[NET_DVR_DEVICEINFO_V30] = None

    def __enter__(self) -> "HikvisionSdkSession":
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def open(self) -> None:
        if not self.sdk_dir.exists():
            raise HikvisionSdkError(f"Hikvision SDK dir does not exist: {self.sdk_dir}")
        if os.name == "nt":
            os.add_dll_directory(str(self.sdk_dir))
        else:
            _prepend_env_path("LD_LIBRARY_PATH", self.sdk_dir)
            hcnet_com_dir = self.sdk_dir / "HCNetSDKCom"
            if hcnet_com_dir.exists():
                _prepend_env_path("LD_LIBRARY_PATH", hcnet_com_dir)
        self._hcnet = ctypes.CDLL(str(self.sdk_dir / _hcnet_library_name()))
        _configure_and_init_hcnet_sdk(self._hcnet, self.sdk_dir)

        device_info = NET_DVR_DEVICEINFO_V30()
        self._user_id = self._hcnet.NET_DVR_Login_V30(
            ctypes.create_string_buffer(self.host.encode()),
            self.port,
            ctypes.create_string_buffer(self.username.encode()),
            ctypes.create_string_buffer(self.password.encode()),
            ctypes.byref(device_info),
        )
        if self._user_id < 0:
            raise HikvisionSdkError(f"NET_DVR_Login_V30 failed: {self.last_error}")
        self.device_info = device_info

    def close(self) -> None:
        if self._hcnet is not None and self._user_id >= 0:
            self._hcnet.NET_DVR_Logout(self._user_id)
        self._user_id = -1

    @property
    def last_error(self) -> int:
        return int(self._hcnet.NET_DVR_GetLastError()) if self._hcnet is not None else -1

    def get_compression_config(self, channel: int, stream_type: str = "sub") -> dict:
        try:
            cfg = self._get_multi_stream_compression_config(channel, stream_type)
            return _multi_stream_config_to_dict(cfg)
        except HikvisionSdkError as exc:
            logger.info(
                "Multi-stream compression config failed, falling back to V30: %s",
                exc,
            )
            cfg = self._get_raw_compression_config(channel)
            info = _select_compression_info(cfg, stream_type)
            return _compression_info_to_dict(info)

    def update_compression_config(self, channel: int, stream_type: str, updates: dict) -> dict:
        try:
            cfg = self._get_multi_stream_compression_config(channel, stream_type)
        except HikvisionSdkError as exc:
            logger.info(
                "Multi-stream compression config read failed, falling back to V30: %s",
                exc,
            )
            cfg = self._get_raw_compression_config(channel)
            info = _select_compression_info(cfg, stream_type)
            _apply_compression_updates(info, updates)
            if not self._hcnet.NET_DVR_SetDVRConfig(
                self._user_id,
                NET_DVR_SET_COMPRESSCFG_V30,
                int(channel),
                ctypes.byref(cfg),
                ctypes.sizeof(cfg),
            ):
                raise HikvisionSdkError(
                    f"NET_DVR_SET_COMPRESSCFG_V30 failed: {self.last_error}; "
                    f"channel={channel}, stream_type={stream_type}, updates={updates}"
                )
            return self.get_compression_config(channel, stream_type)

        _apply_multi_stream_updates(cfg, updates)
        cond = _build_multi_stream_cond(channel, _stream_type_to_sdk(stream_type))
        status = ctypes.c_uint32(0)
        if not self._hcnet.NET_DVR_SetDeviceConfig(
            self._user_id,
            NET_DVR_SET_MULTI_STREAM_COMPRESSIONCFG,
            1,
            ctypes.byref(cond),
            ctypes.sizeof(cond),
            ctypes.byref(status),
            ctypes.byref(cfg),
            ctypes.sizeof(cfg),
        ):
            raise HikvisionSdkError(
                f"NET_DVR_SET_MULTI_STREAM_COMPRESSIONCFG failed: {self.last_error}, "
                f"status={status.value}; channel={channel}, stream_type={stream_type}, updates={updates}"
            )
        return self.get_compression_config(channel, stream_type)

    def _get_multi_stream_compression_config(
        self,
        channel: int,
        stream_type: str,
    ) -> NET_DVR_MULTI_STREAM_COMPRESSIONCFG:
        sdk_stream_type = _stream_type_to_sdk(stream_type)
        cond = _build_multi_stream_cond(channel, sdk_stream_type)
        cfg = NET_DVR_MULTI_STREAM_COMPRESSIONCFG()
        cfg.dwSize = ctypes.sizeof(cfg)
        status = ctypes.c_uint32(0)
        if not self._hcnet.NET_DVR_GetDeviceConfig(
            self._user_id,
            NET_DVR_GET_MULTI_STREAM_COMPRESSIONCFG,
            1,
            ctypes.byref(cond),
            ctypes.sizeof(cond),
            ctypes.byref(status),
            ctypes.byref(cfg),
            ctypes.sizeof(cfg),
        ):
            raise HikvisionSdkError(
                f"NET_DVR_GET_MULTI_STREAM_COMPRESSIONCFG failed: {self.last_error}, status={status.value}"
            )
        return cfg

    def _get_raw_compression_config(self, channel: int) -> NET_DVR_COMPRESSIONCFG_V30:
        cfg = NET_DVR_COMPRESSIONCFG_V30()
        cfg.dwSize = ctypes.sizeof(cfg)
        returned = ctypes.c_uint32(0)
        if not self._hcnet.NET_DVR_GetDVRConfig(
            self._user_id,
            NET_DVR_GET_COMPRESSCFG_V30,
            int(channel),
            ctypes.byref(cfg),
            ctypes.sizeof(cfg),
            ctypes.byref(returned),
        ):
            raise HikvisionSdkError(f"NET_DVR_GET_COMPRESSCFG_V30 failed: {self.last_error}")
        return cfg


def test_hikvision_sdk_connection(
    sdk_dir: str | Path,
    host: str,
    username: str,
    password: str,
    port: int = 8000,
    channel: int = 1,
    stream_type: str = "sub",
    timeout: float = 8.0,
) -> dict:
    source = HikvisionSdkFrameSource(
        sdk_dir=sdk_dir,
        host=host,
        username=username,
        password=password,
        port=port,
        channel=channel,
        stream_type=stream_type,
        link_mode=0,
    )
    try:
        source.open()
        frame = source.wait_for_frame(timeout)
        if frame is None:
            raise HikvisionSdkError(f"No decoded frame received within {timeout} seconds")
        serial = ""
        if source.device_info is not None:
            serial = bytes(source.device_info.sSerialNumber).split(b"\x00", 1)[0].decode("utf-8", errors="ignore")
        return {
            "serial_number": serial,
            "channel": channel,
            "stream_type": stream_type,
            "frame_shape": list(frame.shape),
            "sdk_port": port,
        }
    finally:
        source.close()


def _select_compression_info(cfg: NET_DVR_COMPRESSIONCFG_V30, stream_type: str) -> NET_DVR_COMPRESSION_INFO_V30:
    return cfg.struNetPara if stream_type.lower() == "sub" else cfg.struNormHighRecordPara


def _stream_type_to_sdk(stream_type: str) -> int:
    return 1 if stream_type.lower() == "sub" else 0


def _build_multi_stream_cond(
    channel: int,
    sdk_stream_type: int,
) -> NET_DVR_MULTI_STREAM_COMPRESSIONCFG_COND:
    cond = NET_DVR_MULTI_STREAM_COMPRESSIONCFG_COND()
    cond.dwSize = ctypes.sizeof(cond)
    cond.struStreamInfo.dwSize = ctypes.sizeof(cond.struStreamInfo)
    cond.struStreamInfo.dwChannel = int(channel)
    cond.dwStreamType = int(sdk_stream_type)
    return cond


def _multi_stream_config_to_dict(cfg: NET_DVR_MULTI_STREAM_COMPRESSIONCFG) -> dict:
    data = _compression_info_to_dict(cfg.struStreamPara)
    resolution_code = int(cfg.dwResolution) if int(cfg.dwResolution) > 0 else int(cfg.struStreamPara.byResolution)
    if resolution_code:
        data["video_resolution"] = SDK_RESOLUTION_TO_NAME.get(
            resolution_code,
            data.get("video_resolution"),
        )
    data["sdk_raw"].update(
        {
            "dwStreamType": int(cfg.dwStreamType),
            "dwResolution": int(cfg.dwResolution),
            "config_source": "multi_stream",
        }
    )
    return data


def _compression_info_to_dict(info: NET_DVR_COMPRESSION_INFO_V30) -> dict:
    resolution = SDK_RESOLUTION_TO_NAME.get(int(info.byResolution))
    raw_frame_rate = int(info.dwVideoFrameRate)
    frame_rate = _decode_frame_rate(raw_frame_rate)
    bitrate = _decode_bitrate(int(info.dwVideoBitrate))
    encoding = SDK_ENCODING_TO_NAME.get(int(info.byVideoEncType), f"unknown:{int(info.byVideoEncType)}")
    return {
        "video_encoding": encoding,
        "video_resolution": resolution,
        "frame_rate": frame_rate,
        "frame_rate_label": _decode_frame_rate_label(raw_frame_rate),
        "max_bitrate": bitrate,
        "gov_length": int(info.wIntervalFrameI),
        "transport_protocol": "SDK_TCP",
        "sdk_raw": {
            "byResolution": int(info.byResolution),
            "dwVideoFrameRate": raw_frame_rate,
            "dwVideoBitrate": int(info.dwVideoBitrate),
            "byVideoEncType": int(info.byVideoEncType),
            "byBitrateType": int(info.byBitrateType),
            "wIntervalFrameI": int(info.wIntervalFrameI),
            "bySmartCodec": int(info.bySmartCodec),
        },
    }


def _decode_frame_rate(raw_value: int):
    if raw_value == 0xFFFFFFFE:
        return None
    if raw_value == 33:
        return 8.3
    if raw_value == 36:
        return 12.5
    if raw_value == 41:
        return 6.25
    return SDK_FRAME_RATE_TO_FPS.get(raw_value)


def _decode_frame_rate_label(raw_value: int) -> Optional[str]:
    if raw_value == 0:
        return "全帧率"
    if raw_value == 0xFFFFFFFE:
        return "自动"
    frame_rate = _decode_frame_rate(raw_value)
    if frame_rate is None:
        return f"unknown:{raw_value}"
    return f"{frame_rate}fps"


def _decode_bitrate(raw_value: int):
    if raw_value == 0xFFFFFFFE:
        return None
    # Custom bitrate is stored as 0x80000000 | (kbps * 1024).
    if raw_value & 0x80000000:
        return (raw_value & 0x7FFFFFFF) // 1024
    return SDK_BITRATE_TO_KBPS.get(raw_value)


def _encode_bitrate(kbps: int) -> int:
    if kbps in KBPS_TO_SDK_BITRATE:
        return KBPS_TO_SDK_BITRATE[kbps]
    return 0x80000000 | (int(kbps) * 1024)


def _apply_multi_stream_updates(
    cfg: NET_DVR_MULTI_STREAM_COMPRESSIONCFG,
    updates: dict,
) -> None:
    cfg.dwSize = ctypes.sizeof(cfg)
    _apply_compression_updates(cfg.struStreamPara, updates, apply_resolution=False)
    width = updates.get("video_resolution_width")
    height = updates.get("video_resolution_height")
    if width and height:
        resolution = f"{width}x{height}"
        current_resolution_code = int(cfg.dwResolution) if int(cfg.dwResolution) > 0 else int(cfg.struStreamPara.byResolution)
        current_resolution = SDK_RESOLUTION_TO_NAME.get(current_resolution_code)
        if current_resolution == resolution:
            return
        resolution_code = RESOLUTION_TO_SDK.get(resolution)
        if resolution_code is None:
            raise HikvisionSdkError(f"Unsupported resolution for SDK config: {resolution}")
        if resolution_code <= 255 and resolution_code != 254:
            cfg.struStreamPara.byResolution = resolution_code
            cfg.dwResolution = resolution_code
        else:
            cfg.struStreamPara.byResolution = 254
            cfg.dwResolution = resolution_code


def _apply_compression_updates(
    info: NET_DVR_COMPRESSION_INFO_V30,
    updates: dict,
    *,
    apply_resolution: bool = True,
) -> None:
    if "video_encoding" in updates:
        value = updates["video_encoding"]
        if value not in ENCODING_TO_SDK:
            raise HikvisionSdkError(f"Unsupported video encoding for SDK config: {value}")
        info.byVideoEncType = ENCODING_TO_SDK[value]
    width = updates.get("video_resolution_width")
    height = updates.get("video_resolution_height")
    if apply_resolution and width and height:
        resolution = f"{width}x{height}"
        if resolution not in RESOLUTION_TO_SDK:
            raise HikvisionSdkError(f"Unsupported resolution for SDK config: {resolution}")
        info.byResolution = RESOLUTION_TO_SDK[resolution]
    if "frame_rate" in updates:
        value = float(updates["frame_rate"])
        if value not in FPS_TO_SDK_FRAME_RATE:
            raise HikvisionSdkError(f"Unsupported frame rate for SDK config: {value}")
        info.dwVideoFrameRate = FPS_TO_SDK_FRAME_RATE[value]
    bitrate = updates.get("max_bitrate") or updates.get("bit_rate")
    if bitrate is not None:
        value = int(bitrate)
        if value < 32 or value > 262144:
            raise HikvisionSdkError(f"Unsupported bitrate for SDK config: {value}")
        info.dwVideoBitrate = _encode_bitrate(value)
    if "gov_length" in updates:
        info.wIntervalFrameI = int(updates["gov_length"])
