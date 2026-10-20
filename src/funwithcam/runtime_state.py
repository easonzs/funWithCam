import threading


_CALIBRATION_ACTIVE = threading.Event()
_ASSISTANT_RUNNING = threading.Event()
_ASSISTANT_PAUSED = threading.Event()
_CHECKIN_PTZ_ACTIVE = threading.Event()


def set_calibration_active(active: bool) -> None:
    if active:
        _CALIBRATION_ACTIVE.set()
    else:
        _CALIBRATION_ACTIVE.clear()


def calibration_active() -> bool:
    return _CALIBRATION_ACTIVE.is_set()


def set_checkin_ptz_active(active: bool) -> None:
    """打卡低头期间置位：助手在此时停止姿态/在座观测，避免拍到桌面产生假数据。"""
    if active:
        _CHECKIN_PTZ_ACTIVE.set()
    else:
        _CHECKIN_PTZ_ACTIVE.clear()


def checkin_ptz_active() -> bool:
    return _CHECKIN_PTZ_ACTIVE.is_set()


def set_assistant_running(running: bool) -> None:
    if running:
        _ASSISTANT_RUNNING.set()
    else:
        _ASSISTANT_RUNNING.clear()
        _ASSISTANT_PAUSED.clear()


def set_assistant_paused(paused: bool) -> None:
    if paused:
        _ASSISTANT_PAUSED.set()
    else:
        _ASSISTANT_PAUSED.clear()


def wait_for_assistant_pause(timeout: float) -> bool:
    if not _ASSISTANT_RUNNING.is_set():
        return True
    return _ASSISTANT_PAUSED.wait(timeout)
