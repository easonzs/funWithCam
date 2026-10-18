#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

BUILD_LOCAL=0
if [ "${1:-}" = "--build" ]; then
    BUILD_LOCAL=1
    shift
fi
if [ "$#" -ne 0 ]; then
    echo "用法: ./deploy.sh [--build]" >&2
    exit 2
fi

fail() {
    echo "部署未完成：$1" >&2
    exit 1
}

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

stream_is_defined() {
    curl --fail --silent --show-error \
        "http://127.0.0.1:${GO2RTC_API_PORT}/api/streams" \
        | grep -Eq '"study_room"[[:space:]]*:'
}

read_secret() {
    prompt=$1
    printf "%s" "$prompt" >&2
    terminal_state=$(stty -g)
    stty -echo
    IFS= read -r secret
    stty "$terminal_state"
    printf "\n" >&2
    printf "%s" "$secret"
}

if ! command -v docker >/dev/null 2>&1; then
    fail "未找到 Docker。请先安装并启动 Docker Desktop。"
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker Desktop 尚未启动，或当前用户无法连接 Docker。"
fi
if ! command -v curl >/dev/null 2>&1; then
    fail "未找到 curl，无法执行服务和摄像头检查。"
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "已生成环境配置：$PROJECT_DIR/.env"
else
    echo "保留已有环境配置：$PROJECT_DIR/.env"
fi

ADMIN_USERNAME=$(env_value ADMIN_USERNAME)
ADMIN_USERNAME=${ADMIN_USERNAME:-parent}
WEB_PORT=$(env_value WEB_PORT)
WEB_PORT=${WEB_PORT:-8000}
GO2RTC_API_PORT=$(env_value GO2RTC_API_PORT)
GO2RTC_API_PORT=${GO2RTC_API_PORT:-1984}

"$PROJECT_DIR/scripts/prepare-runtime.sh"
docker compose config --quiet

if [ "$BUILD_LOCAL" -eq 1 ]; then
    export FUNWITHCAM_IMAGE=funwithcam:local
    export GO2RTC_IMAGE=go2rtc-xiaomi-audio:local
    echo "开发模式：从源码构建 FunWithCam 和 Xiaomi 音频修正版 go2rtc。"
    docker compose build go2rtc funwithcam
else
    echo "正式模式：从镜像仓库拉取 FunWithCam 和 go2rtc。"
    if ! docker compose pull; then
        echo >&2
        echo "无法拉取 FunWithCam 镜像。" >&2
        echo "默认地址记录在 .env 的 FUNWITHCAM_IMAGE 中。" >&2
        echo "请确认 GitHub Actions 已发布镜像，且 GHCR 包已设为公开。" >&2
        echo "如果包保持私有，请先执行 docker login ghcr.io。" >&2
        echo "开发者也可显式运行 ./deploy.sh --build 在本机构建。" >&2
        exit 1
    fi
fi

docker compose up -d go2rtc
if ! wait_for_url \
    "http://127.0.0.1:${GO2RTC_API_PORT}/api/streams" 30; then
    echo "go2rtc 最近日志：" >&2
    docker compose logs --tail=100 go2rtc >&2 || true
    fail "go2rtc 未能在 ${GO2RTC_API_PORT} 端口正常启动。"
fi

if ! stream_is_defined; then
    echo
    echo "需要完成小米摄像头初始化："
    echo "  1. 浏览器打开 http://127.0.0.1:${GO2RTC_API_PORT}"
    echo "  2. 选择 Add -> Xiaomi，按页面完成小米账号登录和验证码"
    echo "  3. 选择目标摄像头，并将视频流名称设置为 study_room"
    echo
    echo "小米账号密码由 go2rtc 页面处理，不写入 .env。"
    if command -v open >/dev/null 2>&1; then
        open "http://127.0.0.1:${GO2RTC_API_PORT}" >/dev/null 2>&1 || true
    fi
    if [ -t 0 ]; then
        printf "配置并保存完成后，按回车继续检查："
        IFS= read -r ignored
    else
        fail "当前终端不可交互。请配置 study_room 后重新运行 ./deploy.sh。"
    fi
fi

if ! stream_is_defined; then
    echo >&2
    echo "仍未发现名为 study_room 的视频流。请检查：" >&2
    echo "  配置文件：$PROJECT_DIR/runtime/config/go2rtc.yaml" >&2
    echo "  管理页面：http://127.0.0.1:${GO2RTC_API_PORT}" >&2
    echo "  日志命令：docker compose logs --tail=100 go2rtc" >&2
    echo "  网络要求：新 Mac 与摄像头位于同一家庭局域网，摄像头 IP 可达" >&2
    exit 1
fi

docker compose up -d --remove-orphans
if ! wait_for_url "http://127.0.0.1:${WEB_PORT}/health/live" 45; then
    echo "FunWithCam 最近日志：" >&2
    docker compose logs --tail=100 funwithcam >&2 || true
    fail "FunWithCam Web 服务未能正常启动。"
fi

if ! docker compose exec -T funwithcam \
    funwithcam admin-exists \
    --database /app/data/funwithcam.db \
    --username "$ADMIN_USERNAME" >/dev/null 2>&1; then
    if [ ! -t 0 ]; then
        fail "尚未创建家长账号，请在交互式终端重新运行 ./deploy.sh。"
    fi
    echo
    echo "创建家长 Web 账号：$ADMIN_USERNAME"
    while :; do
        password=$(read_secret "家长密码（至少 8 个字符）: ")
        confirmation=$(read_secret "再次输入密码: ")
        if [ "$password" != "$confirmation" ]; then
            echo "两次输入的密码不一致，请重试。" >&2
            continue
        fi
        if [ "${#password}" -lt 8 ]; then
            echo "密码至少需要 8 个字符，请重试。" >&2
            continue
        fi
        break
    done
    printf "%s\n" "$password" \
        | docker compose exec -T funwithcam \
            funwithcam create-admin \
            --config /app/config/config.yaml \
            --database /app/data/funwithcam.db \
            --username "$ADMIN_USERNAME" \
            --password-stdin
    unset password confirmation
fi

echo
echo "检查摄像头视频和音频数据流..."
if ! docker compose exec -T funwithcam \
    funwithcam media-check \
    --url rtsp://go2rtc:8554/study_room \
    --timeout-seconds 20 \
    --require-audio; then
    echo >&2
    echo "摄像头初始化未通过。请按以下顺序排查：" >&2
    echo "  1. 在 http://127.0.0.1:${GO2RTC_API_PORT} 查看 study_room 状态" >&2
    echo "  2. 确认新 Mac 与小米摄像头在同一局域网" >&2
    echo "  3. 查看 runtime/config/go2rtc.yaml 是否已保存设备信息" >&2
    echo "  4. 运行 docker compose logs --tail=100 go2rtc" >&2
    echo "  5. 修复后重新运行 ./deploy.sh；已有账号和数据不会被覆盖" >&2
    exit 1
fi

echo
echo "FunWithCam 冷启动初始化完成："
echo "  Web:    http://127.0.0.1:${WEB_PORT}"
echo "  账号:   ${ADMIN_USERNAME}"
echo "  健康:   http://127.0.0.1:${WEB_PORT}/health/ready"
echo "  go2rtc: http://127.0.0.1:${GO2RTC_API_PORT}"
echo
echo "配置保存在 .env 和 runtime/config/；家庭记录保存在 runtime/data/。"
echo "查看日志：docker compose logs -f"
