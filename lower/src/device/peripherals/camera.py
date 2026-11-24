import logging
import os
import subprocess
import time
from typing import Any, Callable

from device.base import Device, DeviceError
from device.hardware import PROCESS_GRACE_S, Hardware

logger = logging.getLogger(__name__)

class Camera(Device):
    name = "camera"
    related = "stream"

    def __init__(
        self,
        hardware: Hardware,
        settings: dict[str, Any],
        path: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._hardware = hardware

        # 加载参数
        self._path = path
        self._source_env = settings["source_url_env_name"]
        self._publish_env = settings["publish_base_url_env_name"]
        self._backoff_max = float(settings.get("restart_backoff_max_s", 30))

        self._clock = clock
        self._process: Any = None

        self._restarts = 0
        self._backoff = 1.0
        self._next_restart: float | None = None

    def _argv(self) -> list[str]:
        FFMPEG = "ffmpeg"
        READ_TIMEOUT_US = 5000000
        source = os.environ.get(self._source_env, "")
        base = os.environ.get(self._publish_env, "")
        if not source or not base:
            raise DeviceError(
                f"Environment variables {self._source_env} and {self._publish_env} must be set"
            )
        # 该 FFMPEG 函数执行以下行为
        # 不打印启动横幅，日志级别为 error，禁止读 stdin
        # 使用 TCP 输入 RTSP 协议
        # -timeout 超时时间为 READ_TIMEOUT_US，流卡死时在超时时间后进程终结
        # -i 从 source 处指定输入 URL
        # -c 进行 stream copy，直接转发流
        # -f 输出封装为 RTSP
        # 指明的输出路径是 base/path，base 来自 publish_base_url_env_name，path 是 manager_id
        return [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            str(READ_TIMEOUT_US),
            "-i",
            source,
            "-c",
            "copy",
            "-f",
            "rtsp",
            f"{base.rstrip('/')}/{self._path}",
        ]

    # spawn 在 Hardware 注册 ffmpeg 的 subprocess 以启动推送
    def _spawn(self) -> None:
        try:
            self._process = self._hardware.spawn(self._argv())
        except Exception as exc:
            self._process = None
            raise DeviceError(f"Cannot start ffmpeg: {exc}") from exc

    def start(self) -> None:
        self._terminate()
        # 记录重启次数
        self._restarts = 0
        # backoff 是下次重启的时间间隔
        self._backoff = 1.0
        # 下一次重启时间戳
        self._next_restart = None
        self._spawn()

        # 值得注意的是，此处的 available 不和其它外设行为一致
        # 其它外设故障时 available 会被置为无效，所以 available 表示设备是否可用，且指示设备是否需要重启
        # 但是此处 camera 的 available 仅指示是否启动完毕/执行终结
        # 重启的标志为 process 的 poll 结果，而非之
        self._available = True

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=PROCESS_GRACE_S)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=PROCESS_GRACE_S)
        except Exception as exc:
            raise DeviceError(f"cannot stop ffmpeg: {exc}") from exc

    def stop(self) -> None:
        self._available = False
        self._terminate()

    # camera 不支持外部操作
    def do_command(self, operation: str, parameters: list[Any]) -> bool:
        raise DeviceError(f"unknown operation: {operation}")

    def _streaming(self) -> bool:
        if not self._available or self._process is None:
            return False
        return self._process.poll() is None

    def _schedule_restart(self, now: float) -> None:
        self._next_restart = now + self._backoff
        self._backoff = min(self._backoff * 2, self._backoff_max)

    def update(self) -> bool:
        if not self._available:
            return False
        now = self._clock()
        if self._process is not None and self._process.poll() is not None:
            # poll() 方法在进程退出时返回 0，还在运行是返回 None，错误时返回非 0 值
            # 退出的进程需要重启
            returncode = self._process.returncode
            self._process = None
            logger.warning("ffmpeg exited (returncode %s), restart scheduled", returncode)
            self._schedule_restart(now)
            return True
        if self._process is None:
            if self._next_restart is None or now < self._next_restart:
                return False
            self._next_restart = None
            # 执行重启
            try:
                self._spawn()
            except DeviceError as exc:
                logger.error("camera restart failed: %s", exc)
                self._schedule_restart(now)
                return False
            if not self._available:
                self._terminate()
                return False
            self._restarts += 1
            logger.warning("camera stream restarted, restarts=%d", self._restarts)
            return True
        return False

    def send_status(self) -> dict[str, Any]:
        # 返回 feedback
        if not self._available:
            return self.error_status("device unavailable")
        return self.status(
            [
                {
                    "streaming": self._streaming(),
                    "path": self._path,
                    "restarts": self._restarts,
                }
            ]
        )
