# magnet.py
# 电磁铁

from typing import Any

from device.base import Device, DeviceError, close_quietly, require_bool, require_object
from device.hardware import Hardware

class Magnet(Device):
    name = "magnet"
    related = "switch"

    def __init__(self, hardware: Hardware, settings: dict[str, Any]) -> None:
        super().__init__()
        self._hardware = hardware

        self._chip = settings["gpio"].get("chip") or None
        self._line = int(settings["gpio"]["line"])

        self._gpio: Any = None
        self._enabled = False

    # 获取 GPIO 句柄，然后设置
    def start(self) -> None:
        self._release()
        try:
            self._gpio = self._hardware.open_gpio(self._chip, self._line)
        except Exception as exc:
            self._gpio = None
            self._available = False
            raise DeviceError(f"Cannot open gpio: {exc}") from exc
        self._enabled = False
        self._available = True

    def stop(self) -> None:
        self._available = False
        self._release()
        self._enabled = False

    # 释放
    def _release(self) -> None:
        gpio, self._gpio = self._gpio, None
        if gpio is None:
            return
        try:
            gpio.write(False)
        except Exception as exc:
            raise DeviceError(f"Cannot set gpio low: {exc}") from exc
        finally:
            close_quietly(gpio)

    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        if not self._available:
            raise DeviceError("Device unavailable")

        # 校验参数合法并获取
        if operation != "switch":
            raise DeviceError(f"Unknown operation: {operation}")
        if len(parameters) != 1:
            raise DeviceError("Switch requires exactly one parameter object")
        entry = require_object(parameters[0], "parameters[0]")
        enable = require_bool(entry.get("enable"), "parameters[0].enable")

        # 写状态
        try:
            self._gpio.write(enable)
        except Exception as exc:
            raise DeviceError(f"Cannot set gpio: {exc}") from exc
        self._enabled = enable
        return True

    # 返回 feedback
    def send_status(self) -> dict[str, Any]:
        if not self._available:
            return self.error_status("device unavailable")
        return self.status([{"enable": self._enabled}])
