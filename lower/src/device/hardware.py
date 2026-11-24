# hardware.py 是通过调用 periphery 软件包
# 将硬件外设句柄发送给操作者供其操作
# 同时登记 spawn 出去的子进程，close() 保证它们不比本程序活得久

import logging
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 进程终结后，允许的最大阻塞时间
PROCESS_GRACE_S = 5.0

class Hardware:
    def __init__(self) -> None:
        self.pwms: list[Any] = []
        self.gpios: list[Any] = []
        self.serials: list[Any] = []

        self.processes: list[Any] = []
        self._processes_lock = threading.Lock()

    def open_pwm(self, chip: int, channel: int) -> Any:
        from periphery import PWM

        handle = PWM(chip, channel)
        self.pwms.append(handle)
        return handle

    def open_gpio(self, chip: str | None, line: int) -> Any:
        from periphery import GPIO

        handle = GPIO(chip, line, "out") if chip else GPIO(line, "out")
        self.gpios.append(handle)
        return handle

    def open_serial(self, devpath: str, baudrate: int) -> Any:
        from periphery import Serial

        handle = Serial(devpath, baudrate)
        self.serials.append(handle)
        return handle

    def spawn(self, argv: list[str]) -> Any:
        process = subprocess.Popen(argv)
        with self._processes_lock:
            self.processes = [p for p in self.processes if p.poll() is None]
            self.processes.append(process)
        return process

    def close(self) -> None:
        with self._processes_lock:
            processes, self.processes = self.processes, []
        alive = [p for p in processes if p.poll() is None]
        for process in alive:
            try:
                # terminate 所有子进程
                process.terminate()
            except Exception:
                logger.exception("Cannot terminate process %s", process.pid)
        for process in alive:
            try:
                # 子进程理应在终结后停止阻塞
                process.wait(timeout=PROCESS_GRACE_S)
            except subprocess.TimeoutExpired:
                try:
                    # 对于仍然阻塞的进程，执行 kill
                    process.kill()
                    process.wait(timeout=PROCESS_GRACE_S)
                except Exception:
                    logger.exception("Cannot kill process %s", process.pid)
            except Exception:
                logger.exception("Cannot wait process %s", process.pid)
