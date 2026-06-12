# AcademicAR

AcademicAR is a platform that empowers researchers to publish 3D and AR (Augmented Reality) models alongside their academic papers, posters, and presentations. It turns technical model files (STL, GLB, OBJ, FBX) into shareable academic assets accessible via browser-ready viewer links and QR codes.

## Core Features

- **3D Model Upload & Conversion**: Supports STL, GLB, OBJ, and FBX with automated conversion to AR-ready GLB formats.
- **Web-Based Viewer**: A full-screen browser viewer with metadata, screenshot capabilities, and mobile AR controls.
- **QR Code Generation**: Every model receives a persistent, shareable viewer URL and QR code.
- **Publication Archive**: Project-based model records preserving context, identifiers (DOI/PMID), and supplementary materials (PDFs).
- **Asynchronous Processing**: Background job queues for non-blocking file conversions.

## Project Structure

```text
academic_ar/
├── app.py                 # Main Flask application and route definitions
├── worker.py              # Background worker for handling conversion tasks
├── config.py              # Centralized environment configurations
├── models.py              # SQLAlchemy database schema definitions
├── auth.py                # Authentication, OAuth, and user management
├── converters/            # 3D model processing logic
│   ├── base_converter.py  # Abstract base class for converters
│   ├── external_converter.py # Wrappers for external CLI tools (fbx2gltf, obj2gltf)
│   └── stl_converter.py   # Native STL to GLB conversion logic
├── templates/             # Jinja2 HTML templates
├── static/                # Static assets (CSS, JS, images, models)
├── tests/                 # Pytest integration and unit tests
├── requirements.txt       # Python dependencies
└── package.json           # Node.js dependencies for conversion tools
```

## Setup Instructions

### Prerequisites
- **Python 3.12+**
- **Node.js 18+** (Required for `fbx2gltf` and `obj2gltf`)
- **PostgreSQL** (Recommended for production, SQLite used for local dev)

### 1. Environment Setup

Clone the repository and install dependencies:

```bash
# Set up Python virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Install Node tools for conversions
npm install
```

### 2. Configuration

Create a `.env` file in the root directory based on the `.env.example`:

```bash
cp .env.example .env
```

Ensure you set a secure `SECRET_KEY` and provide your database credentials if using PostgreSQL.

### 3. Database Initialization

Run the Flask application once to automatically initialize the SQLite database (if no `DATABASE_URL` is provided) or apply migrations:

```bash
python run_local_server.py
```

### 4. Running the Application Locally

Local development defaults to `DEV_INLINE_JOBS=1`, so model uploads are converted
immediately by the local web process. This keeps manual testing simple: upload a
model and it should appear in the registry as soon as the request finishes.

**Terminal 1 (Web Server):**
```bash
python run_local_server.py
```

**Optional worker parity test:**
```bash
# Set DEV_INLINE_JOBS=0 first, then run:
python worker.py
```

The application will be available at `http://localhost:5000`.

In production, model conversion is intentionally handled only by `worker.py`.
The web service writes `ConversionJob` records and returns; it must not run
CPU/RAM-heavy 3D conversion work inline.

## Testing

This project uses `pytest` for automated testing.

```bash
# Run the complete test suite
python -m pytest tests/ -v
```

## Linting & Formatting

The codebase uses `Black` for formatting and `Ruff` for linting. Configuration is located in `pyproject.toml`.

```bash
# Format code
black .

# Run linter
ruff check .
```

## Deployment

The project is configured for Railway (`railway.json`, `nixpacks.toml`, `Procfile`).
Use two Railway services from the same repo:

- **Web service:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 8 --timeout 180`
- **Worker service:** `python worker.py`

Set `APP_ENV=production`, `DATABASE_URL` to PostgreSQL, and `RATELIMIT_STORAGE_URI`
to the Redis URL. Attach a Railway Volume and set `STORAGE_ROOT` to the mount
path so uploaded sources, converted GLBs, PDFs, and QR images persist across
deploys. Production ignores `DEV_INLINE_JOBS` unless
`ALLOW_PRODUCTION_INLINE_JOBS=1` is explicitly set for emergency debugging.

### Conversion runtime dependencies

- **OBJ / FBX (CONVERT-3):** Conversion shells out to the Node CLIs `obj2gltf`
  and `fbx2gltf` (see `converters/external_converter.py`). These come from
  `package.json`, so **the deployment image must run `npm install`** and keep
  `node_modules/.bin` on `PATH`. If the binaries are missing the converter falls
  back to `npx`, which fetches them from the network at runtime — slow and
  fragile. On Railway, `nixpacks.toml` installs Node and runs `npm install`;
  verify this for any other host. STL/GLB conversion is pure-Python and needs no
  Node.
- **iOS USDZ (CONVERT-2):** The iOS AR Quick Look companion (`model.usdz`,
  served as `ios-src`) is generated by **headless Blender** via
  `tools/blender_usdz_export.py` (`converters.stl_converter.convert_glb_to_usdz`).
  A real `ios-src` USDZ is what makes AR launch in **both Safari and Chrome on
  iOS** (model-viewer's client-side blob auto-generation only works in Safari),
  and Blender writes the correct `metersPerUnit` so the model appears at a sane
  real-world scale. Blender is installed via `nixpacks.toml` in production; if
  the `blender` binary is absent (e.g. local dev), USDZ is skipped and the
  viewer falls back to GLB-only / model-viewer's on-device generation.
  Models uploaded before this was enabled can be backfilled in the
  production/worker container: `python scripts/backfill_usdz.py`
  (`--force` to regenerate, `--model <id>` for one, `--dry-run` to preview).
- **Mesh complexity caps (SEC-6/PERF-3):** Uploads above `MAX_MESH_FACES` /
  `MAX_MESH_VERTICES` (default 2,000,000 each) are rejected with a clear error
  to protect the worker from out-of-memory crashes. Tune via env.

> **Large binaries:** the repository tracks several large PDFs (e.g.
> `AcademicAR_Sunumu.pdf`, ~71 MB). Move these to Git LFS or out of the repo to
> keep clones fast (`git lfs track "*.pdf"`).
