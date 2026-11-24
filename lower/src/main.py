# main.py
# 工控机程序入口文件

import asyncio
import json
import logging
import signal
from pathlib import Path

from device import Hardware, build_devices
from manager.devices import DeviceManager
from manager.manager import Manager
from network.mqtt import MqttService

async def main() -> None:
    # 启用日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("main")

    # 读取配置到 config
    config_path = Path(__file__).parent / "lower.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    # stop 是一个 Event，当其置真，所有 stop.is_set() 和 stop.wait() 将反应
    stop = asyncio.Event()

    # 所有的中断信号触发回调到就绪队列，置位 stop
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    mqtt = MqttService(config)
    uuid_path = Path(__file__).parent / "uuid.config.json"
    manager = Manager(mqtt, config, uuid_path)

    # Hardware 负责管理硬件的子进程，也提供操作句柄
    hardware = Hardware()
    # Device 利用 Hardware 的接口记录设备状态，执行外设操作
    devices = build_devices(config, manager.id, hardware)
    device_manager = DeviceManager(devices, manager, mqtt, config)

    logger.info("Starting lower services.")
    # asyncio.gather 将多个 run 协程发配到同一个事件循环，然后创建任务执行
    # 仅当所有协程都停止，才会停止阻塞
    try:
        await asyncio.gather(mqtt.run(stop), manager.run(stop), device_manager.run(stop))
    finally:
        # 阻塞直到全部子进程终结，保证它们不比本程序活得久
        hardware.close()
    logger.info("Stopped lower services.")


if __name__ == "__main__":
    asyncio.run(main())
