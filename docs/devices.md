# 无人船外设列表与操控协议

protocol 文件种提到，命令和数据的交换格式如下
```json
{
    "type": "command",
    "host_id": "UUID",
    "lower_id": "UUID",
    "command": {
        ...
    }
}
```
```json
{
    "type": "feedback",
    "lower_id": "UUID",
    "feedback": {
        ...
    }
}
```

`type` `host_id` 和 `lower_id` 主要用于校验命令的合法性，`command` 和 `feedback` 是数据主体，接下来的协议阐述数据主体的格式。

- 推进器

推进器连带电子调速器连接板卡的 PWM 输出端，所以需要板卡发出 PWM 波型，参考资料位于：https://doc.embedfire.com/linux/rk356x/Python/zh/latest/circuit/pwm_output.html。

下位机在启动后，其两路 PWM 默认开启，连接两个推进器标号为 1 和 2，占空比未设置默认为 0；下位机程序启动后，占空比被默认设置为 7.5%，完成校准。

由上位机发来的消息 command 为
```json
{
    "device": "thruster",
    "operation": "pwm_duty",
    "parameters": [
        {
            "id": 1,
            "value": 0
        },
        {
            "id": 2,
            "value": 0
        }
    ]
}
```
其中 parameters 中指明要调整的推进器 id 和值，值是介于 [-1, 1] 之间的浮点数，占空比按 7.5% + value × 2.5% 计算，即在校准占空比的基础上偏移 [-2.5%, 2.5%]。PWM 频率为 50Hz，周期 20ms，占空比 5%、7.5%、10% 分别对应 1ms、1.5ms、2ms 的高电平时间。value 为负表示反转，电调需要支持双向。命令的 parameters 可以只列出部分推进器，下位机只修改列出的推进器，反馈始终包含全部推进器的当前值。

下位机接收到消息后，应当返回一次反馈，feedback 为
```json
{
    "device": "thruster",
    "related": "pwm_duty",
    "parameters": [
        {
            "id": 1,
            "value": 0
        },
        {
            "id": 2,
            "value": 0
        }
    ]
}
```
这将令上位机确认操作已经接受。

下位机程序正常退出时，PWM 的占空比应当再设置为 0%，意为关闭电机。

- 电磁铁

电磁铁连接电源与继电器，继电器的输入连接板卡的 GPIO，板卡通过 GPIO 可以控制其磁性，参考资料位于：https://doc.embedfire.com/linux/rk3588/quick_start/zh/latest/quick_start/40pin/gpio/gpio.html；https://doc.embedfire.com/linux/imx6/pinout/zh/index.html。

下位机/下位机程序默认启动时，GPIO 默认拉低。

由上位机发来的消息 command 为
```json
{
    "device": "magnet",
    "operation": "switch",
    "parameters": [
        {
            "enable": true
        }
    ]
}
```
enable 参数为布尔值，表明电磁铁是否打开（四个电磁铁的开关状态同步，它们的继电器输入短接到同一个 GPIO 输出上）

下位机接收到消息后，应当返回一次反馈，feedback 为
```json
{
    "device": "magnet",
    "related": "switch",
    "parameters": [
        {
            "enable": true
        }
    ]
}
```
这将令上位机确认操作已经接受。

下位机程序正常退出时，GPIO 应当被设置为低。

- 警示灯

也采用继电器，GPIO 控制，有两个警示灯需要两个继电器。GPIO 的初始化设定同上。

由上位机发来的消息 command 为
```json
{
    "device": "light",
    "operation": "switch",
    "parameters": [
        {
            "id": 1,
            "enable": true
        },
        {
            "id": 2,
            "enable": true
        }
    ]
}
```
enable 参数为布尔值，表明灯是否打开。

下位机接收到消息后，应当返回一次反馈，feedback 为
```json
{
    "device": "light",
    "related": "switch",
    "parameters": [
        {
            "id": 1,
            "enable": true
        },
        {
            "id": 2,
            "enable": true
        }
    ]
}
```
这将令上位机确认操作已经接受。

- RTK 定位装置

下位机使用 USB 连接设备，然后从虚拟串口读取 GNGGA 等协议的定位数据。数据会在 GNSS 启动后在串口上一直发送，下位机将读取，并按照一定的广播周期 2000ms 发回，feedback 结构为。

```json
{
    "device": "gnss",
    "related": "locate",
    "parameters": [
        {
            "type": "GNGGA",
            "utc_time": "09:27:50.000",
            "latitude": {
                "degrees": 53.3613367,
                "direction": "N"
            },
            "longitude": {
                "degrees": -6.50562,
                "direction": "W"
            },
            "fix_quality": 1,
            "satellites": 8,
            "hdop": 0.9,
            "altitude": {
                "value": 545.4,
                "unit": "M"
            },
            "geoid_height": {
                "value": 46.9,
                "unit": "M"
            },
            "dgps_age": null,
            "dgps_station_id": null,
            "checksum": "47"
        }
    ]
}
```

