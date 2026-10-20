import json
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import CheckinPtzConfig


LOGGER = logging.getLogger(__name__)

# go2rtc 云台操作编码与 speech.py 中的定义保持一致。
_MOTOR_OPERATIONS = {
    "left": "1",
    "right": "2",
    "up": "3",
    "down": "4",
}
_PTZ_TIMEOUT_SECONDS = 8
_POSITION_READ_TIMEOUT_SECONDS = 8


@dataclass(frozen=True)
class CameraPosition:
    angle: int
    elevation: int

    def close_to(self, other: "CameraPosition", tolerance: int) -> bool:
        return (
            abs(self.angle - other.angle) <= tolerance
            and abs(self.elevation - other.elevation) <= tolerance
        )


@dataclass(frozen=True)
class CheckinPtzResult:
    success: bool
    details: str
    """描述发生了什么：'tilted' / 'not_enabled' / 'unavailable' / 'restored' / 'restore_failed'"""
    message: str = ""
    """失败时供语音播报的中文说明。"""


class CameraPtz:
    """通过 go2rtc 的 /api/xiaomi/motor 驱动云台。

    复用 speech.py 中转向口令同样的请求方式（见 ``_ptz_move``），
    额外提供读取当前角度/仰角的能力。
    """

    def __init__(self, api_url: str, stream: str):
        self._api_url = api_url.rstrip("/")
        self._stream = stream

    def move(self, direction: str) -> None:
        operation = _MOTOR_OPERATIONS[direction]
        query = urlencode(
            {
                "src": self._stream,
                "operation": operation,
            }
        )
        url = "{}/api/xiaomi/motor?{}".format(self._api_url, query)
        request = Request(url, method="POST")
        try:
            with urlopen(request, timeout=_PTZ_TIMEOUT_SECONDS) as response:
                response.read()
        except (URLError, HTTPError, OSError) as exc:
            raise RuntimeError("go2rtc motor move failed: {}".format(exc)) from exc

    def read_position(self) -> CameraPosition:
        query = urlencode(
            {
                "src": self._stream,
                "read": "1",
            }
        )
        url = "{}/api/xiaomi/motor?{}".format(self._api_url, query)
        request = Request(url, method="GET")
        try:
            with urlopen(
                request, timeout=_POSITION_READ_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
        except (URLError, HTTPError, OSError, ValueError) as exc:
            raise RuntimeError(
                "go2rtc motor read failed: {}".format(exc)
            ) from exc
        try:
            angle = int(payload["angle"])
            elevation = int(payload["elevation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "go2rtc motor response missing position: {}".format(payload)
            ) from exc
        return CameraPosition(angle=angle, elevation=elevation)


class CheckinPtzController:
    """打卡低头与恢复的闭环控制。

    云台只有单步转动，没有绝对定位，因此“恢复到标准位置”不能盲走对称步数，
    而是：走回去 → 读位置 → 与低头前基准比较 → 超差则反向微调，
    用轮数兜底，避免步进回差导致回不到原位。
    """

    def __init__(
        self,
        config: CheckinPtzConfig,
        api_url: str,
        stream: str,
    ):
        self._config = config
        self._ptz = CameraPtz(api_url, stream)
        self._baseline: Optional[CameraPosition] = None

    def begin(self) -> CheckinPtzResult:
        """低头前记录基准位置并向下低头。失败时静默降级，不阻碍打卡。"""
        self._baseline = None
        if not self._config.enabled:
            return CheckinPtzResult(True, "not_enabled")
        try:
            self._baseline = self._ptz.read_position()
        except RuntimeError as exc:
            return self._degrade(begin=True, exc=exc)
        try:
            for _ in range(self._config.tilt_steps):
                self._ptz.move("down")
                self._step_delay()
        except RuntimeError as exc:
            return self._degrade(begin=True, exc=exc)
        return CheckinPtzResult(True, "tilted")

    def restore(self) -> CheckinPtzResult:
        """打卡结束后恢复到低头前的姿势。返回失败供语音提醒。"""
        if not self._config.enabled:
            return CheckinPtzResult(True, "not_enabled")
        baseline = self._baseline
        if baseline is None:
            return CheckinPtzResult(True, "restored", "")
        tolerance = self._config.restore_tolerance
        max_attempts = self._config.restore_max_attempts
        try:
            for _ in range(max_attempts):
                current = self._ptz.read_position()
                if current.close_to(baseline, tolerance):
                    return CheckinPtzResult(True, "restored")
                # 误差驱动：仰角高于基准则向下，低于基准则向上，逐轮收敛。
                if current.elevation > baseline.elevation:
                    self._ptz.move("down")
                else:
                    self._ptz.move("up")
                self._step_delay()
                LOGGER.info(
                    "checkin ptz restore attempt: %s vs baseline %s",
                    current,
                    baseline,
                )
        except RuntimeError as exc:
            LOGGER.warning("checkin ptz restore failed: %s", exc)
            return self._degrade(begin=False, exc=exc)
        return CheckinPtzResult(
            False,
            "restore_failed",
            "摄像头没有完全回到原来的位置，请检查或重新调整。",
        )

    def _step_delay(self) -> None:
        if self._config.step_delay_seconds > 0:
            time.sleep(self._config.step_delay_seconds)

    def _degrade(self, begin: bool, exc: RuntimeError) -> CheckinPtzResult:
        if begin:
            detail = "unavailable"
            message = ""
        else:
            detail = "restore_failed"
            message = "摄像头复位失败：{}".format(exc)
        LOGGER.warning("checkin ptz %s unavailable/move failed: %s", detail, exc)
        return CheckinPtzResult(False, detail, message)