import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

from exe_runtime import ExeRuntime, decode_console

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stop_server(process, job):
    if job is not None:
        job.close()
    if process is not None and process.poll() is None:
        if sys.platform == "win32":
            process.terminate()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify_outputs(out, inputs):
    """仅依据原版服务端日志与结果文件核对完成情况，与客户端语言无关。"""
    text = decode_console((out / "logs/server.log").read_bytes())
    finished = re.findall(r"\[([^\]\r\n]+\.dat)\] 全部 (\d+) 圈结束 -> 回收 (\d+) 条航迹落库", text)
    frames = re.findall(r"\[([^\]\r\n]+\.dat)\] 圈\s*(\d+)\s+收到(\d+)条航迹\(耗时([\d.]+)ms\)", text)
    if "推理超时" in text or [row[0] for row in finished] != [p.name for p in inputs]:
        raise RuntimeError("服务端未完成所有数据包，或存在超时；请查看 logs/server.log")
    packages = []
    for name, circles, tracks in finished:
        circles, tracks = int(circles), int(tracks)
        rows = [(int(c), int(n), float(ms)) for f, c, n, ms in frames if f == name]
        if (circles < 1 or len(rows) != circles or len({c for c, _, _ in rows}) != circles
                or sum(n for _, n, _ in rows) != tracks
                or any(not 0 <= ms < 30000 for _, _, ms in rows)):
            raise RuntimeError(f"服务端逐圈记录不完整或统计不一致：{name}")
        for filename in (name, Path(name).stem + "_rt.dat"):
            path = out / filename
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"缺少服务端输出文件：{filename}")
        packages.append({"name": name, "circles": circles, "tracks": tracks})
    return {"packages": len(packages), "circles": sum(p["circles"] for p in packages),
            "tracks": sum(p["tracks"] for p in packages), "per_package": packages}


def wait_for_session(process, client_status_file=None):
    """等待服务端结束；由 run.sh 提供参赛端退出码，任一端异常都不能算成功。"""
    deadline = None
    while True:
        server_code = process.poll()
        client_code = None
        if client_status_file is not None and client_status_file.exists():
            client_code = int(client_status_file.read_text(encoding="ascii").strip())
            if client_code != 0:
                raise RuntimeError(f"参赛端异常退出，退出码：{client_code}")
        if server_code is not None:
            if server_code != 0:
                raise RuntimeError(f"收发软件异常退出，退出码：{server_code}；请查看 logs/server.log")
            if client_status_file is None or client_code == 0:
                return
        # 一端结束后，另一端最多有 60 秒完成收尾。
        if server_code is not None or client_code is not None:
            if deadline is None:
                deadline = time.monotonic() + 60
            if time.monotonic() >= deadline:
                raise TimeoutError("一端已退出，另一端未在 60 秒内完成收尾")
        time.sleep(0.1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立启动原版数据收发服务，等待参赛端 TCP 连接")
    parser.add_argument("--input-dir", type=Path,
                        default=Path(os.environ.get("DATA_ROOT", ROOT / "data/inputs")))
    parser.add_argument("--out-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--port", type=int, default=9902)
    parser.add_argument("--task", default="all", help="保留评测平台调用参数")
    parser.add_argument("--test", action="store_true", help="保留评测平台调用参数，仍处理全部输入")
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--client-status-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    input_dir = args.input_dir.resolve()
    out = args.out_dir.resolve()
    inputs = sorted(input_dir.glob("*.dat"))
    if not inputs or any(not p.is_file() or p.stat().st_size == 0 for p in inputs):
        parser.error(f"输入目录需要包含非空的 .dat 文件：{input_dir}")
    if out == input_dir or input_dir in out.parents or out in input_dir.parents:
        parser.error("输入目录和输出目录必须分开，不能互相包含")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error("输出目录必须为空；请用 --out-dir 指定一个新目录")
    executable = ROOT / "data/official/serve.exe"
    if not executable.is_file():
        parser.error(f"缺少收发软件：{executable}")
    # 避免误连到另一场正在运行的收发会话。
    with socket.socket() as probe:
        if sys.platform == "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error(f"端口 {args.port} 已被占用或不可用，请用 --port 换一个端口")
    logs = out / "logs"
    logs.mkdir(parents=True)
    summary = {"status": "failed", "input": str(input_dir), "output": str(out),
               "task": args.task, "test": args.test,
               "started_at": datetime.now(timezone.utc).isoformat()}
    started = time.monotonic()
    try:
        with ExeRuntime(logs) as runtime:
            config = logs / "serve-config.json"
            write_json(config, {"host": "127.0.0.1", "port": args.port,
                                "serve": {"truth_dir": runtime.path(input_dir),
                                          "out_dir": runtime.path(out), "select": None}})
            command = runtime.command(executable, ["--config", runtime.path(config),
                                      "--truth-dir", runtime.path(input_dir), "--out", runtime.path(out)])
            job = None
            process = None
            try:
                if sys.platform == "win32":
                    from windows_job import ProcessJob
                    job = ProcessJob()
                with (logs / "server.log").open("wb") as log:
                    process = subprocess.Popen(command, cwd=ROOT, env=runtime.env, stdout=log,
                                               stderr=subprocess.STDOUT,
                                               start_new_session=sys.platform != "win32",
                                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                    if job is not None:
                        job.add(process)
                    print(f"[demo] 启动收发软件，共 {len(inputs)} 包；输出：{out}", flush=True)
                    if args.ready_file is not None:
                        # 原子发布启动信息，不用探测连接消耗服务端唯一客户端会话。
                        temporary = args.ready_file.with_suffix(".tmp")
                        temporary.write_text(f"{args.port} {len(inputs)}\n", encoding="ascii")
                        temporary.replace(args.ready_file)
                    wait_for_session(process, args.client_status_file)
            finally:
                stop_server(process, job)
        summary.update(verify_outputs(out, inputs))
        summary["status"] = "success"
        print(f"[demo] 收发完成：{summary['packages']} 包 / {summary['circles']} 圈 / "
              f"{summary['tracks']} 个输出点", flush=True)
        print("[demo] 服务端结果与逐圈记录检查通过。", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[demo] 运行失败：{summary['error']}", file=sys.stderr, flush=True)
        return 1
    finally:
        summary["wall_seconds"] = round(time.monotonic() - started, 3)
        write_json(out / "run_summary.json", summary)


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"收到停止信号 {signum}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
