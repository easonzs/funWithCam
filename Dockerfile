FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        espeak-ng \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".[speech]"

ARG SPEECH_MODEL_NAME=sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23
ARG SPEECH_MODEL_BASE=https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23/resolve/main

RUN mkdir -p "/app/models/${SPEECH_MODEL_NAME}" \
    && curl --fail --location --retry 3 \
        "${SPEECH_MODEL_BASE}/encoder-epoch-99-avg-1.int8.onnx?download=true" \
        --output "/app/models/${SPEECH_MODEL_NAME}/encoder-epoch-99-avg-1.int8.onnx" \
    && echo "1c556ea57cec304e55ec4b72e52c1cc098bb01476ed7d90f3de939fe126487b1  /app/models/${SPEECH_MODEL_NAME}/encoder-epoch-99-avg-1.int8.onnx" \
        | sha256sum --check - \
    && curl --fail --location --retry 3 \
        "${SPEECH_MODEL_BASE}/decoder-epoch-99-avg-1.onnx?download=true" \
        --output "/app/models/${SPEECH_MODEL_NAME}/decoder-epoch-99-avg-1.onnx" \
    && echo "5ee0f03a2768ff1d5c83ef3a493243c7935d316cd41280037b14783a3467cc78  /app/models/${SPEECH_MODEL_NAME}/decoder-epoch-99-avg-1.onnx" \
        | sha256sum --check - \
    && curl --fail --location --retry 3 \
        "${SPEECH_MODEL_BASE}/joiner-epoch-99-avg-1.int8.onnx?download=true" \
        --output "/app/models/${SPEECH_MODEL_NAME}/joiner-epoch-99-avg-1.int8.onnx" \
    && echo "a7cf9d82757bdcf786059454495a9ca95e4bd7347f72473fc08d794475c36169  /app/models/${SPEECH_MODEL_NAME}/joiner-epoch-99-avg-1.int8.onnx" \
        | sha256sum --check - \
    && curl --fail --location --retry 3 \
        "${SPEECH_MODEL_BASE}/tokens.txt?download=true" \
        --output "/app/models/${SPEECH_MODEL_NAME}/tokens.txt" \
    && echo "8b294db9045d6e5f94647f4c1eec1af4da143a75053c399611444b378ff966ac  /app/models/${SPEECH_MODEL_NAME}/tokens.txt" \
        | sha256sum --check -

COPY models/face_detection_yunet_2023mar.onnx \
    models/face_recognition_sface_2021dec.onnx \
    models/LICENSE-YuNet.txt \
    models/LICENSE-SFace.txt \
    /app/models/

RUN useradd --create-home --uid 1000 funwithcam \
    && mkdir -p /app/audio /app/config /app/data \
    && chown -R funwithcam:funwithcam /app

USER funwithcam

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

ENTRYPOINT ["funwithcam"]
CMD ["serve", "--config", "/app/config/config.yaml", "--database", "/app/data/funwithcam.db"]
