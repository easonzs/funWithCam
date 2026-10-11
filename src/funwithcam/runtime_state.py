import threading


_CALIBRATION_ACTIVE = threading.Event()
_ASSISTANT_RUNNING = threading.Event()
_ASSISTANT_PAUSED = threading.Event()


def set_calibration_active(active: bool) -> None:
    if active:
        _CALIBRATION_ACTIVE.set()
    else:
        _CALIBRATION_ACTIVE.clear()


def calibration_active() -> bool:
    return _CALIBRATION_ACTIVE.is_set()


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
