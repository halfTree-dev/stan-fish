from typing import Any

from device.base import (
    Device,
    DeviceError,
    close_quietly,
    require_bool,
    require_integer,
    require_object,
)
from device.hardware import Hardware


class Light(Device):
    name = "light"
    related = "switch"

    def __init__(self, hardware: Hardware, settings: dict[str, Any]) -> None:
        super().__init__()
        self._hardware = hardware
        self._lines = {
            int(key): (value.get("chip") or None, int(value["line"]))
            for key, value in settings["gpios"].items()
        }

        self._gpios: dict[int, Any] = {}
        self._enabled: dict[int, bool] = {key: False for key in self._lines}

    # 获取句柄并记载实例
    def start(self) -> None:
        self._release()
        opened: dict[int, Any] = {}
        try:
            for key, (chip, line) in self._lines.items():
                opened[key] = self._hardware.open_gpio(chip, line)
        except Exception as exc:
            self._available = False
            for gpio in opened.values():
                close_quietly(gpio)
            raise DeviceError(f"Cannot open gpio: {exc}") from exc
        self._gpios = opened
        self._enabled = {key: False for key in self._lines}
        self._available = True

    # 释放并拉低
    def stop(self) -> None:
        self._available = False
        self._release()
        self._enabled = {key: False for key in self._lines}

    def _release(self) -> None:
        gpios, self._gpios = self._gpios, {}
        errors = []
        for key, gpio in gpios.items():
            try:
                gpio.write(False)
            except Exception as exc:
                errors.append(f"{key}: {exc}")
            close_quietly(gpio)
        if errors:
            raise DeviceError("Cannot set gpio low: " + "; ".join(errors))

    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        if not self._available:
            raise DeviceError("Device unavailable")

        # 校验命令并获取参数
        if operation != "switch":
            raise DeviceError(f"Unknown operation: {operation}")
        updates: dict[int, bool] = {}
        for index, item in enumerate(parameters):
            entry = require_object(item, f"parameters[{index}]")
            key = require_integer(entry.get("id"), f"parameters[{index}].id")
            if key not in self._lines:
                raise DeviceError(f"Unknown light id: {key}")
            enable = require_bool(entry.get("enable"), f"parameters[{index}].enable")
            updates[key] = enable

        # 执行 GPIO 写
        for key, enable in updates.items():
            try:
                self._gpios[key].write(enable)
            except Exception as exc:
                raise DeviceError(f"Cannot set gpio: {exc}") from exc
            self._enabled[key] = enable
        return True

    # 返回 feedback 结构体
    def send_status(self) -> dict[str, Any]:
        if not self._available:
            return self.error_status("device unavailable")
        parameters = [
            {"id": key, "enable": self._enabled[key]} for key in sorted(self._enabled)
        ]
        return self.status(parameters)