latitude 与 longitude 的 degrees 是带符号的十进制度，南纬与西经为负，direction 是半球字母。type 记录实际收到的语句类型，例如 GPGGA 或 GNGGA，本轮只解析 GGA 语句。下位机尚未收到数据，或数据超过 10000ms 没有更新时，parameters 为空列表。

- 摄像头

摄像头外接电源，通过以太网线连接板卡，可以从摄像头上拉取 RTSP H.264 推流。

摄像头的视频流不走 MQTT 传递，相对应的，下位机摄像头视频流会推送到位于 VPS 上的 MediaMTX，上位机应当从 MediaMTX 拉取视频流。

视频流虽然不走 MQTT，但下位机仍然上报推流状态，feedback 为
```json
{
    "device": "camera",
    "related": "stream",
    "parameters": [
        {
            "streaming": true,
            "path": "UUID",
            "restarts": 0
        }
    ]
}
```
其中 streaming 表示推流进程是否存活，path 是下位机在 MediaMTX 上发布的路径名，等于下位机的 UUID，restarts 是启动以来推流进程重启的次数。



- 路由器

路由器将为无人船提供 4G 网络，通过以太网线接入，默认网关设置为路由器。

下位机不发送路由器元数据。

## 错误反馈

下位机收到的命令校验失败时（未知的 device 或 operation、参数越界或类型错误、外设不可用），返回一次反馈
```json
{
    "device": "thruster",
    "related": "error",
    "parameters": [
        {
            "operation": "pwm_duty",
            "message": "value out of range: 1.5"
        }
    ]
}
```
其中 device 与 operation 取自被拒绝的命令，message 说明原因。参数越界的命令整体被拒绝，下位机不做截断。外设不可用时，它的周期上报也采用这一形式，parameters 中只有 message。

## 失控复位

下位机在控制清除（dominate 超时、收到 betray、下位机主动 betray）以及 MQTT 会话断线时，将全部推进器的 value 置 0，即占空比回到 7.5%；警示灯、电磁铁、摄像头与定位装置的状态保持不变。复位后下位机照常上报推进器状态。


# 无人船工控机硬件情况

要操作上述外设，必须利用板卡本身的 GPIO, PWM, Serial 能力，计划采用 periphery 的 Python 软件包控制

对于 GPIO，可以使用外设地址和偏移量控制
```python
from periphery import GPIO

LED_CHIP = "/dev/gpiochip0"
LED_LINE_OFFSET = 8

led = GPIO(LED_CHIP, LED_LINE_OFFSET, "out")

led.write(True)
led.close()
```

对于 PWM，可以使用标号和频道控制
```python
from periphery import PWM
import time

try:
    pwm = PWM(2, 0)
    pwm.frequency = 1e3
    pwm.duty_cycle = 0.00
    pwm.enable()
    while True:
        for i in range(0, 20):
            time.sleep(0.05)
            pwm.duty_cycle = round(pwm.duty_cycle + 0.05, 2)
except:
    print("Some errors occur!\n")
finally:
    pwm.duty_cycle = 1.0
    pwm.close()
```

对于 Serial，可以使用设备地址和 Baudrate 控制
```python
from periphery import Serial

try:
    serial = Serial(
        "/dev/ttymxc2",
        baudrate=115200,
        databits=8,
        parity="none",
        stopbits=1,
        xonxoff=False,
        rtscts=False,
    )
    serial.write(b"Hello World!\n")
    buf = serial.read(128, 1)

    print("原始数据: \n", buf)

    data_strings = buf.decode("gbk")
    print("读取到 {:d} 个字节 , 以字符串形式打印:\n {:s}".format(len(buf), data_strings))
finally:
    serial.close()
```

以下网址表明了 IO 编号和设备地址的关系：https://doc.embedfire.com/linux/rk356x/quick_start/zh/latest/quick_start/40pin/gpio/gpio.html

```json
    "thruster": {
        "frequency_hz": 50,
        "neutral_duty": 0.075,
        "range_duty": 0.025,
        "channels": {
            "1": {"chip": 1, "channel": 0},
            "2": {"chip": 1, "channel": 1}
        }
    },
    "magnet": {
        "gpio": {"chip": "/dev/gpiochip0", "line": 0}
    },
    "light": {
        "gpios": {
            "1": {"chip": "/dev/gpiochip0", "line": 0},
            "2": {"chip": "/dev/gpiochip0", "line": 0}
        }
    },
    "gnss": {
        "device": "/dev/ttyUSB0",
        "baudrate": 115200,
        "stale_after_ms": 10000
    },
```


