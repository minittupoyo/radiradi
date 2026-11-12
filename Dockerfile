# syntax=docker/dockerfile:1

# ---- ビルド段: 依存を venv に入れる。uv やビルド成果物は最終イメージに持ち込まない ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# 依存だけ先に入れて、ソース変更時もこの層をキャッシュできるようにする
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- ffmpeg 段: GitHub Actions (.github/workflows/ffmpeg.yml) でビルドしたバイナリを取得する ----
# このBOT専用の最小構成 (約3MB)。ビルド手順は docker/ffmpeg/Dockerfile。
# 更新するときは、ワークフローが作るリリースのタグに FFMPEG_RELEASE を合わせる。
FROM python:3.12-slim AS ffmpeg
ARG TARGETARCH
ARG FFMPEG_RELEASE=ffmpeg-7.1.5-r1
ARG FFMPEG_BASE_URL=https://github.com/minittupoyo/radiradi/releases/download/${FFMPEG_RELEASE}
ADD ${FFMPEG_BASE_URL}/ffmpeg-linux-${TARGETARCH} /dl/ffmpeg-linux-${TARGETARCH}
ADD ${FFMPEG_BASE_URL}/SHA256SUMS /dl/SHA256SUMS
RUN cd /dl \
    && grep " ffmpeg-linux-${TARGETARCH}\$" SHA256SUMS | sha256sum -c - \
    && install -m 0755 "ffmpeg-linux-${TARGETARCH}" /ffmpeg

# ---- 実行段 ----
FROM python:3.12-slim

# libopus0: 音声エンコード / quickjs: yt-dlp の EJS 用 JS ランタイム (deno や node より遥かに小さい)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 quickjs \
    && rm -rf /var/lib/apt/lists/*

# 最小構成の ffmpeg (libssl3 はベースイメージに含まれる)
COPY --from=ffmpeg /ffmpeg /usr/local/bin/ffmpeg

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CACHE_HOME=/tmp/.cache

RUN useradd --system --no-create-home bot
USER bot

CMD ["radiradi"]
