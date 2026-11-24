# thruster.py
# 推进器

from typing import Any

from device.base import (
    Device,
    DeviceError,
    close_quietly,
    require_integer,
    require_number,
    require_object,
)
from device.hardware import Hardware

class Thruster(Device):
    name = "thruster"
    related = "pwm_duty"

    def __init__(self, hardware: Hardware, settings: dict[str, Any]) -> None:
        super().__init__()
        self._hardware = hardware

        # 阅读配置文件
        self._frequency = float(settings["frequency_hz"])
        self._neutral = float(settings["neutral_duty"])
        self._range = float(settings["range_duty"])
        self._channels = {
            int(key): (int(value["chip"]), int(value["channel"]))
            for key, value in settings["channels"].items()
        }

        # pwm 句柄和设置值
        self._pwms: dict[int, Any] = {}
        self._values: dict[int, float] = {key: 0.0 for key in self._channels}

    def start(self) -> None:
        self._release()
        opened: dict[int, Any] = {}
        try:
            for key, (chip, channel) in self._channels.items():
                # 从配置文件读取所有句柄然后启用
                pwm = self._hardware.open_pwm(chip, channel)
                opened[key] = pwm
                pwm.frequency = self._frequency
                pwm.duty_cycle = 0.0
                pwm.enable()
            for pwm in opened.values():
                pwm.duty_cycle = self._neutral
        except Exception as exc:
            self._available = False
            for pwm in opened.values():
                self._halt(pwm)
            raise DeviceError(f"Cannot open pwm: {exc}") from exc
        # 将可用的句柄保存到本地
        self._pwms = opened
        self._values = {key: 0.0 for key in self._channels}
        self._available = True

    def stop(self) -> None:
        self._available = False
        # 释放：关闭并归中所有设备
        self._release()

    def _release(self) -> None:
        # 关闭并归中所有设备
        pwms, self._pwms = self._pwms, {}
        errors = []
        for key, pwm in pwms.items():
            try:
                pwm.duty_cycle = 0.0
            except Exception as exc:
                errors.append(f"{key}: {exc}")
            close_quietly(pwm)
        if errors:
            raise DeviceError("Cannot reset pwm: " + "; ".join(errors))

    @staticmethod
    def _halt(pwm: Any) -> None:
        try:
            pwm.duty_cycle = 0.0
        except Exception:
            pass
        close_quietly(pwm)

    def reset(self) -> bool:
        # 设置归中
        changed = False
        errors = []
        for key, value in self._values.items():
            if value == 0.0:
                continue
            try:
                self._apply(key, 0.0)
            except DeviceError as exc:
                errors.append(f"{key}: {exc}")
                continue
            changed = True
        if errors:
            raise DeviceError("Cannot reset thruster: " + "; ".join(errors))
        return changed

    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        # 校验命令合法
        if not self._available:
            raise DeviceError("Device unavailable")
        if operation != "pwm_duty":
            raise DeviceError(f"Unknown operation: {operation}")

        # 校验参数合法并获取
        updates: dict[int, float] = {}
        for index, item in enumerate(parameters):
            entry = require_object(item, f"parameters[{index}]")

            key = require_integer(entry.get("id"), f"parameters[{index}].id")
            if key not in self._channels:
                raise DeviceError(f"Unknown thruster id: {key}")

            value = require_number(entry.get("value"), f"parameters[{index}].value")
            if not -1.0 <= value <= 1.0:
                raise DeviceError(f"Value out of range: {entry.get('value')}")

            updates[key] = value
        for key, value in updates.items():
            # 应用最新占空比
            self._apply(key, value)
        return True

    def _apply(self, key: int, value: float) -> None:
        pwm = self._pwms.get(key)
        if pwm is None:
            raise DeviceError("Device unavailable")
        try:
            pwm.duty_cycle = self._neutral + self._range * value
        except Exception as exc:
            raise DeviceError(f"Cannot set pwm duty: {exc}") from exc
        self._values[key] = value

    # 发回 feedback 结构体
    def send_status(self) -> dict[str, Any]:
        if not self._available:
            return self.error_status("device unavailable")
        parameters = [{"id": key, "value": self._values[key]} for key in sorted(self._values)]
        return self.status(parameters)
