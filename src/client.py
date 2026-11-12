import math
import socket
import struct
import time

FRAME_PACKET = 1
FRAME_POINTS = 2
FRAME_TRACKS = 3
PACKET = struct.Struct("<II")
POINT = struct.Struct("<IfffffII")
TRACK = struct.Struct("<IfffIII")
POINT_KEYS = ("Time", "Azimuth", "Elevation", "Distance", "Amp", "SNR", "CircleID", "PntID")
TRACK_KEYS = ("Time", "Azimuth", "Elevation", "Distance", "BatchID", "CircleID", "PntID")


class ProtocolError(RuntimeError):
    pass


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError("消息尚未收完整，连接已断开")
        data.extend(chunk)
    return bytes(data)


def receive(sock):
    prefix = sock.recv(4)
    if not prefix:
        return None
    prefix += recv_exact(sock, 4 - len(prefix))
    size = struct.unpack("<I", prefix)[0]
    if not 1 <= size <= 64 * 1024 * 1024:
        raise ProtocolError(f"无效消息长度：{size}")
    body = recv_exact(sock, size)
    if recv_exact(sock, 4) != b"\0" * 4:
        raise ProtocolError("消息结束标记必须为 0")
    return body[0], body[1:]


def encode_tracks(tracks, source_points):
    """先检查字段与原点对应关系，再按 28 字节一条编码。"""
    if not isinstance(tracks, list):
        raise ValueError("process_circle 必须返回 list；没有结果请返回 []")
    payload = bytearray()
    seen = set()
    for track in tracks:
        if not isinstance(track, dict) or any(key not in track for key in TRACK_KEYS):
            raise ValueError(f"每条输出必须包含字段：{', '.join(TRACK_KEYS)}")
        for key in ("Time", "BatchID", "CircleID", "PntID"):
            value = track[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{key} 必须是 0 到 4294967295 的 Python 整数")
        if track["BatchID"] == 0:
            raise ValueError("BatchID 必须大于 0；杂波点请不要放入返回列表")
        for key in ("Azimuth", "Elevation", "Distance"):
            value = track[key]
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} 必须是有限数值")
        identity = (track["Time"], track["CircleID"], track["PntID"])
        position = (track["Azimuth"], track["Elevation"], track["Distance"])
        if source_points.get(identity) != position:
            raise ValueError("输出必须对应本圈的原始点，保留时间、圈号、点号和坐标")
        if identity in seen:
            raise ValueError("同一个点不能在本圈结果里重复上报")
        seen.add(identity)
        payload.extend(TRACK.pack(*(track[key] for key in TRACK_KEYS)))
    body = bytes([FRAME_TRACKS]) + payload
    return struct.pack("<I", len(body)) + body + b"\0" * 4


def connect(host, port):
    deadline = time.monotonic() + 30
    while True:
        try:
            sock = socket.create_connection((host, port), timeout=2)
            sock.settimeout(60)
            return sock
        except OSError:
            if time.monotonic() >= deadline:
                raise ConnectionError(f"无法连接 {host}:{port}，请查看 logs/server.log")
            time.sleep(0.25)


def run_client(handler, expected_packages, host="127.0.0.1", port=9902):
    counts = {"packages": 0, "circles": 0, "points": 0, "tracks": 0, "per_package": []}
    current = None
    state = {}
    seen_packages = set()
    with connect(host, port) as sock:
        while True:
            message = receive(sock)
            if message is None:
                if (current is None or current["circles"] != current["expected_circles"]
                        or counts["packages"] != expected_packages):
                    raise ProtocolError("连接提前结束，收到的数据包或圈数不足；请重新完整运行")
                return counts
            tag, payload = message
            if tag == FRAME_PACKET:
                if len(payload) != PACKET.size:
                    raise ProtocolError("PACKET 长度必须为 8 字节")
                index, circles = PACKET.unpack(payload)
                if current and current["circles"] != current["expected_circles"]:
                    raise ProtocolError("上一包未收完就收到下一包")
                if index in seen_packages or circles < 1 or counts["packages"] >= expected_packages:
                    raise ProtocolError("数据包重复、数量超出预期或圈数无效")
                seen_packages.add(index)
                state = {}  # 新包使用全新状态，避免不同数据包互相影响。
                current = {"index": index, "expected_circles": circles,
                           "circles": 0, "points": 0, "tracks": 0}
                counts["per_package"].append(current)
                counts["packages"] += 1
                print(f"[client] 数据包 {index}：共 {circles} 圈", flush=True)
            elif tag == FRAME_POINTS:
                if current is None or current["circles"] >= current["expected_circles"]:
                    raise ProtocolError("POINTS 不在有效数据包内")
                started = time.monotonic()
                if len(payload) % POINT.size:
                    raise ProtocolError("POINTS 长度必须是 32 字节的整数倍")
                points = [dict(zip(POINT_KEYS, row)) for row in POINT.iter_unpack(payload)]
                # 在调用参赛函数之前保存原值，避免误修改输入后仍通过检查。
                source_points = {
                    (p["Time"], p["CircleID"], p["PntID"]):
                    (p["Azimuth"], p["Elevation"], p["Distance"]) for p in points
                }
                tracks = handler(points, state)
                response = encode_tracks(tracks, source_points)
                if time.monotonic() - started >= 29:
                    raise TimeoutError("单圈处理达到 29 秒，已停止，避免超过服务端 30 秒期限后错圈回传")
                sock.sendall(response)  # [] 也会编码为 9 字节空 TRACKS，不能省略。
                for record in (current, counts):
                    record["circles"] += 1
                    record["points"] += len(points)
                    record["tracks"] += len(tracks)
                done = current["circles"]
                if done % 100 == 0 or done == current["expected_circles"]:
                    print(f"[client] 包 {current['index']}：{done}/{current['expected_circles']} 圈", flush=True)
            else:
                raise ProtocolError(f"未知消息类型：{tag}")
