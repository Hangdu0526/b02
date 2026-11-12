# 输入输出协议

使用 Python demo 时无需修改协议代码。自行用其他语言实现客户端时，按此协议连接原版 `serve.exe`。默认地址为 `127.0.0.1:9902`，连接建立后不发送握手字节。

自动启动时，`run.sh` 分别启动服务端 `src/serve.py` 和参赛端 `run_client.sh`。更换语言只需修改 `run_client.sh`，客户端读取 `SERVER_HOST`、`SERVER_PORT`、`EXPECTED_PACKAGES` 环境变量（或在脚本中转成命令行参数）。客户端应重试初始连接，并在全部包和圈收齐后返回退出码 0，异常返回非零；无需生成额外统计文件。

所有数值为**小端序**，记录紧密排列，无对齐填充。传输顺序：

```text
serve.exe                         参赛端
    ── PACKET（包号、总圈数） ──→ 清空跨包状态
    ── POINTS（本圈点迹） ──────→ 处理本圈
    ←─ TRACKS（本圈航迹） ─────── 回传结果，空结果也要回传
    ...逐圈重复；下一包仍沿用同一连接...
    ── 所有包结束，关闭连接 ────→ 核对包数、圈数后退出
```

## 帧结构

| 顺序 | 字段 | 字节数 | 含义 |
| --- | --- | --- | --- |
| 1 | `NumBytes` | 4 | uint32；等于 `1 + payload 字节数` |
| 2 | `tag` | 1 | `1=PACKET`、`2=POINTS`、`3=TRACKS` |
| 3 | `payload` | 可变 | 下表所述记录的连续排列 |
| 4 | `END` | 4 | uint32，恒为 0 |

`NumBytes` 不包含自身与 `END`。空 `TRACKS` 长度为 9 字节，内容为 `01 00 00 00 03 00 00 00 00`。

## 记录字段

| 消息 | 每条字节数 | 按顺序排列的字段及类型 |
| --- | --- | --- |
| `PACKET` | 8（固定一条） | `PacketIndex: uint32`、`CircleCount: uint32` |
| `POINTS` | 32 | `Time: uint32`、`Azimuth: float32`、`Elevation: float32`、`Distance: float32`、`Amp: float32`、`SNR: float32`、`CircleID: uint32`、`PntID: uint32` |
| `TRACKS` | 28 | `Time: uint32`、`Azimuth: float32`、`Elevation: float32`、`Distance: float32`、`BatchID: uint32`、`CircleID: uint32`、`PntID: uint32` |

TCP 的一次 `recv` 不保证得到一整帧，必须按指定长度循环读取。每个 `POINTS` 对应一个 `TRACKS`，不能跳过空回包或一次发送多圈结果。服务端单圈回传期限为 30 秒。

`BatchID > 0` 表示有效航迹，杂波点不放入结果。同一个目标跨圈保持相同批号。该 demo 返回本圈选中的原始点，并检查其时间、圈号、点号和坐标；跨圈状态由 `algorithm.py` 中的 `state` 保存。

协议整理自原发布包 `competition_v1.0.0_serve/README.md` 和 `model/client.py`。发行包内的 `serve.exe` 与输入 `.dat` 均直接复制自该发布包，不由 demo 重新生成或解码。
