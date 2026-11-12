# syntax=docker/dockerfile:1
#
# このBOT専用の最小構成 ffmpeg をビルドする。
# 用途: HTTPS/HLS から取得した音声をデコードして 48kHz/2ch の s16le を stdout に出すだけ。
# (フル機能の静的ビルドは約130MB、これは数MB)
#
# ローカルでのビルド:
#   docker buildx build --target binary --output type=local,dest=out docker/ffmpeg
#   -> out/ffmpeg
#
# 実行側イメージ (python:3.12-slim, Debian trixie) の libssl3 / glibc にリンクするので、
# ビルド環境も同じベースイメージにしている。ベースを変えるときは両方そろえること。
FROM python:3.12-slim AS build

ARG FFMPEG_VERSION=7.1.5
ARG FFMPEG_SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
# 同じ FFMPEG_VERSION でビルド設定 (configure フラグなど) を変えたときに上げる。
# リリースタグは ffmpeg-<FFMPEG_VERSION>-r<FFMPEG_REVISION>
ARG FFMPEG_REVISION=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential nasm pkg-config libssl-dev curl ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSLO "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.xz" | sha256sum -c - \
    && tar xf "ffmpeg-${FFMPEG_VERSION}.tar.xz" --strip-components=1
RUN ./configure \
        --prefix=/opt/ffmpeg \
        --disable-everything --disable-autodetect \
        --disable-doc --disable-debug --disable-ffplay --disable-ffprobe \
        --enable-small --enable-version3 --enable-openssl \
        --enable-protocol=file,pipe,http,https,tcp,tls,crypto,data \
        --enable-demuxer=matroska,mov,hls,mpegts,aac,mp3,ogg,flac,wav \
        --enable-parser=opus,vorbis,aac,aac_latm,mpegaudio,flac,h264,hevc,av1,vp9 \
        --enable-decoder=opus,vorbis,aac,aac_latm,mp3,mp3float,flac,pcm_s16le,pcm_s16be \
        --enable-encoder=pcm_s16le \
        --enable-muxer=pcm_s16le \
        --enable-filter=aresample,aformat,anull,abuffer,abuffersink \
        --enable-bsf=aac_adtstoasc,extract_extradata \
    && make -j"$(nproc)" install \
    && strip /opt/ffmpeg/bin/ffmpeg

# `--target binary --output type=local,dest=<dir>` でバイナリだけを取り出す
FROM scratch AS binary
COPY --from=build /opt/ffmpeg/bin/ffmpeg /ffmpeg
