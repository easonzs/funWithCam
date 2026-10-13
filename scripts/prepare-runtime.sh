#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR="$PROJECT_DIR/runtime"
GO2RTC_SOURCE=""

if [ "${1:-}" = "--go2rtc-config" ]; then
    if [ -z "${2:-}" ]; then
        echo "用法: $0 [--go2rtc-config /path/to/go2rtc.yaml]" >&2
        exit 2
    fi
    GO2RTC_SOURCE=$2
fi

mkdir -p \
    "$RUNTIME_DIR/audio" \
    "$RUNTIME_DIR/config" \
    "$RUNTIME_DIR/data"

if [ ! -f "$RUNTIME_DIR/config/funwithcam.yaml" ]; then
    cp "$PROJECT_DIR/config/config.docker.yaml" \
        "$RUNTIME_DIR/config/funwithcam.yaml"
fi

if [ -n "$GO2RTC_SOURCE" ]; then
    if [ ! -f "$GO2RTC_SOURCE" ]; then
        echo "找不到 go2rtc 配置: $GO2RTC_SOURCE" >&2
        exit 1
    fi
    cp "$GO2RTC_SOURCE" "$RUNTIME_DIR/config/go2rtc.yaml"
elif [ ! -f "$RUNTIME_DIR/config/go2rtc.yaml" ]; then
    cp "$PROJECT_DIR/deploy/go2rtc.example.yaml" \
        "$RUNTIME_DIR/config/go2rtc.yaml"
fi

# 容器间访问需要 go2rtc API 监听所有容器网卡。宿主机端口仍由
# compose.yaml 限制为 127.0.0.1，不会向家庭局域网直接公开。
sed -i.bak \
    -e 's/listen: "127\.0\.0\.1:1984"/listen: ":1984"/' \
    -e "s/listen: '127\\.0\\.0\\.1:1984'/listen: ':1984'/" \
    -e 's/listen: 127\.0\.0\.1:1984/listen: ":1984"/' \
    "$RUNTIME_DIR/config/go2rtc.yaml"
rm -f "$RUNTIME_DIR/config/go2rtc.yaml.bak"

if [ ! -f "$RUNTIME_DIR/data/funwithcam.db" ] \
    && [ -f "$PROJECT_DIR/data/funwithcam.db" ]; then
    rsync -a "$PROJECT_DIR/data/" "$RUNTIME_DIR/data/"
fi

chmod 700 "$RUNTIME_DIR/config"
chmod 600 "$RUNTIME_DIR/config/go2rtc.yaml"

echo "运行目录已准备: $RUNTIME_DIR"
