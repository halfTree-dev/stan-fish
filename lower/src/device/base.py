# abc 是 Python 的抽象类软件包，用于设计 Device 设备抽象类，供
from abc import ABC, abstractmethod
from typing import Any, ClassVar

# 设备内置错误基本类型
class DeviceError(Exception):
    pass

# 调用硬件的 close，同时忽略所有异常
def close_quietly(handle: Any) -> None:
    try:
        handle.close()
    except Exception:
        pass

# 以下是带类型检查的字段提取

def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeviceError(f"{label} must be an object")
    return value


def require_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DeviceError(f"{label} must be an integer")
    return value


def require_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeviceError(f"{label} must be a number")
    return float(value)


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise DeviceError(f"{label} must be a boolean")
    return value


# 构造错误信息对象
def error_feedback(device: str, message: str, operation: str | None = None) -> dict[str, Any]:
    parameter: dict[str, Any] = {"message": message}
    if operation is not None:
        parameter = {"operation": operation, "message": message}
    return {"device": device, "related": "error", "parameters": [parameter]}


class Device(ABC):
    # ClassVar 类型的字段将会被视为不可修改的静态字段
    # 标记有 ClassVar 的字段在初始化之后就会确定，之后被所有实例共享
    name: ClassVar[str] = ""
    related: ClassVar[str] = ""
    report_interval: float = 2.0

    def __init__(self) -> None:
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def reset(self) -> bool:
        return False

    @abstractmethod
    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        raise NotImplementedError

    def update(self) -> bool:
        return False

    @abstractmethod
    def send_status(self) -> dict[str, Any]:
        raise NotImplementedError

    def status(self, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        return {"device": self.name, "related": self.related, "parameters": parameters}

    def error_status(self, message: str, operation: str | None = None) -> dict[str, Any]:
        return error_feedback(self.name, message, operation)
