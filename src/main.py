import argparse
import os
import signal
import sys

from algorithm import process_circle
from client import run_client


def main(argv=None):
    parser = argparse.ArgumentParser(description="Python 参赛端：接收点迹并回传航迹")
    parser.add_argument("--host", default=os.environ.get("SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.environ.get("SERVER_PORT", "9902"))
    parser.add_argument("--expected-packages", type=int,
                        default=os.environ.get("EXPECTED_PACKAGES"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    if args.expected_packages is None or args.expected_packages < 1:
        parser.error("请指定 --expected-packages，或通过 run.sh 自动设置 EXPECTED_PACKAGES")
    try:
        counts = run_client(process_circle, args.expected_packages, args.host, args.port)
        print(f"[client] 完成：{counts['packages']} 包 / {counts['circles']} 圈 / "
              f"{counts['points']} 个输入点 / {counts['tracks']} 个输出点", flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"[client] 运行失败：{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"收到停止信号 {signum}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
