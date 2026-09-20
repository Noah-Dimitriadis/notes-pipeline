# M10 — api service image.
#
# Build context is the repo root (see deploy/docker-compose.yml), so this
# can `pip install` notes_pipeline directly. CMD runs the real FastAPI +
# FastMCP app (notes_pipeline/webapi.py, M13) via uvicorn.
# deploy/api/placeholder.py is now unused, kept only as the pre-M13 record.
#
# GPU passthrough is AMD Vulkan (RADV), not ROCm/HIP — whisper.cpp is built
# with -DGGML_VULKAN=1. /dev/kfd is still passed through by docker-compose.yml
# per PLAN.md M10, in case a future ROCm build wants it; this image itself
# only needs /dev/dri + libvulkan.

# ---- stage 1: build whisper.cpp with Vulkan -------------------------------
# trixie, not bookworm: bookworm's libvulkan-dev (1.3.239) predates the
# VK_EXT_layer_settings API ggml-vulkan's instance init now uses, and fails
# to compile with "'LayerSettingEXT' is not a member of 'vk'". Matches the
# runtime stage below, which is python:3.13-slim (also trixie-based).
FROM debian:trixie-slim AS whisper-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        libvulkan-dev glslang-tools glslc spirv-headers spirv-tools pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Pin a specific tag before this ever gets built for real — HEAD is not
# reproducible. Verify -DGGML_VULKAN=1 is still the correct flag name for
# whatever tag is pinned; ggml's build flags have moved before.
ARG WHISPER_CPP_REF=master
RUN git clone --depth 1 --branch ${WHISPER_CPP_REF} \
        https://github.com/ggerganov/whisper.cpp.git /src/whisper.cpp
WORKDIR /src/whisper.cpp
RUN cmake -B build -DGGML_VULKAN=1 -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j --config Release

# ---- stage 2: runtime -------------------------------------------------------
FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg poppler-utils \
        libvulkan1 mesa-vulkan-drivers \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-cli /usr/local/bin/whisper-cli
# whisper.cpp's shared build also produces libwhisper/libggml*.so alongside
# the binary in build/bin on most recent tags — copy the whole directory's
# .so files too so whisper-cli can find them without a separate ldconfig step.
COPY --from=whisper-builder /src/whisper.cpp/build/bin/*.so /usr/local/lib/
RUN ldconfig

# Must numerically match the host's `render`/`video` GIDs for /dev/dri
# passthrough to actually grant access — placeholders here, override with
# --build-arg from deploy/.env (see .env.example). Verify on treehouse with
# `getent group render video`.
ARG RENDER_GID=104
ARG VIDEO_GID=44
RUN groupadd -g ${VIDEO_GID} video 2>/dev/null || true \
    && groupadd -g ${RENDER_GID} render 2>/dev/null || true \
    && useradd --create-home --uid 1000 --gid 100 \
        --groups video,render notes

WORKDIR /app
COPY pyproject.toml ./
COPY notes_pipeline/ ./notes_pipeline/
RUN pip install --no-cache-dir .

USER notes
EXPOSE 8000
CMD ["uvicorn", "notes_pipeline.webapi:app", "--host", "0.0.0.0", "--port", "8000"]
