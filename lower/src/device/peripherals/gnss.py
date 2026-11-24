import logging
import time
from typing import Any, Callable

from device.base import Device, DeviceError, close_quietly
from device.hardware import Hardware

logger = logging.getLogger(__name__)

# 以下是 GNSS GGA 系协议解析代码
def _decimal(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None

def _integer(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None

def _clock_time(text: str) -> str | None:
    text = text.strip()
    if len(text) < 6:
        return None
    return f"{text[0:2]}:{text[2:4]}:{text[4:]}"

def _coordinate(value: str, hemisphere: str) -> dict[str, Any] | None:
    raw = _decimal(value)
    if raw is None or hemisphere not in ("N", "S", "E", "W"):
        return None
    degrees = int(raw // 100) + (raw % 100.0) / 60.0
    if hemisphere in ("S", "W"):
        degrees = -degrees
    return {"degrees": round(degrees, 7), "direction": hemisphere}

def parse_gga(sentence: str) -> dict[str, Any] | None:
    sentence = sentence.strip()
    if not sentence.startswith("$") or "*" not in sentence:
        return None
    body, _, checksum = sentence[1:].partition("*")
    fields = body.split(",")
    if len(fields[0]) != 5 or not fields[0].endswith("GGA"):
        return None
    expected = 0
    for byte in body.encode("utf-8"):
        expected ^= byte
    try:
        if int(checksum.strip(), 16) != expected:
            return None
    except ValueError:
        return None
    fields += [""] * (15 - len(fields))
    return {
        "type": fields[0],
        "utc_time": _clock_time(fields[1]),
        "latitude": _coordinate(fields[2], fields[3]),
        "longitude": _coordinate(fields[4], fields[5]),
        "fix_quality": _integer(fields[6]),
        "satellites": _integer(fields[7]),
        "hdop": _decimal(fields[8]),
        "altitude": {"value": _decimal(fields[9]), "unit": fields[10]},
        "geoid_height": {"value": _decimal(fields[11]), "unit": fields[12]},
        "dgps_age": _decimal(fields[13]),
        "dgps_station_id": fields[14].strip() or None,
        "checksum": checksum.strip().upper(),
    }


class Gnss(Device):
    name = "gnss"
    related = "locate"

    def __init__(
        self,
        hardware: Hardware,
        settings: dict[str, Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._hardware = hardware

        self._devpath = settings["device"]
        self._baudrate = int(settings["baudrate"])
        self._stale_after = float(settings.get("stale_after_ms", 10000)) / 1000.0

        self._clock = clock
        self._serial: Any = None
        self._inbox = bytearray()
        self._fix: dict[str, Any] | None = None
        self._fix_at: float | None = None
        self._had_data = False

    # 获取串口句柄
    def start(self) -> None:
        self._release()
        try:
            self._serial = self._hardware.open_serial(self._devpath, self._baudrate)
        except Exception as exc:
            self._serial = None
            self._available = False
            raise DeviceError(f"cannot open serial port: {exc}") from exc
        self._inbox.clear()
        self._had_data = False
        self._available = True

    # 停止并释放
    def stop(self) -> None:
        self._available = False
        self._release()

    def _release(self) -> None:
        serial, self._serial = self._serial, None
        if serial is not None:
            close_quietly(serial)

    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        # GNSS 没有主动操作
        raise DeviceError(f"unknown operation: {operation}")

    def _has_data(self) -> bool:
        if self._fix is None or self._fix_at is None:
            return False
        return self._clock() - self._fix_at <= self._stale_after

    def update(self) -> bool:
        if not self._available:
            return False
        try:
            # 此处的 serial 是 periphery Serial
            # input_waiting() 查询串口驱动缓冲区里当前已有多少字节可读
            # 将可读字节读取到 data
            # 注意该三元表达式，当 if 表达式为真取前者，否则后者，即空串
            waiting = self._serial.input_waiting()
            data = self._serial.read(waiting, 0) if waiting > 0 else b""
        except Exception as exc:
            logger.error("GNSS serial read failed: %s", exc)
            self._available = False
            self._had_data = False
            self._release()
            return True
        # _inbox 是字节数组，data 追加到输入缓冲
        self._inbox.extend(data)
        while True:
            # 输出 GNSS 消息每一行为一段
            index = self._inbox.find(b"\n")
            if index < 0:
                break
            line = self._inbox[:index].decode("ascii", errors="replace").strip()
            del self._inbox[: index + 1]
            # 对指定一段消息进行解析
            fix = parse_gga(line)
            if fix is not None:
                self._fix = fix
                self._fix_at = self._clock()
        if len(self._inbox) > 4096:
            # 当输入缓冲消息过多丢弃
            self._inbox.clear()
        has_data = self._has_data()
        changed = has_data != self._had_data
        self._had_data = has_data
        return changed

    # 返回 feedback 结构体
    def send_status(self) -> dict[str, Any]:
        if not self._available:
            return self.error_status("Device unavailable")
        fix = self._fix
        if fix is None or not self._has_data():
            return self.status([])
        return self.status([dict(fix)])
