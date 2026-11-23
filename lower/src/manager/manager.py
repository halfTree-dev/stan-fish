import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from pyee.asyncio import AsyncIOEventEmitter

from network.mqtt import MqttService

logger = logging.getLogger(__name__)

class Manager:
    def __init__(self, mqtt: MqttService, config: dict[str, Any], uuid_path: Path):
        # Manager 的事件发布者
        self.events = AsyncIOEventEmitter()
        self.events.on("error", self._on_emitter_error)

        # 装载 UUID，首次生成并持久化
        self._id = self._load_or_create_uuid(uuid_path)
        self._client_name = config["client_name"]

        # 协议周期与超时
        self._alive_interval = config["alive_interval_ms"] / 1000.0
        self._alive_timeout = config["alive_timeout_ms"] / 1000.0
        self._obey_interval = config["obey_interval_ms"] / 1000.0
        self._dominate_timeout = config["dominate_timeout_ms"] / 1000.0

        # 存活列表：host_id -> 最近一次 host/alive 的时刻
        self._alive_hosts: dict[str, float] = {}
        # 控制状态：控制者与其最近一次 dominate 的时刻
        self._controller_id: str | None = None
        self._controller_last_seen: float | None = None
        self._next_alive_send: float = 0.0
        self._next_obey_send: float | None = None

        # 需要订阅的话题
        self._host_alive_topic = f"host/alive"
        self._dominate_topic = f"host/dominate/{self._id}"
        self._betray_topic = f"host/betray/{self._id}"
        self._command_topic = f"host/command/{self._id}"

        # MQTT 对象
        # 从 MQTT 网络层传入事件
        self._mqtt = mqtt
        self._mqtt.events.on("connected", self._on_mqtt_connected)
        self._mqtt.events.on("disconnected", self._on_mqtt_disconnected)
        self._mqtt.events.on("message", self._on_mqtt_message)

        # 定时器事件驱动广播与超时扫描
        self.events.on("tick", self._on_tick)

    async def run(self, stop: asyncio.Event) -> None:
        # 订阅协议话题
        # 重连后 MQTT 会自动重连
        await self._mqtt.subscribe(self._host_alive_topic)
        await self._mqtt.subscribe(self._dominate_topic)
        await self._mqtt.subscribe(self._betray_topic)
        await self._mqtt.subscribe(self._command_topic)
        # 启用定时器
        ticker = asyncio.create_task(self._tick_loop(stop))
        try:
            # 阻滞直到停止信号
            await stop.wait()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    TICK_INTERVAL = 0.25
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

    # 新建 UUID 或从已有文件中读取
    @staticmethod
    def _load_or_create_uuid(uuid_path: Path) -> str:
        # 存在文件时读取
        if uuid_path.exists():
            try:
                data = json.loads(uuid_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise RuntimeError(f"Cannot read UUID file {uuid_path}") from None
            value = data.get("uuid") if isinstance(data, dict) else None
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"Invalid UUID in {uuid_path}")
            return value
        # 新建 UUID 并写入
        value = str(uuid.uuid4())
        try:
            uuid_path.write_text(
                json.dumps({"uuid": value}, indent=4) + "\n", encoding="utf-8"
            )
        except OSError:
            raise RuntimeError(f"Cannot persist UUID to {uuid_path}") from None
        return value

    async def _on_tick(self) -> None:
        # 获取当前事件循环，得到时间戳
        loop = asyncio.get_running_loop()
        now = loop.time()

        # 对于所有超时的存活对象，消除之
        expired = [
            host_id
            for host_id, seen_at in self._alive_hosts.items()
            if now - seen_at > self._alive_timeout
        ]
        for host_id in expired:
            del self._alive_hosts[host_id]
            logger.info("host %s removed from alive list (timeout)", host_id)
        if (
            self._controller_id is not None
            and self._controller_last_seen is not None
            and now - self._controller_last_seen > self._dominate_timeout
        ):
            self._clear_control("dominate timeout")

        # 当下一次发送节点来临，发送
        if now >= self._next_alive_send:
            await self._send_alive()
            self._next_alive_send += self._alive_interval
            if self._next_alive_send < now:
                self._next_alive_send = now + self._alive_interval

        # 如果正在被控制，那么传输 obey 指令
        next_obey_send = self._next_obey_send
        if (
            self._controller_id is not None
            and next_obey_send is not None
            and now >= next_obey_send
        ):
            await self._send_obey()
            next_obey_send += self._obey_interval
            if next_obey_send < now:
                next_obey_send = now + self._obey_interval
            self._next_obey_send = next_obey_send

    # MQTT 接收到消息回调
    # 路由到其它行为
    async def _on_mqtt_message(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("MQTT malformed message on %s ignored", topic)
            return
        if topic == self._host_alive_topic:
            await self._handle_alive(data)
        elif topic == self._dominate_topic:
            await self._handle_dominate(data)
        elif topic == self._betray_topic:
            await self._handle_betray(data)
        elif topic == self._command_topic:
            await self._handle_command(data)

    # 接收到主机 alive 消息
    async def _handle_alive(self, data: dict[str, Any]) -> None:
        host_id = data.get("id")
        if data.get("type") != "alive" or not isinstance(host_id, str) or not host_id:
            logger.warning("Invalid alive message ignored")
            return
        now = asyncio.get_running_loop().time()
        if host_id not in self._alive_hosts:
            logger.info("Host %s joined alive list", host_id)
        self._alive_hosts[host_id] = now

    # 对于上位机申请控制时
    async def _handle_dominate(self, data: dict[str, Any]) -> None:
        # 验证上位机的各项控制参数匹配
        host_id = data.get("host_id")
        if (
            data.get("type") != "dominate"
            or data.get("lower_id") != self._id
            or not isinstance(host_id, str)
            or not host_id
        ):
            logger.warning("Invalid dominate message ignored")
            return

        # 匹配时，填写控制字段
        # 已经有控制参数时拒绝其它主机
        now = asyncio.get_running_loop().time()
        if self._controller_id is None:
            self._controller_id = host_id
            self._controller_last_seen = now
            self._next_obey_send = now + self._obey_interval
            await self._send_obey()
            logger.info("Host %s took control", host_id)
        elif host_id == self._controller_id:
            self._controller_last_seen = now
        else:
            await self._send_reject(host_id)
            logger.info("Rejected dominate from host %s", host_id)

    # 发送解除控制消息
    async def _handle_betray(self, data: dict[str, Any]) -> None:
        if (
            data.get("type") != "betray"
            or data.get("lower_id") != self._id
            or data.get("host_id") != self._controller_id
        ):
            logger.warning("Invalid betray message ignored")
            return
        self._clear_control("betray")

    # 执行命令
    async def _handle_command(self, data: dict[str, Any]) -> None:
        if (
            data.get("type") != "command"
            or data.get("lower_id") != self._id
            or data.get("host_id") != self._controller_id
        ):
            logger.warning("invalid command message ignored")
            return
        # 校验合理的命令将触发命令事件
        self.events.emit("command", data.get("command"))

    # 发送自身存活
    async def _send_alive(self) -> None:
        payload = json.dumps(
            {"type": "alive", "id": self._id, "client_name": self._client_name}
        )
        await self._mqtt.publish("lower/alive", payload)

    # 被控制时发送 ping 指令
    async def _send_obey(self) -> None:
        controller_id = self._controller_id
        if controller_id is None:
            return
        payload = json.dumps(
            {"type": "obey", "host_id": controller_id, "lower_id": self._id}
        )
        await self._mqtt.publish(f"lower/obey/{controller_id}", payload)

    # 拒绝控制指令
    async def _send_reject(self, host_id: str) -> None:
        payload = json.dumps(
            {"type": "reject", "host_id": host_id, "lower_id": self._id}
        )
        await self._mqtt.publish(f"lower/reject/{host_id}", payload)

    # 取消控制状态
    def _clear_control(self, reason: str) -> None:
        logger.info("Control by host %s cleared (%s)", self._controller_id, reason)
        self._controller_id = None
        self._controller_last_seen = None
        self._next_obey_send = None

    # 发送主动断开连接指令
    async def betray(self) -> None:
        controller_id = self._controller_id
        if controller_id is None:
            return
        payload = json.dumps(
            {"type": "betray", "host_id": controller_id, "lower_id": self._id}
        )
        await self._mqtt.publish(f"lower/betray/{controller_id}", payload)
        self._clear_control("betray sent")

    # 发送反馈
    async def send_feedback(self, data: dict[str, Any]) -> None:
        payload = json.dumps(
            {"type": "feedback", "lower_id": self._id, "feedback": data}
        )
        # 反馈数据包将通过 MQTT 发回
        await self._mqtt.publish(f"lower/feedback/{self._id}", payload)

    def _on_mqtt_connected(self) -> None:
        logger.info("Manager: MQTT connected")

    def _on_mqtt_disconnected(self, reason: str) -> None:
        logger.info("Manager: mqtt disconnected (%s)", reason)

    def _on_emitter_error(self, exc: Exception) -> None:
        logger.error("event handler raised: %s", exc)
