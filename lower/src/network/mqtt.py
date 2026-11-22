import asyncio
import logging
import os
import ssl
from typing import Any

import aiomqtt
from pyee.asyncio import AsyncIOEventEmitter

logger = logging.getLogger(__name__)

class MqttService:
    def __init__(self, config: dict[str, Any]):
        # MQTTService 的事件发布者
        self.events = AsyncIOEventEmitter()
        self.events.on("error", self._on_emitter_error)

        # 读取配置
        self._hostname = config["mqtt_broker"]
        self._port = int(config["mqtt_port"])
        self._username = config["mqtt_user_name"]
        self._password = self._load_password(config["mqtt_password_env_name"])
        self._identifier = config["client_name"]
        self._retry_interval = float(config["mqtt_connect_retry_interval"])
        self._tls_context = self._build_tls_context(config)

        self._subscriptions: dict[str, int] = {}

        self._client: aiomqtt.Client | None = None

    @staticmethod
    def _load_password(env_name: str) -> str:
        # 从环境变量中读取 MQTT 连接密码
        password = os.environ.get(env_name)
        if password is None:
            raise RuntimeError(f"Cannot get MQTT password because environment variable {env_name} is not set")
        return password

    @staticmethod
    def _build_tls_context(config: dict[str, Any]) -> ssl.SSLContext | None:
        # 从指定的 TLS 证书构建 TLS 上下文
        if not config.get("mqtt_use_tls", False):
            return None
        cert_path = config.get("mqtt_tls_cert_path", "")
        if cert_path:
            return ssl.create_default_context(cafile=cert_path)
        return ssl.create_default_context()

    # 作为协程启动入口
    async def run(self, stop: asyncio.Event) -> None:
        # 定时器任务启动
        ticker = asyncio.create_task(self._tick_loop(stop))
        try:
            # 启动监管函数，该函数只要阻塞，那么协程不会终结
            await self._supervise(stop)
        finally:
            # 终结定时器，阻塞直到定时器完成终结。
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    async def _supervise(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                # 启用 MQTT 会话
                await self._run_session(stop)
                self.events.emit("disconnected", "shutdown")
                return
            except aiomqtt.MqttError as exc:
                # run_session 的阻塞被打断证明故障出现
                logger.warning("MQTT session ended because of: %s", exc)
                self.events.emit("disconnected", str(exc))
                if stop.is_set():
                    return
                # 阻塞直到重连时间
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._retry_interval)
                except asyncio.TimeoutError:
                    pass
                # 然后重新启动 MQTT 会话
        self.events.emit("disconnected", "shutdown")

    async def _run_session(self, stop: asyncio.Event) -> None:
        async with aiomqtt.Client(
            hostname=self._hostname,
            port=self._port,
            username=self._username,
            password=self._password,
            identifier=self._identifier,
            tls_context=self._tls_context,
        ) as client:
            # 连接 MQTT 客户端
            self._client = client
            logger.info(
                "MQTT client connected to %s:%d as %s",
                self._hostname,
                self._port,
                self._identifier,
            )
            self.events.emit("connected")
            try:
                # 订阅到指定频道
                await self._resubscribe(client)
                # 开始消费，阻塞直到出现故障或停止指令
                await self._consume_until_stop(client, stop)
            finally:
                # 出现错误将会清销实例
                # 但是不 catch，此时 supervise 中 except 会抓住连接异常
                # run_session 停止阻塞
                self._client = None

    async def _resubscribe(self, client: aiomqtt.Client) -> None:
        for topic, qos in self._subscriptions.items():
            try:
                await client.subscribe(topic, qos)
            except aiomqtt.MqttError as exc:
                logger.warning("MQTT client resubscribe %s failed: %s", topic, exc)

    async def _consume_until_stop(
        self, client: aiomqtt.Client, stop: asyncio.Event
    ) -> None:
        # consume 任务被注册，加入事件循环
        consumer = asyncio.create_task(self._consume(client))
        # stop 事件也被注册
        stop_task = asyncio.create_task(stop.wait())
        try:
            # 挂起监测事件，阻塞直到消费者出现故障或停止事件发出
            done, _ = await asyncio.wait(
                {consumer, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if consumer in done:
                # 消费者的故障丢回上层
                exc = consumer.exception()
                if exc is not None:
                    raise exc
        finally:
            # 阻塞直到任务全部关闭
            for task in (consumer, stop_task):
                task.cancel()
            await asyncio.gather(consumer, stop_task, return_exceptions=True)

    # 缓冲区消息重复消耗，触发 message 事件
    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            topic = str(message.topic)
            payload = bytes(message.payload)
            self.events.emit("message", topic, payload)
            self._dispatch_user_event(topic, payload)

    def _dispatch_user_event(self, topic: str, payload: bytes) -> None:
        pass

    # 发送
    async def publish(
        self,
        topic: str,
        payload: str | bytes | bytearray | int | float | None,
        qos: int = 0,
        retain: bool = False,
    ) -> bool:
        client = self._client
        if client is None:
            logger.warning("Publish dropped because we're not connected: %s", topic)
            return False
        try:
            await client.publish(topic, payload, qos=qos, retain=retain)
            return True
        except aiomqtt.MqttError as exc:
            logger.warning("Publish failed on %s: %s", topic, exc)
            return False

    # 订阅
    async def subscribe(self, topic: str, qos: int = 0) -> None:
        self._subscriptions[topic] = qos
        client = self._client
        if client is not None:
            try:
                await client.subscribe(topic, qos)
            except aiomqtt.MqttError as exc:
                logger.warning("subscribe %s failed: %s", topic, exc)


    TICK_INTERVAL = 0.25
    async def _tick_loop(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time() + self.TICK_INTERVAL
        while not stop.is_set():
            delay = next_at - loop.time()
            if delay <= 0:
                next_at = loop.time() + self.TICK_INTERVAL
                delay = self.TICK_INTERVAL
            try:
                # 阻塞直到下一个 tick 时刻来临
                # 或者停止指令触发
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                next_at += self.TICK_INTERVAL
                self.events.emit("tick")


    # pyee 出故障时触发
    def _on_emitter_error(self, exc: Exception) -> None:
        logger.error("Pyee event handler raised: %s", exc)
