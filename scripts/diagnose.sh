#!/bin/sh
set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

env_value() {
    key=$1
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        return
    fi
    sed -n "s/^${key}=//p" "$PROJECT_DIR/.env" | tail -n 1
}

WEB_PORT=$(env_value WEB_PORT)
WEB_PORT=${WEB_PORT:-8000}
GO2RTC_API_PORT=$(env_value GO2RTC_API_PORT)
GO2RTC_API_PORT=${GO2RTC_API_PORT:-1984}

echo "== Docker =="
docker version 2>&1 || true
echo
echo "== Compose services =="
docker compose ps 2>&1 || true
echo
echo "== FunWithCam health =="
curl --silent --show-error \
    "http://127.0.0.1:${WEB_PORT}/health/ready" 2>&1 || true
echo
echo
echo "== go2rtc streams =="
curl --silent --show-error \
    "http://127.0.0.1:${GO2RTC_API_PORT}/api/streams" 2>&1 || true
echo
echo
echo "== Camera video/audio probe =="
docker compose exec -T funwithcam \
    funwithcam media-check \
    --url rtsp://go2rtc:8554/study_room \
    --timeout-seconds 20 \
    --require-audio 2>&1 || true
echo
echo
echo "== Recent FunWithCam logs =="
docker compose logs --tail=100 funwithcam 2>&1 || true
echo
echo "== Recent go2rtc logs =="
docker compose logs --tail=100 go2rtc 2>&1 || true
