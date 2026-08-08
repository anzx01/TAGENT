#!/usr/bin/env bash
# 停止智能体后端
cd "$(dirname "$0")/.."

if [ -f .backend.pid ]; then
    PID=$(cat .backend.pid)
    if kill "$PID" 2>/dev/null; then
        echo "已停止后端 (pid $PID)"
    else
        echo "后端进程不存在或已退出"
    fi
    rm -f .backend.pid
else
    # 通过端口查找兜底
    PID=$(netstat -ano | grep ':18899' | grep LISTENING | awk '{print $NF}' | head -1)
    if [ -n "$PID" ]; then
        taskkill //PID "$PID" //F 2>/dev/null && echo "已按端口停止后端 (pid $PID)"
    else
        echo "未发现运行中的后端"
    fi
fi