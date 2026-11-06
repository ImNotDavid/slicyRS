# syntax=docker/dockerfile:1
# --- web build -------------------------------------------------------------
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- API + Bambu Studio CLI --------------------------------------------------
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip curl ca-certificates xvfb xauth \
      libgtk-3-0 libwebkit2gtk-4.1-0 libglu1-mesa libosmesa6 libegl1 libgl1 libgl1-mesa-dri \
      libsecret-1-0 libdbus-1-3 libmspack0 libsoup2.4-1 libnotify4 libxkbcommon0 libfontconfig1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# The slicer engine. Pin an exact release: profile names and file layout change between versions, and the tests in
# backend/tests/test_profile_fidelity.py must pass against whatever is pinned here.
# Releases: https://github.com/bambulab/BambuStudio/releases (the "ubu22" AppImage matches this base image).
ARG BAMBU_URL=https://github.com/bambulab/BambuStudio/releases/download/v02.09.01.70/BambuStudio_ubu22_v02.09.01.70-20261103183404.AppImage
# Optional integrity check: set to the file's SHA-256 once you have verified a download (the build prints it).
ARG BAMBU_SHA256=e0dfb6dafe0181b6bf8635bbb9a7b106f02f2343f8e41949f1d156b3f36241e5
RUN curl -fL --retry 3 "$BAMBU_URL" -o /tmp/bambu.AppImage \
    && echo "AppImage SHA-256: $(sha256sum /tmp/bambu.AppImage | cut -d' ' -f1)" \
    && if [ -n "$BAMBU_SHA256" ]; then echo "$BAMBU_SHA256  /tmp/bambu.AppImage" | sha256sum -c -; fi \
    && chmod +x /tmp/bambu.AppImage \
    && cd /opt && /tmp/bambu.AppImage --appimage-extract >/dev/null \
    && mv /opt/squashfs-root /opt/bambu-studio && rm /tmp/bambu.AppImage \
    && chmod -R a+rX /opt/bambu-studio \
    && PROFILES="$(find /opt/bambu-studio -type d -path '*profiles/BBL' | head -1 | xargs -r dirname)" \
    && test -n "$PROFILES" || (echo "no profiles directory found in the AppImage" && exit 1) \
    && ln -s "$(find /opt/bambu-studio -type d -path '*profiles/BBL' | head -1 | xargs dirname)" /opt/slicer-profiles \
    && printf '#!/bin/sh\nexport XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-slicer}"; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"\nexport GDK_BACKEND=x11; unset WAYLAND_DISPLAY\nexec xvfb-run -a /opt/bambu-studio/AppRun "$@"\n' > /usr/local/bin/slicer \
    && chmod +x /usr/local/bin/slicer

WORKDIR /srv/slicyrs
COPY backend/requirements.txt backend/
RUN pip3 install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/scripts backend/scripts
COPY --from=web /web/dist frontend/dist

ENV SLICER_MODE=orca \
    ORCA_BIN=/usr/local/bin/slicer \
    ORCA_PROFILES_DIR=/opt/slicer-profiles \
    SLICER_NAME="Bambu Studio" \
    SLICY_DATA_DIR=/data \
    SEED_DEMO=0
RUN useradd --system --create-home slicy && mkdir -p /data && chown slicy /data
VOLUME /data
USER slicy
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:8000/api/health || exit 1
CMD ["python3", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
