#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# run.sh 已提供 SERVER_HOST、SERVER_PORT、EXPECTED_PACKAGES。
# 换语言时，把下一行替换为自己的可执行程序，例如：exec ./bin/contestant
# 自有程序读取上述环境变量，或在此将其转换为自己程序的命令行参数。
# 保持前台运行并使用 exec，正常完成返回 0，失败返回非零值。
exec python3 -u src/main.py
