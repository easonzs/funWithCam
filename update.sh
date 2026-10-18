#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

env_value() {
    key=$1
    sed -n "s/^${key}=//p" "$PROJECT_DIR/.env" | tail -n 1
}

wait_for_url() {
    url=$1
    attempts=$2
    while [ "$attempts" -gt 0 ]; do
        if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 2
    done
    return 1
}

pull_image() {
    label=$1
    image=$2
    echo "拉取 ${label}：${image}"
    if docker pull "$image"; then
        return
    fi
    echo "${label}拉取失败：${image}" >&2
    echo "私有 GHCR Package 请先执行 docker login ghcr.io。" >&2
    exit 1
}

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
if grep -Eq '^GO2RTC_IMAGE=(alexxit/go2rtc:1\.9\.14|ghcr\.io/easonzs/go2rtc-xiaomi-audio:1\.9\.14-fix\.[12])$' "$PROJECT_DIR/.env"; then
    sed -i.bak \
        -e 's|^GO2RTC_IMAGE=alexxit/go2rtc:1\.9\.14$|GO2RTC_IMAGE=ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-fix.3|' \
        -e 's|^GO2RTC_IMAGE=ghcr\.io/easonzs/go2rtc-xiaomi-audio:1\.9\.14-fix\.[12]$|GO2RTC_IMAGE=ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-fix.3|' \
        "$PROJECT_DIR/.env"
    rm -f "$PROJECT_DIR/.env.bak"
    echo "已切换到 Xiaomi 扬声器噪音修正版 go2rtc 镜像。"
fi

FUNWITHCAM_IMAGE=$(env_value FUNWITHCAM_IMAGE)
FUNWITHCAM_IMAGE=${FUNWITHCAM_IMAGE:-ghcr.io/easonzs/funwithcam:latest}
GO2RTC_IMAGE=$(env_value GO2RTC_IMAGE)
GO2RTC_IMAGE=${GO2RTC_IMAGE:-ghcr.io/easonzs/go2rtc-xiaomi-audio:1.9.14-fix.3}
WEB_PORT=$(env_value WEB_PORT)
WEB_PORT=${WEB_PORT:-8000}
GO2RTC_API_PORT=$(env_value GO2RTC_API_PORT)
GO2RTC_API_PORT=${GO2RTC_API_PORT:-1984}
export FUNWITHCAM_IMAGE GO2RTC_IMAGE

docker compose config --quiet
pull_image "go2rtc 镜像" "$GO2RTC_IMAGE"
pull_image "FunWithCam 镜像" "$FUNWITHCAM_IMAGE"
docker compose up -d --remove-orphans

if ! wait_for_url \
    "http://127.0.0.1:${GO2RTC_API_PORT}/api/streams" 30; then
    docker compose logs --tail=100 go2rtc >&2 || true
    echo "容器已更新，但 go2rtc 健康检查失败。" >&2
    exit 1
fi
if ! wait_for_url "http://127.0.0.1:${WEB_PORT}/health/ready" 45; then
    docker compose logs --tail=100 funwithcam >&2 || true
    echo "容器已更新，但 FunWithCam 健康检查失败。" >&2
    exit 1
fi
if ! docker compose exec -T funwithcam \
    funwithcam media-check \
    --url rtsp://go2rtc:8554/study_room \
    --timeout-seconds 20 \
    --require-audio; then
    echo "容器已更新，但摄像头视频/音频验证失败。" >&2
    echo "请运行 ./scripts/diagnose.sh 查看详细信息。" >&2
    exit 1
fi

echo "更新完成。runtime/ 中的账号、配置、历史记录和人脸资料均已保留。"
echo "FunWithCam: $FUNWITHCAM_IMAGE"
echo "go2rtc:     $GO2RTC_IMAGE"
docker compose ps
