import asyncio
import logging
from typing import Any, Coroutine

from device.base import Device, DeviceError, error_feedback
from manager.manager import Manager
from network.mqtt import MqttService

logger = logging.getLogger(__name__)

class DeviceManager:
    def __init__(
        self,
        devices: dict[str, Device],
        manager: Manager,
        mqtt: MqttService,
        config: dict[str, Any],
    ) -> None:
        self._devices = devices
        self._manager = manager

        # 读取硬件设定参数
        # 硬件更新函数调用间隔
        self._update_interval = config["device_update_interval_ms"] / 1000.0
        # 硬件操作函数，如 start, stop 等执行的超时时间
        self._call_timeout = config["device_call_timeout_ms"] / 1000.0
        self._stop_timeout = config["device_stop_timeout_ms"] / 1000.0
        self._restart_interval = config["device_restart_interval_ms"] / 1000.0

        self._locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in devices}

        self._last_report: dict[str, float] = {}
        self._last_start: dict[str, float] = {}

        self._ready = asyncio.Event()
        self._stop: asyncio.Event | None = None
        self._tasks: set[asyncio.Task[None]] = set()

        # 监听由 manager 转发的事件和 MQTT 的
        manager.events.on("command", self._on_command)
        manager.events.on("request_full_update", self._on_full_update)
        manager.events.on("control_cleared", self._on_control_cleared)
        mqtt.events.on("disconnected", self._on_mqtt_disconnected)

    # 协程启动函数
    async def run(self, stop: asyncio.Event) -> None:
        self._stop = stop

        # 启动全部外设
        try:
            # 挂起直到外设全部启动
            await self._start_all(stop)
        finally:
            # 准备置位
            self._ready.set()
        # 启动定时器
        ticker = asyncio.create_task(self._tick_loop(stop))
        try:
            # 阻塞直到停止指令发出
            await stop.wait()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            # 取消进行中的任务
            await self._drain_tasks()
            # 停止所有外设
            await self._stop_all()

    async def _start_all(self, stop: asyncio.Event) -> None:
        for name, device in self._devices.items():
            if stop.is_set():
                return
            await self._start(name, device)

    # 启动外设
    async def _start(self, name: str, device: Device) -> bool:
        self._last_start[name] = asyncio.get_running_loop().time()
        try:
            # 启动外设，阻塞直到启动完毕或者超时
            await self._call(name, device.start, self._call_timeout)
        except asyncio.TimeoutError:
            logger.error("Device %s start timed out", name)
            return False
        except Exception as exc:
            logger.error("Device %s start failed: %s", name, exc)
            return False
        logger.info("Device %s started", name)
        return True

    # 停止外设
    STOP_ORDER = ("thruster", "magnet", "light", "camera", "gnss")
    async def _stop_all(self) -> None:
        order = [name for name in self.STOP_ORDER if name in self._devices]
        order += [name for name in self._devices if name not in self.STOP_ORDER]
        for name in order:
            try:
                # 停止外设，阻塞直到停止完毕或者超时
                await self._call(name, self._devices[name].stop, self._stop_timeout)
            except asyncio.TimeoutError:
                logger.error("Device %s stop timed out", name)
            except Exception as exc:
                logger.error("Device %s stop failed: %s", name, exc)
            else:
                logger.info("Device %s stopped", name)

    # 该函数调用指定方法，同时将方法通入线程池，阻塞直到线程池中的方法终结
    # method 不会阻塞主事件循环
    async def _call(self, name: str, method: Any, timeout: float, *args: Any) -> Any:
        async with self._locks[name]:
            return await asyncio.wait_for(asyncio.to_thread(method, *args), timeout)

    # 该函数调用指定方法，同时将方法通入任务列表
    # 任务不会阻塞，在事件循环中执行，在完成时将自己从列表中剔除
    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


    async def _drain_tasks(self) -> None:
        # 创建当前任务快照，逐个取消
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        # *tasks 是可迭代对象解包语法，等价于对逐个任务操作
        # gather(tasks[0], tasks[1], tasks[2], ...)
        # return_exceptions 令 task 的 CancelException 不会终结 gather
        # 该代码会在所有 task 都终结时取消阻塞
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _tick_loop(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time() + self._update_interval
        while not stop.is_set():
            delay = next_at - loop.time()
            if delay <= 0:
                next_at = loop.time() + self._update_interval
                delay = self._update_interval
            try:
                # 阻塞直到下一次定时器事件执行时间到
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                # 执行定时器事件
                self._on_tick(next_at)
                next_at += self._update_interval

    def _on_tick(self, at: float) -> None:
        for name, device in self._devices.items():
            # 将设备更新事件加入任务列表
            self._spawn(self._tick_device(name, device, at))

    async def _tick_device(self, name: str, device: Device, at: float) -> None:
        if not device.available:
            await self._retry_start(name, device)
            return
        if self._locks[name].locked():
            return
        try:
            # 执行设备更新，当设备状态发生改变，返回值为 True
            # 挂起直到完成任务或超时或出错
            changed = await self._call(name, device.update, self._call_timeout)
        except asyncio.TimeoutError:
            logger.warning("Device %s update timed out", name)
            changed = True
        except DeviceError as exc:
            logger.warning("Device %s update failed: %s", name, exc)
            changed = True
        except Exception:
            logger.exception("Device %s update raised", name)
            changed = True
        due = at - self._last_report.get(name, float("-inf")) >= device.report_interval
        if changed or due:
            await self._publish_status(name, device, at)

    async def _retry_start(self, name: str, device: Device) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_start.get(name, float("-inf")) < self._restart_interval:
            return
        if await self._start(name, device):
            await self._publish_status(name, device)

    # 请求发送设备状态
    async def _publish_status(
        self, name: str, device: Device, at: float | None = None
    ) -> None:
        try:
            # 阻塞直到设备状态发送有结果
            # 结果是当前设备的 feedback 快照
            status = await self._call(name, device.send_status, self._call_timeout)
        except asyncio.TimeoutError:
            logger.warning("Device %s status timed out", name)
            return
        except Exception as exc:
            logger.warning("Device %s status failed: %s", name, exc)
            status = device.error_status(str(exc))
        if at is None:
            at = asyncio.get_running_loop().time()
        self._last_report[name] = at
        # 阻塞直到快照成功发送到目标
        await self._manager.send_feedback(status)

    async def _publish_error(self, name: str, operation: str, message: str) -> None:
        await self._manager.send_feedback(error_feedback(name, message, operation))

    # 接收到上位机命令后的回调
    async def _on_command(self, command: Any) -> None:
        # 阻塞直到设备准备好
        await self._ready.wait()
        # 判断 command 结构体是否合法
        if not isinstance(command, dict):
            logger.warning("Command is not an object, ignored")
            return
        name = command.get("device")
        operation = command.get("operation")
        parameters = command.get("parameters")
        if not isinstance(name, str) or not name:
            logger.warning("Command without device name ignored")
            return
        if (
            not isinstance(operation, str)
            or not operation
            or not isinstance(parameters, list)
        ):
            logger.warning("Command for device %s is malformed, ignored", name)
            return
        device = self._devices.get(name)
        if device is None:
            logger.warning("Command for unknown device %s rejected", name)
            await self._publish_error(name, operation, "unknown device")
            return

        # 合法的结构体送入 call
        try:
            # 阻塞直到命令执行完毕
            # 根据执行结果发送反馈
            await self._call(
                name, device.do_command, self._call_timeout, operation, parameters
            )
        except asyncio.TimeoutError:
            logger.warning("Command for device %s timed out", name)
            await self._publish_error(name, operation, "command timed out")
            return
        except DeviceError as exc:
            logger.warning("Command for device %s rejected: %s", name, exc)
            await self._publish_error(name, operation, str(exc))
            return
        except Exception as exc:
            logger.exception("Command for device %s failed", name)
            await self._publish_error(name, operation, f"command failed: {exc}")
            return
        await self._publish_status(name, device)

    # 执行全量更新
    async def _on_full_update(self, host_id: str) -> None:
        await self._ready.wait()
        logger.info("Full update requested by host %s", host_id)
        await asyncio.gather(
            *(
                self._publish_status(name, device)
                for name, device in self._devices.items()
            )
        )

    # 停止控制
    # 同时重置设备
    async def _on_control_cleared(self, reason: str) -> None:
        logger.info("Control cleared (%s), resetting devices", reason)
        await self._reset_all()

    def _on_mqtt_disconnected(self, reason: str) -> None:
        if self._stop is not None and self._stop.is_set():
            return
        logger.warning("mqtt disconnected (%s), resetting devices", reason)
        self._spawn(self._reset_all())

    async def _reset_all(self) -> None:
        # 阻塞直到设备准备好
        await self._ready.wait()
        names = list(self._devices)
        # 对每个设备执行 reset，阻塞直到所有的协程完成重置
        results = await asyncio.gather(
            *(self._reset(name, self._devices[name]) for name in names)
        )
        # zip 方法将 names 和 results 一对一按照下标构造元组列表，元组的元素被提取
        # 有状态更改的设备会进行广播
        for name, changed in zip(names, results):
            if changed:
                await self._publish_status(name, self._devices[name])

    # 重置设备
    async def _reset(self, name: str, device: Device) -> bool:
        try:
            return bool(await self._call(name, device.reset, self._call_timeout))
        except asyncio.TimeoutError:
            logger.error("Device %s reset timed out", name)
            return False
        except DeviceError as exc:
            logger.error("Device %s reset failed: %s", name, exc)
            return True
        except Exception:
            logger.exception("Device %s reset raised", name)
            return True
