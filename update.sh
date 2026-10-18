#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "尚未完成初始化，请先运行 ./deploy.sh。" >&2
    exit 1
fi
if ! command -v docker >/dev/null 2>&1 \
    || ! docker info >/dev/null 2>&1; then
    echo "Docker Desktop 未安装或尚未启动。" >&2
    exit 1
fi

git pull --ff-only
if grep -q '^GO2RTC_IMAGE=alexxit/go2rtc:1\.9\.14$' "$PROJECT_DIR/.env"; then
    sed -i.bak \
        's|^GO2RTC_IMAGE=alexxit/go2rtc:1\.9\.14$|GO2RTC_IMAGE=ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-fix.1|' \
        "$PROJECT_DIR/.env"
    rm -f "$PROJECT_DIR/.env.bak"
    echo "已切换到 Xiaomi 扬声器噪音修正版 go2rtc 镜像。"
fi
docker compose config --quiet
docker compose pull
docker compose up -d --remove-orphans

echo "更新完成。runtime/ 中的账号、配置、历史记录和人脸资料均已保留。"
docker compose ps
