# main.py
# 工控机程序入口文件

import asyncio
import json
import logging
import signal
from pathlib import Path

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
    manager = Manager(mqtt)

    logger.info("Starting lower services.")
    # asyncio.gather 将两个 run 协程发配到同一个事件循环，然后创建任务执行
    await asyncio.gather(mqtt.run(stop), manager.run(stop))
    logger.info("Stopped lower services.")


if __name__ == "__main__":
    asyncio.run(main())
