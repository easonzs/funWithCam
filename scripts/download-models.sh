#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODEL_DIR="$PROJECT_DIR/runtime/models"
SPEECH_NAME=sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23
SPEECH_DIR="$MODEL_DIR/$SPEECH_NAME"
SPEECH_BASE="https://huggingface.co/csukuangfj/$SPEECH_NAME/resolve/main"

mkdir -p "$MODEL_DIR" "$SPEECH_DIR"

verify_file() {
    file_path=$1
    expected=$2
    actual=$(shasum -a 256 "$file_path" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        echo "模型校验失败: $file_path" >&2
        echo "期望: $expected" >&2
        echo "实际: $actual" >&2
        exit 1
    fi
}

copy_face_model() {
    filename=$1
    checksum=$2
    destination="$MODEL_DIR/$filename"
    if [ ! -f "$destination" ]; then
        if [ ! -f "$PROJECT_DIR/models/$filename" ]; then
            echo "仓库缺少人脸模型: models/$filename" >&2
            exit 1
        fi
        cp "$PROJECT_DIR/models/$filename" "$destination"
    fi
    verify_file "$destination" "$checksum"
}

download_speech_file() {
    filename=$1
    checksum=$2
    destination="$SPEECH_DIR/$filename"
    if [ ! -f "$destination" ]; then
        temporary="$destination.download"
        echo "下载语音模型: $filename"
        curl --fail --location --retry 3 \
            "$SPEECH_BASE/$filename?download=true" \
            --output "$temporary"
        mv "$temporary" "$destination"
    fi
    verify_file "$destination" "$checksum"
}

copy_face_model \
    face_detection_yunet_2023mar.onnx \
    8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
copy_face_model \
    face_recognition_sface_2021dec.onnx \
    0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79

download_speech_file \
    encoder-epoch-99-avg-1.int8.onnx \
    1c556ea57cec304e55ec4b72e52c1cc098bb01476ed7d90f3de939fe126487b1
download_speech_file \
    decoder-epoch-99-avg-1.onnx \
    5ee0f03a2768ff1d5c83ef3a493243c7935d316cd41280037b14783a3467cc78
download_speech_file \
    joiner-epoch-99-avg-1.int8.onnx \
    a7cf9d82757bdcf786059454495a9ca95e4bd7347f72473fc08d794475c36169
download_speech_file \
    tokens.txt \
    8b294db9045d6e5f94647f4c1eec1af4da143a75053c399611444b378ff966ac

echo "模型已准备并完成 SHA-256 校验。"
