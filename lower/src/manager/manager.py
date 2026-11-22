import asyncio
import logging
from typing import Any

from pyee.asyncio import AsyncIOEventEmitter

from network.mqtt import MqttService

logger = logging.getLogger(__name__)

class Manager:
    TICK_INTERVAL = 0.25

    def __init__(self, mqtt: MqttService):
        # Manager 的事件发布者
        self.events = AsyncIOEventEmitter()
        self.events.on("error", self._on_emitter_error)

        # MQTT 对象
        # 从 MQTT 网络层传入事件
        self._mqtt = mqtt
        self._mqtt.events.on("connected", self._on_mqtt_connected)
        self._mqtt.events.on("disconnected", self._on_mqtt_disconnected)
        self._mqtt.events.on("message", self._on_mqtt_message)
        self._mqtt.events.on("user_connect", self._on_user_connect)
        self._mqtt.events.on("user_disconnect", self._on_user_disconnect)

    async def run(self, stop: asyncio.Event) -> None:
        # 启用定时器
        ticker = asyncio.create_task(self._tick_loop(stop))
        try:
            # 阻滞直到停止信号
            await stop.wait()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    async def _tick_loop(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time() + self.TICK_INTERVAL
        while not stop.is_set():
            delay = next_at - loop.time()
            if delay <= 0:
                # 超时时间是当前时间加延迟时间
                next_at = loop.time() + self.TICK_INTERVAL
                delay = self.TICK_INTERVAL
            try:
                # 阻滞直到停止或超时
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                next_at += self.TICK_INTERVAL
                self.events.emit("tick")

    def _on_mqtt_connected(self) -> None:
        logger.info("manager: mqtt connected")

    def _on_mqtt_disconnected(self, reason: str) -> None:
        logger.info("manager: mqtt disconnected (%s)", reason)

    def _on_mqtt_message(self, topic: str, payload: bytes) -> None:
        logger.info("manager: message on %s (%d bytes)", topic, len(payload))

    def _on_user_connect(self, *args: Any, **kwargs: Any) -> None:
        logger.info("manager: user connect")

    def _on_user_disconnect(self, *args: Any, **kwargs: Any) -> None:
        logger.info("manager: user disconnect")

    def _on_emitter_error(self, exc: Exception) -> None:
        logger.error("event handler raised: %s", exc)
