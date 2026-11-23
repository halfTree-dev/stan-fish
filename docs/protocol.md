# 上位机与下位机 MQTT 协议

host/ 和 lower/ 之间依靠 MQTT 进行元数据交换，MQTT 的协议如下

## 发现机制

上位机和下位机各自拥有一个 UUID。UUID 在首次启动时随机生成，并持久化存储在本地（如配置文件中），此后每次启动都沿用同一 UUID。这样设备重启后 UUID 保持不变，对端可以在其重新上线后凭同一 UUID 识别出同一设备并重新建立连接。

接下来，下位机会以 1000ms 的周期，在话题 `lower/alive` 下广播以下消息
```json
{
    "type": "alive",
    "id": "UUID",
    "client_name": "Name"
}
```
其中 `id` 字段是下位机的 UUID，`client_name` 是下位机的设定名称。

每台上位机也都会在话题 `host/alive` 下广播以下消息
```json
{
    "type": "alive",
    "id": "UUID"
}
```

作为上位机，其维护以下信息：
- 所有的存活下位机列表
- 关注中的下位机列表 UUID
- 是否正在控制指定下位机 & 控制指定下位机的 UUID & 指定控制下位机上次传来 obey 消息的时间戳

作为下位机，其维护以下信息：
- 所有存活的上位机列表
- 是否有上位机宣布对自身控制/宣布对自身控制的上位机 UUID & 指定控制上位机上次传来 dominate 消息的时间戳

上位机/下位机监听 `lower/alive` 和 `host/alive`，在其下收到消息时，将对应的 ID 加入存活列表，同时记录时间戳
当存活列表中的某个 ID，当前时间与最后一次接受时间戳已经相隔 6000ms，则认为超时，将其剔除出存活列表。

特别的，上位机若正在控制的设备因为超时从存活列表中移除，那么自动断开控制。

## 控制机制

上位机可以申请控制某个下位机，建立一对一通信链路，具体过程如下：

下位机监听 `host/dominate/{lower_id}`，其中 `lower_id` 是下位机自己的 ID，上位机想要控制某下位机时，在该链路上以 1000ms 的周期反复发送消息
```json
{
    "type": "dominate",
    "host_id": "UUID",
    "lower_id": "UUID"
}
```
若 `lower_id` 是自己的 ID，且自己没有正在被控制，那么设置 host_id 对应的上位机是宣布对自身控制的上位机，同时在话题 `lower/obey/{host_id}` 上以 1000ms 的周期反复发送消息
```json
{
    "type": "obey",
    "host_id": "UUID",
    "lower_id": "UUID"
}
```
上位机监听话题，若 ID 校验正确，那么设置指定控制下位机的 ID 是对应设备的。

若下位机已处于被控制状态，又收到 `host_id` 与当前控制者不一致的 dominate 消息，则在话题 `lower/reject/{host_id}` 上回复一次以下消息，其中 `host_id` 是被拒绝的上位机的 UUID：
```json
{
    "type": "reject",
    "host_id": "UUID",
    "lower_id": "UUID"
}
```
被拒绝的上位机校验 `lower_id` 正确后，应立刻停止 dominate 广播并放弃本次控制申请。若 reject 意外丢失，由于 dominate 仍在周期性重发，下位机会随之再次回复 reject，因此无需额外的重传机制。来自当前控制者的 dominate 消息用于心跳保活，不会触发 reject。

上位机只应对存活列表中的下位机发起 dominate，并在收到有效 obey（进入控制状态）、收到 reject 或主动放弃时停止 dominate 广播。

上述操作的时间戳都会被记录。特别地，控制关系建立后 dominate 与 obey 并不会停止：上位机在整个控制期间持续以 1000ms 的周期发送 dominate，下位机持续以 1000ms 的周期发送 obey，二者兼作控制链路的心跳。上位机以最近一次收到的 obey 刷新超时计时，下位机以最近一次收到的 dominate 刷新超时计时，命令与数据回传不参与刷新。如果上位机/下位机超过 6000ms 没有收到对应的消息，那么认定对方断链，清除控制/被控制数据，同时取消控制状态。

若上位机/下位机希望主动与对方断开连接，可以在 `host/betray/{lower_id}` 或 `lower/betray/{host_id}` 上发送一次以下命令，同时停止在 dominate 和 obey 上的广播
```json
{
    "type": "betray",
    "host_id": "UUID",
    "lower_id": "UUID"
}
```
接到消息的另一端校验后会立刻终结控制。即便这次命令出于意外失败，在一定时间后对方也会被动断链。

## 关注机制

上位机只能明确控制一个下位机，但可以关注多个下位机。关注下位机在前端设置即可，下位机不需要知道谁在关注它。

是否关注下位机会影响上位机是否接受其 `lower/feedback/{lower_id}` 路由的消息。

特别的，当关注列表中的元素被从存活列表中移除，那么其也将被移除。

当上位机声明了对某下位机的控制，那么其自动加入关注列表。当断开控制，不会自动将其移除出关注列表，除非是因为存活超时移除的。

## 命令发送机制

当上位机明确控制某下位机，那么上位机可以在 `host/command/{lower_id}` 上发送消息
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
下位机应当时刻监听，当 UUID 校验通过，且上位机仍然控制置位，那么消息有效。

## 元数据回传机制

下位机会广播其数据到 `lower/feedback/{lower_id}`，其中 `host_id` 表示该消息的目标接收者，即当前控制该下位机的上位机的 UUID
```json
{
    "type": "feedback",
    "lower_id": "UUID",
    "feedback": {
        ...
    }
}
```
上位机会监听 `lower/feedback/{lower_id}`，其中 lower_id 是所有关注中的 UUID 集合。

下位机只要存活，就会按照一定规则反馈自己的状态。

## 安全机制

所有 MQTT 连接启用 TLS 加密，broker 仅接受持有有效客户端证书的连接。上位机与下位机的 UUID 及全部消息内容仅在加密通道内传输，未持有证书的第三方无法接入 broker，因此无法窃听或伪造 dominate/obey/reject/betray/command/feedback 等消息。
