# __init__.py 文件使得 import device 的默认门面为该文件
# 该文件包含设备的基本函数与方法

from typing import Any

from device.base import Device
from device.peripherals.camera import Camera
from device.peripherals.gnss import Gnss
from device.hardware import Hardware
from device.peripherals.light import Light
from device.peripherals.magnet import Magnet
from device.peripherals.thruster import Thruster


# 从配置文件中读取指定设备的配置
def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    section = config.get(name)
    if not isinstance(section, dict):
        raise RuntimeError(f"Config section {name} is missing")
    return section

# 构建设备对象，实例化对象
def build_devices(
    config: dict[str, Any], manager_id: str, hardware: Hardware
) -> dict[str, Device]:
    report_interval = config["device_status_interval_ms"] / 1000.0
    devices: list[Device] = [
        Thruster(hardware, _section(config, "thruster")),
        Magnet(hardware, _section(config, "magnet")),
        Light(hardware, _section(config, "light")),
        Camera(hardware, _section(config, "camera"), manager_id),
        Gnss(hardware, _section(config, "gnss")),
    ]
    for device in devices:
        device.report_interval = report_interval
    # 返回字典，键值对是设备名称和设备
    return {device.name: device for device in devices}
