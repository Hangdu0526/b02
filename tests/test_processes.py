import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StandaloneClientTests(unittest.TestCase):
    def test_client_runs_against_tcp_server_without_starting_official_exe(self):
        replies = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(10)

            def send_circle():
                with listener.accept()[0] as connection:
                    connection.settimeout(10)
                    connection.sendall(struct.pack("<IBIII", 9, 1, 1, 1, 0))
                    connection.sendall(bytes.fromhex("010000000200000000"))
                    data = b""
                    while len(data) < 9:
                        chunk = connection.recv(9 - len(data))
                        if not chunk:
                            break
                        data += chunk
                    replies.append(data)

            thread = threading.Thread(target=send_circle, daemon=True)
            thread.start()
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "src/main.py"), "--host", "127.0.0.1",
                 "--port", str(listener.getsockname()[1]), "--expected-packages", "1"],
                capture_output=True, timeout=15)
            thread.join(timeout=11)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(replies, [bytes.fromhex("010000000300000000")])


@unittest.skipUnless(sys.platform == "linux", "run.sh 进程管理在 Linux 容器内验证")
class LauncherTests(unittest.TestCase):
    def run_session(self, client_script, server_fails=False, stop=False):
        # 用可控的独立进程替代耗时的官方软件，测试实际 run.sh 的失败与清理行为。
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / "run.sh", root / "run.sh")
            (root / "src").mkdir()
            (root / "src/serve.py").write_text('''
import argparse, os, pathlib, time
p = argparse.ArgumentParser()
p.add_argument('--ready-file', type=pathlib.Path)
p.add_argument('--client-status-file', type=pathlib.Path)
a = p.parse_args()
pathlib.Path('server.pid').write_text(str(os.getpid()))
a.ready_file.write_text('19902 3\\n')
while not pathlib.Path('client.pid').exists():
    time.sleep(.01)
if os.environ.get('SERVER_FAILS') == '1':
    raise SystemExit(9)
while not a.client_status_file.exists():
    time.sleep(.01)
raise SystemExit(0 if a.client_status_file.read_text().strip() == '0' else 1)
''', encoding="utf-8")
            (root / "run_client.sh").write_text(
                'echo "$$" > client.pid\n'
                'printf "%s %s %s" "$SERVER_HOST" "$SERVER_PORT" "$EXPECTED_PACKAGES" > endpoint\n'
                + client_script, encoding="utf-8")
            process = subprocess.Popen(["bash", "run.sh"], cwd=root,
                                       env={**os.environ, "SERVER_FAILS": str(int(server_fails))},
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                if stop:
                    deadline = time.monotonic() + 5
                    while not (root / "child.pid").exists():
                        if time.monotonic() >= deadline:
                            self.fail("客户端未启动")
                        time.sleep(.02)
                    process.send_signal(signal.SIGTERM)
                _, errors = process.communicate(timeout=10)
                self.assertEqual((root / "endpoint").read_text(), "127.0.0.1 19902 3")
                # /proc 中的僵尸已终止；可运行或睡眠状态则表示子进程泄漏。
                for name in ("server.pid", "client.pid", "child.pid"):
                    if (root / name).exists():
                        pid = int((root / name).read_text())
                        status = Path(f"/proc/{pid}/stat")
                        if status.exists():
                            self.assertEqual(status.read_text().split(") ", 1)[1][0], "Z", name)
                return process.returncode, errors
            finally:
                if process.poll() is None:
                    process.kill()
                for path in root.glob("*.pid"):
                    try:
                        os.kill(int(path.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.communicate(timeout=5)

    def test_non_python_client_success_and_environment(self):
        code, errors = self.run_session("sleep 60 &\necho $! > child.pid\nexit 0\n")
        self.assertEqual(code, 0, errors)

    def test_client_failure_stops_server(self):
        code, errors = self.run_session("exit 7\n")
        self.assertEqual(code, 7, errors)

    def test_server_failure_stops_client_and_children(self):
        code, errors = self.run_session("sleep 60 &\necho $! > child.pid\nwait\n", server_fails=True)
        self.assertEqual(code, 9, errors)

    def test_container_signal_stops_both_sides(self):
        code, errors = self.run_session("sleep 60 &\necho $! > child.pid\nwait\n", stop=True)
        self.assertEqual(code, 143, errors)


if __name__ == "__main__":
    unittest.main()
