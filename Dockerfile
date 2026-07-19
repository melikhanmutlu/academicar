FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Blender provides the headless USD/USDZ exporter used by
# converters.stl_converter.convert_glb_to_usdz to generate the iOS AR Quick Look
# companion (model.usdz served as ios-src). A real ios-src USDZ is what makes AR
# launch in both Safari and Chrome on iOS, with correct real-world scale.
#
# We install the official blender.org build rather than Debian's `blender`
# package: the Debian package is built WITHOUT USD support (bpy.ops.wm.usd_export
# does not exist) and also ships without a bundled numpy or the Draco decoder.
# The official portable build is self-contained — it bundles its own Python with
# numpy, the Draco decoder, and a USD-enabled exporter — so GLB->USDZ works out
# of the box. Only the X/GL shared libraries it dynamically links need to be
# present; no display is required for `blender --background`.
ENV BLENDER_VERSION=4.3.2 \
    BLENDER_MAJOR=4.3
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils nodejs npm postgresql-client libreoffice-impress \
        libgl1 libegl1 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 \
        libsm6 libx11-6 libxext6 libgomp1 \
    && ( curl -fsSL "https://download.blender.org/release/Blender${BLENDER_MAJOR}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o /tmp/blender.tar.xz \
        || curl -fsSL "https://mirrors.ocf.berkeley.edu/blender/release/Blender${BLENDER_MAJOR}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o /tmp/blender.tar.xz ) \
    && mkdir -p /opt/blender \
    && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 \
    && ln -s /opt/blender/blender /usr/local/bin/blender \
    && rm -f /tmp/blender.tar.xz \
    && rm -rf /var/lib/apt/lists/* \
    && blender --background --version

COPY requirements.txt package.json ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && npm install

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --worker-class gthread --threads 8 --timeout 180"]
