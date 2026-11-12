import contextlib
import io
from pathlib import Path
import struct
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import client
from algorithm import process_circle


def make_track(point, batch_id):
    """把一个输入点变成输出航迹点，保留原点字段，只增加航迹编号。"""
    return {
        "Time": point["Time"],
        "Azimuth": point["Azimuth"],
        "Elevation": point["Elevation"],
        "Distance": point["Distance"],
        "BatchID": batch_id,
        "CircleID": point["CircleID"],
        "PntID": point["PntID"],
    }

def frame(tag, payload=b""):
    return struct.pack("<I", len(payload) + 1) + bytes([tag]) + payload + b"\0" * 4


def packet(index, circles):
    return frame(1, struct.pack("<II", index, circles))


def point(circle=1, point_id=1):
    return {"Time": circle * 5000, "Azimuth": 0.25, "Elevation": 0.125,
            "Distance": 0.5, "Amp": 0.75, "SNR": 0.625,
            "CircleID": circle, "PntID": point_id}


def points_frame(points):
    return frame(2, b"".join(struct.pack("<IfffffII", *(p[k] for k in client.POINT_KEYS)) for p in points))


class Connection:
    """模拟 TCP 任意分片与 EOF，并记录客户端真正发送的字节。"""
    def __init__(self, data):
        self.data = io.BytesIO(data)
        self.sent = bytearray()

    def recv(self, size):
        return self.data.read(min(size, 3))

    def sendall(self, data):
        self.sent.extend(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class ContractTests(unittest.TestCase):
    def run_stream(self, data, handler=process_circle, packages=1):
        connection = Connection(data)
        with patch.object(client, "connect", return_value=connection), contextlib.redirect_stdout(io.StringIO()):
            result = client.run_client(handler, packages)
        return result, connection

    def test_default_assigns_fields_and_sends_empty_reply_for_empty_circle(self):
        result, connection = self.run_stream(packet(1, 2) + points_frame([point(), point(point_id=2)]) + points_frame([]))
        self.assertEqual(result["circles"], 2)
        self.assertEqual(result["points"], 2)
        self.assertEqual(result["tracks"], 1)
        expected_payload = struct.pack("<IfffIII", 5000, 0.25, 0.125, 0.5, 1, 1, 1)
        self.assertEqual(connection.sent, frame(3, expected_payload) + frame(3))

    def test_nonempty_reply_preserves_input_fields(self):
        result, connection = self.run_stream(packet(1, 1) + points_frame([point()]),
                                            lambda points, state: [make_track(points[0], 7)])
        expected_payload = struct.pack("<IfffIII", 5000, 0.25, 0.125, 0.5, 7, 1, 1)
        self.assertEqual(connection.sent, frame(3, expected_payload))
        self.assertEqual(result["tracks"], 1)

    def test_state_survives_circles_and_resets_on_new_package(self):
        seen = []
        def handler(points, state):
            seen.append(state.get("count", 0))
            state["count"] = state.get("count", 0) + 1
            return []
        data = packet(1, 2) + points_frame([]) + points_frame([]) + packet(2, 1) + points_frame([])
        result, _ = self.run_stream(data, handler, packages=2)
        self.assertEqual(seen, [0, 1, 0])
        self.assertEqual((result["packages"], result["circles"]), (2, 3))

    def test_truncated_frame_is_not_success(self):
        for data in (b"\x09\0", packet(1, 1)[:-1], packet(1, 1) + points_frame([point()])[:-1]):
            with self.subTest(data=data), self.assertRaises(EOFError):
                self.run_stream(data)

    def test_bad_frame_header_or_end_marker(self):
        for data in (struct.pack("<I", 0), struct.pack("<I", 64 * 1024 * 1024 + 1),
                     packet(1, 1)[:-1] + b"\x01"):
            with self.subTest(data=data), self.assertRaises(client.ProtocolError):
                self.run_stream(data)

    def test_missing_circles_or_packages_is_not_success(self):
        for data, expected in ((b"", 1), (packet(1, 2) + points_frame([]), 1),
                               (packet(1, 1) + points_frame([]), 2)):
            with self.subTest(data=data), self.assertRaisesRegex(client.ProtocolError, "提前结束"):
                self.run_stream(data, packages=expected)

    def test_invalid_message_order_or_payload(self):
        for data in (points_frame([]), packet(1, 1) + points_frame([]) + points_frame([]),
                     frame(1, b"short"), packet(1, 1) + frame(2, b"short"),
                     packet(1, 1) + frame(99), packet(1, 2) + packet(2, 1),
                     packet(1, 1) + points_frame([]) + packet(1, 1)):
            with self.subTest(data=data), self.assertRaises(client.ProtocolError):
                self.run_stream(data, packages=2)

    def test_wrong_output_fields_are_rejected(self):
        for key, value in (("BatchID", 0), ("BatchID", True), ("BatchID", 2**32),
                           ("Time", 1.5), ("Time", 999), ("CircleID", -1), ("PntID", 999),
                           ("Azimuth", float("nan")), ("Distance", 0.75)):
            def handler(points, state):
                return [{**make_track(points[0], 1), key: value}]
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.run_stream(packet(1, 1) + points_frame([point()]), handler)

    def test_none_or_incomplete_output_is_rejected(self):
        for invalid in (None, {}, [None], [{}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.run_stream(packet(1, 1) + points_frame([point()]), lambda points, state: invalid)

    def test_duplicate_or_mutated_input_point_is_rejected(self):
        def mutate(points, state):
            points[0]["Time"] = 999
            return [make_track(points[0], 1)]
        for handler in (mutate, lambda points, state: [make_track(points[0], 1)] * 2):
            with self.subTest(handler=handler), self.assertRaises(ValueError):
                self.run_stream(packet(1, 1) + points_frame([point()]), handler)

    def test_late_reply_is_never_sent_to_the_next_circle(self):
        connection = Connection(packet(1, 1) + points_frame([point()]))
        with patch.object(client, "connect", return_value=connection), \
                patch.object(client.time, "monotonic", side_effect=[0, 29]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(TimeoutError):
            client.run_client(process_circle, 1)
        self.assertEqual(connection.sent, b"")

    def test_occupied_port_does_not_start_or_join_another_session(self):
        import serve as main
        import socket
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            out = main.ROOT / f"output-test-{uuid.uuid4().hex}"
            with patch.object(main.subprocess, "Popen") as launch, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main.main(["--port", str(listener.getsockname()[1]), "--out-dir", str(out),
                           "--input-dir", str(main.ROOT / "data/inputs")])
            self.assertEqual(error.exception.code, 2)
            launch.assert_not_called()
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
