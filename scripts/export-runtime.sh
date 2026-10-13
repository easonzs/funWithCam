#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR="$PROJECT_DIR/runtime"
BACKUP_DIR="$PROJECT_DIR/backups"

if [ ! -d "$RUNTIME_DIR" ]; then
    echo "runtime 目录不存在，请先运行 scripts/prepare-runtime.sh。" >&2
    exit 1
fi
if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "未找到 sqlite3，无法安全备份正在使用的数据库。" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAGING_DIR=$(mktemp -d "${TMPDIR:-/tmp}/funwithcam-export.XXXXXX")
trap 'rm -rf "$STAGING_DIR"' EXIT INT TERM

mkdir -p "$STAGING_DIR/runtime"
rsync -a \
    --exclude 'funwithcam.db' \
    --exclude 'funwithcam.db-shm' \
    --exclude 'funwithcam.db-wal' \
    "$RUNTIME_DIR/" "$STAGING_DIR/runtime/"

DATABASE="$RUNTIME_DIR/data/funwithcam.db"
if [ -f "$DATABASE" ]; then
    mkdir -p "$STAGING_DIR/runtime/data"
    sqlite3 "$DATABASE" \
        ".backup '$STAGING_DIR/runtime/data/funwithcam.db'"
fi

ARCHIVE="$BACKUP_DIR/funwithcam-runtime-$(date '+%Y%m%d-%H%M%S').tar.gz"
tar -czf "$ARCHIVE" -C "$STAGING_DIR" runtime
chmod 600 "$ARCHIVE"

echo "迁移包已生成：$ARCHIVE"
echo "该文件包含家庭数据、人脸特征、摄像头凭据和历史记录，请妥善保管。"
