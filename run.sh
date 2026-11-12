#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
session_dir=$(mktemp -d)
server_pid=""
client_pid=""

cleanup() {
  trap '' INT TERM
  if [[ -n "$client_pid" ]]; then
    # 客户端在独立进程组运行，连同其子进程一起清理。
    kill -TERM -- "-$client_pid" 2>/dev/null || true
    sleep 0.2
    kill -KILL -- "-$client_pid" 2>/dev/null || true
    wait "$client_pid" 2>/dev/null || true
  fi
  if [[ -n "$server_pid" ]]; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
  rm -f -- "$session_dir/ready" "$session_dir/ready.tmp" "$session_dir/status" "$session_dir/status.tmp"
  rmdir -- "$session_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 服务端负责参数/目录检查、Wine 环境和 serve.exe，不导入参赛端代码。
python3 -u src/serve.py "$@" --ready-file "$session_dir/ready" \
  --client-status-file "$session_dir/status" &
server_pid=$!
while [[ ! -f "$session_dir/ready" ]]; do
  if ! kill -0 "$server_pid" 2>/dev/null; then
    wait "$server_pid"
    exit 0
  fi
  sleep 0.1
done

read -r SERVER_PORT EXPECTED_PACKAGES < "$session_dir/ready"
export SERVER_HOST=127.0.0.1 SERVER_PORT EXPECTED_PACKAGES
# 此时 Wine 已初始化、serve.exe 已启动；客户端自行重试连接直到开始监听。
setsid bash ./run_client.sh &
client_pid=$!

# 任一进程结束就立即处理，避免服务端失败后客户端一直挂起。
first_status=0
wait -n -p finished_pid "$server_pid" "$client_pid" || first_status=$?
if [[ "$finished_pid" == "$client_pid" ]]; then
  printf '%s\n' "$first_status" > "$session_dir/status.tmp"
  mv -- "$session_dir/status.tmp" "$session_dir/status"
  server_status=0
  wait "$server_pid" || server_status=$?
  if (( first_status != 0 )); then
    exit "$first_status"
  fi
  exit "$server_status"
fi
# 服务端只会在收到客户端成功退出码后报告成功；提前退出代表启动或运行失败。
exit "$first_status"
