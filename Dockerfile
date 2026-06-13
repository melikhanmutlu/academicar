FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# blender provides the headless USD/USDZ exporter used by
# converters.stl_converter.convert_glb_to_usdz to generate the iOS AR Quick Look
# companion (model.usdz served as ios-src). A real ios-src USDZ is what makes AR
# launch in both Safari and Chrome on iOS, with correct real-world scale. This
# mirrors the `blender` nixPkg in nixpacks.toml so AR works regardless of which
# builder Railway selects. Run as `blender --background` (no display required).
# python3-numpy: Debian's blender uses the system python and ships without a
# bundled numpy; its glTF importer fails with "No module named 'numpy'" unless
# the system numpy is present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates nodejs npm postgresql-client blender python3-numpy \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt package.json ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && npm install

COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --worker-class gthread --threads 8 --timeout 180"]
