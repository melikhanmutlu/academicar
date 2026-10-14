"""Marketing blog content for AcademicAR.

Posts are defined in-repo (version-controlled, no DB/admin needed) and rendered
from Markdown to HTML on demand (cached). Adding a post = append a dict to
``POSTS``. Titles/keywords are English (global SEO focus). When a post's
content is revised, set an optional ``"updated": "YYYY-MM-DD"`` so its
dateModified and sitemap lastmod move with it.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_RENDER_CACHE: dict[str, str] = {}

# Allowlist for sanitizing rendered Markdown. Bodies can come from admin-editable
# DB posts and are emitted with |safe, so raw inline HTML (markdown passes it
# through) must be filtered to block stored XSS.
_ALLOWED_TAGS = [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "del", "s", "sup", "sub", "blockquote",
    "ul", "ol", "li", "a", "code", "pre", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td", "img",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "id"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
    "ol": ["start"],
    "li": ["id"],
    "sup": ["id"],
    "div": ["class"],
    "span": ["class"],
    "code": ["class"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _sanitize_html(html: str) -> str:
    """Strip script/handlers and disallowed tags from rendered HTML."""
    try:
        import bleach
    except Exception:  # pragma: no cover - bleach should be installed
        # Fail closed: without a sanitizer, escape everything rather than emit
        # untrusted HTML through |safe.
        from markupsafe import escape

        return str(escape(html))
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


def render_body(text: str, cache_key: str | None = None) -> str:
    """Render a Markdown body to HTML.

    When ``cache_key`` is given the result is cached under it (use an immutable
    key like a slug for code posts, or ``db:<id>:<updated_at>`` for editable DB
    posts so an edit busts the cache).
    """
    if cache_key is not None and cache_key in _RENDER_CACHE:
        return _RENDER_CACHE[cache_key]
    try:
        import markdown

        html = markdown.markdown(text, extensions=["extra", "sane_lists", "smarty"])
    except Exception:  # pragma: no cover - markdown should be installed
        logger.warning("Markdown unavailable; serving escaped fallback")
        from markupsafe import escape

        html = "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip())
    # Sanitize BEFORE the table-wrap below so the wrapper div we add is kept.
    html = _sanitize_html(html)
    # Wrap tables so a wide one scrolls inside its own box instead of forcing
    # the whole page to scroll horizontally on mobile.
    if "<table>" in html:
        html = html.replace("<table>", '<div class="blog-table-wrap"><table>').replace(
            "</table>", "</table></div>"
        )
    if cache_key is not None:
        _RENDER_CACHE[cache_key] = html
    return html


POSTS: list[dict] = [
    {
        "slug": "how-to-add-3d-models-to-research-papers",
        "title": "How to Add Interactive 3D Models to Your Research Paper",
        "description": "A step-by-step guide to embedding interactive 3D models and AR in academic papers, theses, and posters using a QR code — no coding required.",
        "date": "2026-06-02",
        "author": "AcademicAR Team",
        "tags": ["3D publishing", "how-to", "QR codes"],
        "persona": "Researchers & PhD students",
        "read_minutes": 6,
        "body": """\
Static figures flatten three-dimensional data. A micrograph, a reconstructed
fossil, a protein, an engineered part — all lose information the moment they
become a 2D image on a page. Interactive 3D fixes that: readers rotate, zoom,
and inspect the actual geometry, and on a phone they can place it in their room
in augmented reality. Here's how to add that to your next publication.

## 1. Export a clean 3D model

Start from whatever your pipeline produces — a CT/MRI segmentation, a
photogrammetry scan, a CAD assembly, or a molecular surface — and export to one
of the common interchange formats: **GLB/glTF, STL, OBJ, or FBX**. Decimate
extreme polygon counts (a few hundred thousand triangles is plenty for the web)
and make sure the model is watertight and correctly scaled.

## 2. Upload and let it convert

Upload the file to AcademicAR. It is automatically converted to web-optimized
**GLB** with Draco geometry compression and compressed textures, and a companion
**USDZ** is generated so iPhones and iPads can launch AR directly. You don't need
a 3D engineer or a custom WebGL build.

## 3. Get your viewer link and QR code

Every model gets a stable public viewer URL and a **stable QR code**. Because
the QR resolves through a fixed identifier, it keeps working even if you later
replace the file, recolor it, or extend its license — so the QR you print today
will not rot.

## 4. Place it in your paper, thesis, or poster

- **Journal/PDF:** add a figure note such as *"Interactive 3D model available at
  [link] — scan the QR code"* and drop the QR image next to the figure.
- **Conference poster:** put the QR in the corner of the relevant panel; ~half of
  attendees will scan a poster QR, turning a static panel into a hands-on demo.
- **Thesis:** link from the figure caption; examiners can explore the geometry
  while they read.
- **Website/repository:** embed the viewer as a lightweight iframe widget.

## 5. Keep it compliant

Before sharing, confirm the model is anonymized where required and that you hold
the rights to publish it. Responsible sharing is part of good scholarship, and
the upload step makes that confirmation explicit.

That's it — one upload turns a 3D asset into an interactive, AR-ready figure your
readers can actually explore.

*Ready to try it? [Create a free account](/auth/register) and publish your first
interactive model.*
""",
    },
    {
        "slug": "qr-codes-on-academic-posters-guide",
        "title": "The Complete Guide to QR Codes on Academic Posters",
        "description": "Best practices for putting QR codes on conference posters: what to link, where to place them, sizing, and how to turn a static poster into an interactive demo.",
        "date": "2026-06-03",
        "author": "AcademicAR Team",
        "tags": ["QR codes", "conference posters", "how-to"],
        "persona": "Conference presenters",
        "read_minutes": 5,
        "body": """\
A poster has minutes to make an impression, and wall space is scarce. A QR code
is the cheapest way to extend a poster beyond its borders — linking to your data,
your paper, or, increasingly, an **interactive 3D model** of whatever you're
presenting. Here's how to do it well.

## What to link to

- **An interactive 3D/AR model** of your specimen, structure, or device — the most
  memorable option, because viewers can handle it themselves.
- The full paper or preprint (DOI link).
- Supplementary data, code, or a short video.

Avoid linking to a generic lab homepage; make the destination specific to the
poster.

## Placement and sizing

- Put the QR **near the relevant figure**, not just in a footer, so the link has
  obvious context.
- Make it **at least 3–4 cm square** on a printed poster so phones lock on from a
  comfortable standing distance.
- Keep a quiet margin of white space around the code.
- Add a one-line call to action: *"Scan to explore the model in 3D & AR."*

## Make it durable

Use a QR that points to a **stable resolver URL**, not a raw file path that might
move. AcademicAR's model QR codes resolve through a permanent identifier, so the
same printed poster keeps working after you replace or upgrade the model later.

## Measure it (optional)

If you want to know whether people scanned, link through a destination you control
so you can see views over the conference. Even rough numbers help you justify the
effort next time.

## A quick checklist

1. One specific, valuable destination per QR.
2. Placed next to the figure it explains.
3. 3–4 cm minimum, with margin.
4. A short call to action.
5. A stable URL that won't break.

Done right, a QR turns a poster from something people glance at into something they
hold in their hands.

*[Generate a stable QR code for your 3D model](/auth/register) in a couple of
minutes.*
""",
    },
    {
        "slug": "glb-stl-gltf-obj-3d-format-guide",
        "title": "GLB vs STL vs glTF vs OBJ: Choosing a 3D Format for the Web",
        "description": "A practical comparison of GLB, glTF, STL, OBJ, and FBX for sharing research 3D models online — which to use, and why GLB usually wins for the web.",
        "date": "2026-06-04",
        "author": "AcademicAR Team",
        "tags": ["3D formats", "glTF", "comparison"],
        "persona": "Technical researchers",
        "read_minutes": 6,
        "body": """\
"What format should I export?" is the first question when you want to share a 3D
model online. Here's a researcher-friendly comparison of the formats you'll meet.

## STL — geometry only

STL is the lingua franca of 3D printing and many segmentation tools. It stores
**triangles and nothing else**: no color, no texture, no material. It's perfect as
a *source* format from CT/MRI or CAD, but on its own a web viewer can only show it
as a plain gray shape.

## OBJ — geometry plus simple materials

OBJ adds UV coordinates and references an `.mtl` material file plus texture images.
It's widely supported and human-readable, but it's verbose and splits a model
across several files, which is awkward to host.

## FBX — rich but proprietary

FBX (from Autodesk) carries geometry, materials, rigs, and animation. It's common
in CAD and animation pipelines but is a proprietary binary that needs conversion
for the open web.

## glTF / GLB — built for the web

glTF is the "JPEG of 3D": an open standard designed for efficient delivery and
real-time rendering. **GLB** is its single-file binary form — geometry, materials,
textures, and animation all in one `.glb`. It supports physically based rendering
(PBR) so materials look right, and it compresses well.

## So which should you use?

- **Working/source format:** keep your STL, OBJ, or FBX from the original pipeline.
- **Sharing on the web:** convert to **GLB**. It's one file, renders fast, supports
  real materials, and is what browser viewers and AR expect.

The good news: you don't have to convert by hand. Upload STL, OBJ, FBX, or GLB to
AcademicAR and it produces an optimized GLB (Draco-compressed geometry, compressed
textures) plus a USDZ for iOS AR — so your readers get a fast, correct model
regardless of what you started from.

*[Upload any format and get a web-ready GLB](/auth/register) automatically.*
""",
    },
    {
        "slug": "augmented-reality-anatomy-education",
        "title": "How Augmented Reality Is Transforming Anatomy Education",
        "description": "Why AR and interactive 3D models are reshaping how anatomy is taught — spatial understanding, accessibility, and lower lab costs.",
        "date": "2026-06-05",
        "author": "AcademicAR Team",
        "tags": ["anatomy", "education", "augmented reality"],
        "persona": "Medical & anatomy educators",
        "read_minutes": 5,
        "body": """\
Anatomy is inherently three-dimensional, yet it is still often taught from 2D
atlases. Augmented reality and interactive 3D models close that gap, letting
students manipulate structures in space rather than memorizing flat cross-sections.

## Why 3D and AR help learning

Spatial structures — the branching of a bronchial tree, the layers of the heart,
the course of a nerve — are far easier to understand when you can rotate them and
see how parts relate. AR adds a further step: placing a life-size (or scaled)
model into the room, so students walk around it and view it from any angle.

## Practical classroom uses

- **Pre-lab orientation:** students explore a structure in 3D before a dissection,
  arriving better prepared.
- **Self-study:** an interactive model linked from lecture notes lets students
  review at their own pace.
- **Assessment and demonstration:** annotate key landmarks directly on the model.
- **Accessibility:** remote and distance learners get the same hands-on object as
  those in the lab.

## The cost and access angle

Cadaveric and physical model resources are expensive and limited. Digital 3D
models don't replace them, but they extend access dramatically — every student
with a phone has the structure in their pocket, available any time.

## How to add it to your course

You don't need a development team. Take a 3D anatomical model (from a scan or a
licensed library), upload it, and share the viewer link or QR code in your LMS,
slides, or handouts. Students open it in a browser; on a phone they can switch to
AR with a tap — no app to install.

Interactive 3D won't replace the dissection room, but it makes the spatial
reasoning at the heart of anatomy visible to every student, every time.

*[Publish an interactive anatomy model](/auth/register) and share it with your
class today.*
""",
    },
    {
        "slug": "interactive-3d-figures-scientific-publishing",
        "title": "Why Interactive 3D Figures Belong in Scientific Publishing",
        "description": "Static figures lose information. Interactive 3D figures improve comprehension, reproducibility, and engagement — and they're easier to add than you think.",
        "date": "2026-06-05",
        "author": "AcademicAR Team",
        "tags": ["scientific publishing", "open science", "3D figures"],
        "persona": "Researchers & editors",
        "read_minutes": 5,
        "body": """\
Most scientific results that are three-dimensional are still communicated in two
dimensions. We render a chosen viewpoint, flatten it, and hope the reader
reconstructs the rest. Interactive 3D figures remove that compromise.

## What we lose with static figures

A single rendered angle hides occluded structures, makes spatial relationships
ambiguous, and forces authors to pick one viewpoint among many. Reviewers and
readers can't check the parts the author didn't show.

## What interactive 3D adds

- **Comprehension:** readers explore the geometry themselves, from any angle.
- **Reproducibility:** the actual model — not a screenshot of it — travels with the
  paper, supporting the move toward FAIR, open research objects.
- **Engagement:** an interactive figure is memorable, and on mobile it becomes an
  AR object the reader can place in front of them.

## Why now

Three forces are converging: journals increasingly **mandate data availability**;
3D capture (photogrammetry, micro-CT, AI generation) is getting cheap and common;
and open web standards (glTF/GLB, USDZ, WebXR) make interactive 3D render anywhere
without plugins. The infrastructure to publish 3D well finally exists.

## The friction problem — and the fix

Historically, embedding interactive 3D meant custom web work that most authors
won't do. That's the gap AcademicAR fills: upload your model, get an optimized,
AR-ready viewer and a stable QR code, and link it from your figure caption. The
interactive object lives alongside your paper instead of dying as a static image.

Interactive 3D figures aren't a gimmick — they're a more honest way to communicate
three-dimensional results. As tooling removes the friction, they'll become an
expected part of the record.

*[Turn your next figure into an interactive 3D object](/auth/register).*
""",
    },
    {
        "slug": "ar-for-archaeology-3d-artifacts",
        "title": "AR for Archaeology: Bringing Artifacts to Life in 3D",
        "description": "How 3D scanning and augmented reality help archaeologists share, study, and teach with fragile artifacts — without risking the originals.",
        "date": "2026-06-06",
        "author": "AcademicAR Team",
        "tags": ["archaeology", "museums", "3D scanning"],
        "persona": "Archaeologists & curators",
        "read_minutes": 5,
        "body": """\
Artifacts are fragile, irreplaceable, and often locked away in storage. 3D scanning
plus augmented reality lets archaeologists and museums share them widely while the
originals stay safe.

## Why digitize artifacts

- **Preservation:** a high-fidelity scan is a permanent record if an object is
  damaged, lost, or degrades.
- **Access:** researchers worldwide can examine an object without travel or
  handling the original.
- **Teaching and outreach:** students and the public can rotate, zoom, and place an
  artifact in AR — far more engaging than a photo behind glass.

## From scan to shareable model

Photogrammetry (many overlapping photos) or structured-light scanning produces a
detailed mesh with color. After cleanup and decimation, export to a web format and
publish it as an interactive viewer. Add **annotations** to point out tool marks,
inscriptions, or repairs, turning the model into a guided object lesson.

## Putting it in publications and exhibits

- **Papers:** link the 3D model from your figures so reviewers can inspect the
  surface themselves.
- **Posters and labels:** a QR code next to an artifact label lets visitors explore
  it in 3D and AR on their own phones.
- **Online collections:** embed the viewer in a catalog page to bring a static
  record to life.

## Responsible sharing

Digitization raises questions of provenance, ownership, and cultural sensitivity.
Confirm you have the rights to share an object and respect any restrictions from
source communities — good practice that AcademicAR's upload step makes explicit.

3D and AR let an artifact be studied by everyone, everywhere, while the original
rests safely in its case.

*[Publish a 3D artifact with annotations](/auth/register) and share it via a QR
code.*
""",
    },
    {
        "slug": "dicom-to-3d-model-guide",
        "title": "From DICOM to Interactive 3D: A Practical Guide for Researchers",
        "description": "How to turn a CT or MRI DICOM series into an interactive, layered 3D model with AR — what to export, how anonymization works, and what happens to the raw scan.",
        "date": "2026-09-24",
        "author": "AcademicAR Team",
        "tags": ["DICOM", "medical imaging", "how-to"],
        "persona": "Clinician-researchers & radiology teams",
        "read_minutes": 7,
        "body": """\
![CT slices turning into a 3D skull shown in AR on a phone](/static/images/blog/covers/dicom-to-3d-model-guide.webp)

Most medical 3D work starts in the same place: a stack of DICOM images from a CT or
MRI scanner. Turning that stack into something a reader can rotate, cut open, and
place on the desk in augmented reality used to take a segmentation workstation, a
mesh editor, and a web developer. Here is how to do it in one upload — and what to
think about before you press the button.

![From a DICOM series to an AR-ready 3D model in five steps](/static/images/blog/dicom-pipeline.svg)
*From an anonymized DICOM series to a GLB + USDZ model. The raw scan is deleted after conversion.*

## What a DICOM series actually is

A scan is not one file. It is a **series** of 2D slices, each a separate DICOM file
carrying pixel data plus metadata: slice position, pixel spacing, orientation, and
— importantly — patient identifiers. To rebuild the volume correctly, the converter
needs every slice of the series together, so you upload them as a single **ZIP**.

AcademicAR reads uncompressed series as well as the common compressed transfer
syntaxes (JPEG Lossless, JPEG-LS, JPEG 2000, and RLE), so exports straight from a
PACS or a research archive usually work as they are.

## Step 1: Anonymize before you export

The rule is simple: **remove patient identifiers before the data leaves your
institution.** Use your hospital's de-identification workflow or a tool such as
the anonymizer in your DICOM viewer, and follow your ethics approval. AcademicAR
asks you to confirm anonymization, rights, and ethics responsibility on every
upload, and medical uploads carry an extra confirmation on top of that.

Two further safeguards are built in:

- The **raw scan is deleted after conversion** — whether the conversion succeeds
  or fails. Only the resulting surface mesh is kept.
- Medical uploads are never archived or mirrored as source files, and patient
  identifiers are never written to logs.

## Step 2: Choose what to extract

A volume becomes a 3D model by deciding which voxels belong to a structure. For CT
you can pick one or more **threshold presets**:

| Preset | What it keeps | Works for |
|---|---|---|
| Bone | Dense tissue, 250 HU and above | CT |
| Skin | The outer body surface, -300 HU and above | CT |
| Contrast vessels | Contrast-filled vessels, 150 HU and above | Contrast CT |
| Auto threshold | An automatic (Otsu) split into two intensity classes | CT and MR |
| Custom range | Your own minimum (and optional maximum) in HU | CT |

Each preset you select becomes its own **layer** in the viewer, with its own colour.
If the presets aren't precise enough — for example, you need individual organs —
segment the scan first and upload the segmentation instead (see our
[guide to sharing segmentations](/blog/medical-image-segmentation-to-3d-viewer)).

## Step 3: Let the worker do the heavy lifting

Conversion runs in a background worker, not in your browser. It rebuilds the
volume, extracts a surface for each layer, smooths and decimates it to a size that
renders well on a phone, and produces a web-optimized **GLB** plus a **USDZ** for
iPhone and iPad AR. You can watch the progress on the model page.

## Step 4: Explore and share

The finished model opens in the viewer with a **Layers panel** (show, hide, fade,
or recolour bone, skin, and vessels independently), a **section plane** to cut
through it, and a **2D slice view** that shows the cut in the familiar axial,
coronal, and sagittal conventions. Share it with a stable link or QR code in your
paper, poster, or lecture slides.

## A note on scope

These models are for **research communication and education**. They are surface
reconstructions, not diagnostic images, and they should not be used for clinical
decisions.

*[Create a free account](/auth/register) and turn your next anonymized scan into
an interactive figure.*
""",
    },
    {
        "slug": "hounsfield-units-ct-thresholding-3d",
        "title": "Hounsfield Units Explained: Choosing CT Thresholds for 3D Models",
        "description": "What Hounsfield units are, why bone, skin, and contrast-filled vessels separate at different HU values, and how to pick a preset or custom HU range for a clean 3D model.",
        "date": "2026-09-25",
        "author": "AcademicAR Team",
        "tags": ["DICOM", "CT", "Hounsfield units"],
        "persona": "Medical imaging researchers & students",
        "read_minutes": 6,
        "body": """\
![A skeleton above a CT density scale with a threshold marker](/static/images/blog/covers/hounsfield-units-ct-thresholding-3d.webp)

When you turn a CT scan into a 3D model, one number decides almost everything about
the result: the **threshold**. Set it too low and the bone model is buried in soft
tissue; set it too high and thin structures disappear. Understanding Hounsfield
units makes that choice predictable instead of trial and error.

## What a Hounsfield unit is

CT measures how strongly each voxel attenuates X-rays and rescales it to a
standard scale, the **Hounsfield unit (HU)**:

- Air is about **-1000 HU**.
- Water is **0 HU** by definition.
- Fat sits around **-100 HU**, most soft tissue between **+20 and +80 HU**.
- Contrast-enhanced blood often reaches **150-400 HU**.
- Cortical bone ranges from a few hundred to well over **1000 HU**.

Because the scale is calibrated, the same threshold means roughly the same tissue
across scanners — which is what makes presets possible. MRI, by contrast, has no
absolute intensity scale, so HU thresholds do not apply to it.

![The Hounsfield scale with typical tissue values and the Skin, Contrast and Bone preset thresholds](/static/images/blog/hounsfield-scale.svg)
*Typical tissue values on the Hounsfield scale, and where the CT presets cut.*

## The presets, and why they sit where they do

- **Bone (250 HU and above).** High enough to exclude soft tissue and most
  contrast, low enough to keep thinner cortical bone. Good for skulls, spines,
  pelvises, and fracture morphology.
- **Skin (-300 HU and above).** Everything denser than air counts as "body"; only
  the largest connected piece is kept, so the table and stray noise drop out.
  Useful for facial and body-surface models.
- **Contrast vessels (150 HU and above).** Captures contrast-filled vessels — but
  bone is just as bright, so a threshold alone cannot separate them. If you also
  select Bone, bone is subtracted from the vessel layer so the two layers do not
  overlap.
- **Auto threshold (Otsu).** Splits the volume into two intensity classes
  automatically. It is the only preset that makes sense for **MR**, and a quick
  first look for unfamiliar CT data.

## When to use a custom range

Presets cover the common cases; research questions often don't. A **custom HU
range** lets you set your own minimum and, optionally, a maximum — for instance a
band that isolates dense calcifications, a bone-density window for a specific
specimen, or a soft-tissue band in a contrast study. Each range becomes its own
coloured layer, so you can compare bands side by side in the viewer.

## Practical tips

1. **Start broad, then narrow.** Run one preset, inspect it with the section plane,
   then refine with a custom range.
2. **Watch thin structures.** Orbital walls, nasal turbinates, and small vessels
   are partial-volume voxels; a slightly lower threshold keeps them.
3. **Metal artefacts** from implants show up as streaks at very high HU — a maximum
   in your custom range can trim them.
4. **Need organs, not densities?** Thresholds separate densities, not anatomy.
   Segment first and upload the segmentation for organ-level layers.

*[Upload an anonymized CT series](/auth/register) and try the presets on your own
data.*
""",
    },
    {
        "slug": "medical-image-segmentation-to-3d-viewer",
        "title": "Sharing Medical Segmentations in 3D: NIfTI, NRRD, and DICOM-SEG",
        "description": "Publish segmentations from 3D Slicer, ITK-SNAP, or TotalSegmentator as an interactive, layered 3D model — one colour-coded layer per structure, ready for papers and teaching.",
        "date": "2026-09-26",
        "author": "AcademicAR Team",
        "tags": ["segmentation", "medical imaging", "3D Slicer"],
        "persona": "Medical imaging & AI researchers",
        "read_minutes": 6,
        "body": """\
![Segmented abdominal organs floating above scan slices](/static/images/blog/covers/medical-image-segmentation-to-3d-viewer.webp)

Segmentation is where much of the scientific value in medical imaging lives. Hours
of manual contouring or a carefully validated AI model produce label maps that
describe anatomy structure by structure — and then they end up as a single
screenshot in a figure. Here is how to share the segmentation itself.

## Formats you can upload directly

AcademicAR reads the segmentation formats your tools already write:

- **NIfTI** (`.nii`, `.nii.gz`) — the default for most research pipelines and AI
  models such as TotalSegmentator or nnU-Net.
- **NRRD**, including 3D Slicer's **`.seg.nrrd`** — segment names and colours are
  carried over from Slicer.
- **DICOM-SEG** — the standard segmentation object used by PACS-connected tools.
- **A ZIP of masks** — one binary mask per structure, as many pipelines export them.

The converter checks the content, not just the extension: a file full of continuous
image intensities is recognised as an image, not a segmentation, so you get a
clear message instead of a meaningless model.

## One structure, one layer

Each non-empty label becomes a **separate layer** in the viewer, with up to 64
layers per model. That matters for readers:

- They can **show, hide, or fade** individual structures — hide the ribs to see the
  lungs, fade the liver to reveal the vessels inside it.
- They can **recolour** layers or apply a finish, and you can save a **default
  layer view** so everyone opens the model the way you intended.
- Names come along where the format provides them, so the legend reads "left
  kidney", not "label 7".

![A segmentation label map becomes one named, coloured layer per structure in the viewer](/static/images/blog/segmentation-layers.svg)
*Each non-empty label becomes its own layer that readers can show, hide, fade or recolour.*

Large multi-structure outputs are handled one mask at a time, so a full-body
TotalSegmentator result does not need special preparation.

## Why share the segmentation, not just a render

- **Reviewers can check it.** Over- and under-segmentation is obvious when you can
  cut through the model with the section plane.
- **AI papers become tangible.** Readers see what the model actually segments,
  in 3D, on their own phone.
- **Teaching gets a new resource.** A single labelled segmentation becomes an
  explorable atlas for a whole course.

## Privacy still applies

A segmentation can carry identifying metadata, and a skin surface can be
recognisable. Use de-identified data, follow your ethics approval, and avoid face
surfaces unless you have consent. The uploaded source file is deleted after
conversion; only the mesh is kept.

## Measurements come built in

For layered models, AcademicAR computes per-structure **dimensions** and the
**distances between neighbouring structures** — see our post on
[layer measurements](/blog/measuring-anatomy-3d-layer-metrics).

*[Publish your segmentation](/auth/register) as an interactive, layered 3D model.*
""",
    },
    {
        "slug": "section-plane-slice-view-cross-sections",
        "title": "Reading Cross-Sections in 3D: Section Planes and Colour-Coded Slice Views",
        "description": "How the section plane and the 2D slice view let readers cut through a 3D model, see every structure in its own colour, and measure on the cut — for anatomy and engineering alike.",
        "date": "2026-09-28",
        "author": "AcademicAR Team",
        "tags": ["section plane", "slice view", "cross-sections"],
        "persona": "Medical educators, engineers & architects",
        "read_minutes": 6,
        "body": """\
![A head model cut by a plane next to its colour-coded 2D slice](/static/images/blog/covers/section-plane-slice-view-cross-sections.webp)

A 3D model shows the outside of things. Most of the interesting questions are
about the inside: how a tumour sits against a vessel, how thick a wall is, how a
shaft passes through a housing. Cross-sections answer those questions, and they
are one of the oldest tools in both radiology and technical drawing. In AcademicAR
they work directly in the browser.

## The section plane

Open the **Section** panel and a plane cuts through the model along the X, Y, or
Z axis. Drag it through the model and everything in front disappears, revealing
the interior. A few details make it practical:

- **Axis guides** show where the plane is and how the model is oriented.
- **Face** turns the camera straight onto the cut, so you look at it squarely
  instead of at an angle.
- The cut respects the **Layers panel**: hide the skin and cut only the skeleton,
  or keep a faded outer shell for context.

## The 2D slice view

Next to the 3D cut, the **slice view** draws the cross-section as a flat 2D image.
Each structure's contour is drawn **in its own layer colour**, so in a segmented
scan you can immediately tell the liver from the portal vein from the vertebra —
just like a colour-coded segmentation overlay in a medical imaging workstation.

For scans, the slice view follows the conventions clinicians already know:

- Plane colours follow the 3D Slicer convention — **axial red, coronal green,
  sagittal yellow**.
- Orientation labels (**R/L, A/P, S/I**) sit at the edges, laid out in the
  radiological convention (patient right on the left of the screen).

For CAD and architectural models, it uses the familiar engineering colours
instead — X red, Y green, Z blue — with plus/minus axis labels.

![Axial, coronal and sagittal slice views with Slicer plane colours and radiological orientation labels](/static/images/blog/slice-view-conventions.svg)
*Slice view conventions for scans: each structure in its own colour, Slicer plane colours, radiological orientation.*

The slice view supports zoom and pan, shows a scale bar, and reports which
structures the plane currently cuts.

## Measuring on the cut

A two-point **measure** on the slice view snaps to the contours, so you measure
wall thickness, a canal diameter, or the gap between two parts precisely on the
section, not on an arbitrary surface point. Points that fall outside the solid —
in a gap between parts or inside a hollow — are refused, so you don't accidentally
measure empty space.

## Where it helps

- **Anatomy teaching:** move an axial plane down the torso and relate the 3D model
  to the CT slices students will read later.
- **Medical research:** show the relationship of a lesion to surrounding
  structures at the exact level you discuss in the text.
- **Engineering:** inspect wall thicknesses, internal channels, and fits in an
  assembly without a CAD licence.
- **Architecture:** cut a building model to produce live plans and sections.

*[Upload a layered model](/auth/register) and cut through it in the browser.*
""",
    },
    {
        "slug": "measuring-anatomy-3d-layer-metrics",
        "title": "Measuring in 3D: Structure Dimensions and Distances Between Layers",
        "description": "Layered 3D models come with automatic measurements: oriented dimensions, maximum diameter, and the closest distance between neighbouring structures — useful in anatomy, morphology, and engineering.",
        "date": "2026-09-29",
        "author": "AcademicAR Team",
        "tags": ["measurement", "morphometrics", "segmentation"],
        "persona": "Researchers working with segmented or multi-part models",
        "read_minutes": 5,
        "body": """\
![A kidney in a bounding box and the closest distance to a vessel](/static/images/blog/covers/measuring-anatomy-3d-layer-metrics.webp)

"How big is it?" and "How close is it to that?" are among the first questions a
reader asks about a 3D structure. In a static figure the answer is a number in the
caption. In AcademicAR, layered models carry their own measurements, so readers
can see the numbers in context.

## What is measured

When a model has multiple layers — structures from a segmentation, presets from a
CT, or parts of a CAD assembly — the worker computes, for each layer:

- **Oriented dimensions:** the three edge lengths of the tightest box around the
  structure, aligned with the structure itself rather than the scanner axes. This
  gives a meaningful length, width, and depth even for an oblique organ.
- **Maximum diameter:** the largest distance between any two points of the
  structure — the familiar "longest axis" measure.

And between structures:

- **Minimum distance** between neighbouring layers, with the two closest points
  marked in the viewer. Pairs further than about 5 cm apart are skipped, so the
  list stays focused on relationships that matter.

Values are reported in **millimetres**, computed from the full-resolution geometry
before compression. Layer measurements are included in the paid model plans
([pricing](/pricing)).

![Oriented dimensions and maximum diameter of one layer, and the minimum distance between two layers](/static/images/blog/layer-metrics.svg)
*What is measured per layer and between neighbouring layers.*

## Why this matters in medicine

- **Tumour-to-vessel distance** is central to resectability discussions; showing it
  on an interactive model makes the spatial relationship obvious.
- **Organ and lesion dimensions** support case reports and teaching cases.
- **Anatomical variation** studies can present the measured structure and the
  number together.

## And in engineering and the sciences

- **Clearances in assemblies:** the minimum distance between two parts is a
  clearance check readers can inspect.
- **Morphometrics** in biology, palaeontology, and archaeology: oriented dimensions
  and maximum diameter are standard descriptors of specimens.

## You stay in control

Measurements depend on the model's scale, so check units after upload; if you
rescale the model, its measurements are recomputed. The owner can also **hide the
measurements** from public viewers — useful when numbers are preliminary or when
the model is meant for orientation rather than quantification.

For precise point-to-point checks at a specific level, combine these with the
snapping measure in the [slice view](/blog/section-plane-slice-view-cross-sections).

*[Publish a layered model](/auth/register) and let readers see the numbers in 3D.*
""",
    },
    {
        "slug": "3d-models-surgical-education-case-reports",
        "title": "Interactive 3D in Medical Education and Case Reports",
        "description": "How educators and clinician-researchers use layered 3D models, guided tours, and AR to teach anatomy, discuss complex cases, and enrich case reports — responsibly.",
        "date": "2026-09-30",
        "author": "AcademicAR Team",
        "tags": ["medical education", "case reports", "augmented reality"],
        "persona": "Medical educators & clinician-researchers",
        "read_minutes": 6,
        "body": """\
![Medical students around a 3D heart shown in AR on a tablet](/static/images/blog/covers/3d-models-surgical-education-case-reports.webp)

Medicine is taught and published in two dimensions — slides, atlases, journal
figures — yet the problems it deals with are spatial. Interactive 3D models built
from real imaging close that gap for students, residents, and readers of the
literature.

## In the classroom and the skills lab

- **From slices to anatomy.** Students struggle to connect axial CT slices to 3D
  anatomy. A model with a moving section plane and a colour-coded slice view lets
  them see both at once.
- **Layered dissection.** With one layer per structure, students peel away skin,
  muscle, and bone in order — and rebuild them — as often as they like.
- **AR at the bedside of the textbook.** On a phone or tablet, the model can be
  placed on the desk and walked around, with no app to install.
- **Guided tours.** Save up to 12 views — each with its own camera, visible layers,
  colours, and section cut — and play them as a step-by-step tour. Each view gets
  its own link and QR code for handouts. (More in our post on
  [scenes and tours](/blog/saved-scenes-guided-tours-3d-teaching).)

## In case reports and clinical research papers

Complex anatomy — congenital heart disease, craniofacial deformities, pelvic
fractures, vascular anomalies — is notoriously hard to convey in a few static
figures. Linking an interactive model from the figure caption lets readers:

- rotate the anatomy and view it from the surgical approach,
- toggle structures to see what lies behind them,
- check dimensions and distances that the text refers to.

A QR code next to the figure brings the same model to readers of the printed
journal or a conference poster.

## Doing it responsibly

- **De-identify** imaging before upload and follow your ethics approval and
  journal policy for patient images. Facial surface reconstructions deserve extra
  care.
- **Get consent** where your institution requires it for publication of case
  material.
- **Be clear about scope.** These are communication and teaching tools built from
  surface reconstructions, not diagnostic or planning software.

AcademicAR makes the compliance step explicit: every upload confirms
anonymization, rights, and ethics responsibility, medical uploads carry an extra
confirmation, and the raw scan is deleted once the 3D model is built.

## For departments

Institutional plans let a department or faculty fund models for all its members
from a shared quota, with a public showcase page for its teaching collection.
[Learn more about institutional plans](/institutional).

*[Create your first teaching model](/auth/register) from an anonymized scan or a
segmentation.*
""",
    },
    {
        "slug": "step-cad-assemblies-ar-engineering",
        "title": "From STEP to AR: Sharing CAD Assemblies in Engineering Research",
        "description": "Upload STEP/STP, FBX, or OBJ assemblies and share them as interactive 3D with one layer per part, cross-sections, clearances, and AR — for engineering papers, theses, and teaching.",
        "date": "2026-10-01",
        "author": "AcademicAR Team",
        "tags": ["engineering", "STEP", "CAD"],
        "persona": "Engineering researchers & educators",
        "read_minutes": 6,
        "body": """\
![An exploded gearbox assembly and the same model in AR on a phone](/static/images/blog/covers/step-cad-assemblies-ar-engineering.webp)

Engineering research is full of 3D objects — prototypes, test rigs, mechanisms,
fixtures, sensors — that end up as an isometric screenshot in a paper. Reviewers
can't see the hidden parts, students can't see how the mechanism fits together,
and the CAD file itself stays locked behind a commercial licence. Here's how to
share the assembly instead.

## Upload STEP directly

**STEP (`.step`/`.stp`)** is the neutral exchange format every CAD package can
export, from SolidWorks and Fusion to CATIA, Creo, Inventor, and FreeCAD.
AcademicAR converts STEP with OpenCascade:

- Each part becomes its own node, and **STEP colours** are carried over.
- Geometry is tessellated finely enough that holes and fillets stay smooth on small
  mechanical parts, with a cap on triangle count so the model still loads on a
  phone.
- The output is a compressed GLB plus a USDZ for iOS AR.

FBX, OBJ, STL, and GLB are accepted too, so meshes from simulation post-processing
or 3D scanning work the same way.

## One part, one layer

Multi-part models get a **Layers panel** with one layer per part — repeated parts,
such as a set of bolts, are grouped into one layer. Readers can:

- hide the housing to see the gear train,
- fade a cover to show what sits behind it,
- recolour parts to match the colours used in your paper's figures.

![A STEP assembly tree converted into one viewer layer per part, with repeated parts grouped](/static/images/blog/step-assembly-layers.svg)
*Every part becomes a layer; repeated parts, such as bolts, are grouped.*

## Look inside with sections

The **section plane** cuts through the assembly along any axis, and the **2D slice
view** shows the cut as a clean, colour-coded section — a live version of the
section views in a technical drawing. The snapping measure on the slice reads wall
thicknesses, bore diameters, and gaps directly. (More in our post on
[section planes and slice views](/blog/section-plane-slice-view-cross-sections).)

## Clearances and dimensions, automatically

For layered models, the worker computes each part's oriented dimensions and the
**minimum distance between neighbouring parts** — a quick, visible clearance
check that readers can explore themselves.

## Where engineers use it

- **Papers and theses:** link an interactive model from the figure caption so
  reviewers can inspect the design from every angle.
- **Teaching:** mechanisms, machine elements, and assemblies are far easier to
  understand when students can take them apart layer by layer — in AR on their
  desk.
- **Student projects and capstones:** a QR code on the project poster lets the
  jury explore the actual design at the exhibition.
- **Industry collaboration:** share a design review link without sending native
  CAD files.

## Keep intellectual property in mind

Confirm you have the rights to publish a design before uploading, especially for
work done with industry partners. A simplified or envelope model is often enough
for communication.

*[Upload a STEP assembly](/auth/register) and share it as an interactive, layered
model. See also [AR for engineering](/ar-for-engineering).*
""",
    },
    {
        "slug": "architecture-3d-models-sections-ar",
        "title": "Architecture in 3D and AR: Sharing Building Models, Plans, and Sections",
        "description": "How architecture researchers, studios, and students can share building and heritage models online — with live sections, layer-by-layer exploration, saved views, and AR placement.",
        "date": "2026-10-02",
        "author": "AcademicAR Team",
        "tags": ["architecture", "heritage", "augmented reality"],
        "persona": "Architecture researchers, instructors & students",
        "read_minutes": 6,
        "body": """\
![A building model cut open by a section plane, placed in AR on a table](/static/images/blog/covers/architecture-3d-models-sections-ar.webp)

Architecture has always been communicated through drawings that are cuts through a
3D idea: plans, sections, elevations. Digital models made the 3D idea explicit, yet
most still reach their audience as rendered images. Interactive 3D lets the reader
walk around — and through — the building themselves.

## Getting the model out of your design tool

Most architecture tools export at least one format AcademicAR accepts:

- **Rhino, SketchUp, Blender, 3ds Max:** export **GLB/glTF**, **FBX**, or **OBJ**.
- **Revit and ArchiCAD:** export FBX or OBJ (directly or via a plug-in).
- **Photogrammetry and laser-scan meshes** of existing buildings and heritage sites:
  export OBJ or GLB with textures.
- **Fabrication-oriented models** from CAD tools: **STEP**.

Keep it light: simplify furniture and entourage, merge tiny details, and check the
units — a model in centimetres shown as metres will look a hundred times too big in
AR. Models can be rescaled after upload.

## Layers as building systems

When a model has multiple named parts, each becomes a **layer**. Organise your
export by building system — structure, envelope, floors, circulation, services,
landscape — and readers can switch them on and off, fade the façade to reveal the
structure, or recolour systems to match your diagrams.

## Live plans and sections

The **section plane** is a natural fit for architecture. Cut horizontally for a
live **plan**, vertically for a live **section**, and slide the plane to move
through the building floor by floor. The **2D slice view** shows the cut as a flat
drawing with a scale bar, and the snapping measure reads wall thicknesses and room
widths directly on the section.

![A horizontal cut gives a live plan and a vertical cut gives a live section of a building model](/static/images/blog/architecture-cuts.svg)
*A horizontal plane gives a live plan, a vertical plane a live section, with a scale bar and a snapping measure.*

## Saved views and tours for reviews

Save up to 12 **scenes** — each with its own camera, visible layers, colours, and
section — and play them as a **tour**. A design review, a studio jury, or a
heritage-documentation walkthrough becomes a guided sequence anyone can open from
a link or a QR code.

## AR on site and on the table

On a phone, the model can be placed in AR: a massing model on the review table, a
reconstructed heritage element next to its ruins, a pavilion at its real scale in
the courtyard where it will stand.

## Uses in research and teaching

- **Heritage and conservation:** publish scanned buildings and fragments with
  sections that reveal construction layers.
- **Design research:** attach the actual model to a paper on form, structure, or
  performance.
- **Studio teaching:** students submit an interactive model with their boards; the
  jury scans a QR code on the poster.
- **Building technology courses:** explode a façade or a structural system layer
  by layer.

*[Upload your building model](/auth/register) and share it with live sections and
AR.*
""",
    },
    {
        "slug": "saved-scenes-guided-tours-3d-teaching",
        "title": "Saved Scenes and Guided Tours: Teaching with 3D Models Step by Step",
        "description": "Save up to 12 views of a 3D model — each with camera, layers, colours, and section cut — share each with its own link and QR code, and play them as a guided tour in class or in AR.",
        "date": "2026-10-03",
        "author": "AcademicAR Team",
        "tags": ["teaching", "scenes", "guided tours"],
        "persona": "Educators in medicine, engineering & architecture",
        "read_minutes": 5,
        "body": """\
![Five saved views of a skull connected as a guided tour](/static/images/blog/covers/saved-scenes-guided-tours-3d-teaching.webp)

A free-to-explore 3D model is wonderful for curious readers and overwhelming for
students on their first encounter. Good teaching sequences attention: first this,
then that, now look inside. **Scenes** bring that sequence to a 3D model.

## What a scene stores

A scene is a saved view of a model. It captures:

- the **camera** position and orientation,
- which **layers** are visible, their colours and transparency,
- the **section cut**, if one is active,
- the background, orientation, and **lighting**.

You can save up to **12 scenes per model** and give each a name, such as "1. Skull —
lateral view", "2. Remove the mandible", or "3. Axial cut at the orbits".

## Share a scene on its own

Each scene has its own **link and QR code**. Put scene 3's QR code next to the
paragraph that discusses it, on the slide where you need it, or on the exam
handout. Readers land exactly on the view you prepared — with the right layers
visible and the right cut in place — and can then explore freely from there.

## Play it as a tour

The **tour** plays your scenes in order, moving smoothly from one to the next. In a
lecture it replaces a series of static screenshots with one live model; for
self-study, students step through the sequence at their own pace.

![Four saved scenes of a skull model played as a guided tour](/static/images/blog/scenes-tour.svg)
*A tour plays saved scenes in order; each scene also has its own link and QR code.*

## Scenes in AR

A scene can also be opened in AR with **only its visible layers, its colours, and
its section cut**, so a student placing "the heart, opened at the four-chamber
plane" on their desk sees exactly that — not the whole model.

## Example sequences

**Anatomy:** whole thorax, then hide the ribs, then the lungs faded, then a coronal
cut through the heart, then an isolated vessel tree.

**Engineering:** closed gearbox, then housing hidden, then a section through the
shaft, then a close-up of the bearing seat.

**Architecture:** exterior massing, then façade faded, then a plan cut on each
floor, then a longitudinal section.

## Tips for good tours

1. **One idea per scene.** If a scene needs two sentences to explain, split it.
2. **Change one thing at a time** — a layer, a cut, or the camera — so students
   follow what changed.
3. **Number scene names** so the order is obvious when shared individually.
4. **Pair each scene with a question** in your handout: "What structure lies
   directly anterior to ...?"

*[Create your first tour](/auth/register) on any model you have uploaded.*
""",
    },
    {
        "slug": "publication-ready-3d-figures-comparisons",
        "title": "Publication-Ready Figures and Side-by-Side Comparisons from 3D Models",
        "description": "Export high-resolution figures from an interactive 3D model with a title, layer legend, scale bar, and QR code, and pair two models in a synchronized comparison for before/after and design studies.",
        "date": "2026-10-05",
        "author": "AcademicAR Team",
        "tags": ["scientific figures", "comparison", "publishing"],
        "persona": "Researchers preparing papers & posters",
        "read_minutes": 6,
        "body": """\
![Two femur models compared side by side on a figure with legend, scale bar and QR code](/static/images/blog/covers/publication-ready-3d-figures-comparisons.webp)

An interactive model is the best way to explore 3D results, but papers still need
printed figures, and many findings are comparisons: before and after, healthy and
diseased, design A and design B. Here's how to get both from the same models.

## Figures straight from the viewer

**Figure export** renders the current view at publication resolution — 2000, 3000,
or 4000 pixels wide, well beyond a screenshot — and composes it into a
ready-to-use figure with optional:

- a **title**,
- a **layer legend** listing each visible structure or part with its colour,
- a **scale bar** derived from the model's real dimensions,
- a **QR card** that links to the interactive model,
- a **white or transparent background**, ready to drop into a journal layout.

Because the figure is built from the same view as the model, you can set up layers,
colours, lighting, and a section cut first, then export — and export again later
from a saved scene if a reviewer asks for a different angle.

![An exported figure with title, model render, layer legend, scale bar and QR card](/static/images/blog/figure-export-anatomy.svg)
*The parts of an exported figure.*

## Why the QR card matters

A printed figure is a single viewpoint. The QR card in the corner turns it into a
door to the full model: readers of the PDF or the printed poster scan it and
explore the geometry themselves. That is the most direct way to make a 3D result
both citable as a figure and explorable as data.

## Side-by-side comparisons

**Comparisons** pair two models in one view with their own public link. Typical
uses:

- **Medicine:** pre- and post-operative anatomy, two segmentation methods on the
  same scan, a normal and a variant anatomy.
- **Engineering:** two design iterations, a part before and after topology
  optimization, a simulated and a scanned geometry.
- **Architecture and heritage:** a site before and after restoration, a design
  proposal next to the existing building.
- **Biology and palaeontology:** two specimens or species for morphological
  comparison.

You can pair any two models from projects you are allowed to edit, and share the
comparison link or QR code in your paper or poster just like a single model.

Figure export and comparisons are included in the paid model plans — see
[pricing](/pricing) for details.

## A workflow that holds up in peer review

1. Upload the models and organise layers and colours.
2. Save the key views as **scenes**.
3. Export figures from those scenes with a legend, scale bar, and QR card.
4. Link the interactive model or **comparison** from the caption.
5. When a reviewer asks for another angle, open the scene, adjust, and export
   again — the figure and the model stay consistent.

*[Create a free account](/auth/register) and export your first publication figure
from a 3D model.*
""",
    },
    {
        "slug": "convert-stl-to-glb",
        "title": "How to Convert STL to GLB (and Why It Matters for Sharing)",
        "description": "Convert STL to GLB for the web: set units and colour, keep the file small with Draco, and get a viewer link, AR, and a QR code in one step.",
        "date": "2026-10-08",
        "author": "AcademicAR Team",
        "tags": ["STL", "GLB", "3D formats", "how-to"],
        "persona": "Researchers, engineers & makers",
        "read_minutes": 6,
        "body": """\
STL is the format almost every 3D pipeline can write: segmentation software, CAD,
3D scanners, and slicers for 3D printing. GLB is the format browsers, phones, and
AR viewers want. If you need to show an STL to someone who doesn't have CAD or
printing software, converting it to GLB is the step in between. Here's what the
conversion actually does and how to get it right.

## STL and GLB in one paragraph each

**STL** stores a list of triangles and nothing else: no units, no colour, no
materials, no parts. Most STLs are also uncompressed, so a detailed scan or
segmentation can easily be tens or hundreds of megabytes.

**GLB** is the single-file, binary form of **glTF 2.0**, the open standard built for
real-time 3D on the web. One `.glb` holds geometry, materials, textures, and a
scene of named parts, and it supports compression. Browser viewers such as
Google's `<model-viewer>` and Android's AR viewer load GLB directly.

For a longer comparison with OBJ, FBX, and glTF, see our
[3D format guide](/blog/glb-stl-gltf-obj-3d-format-guide).

## What a good STL to GLB conversion does

A conversion is more than rewriting triangles into another container. To end up
with a model that looks right and loads fast, it should:

1. **Fix the scale.** STL has no units, so the same file can mean 120 mm or 120 m.
   You have to say which one it is, or AR will show your femur the size of a
   building.
2. **Add a material.** A plain STL renders as flat grey. Giving it a colour and a
   physically based material makes the shape readable.
3. **Clean up the mesh**, merging duplicate vertices so the file doesn't waste
   space, and add the texture coordinates some AR viewers expect.
4. **Compress the geometry.** Draco compression typically shrinks mesh data many
   times over, which is the difference between a model that opens instantly on
   mobile data and one that never finishes loading.

## Converting STL to GLB with AcademicAR

1. Create a project and choose **Upload model**.
2. Select your `.stl` file and pick the **source unit**: millimetres, centimetres,
   or metres. This is required for STL and OBJ because the file can't tell us.
3. Optionally set a **model colour**. Without one, the model is shown in a neutral
   material.
4. Confirm the compliance checkbox (that you have the rights to publish the model
   and that any patient data is anonymised) and upload.

Conversion runs in the background. When it finishes you get:

- an optimized **GLB** with Draco-compressed geometry,
- a **USDZ** companion, so iPhones and iPads can open the model in AR,
- a public **viewer link** that works in any modern browser,
- a **QR code** that opens the same model on a phone.

There is nothing to install and no command line involved.

## Tips for large STL files

- **Decimate before uploading.** For on-screen viewing, a few hundred thousand
  triangles is plenty. Tools like Meshmixer, Blender (Decimate modifier), or 3D
  Slicer's surface toolbox can reduce counts by 80–90% with no visible loss.
- **Split by part when it matters.** One STL is one mesh. If readers should be
  able to hide or recolour parts separately, export the parts to a format that
  keeps them apart, such as STEP, FBX, or GLB, or upload a segmentation that
  keeps one label per structure.
- **Check that the mesh is closed.** Holes and flipped faces in an STL often come
  from the original export and are easiest to fix at the source.

## Why not just share the STL?

You can attach an STL to a paper's supplementary material, and you should if
readers might 3D print it. But very few readers will download it, find software
that opens it, and set up the view. A GLB in a web viewer turns the same geometry
into something they can rotate in a second, on any device, and place on their desk
in AR.

*[Create a free account](/auth/register) and convert your first STL to a shareable
GLB with a viewer link and QR code.*
""",
    },
    {
        "slug": "view-stl-online-share-link",
        "title": "How to View and Share an STL File Online (No Software Needed)",
        "description": "Open an STL file in your browser and share it as a link and QR code that opens in 3D and AR on any phone. No CAD or slicer software needed.",
        "date": "2026-10-09",
        "author": "AcademicAR Team",
        "tags": ["STL", "3D viewer", "sharing", "how-to"],
        "persona": "Researchers, teachers & students",
        "read_minutes": 5,
        "body": """\
Someone sends you an STL file, or you need to send one. The usual answer is
"open it in a slicer" or "install a CAD viewer", which is fine for engineers and
a dead end for everyone else. Here are the ways to view an STL online, and how to
turn it into a link anyone can open.

## Option 1: a quick local preview

If you only need to look at a file yourself, a browser-based STL viewer that
renders the file locally is enough: drag the file in and rotate it. Your operating
system may also have a built-in preview (Windows 3D Viewer, macOS Quick Look).

The limitation is that nothing is shared. The model lives on your computer, and
the next person has to repeat the process with their own copy of the file.

## Option 2: a link someone else can open

To show an STL to a colleague, reviewer, student, or the audience at a talk, you
want a **hosted viewer**: the model lives at a URL, and anyone who opens it sees it
in 3D without downloading anything.

With AcademicAR the steps are:

1. Upload the `.stl` and choose its unit (mm, cm, or m) and, optionally, a colour.
2. The file is converted to an optimized GLB (plus a USDZ for iPhone AR).
3. Copy the **viewer link** or download the **QR code**.

Whoever opens the link can rotate, zoom, and pan, and on a phone tap **View in AR** to place the model on their desk at its
real size.

## What readers can do in the viewer

Beyond turning the model around, the viewer includes tools that are useful for
real work:

- **Section plane** to cut through the model along any axis and look inside.
- **Slice view**, which shows that cut as a clean 2D cross-section with a
  snapping two-point measure.
- **Annotations and saved scenes**, so you can point at a feature and guide
  readers through the model in a fixed order.
- **Lighting presets and a light or dark background** to suit the model.
- **Screenshots** of the current view.

## A link that survives updates

A common problem with shared files is versioning: you fix the model and the copy
everyone has is now outdated. On AcademicAR you can **replace the model** while
the link and QR code stay the same, so a QR printed on a poster or in a thesis
keeps pointing at the current version. If a model's access window ends, the link
shows a clear notice instead of an error page.

## Where this helps

- **Papers and theses:** put the link or QR code in the figure caption.
- **Teaching:** share one link with a whole class instead of a file per student.
- **Posters and talks:** a QR code turns a still image into a model the audience
  can explore on their phones (see our
  [poster QR guide](/blog/qr-codes-on-academic-posters-guide)).
- **Collaboration:** show a design or a segmentation to a co-author who doesn't
  have the software you used.

## Privacy note

If the STL comes from patient imaging, make sure it is anonymised and that you have
consent and ethics approval to publish it. Faces reconstructed from head CT or MRI
can be identifying even without any metadata.

*[Create a free account](/auth/register) to share your STL as a link and QR code.*
""",
    },
    {
        "slug": "open-step-stp-file-online",
        "title": "What Is a STEP (.stp) File and How to Open It Without CAD",
        "description": "STEP and STP files explained: what's inside, which programs open them, and how to view and share a STEP assembly in your browser and AR without CAD.",
        "date": "2026-10-10",
        "author": "AcademicAR Team",
        "tags": ["STEP", "CAD", "3D viewer", "how-to"],
        "persona": "Engineers, students & collaborators",
        "read_minutes": 6,
        "body": """\
A `.step` or `.stp` file is the most common way CAD models travel between people
who don't use the same software. If you've received one and don't have
SolidWorks, Fusion, or CATIA, or you need to show one to someone who doesn't, this
guide covers what the file is and the easiest ways to open it.

## What a STEP file is

**STEP** (Standard for the Exchange of Product model data, ISO 10303) is a neutral
CAD format. The `.step` and `.stp` extensions are the same thing. Unlike mesh
formats such as STL, a STEP file describes **exact geometry**: surfaces, edges,
and solids defined mathematically, organised into an **assembly tree** of parts,
often with colours and part names.

That precision is why manufacturers and engineers exchange STEP. It is also why
STEP files can't be shown directly in a browser: the exact surfaces first have to
be turned into triangles (tessellated) before a 3D viewer or AR app can draw them.

## Ways to open a STEP file

- **Desktop CAD:** any major CAD package imports STEP. Free options include
  **FreeCAD** and Autodesk's free viewers.
- **Dedicated viewers:** several free desktop and browser viewers open STEP for
  inspection on your own machine.
- **A hosted 3D viewer:** convert once, then open it anywhere, including on a
  phone, and share it with others as a link.

The first two are good for your own inspection. The third is what you want when
the goal is to **show** the model to reviewers, students, or a project partner.

## Opening and sharing STEP with AcademicAR

Upload the `.step` or `.stp` file to a project. The conversion uses OpenCascade,
the open-source CAD kernel, and runs in the background:

- Each part of the assembly becomes its own node, and **STEP colours** carry over.
- Surfaces are tessellated finely enough that holes and fillets stay smooth on
  small mechanical parts, with a cap on triangle count so the model still loads on
  a phone.
- The result is a compressed **GLB** for browsers and Android AR and a **USDZ**
  for iPhone and iPad AR.
- STEP units are kept as authored, so the model appears at its real size in AR.

You get a viewer link and a QR code. Anyone can open the assembly in their
browser, with no CAD licence and nothing to install.

## Assemblies become layers

Multi-part models get a **Layers panel** with one layer per part, and repeated
parts such as a set of bolts are grouped. Viewers can hide the housing to see the
mechanism, fade a cover, or recolour parts. The **section plane** and **2D slice
view** cut through the assembly like a section view on a drawing. For more on
engineering use, see [From STEP to AR](/blog/step-cad-assemblies-ar-engineering).

## Troubleshooting large STEP files

- **Very large assemblies** can take a while to tessellate. If conversion times
  out, export the sub-assembly you actually need to show, or simplify small
  features (threads, text, fasteners) before export.
- **Missing colours** usually mean the CAD export didn't include them. Most CAD
  tools have an option to export colours or appearance with STEP.
- **IP and confidentiality:** confirm you have the right to publish a design
  before uploading, especially for work with industry partners. An envelope or
  simplified model is often enough.

*[Upload a STEP file](/auth/register) and open it in your browser and in AR,
without CAD software.*
""",
    },
    {
        "slug": "how-to-open-glb-file",
        "title": "How to Open a GLB File on Desktop, iPhone, and Android",
        "description": "What a GLB file is and how to open it on Windows, Mac, iPhone, and Android, why iPhones need USDZ for AR, and how to share a GLB as a link.",
        "date": "2026-10-12",
        "author": "AcademicAR Team",
        "tags": ["GLB", "glTF", "augmented reality", "how-to"],
        "persona": "Anyone sharing 3D models",
        "read_minutes": 5,
        "body": """\
GLB has become the default format for 3D on the web, so more and more people
receive `.glb` files without knowing what to do with them. Here's what the format
is and how to open it on each device.

## What is a GLB file?

A **GLB** file is the binary, single-file version of **glTF 2.0**, an open standard
from the Khronos Group often called "the JPEG of 3D". One `.glb` contains the
model's geometry, materials, textures, and, where present, animations and a tree
of named parts. It is designed to load quickly in real-time viewers, which is why
browsers, game engines, and AR tools support it.

## Opening a GLB on Windows and Mac

- **Windows:** Microsoft's **3D Viewer** app opens GLB where it is installed, and
  Microsoft 365 apps such as PowerPoint can insert GLB files as 3D models.
- **Mac:** Apple's built-in previews are built around USDZ rather than GLB, so use
  a browser-based viewer or a 3D app such as Blender, which imports glTF/GLB.
- **Any computer:** drag the file into a browser-based glTF viewer. This is the
  fastest option and needs no installation.

## Opening a GLB on Android

Android's **Scene Viewer** (part of Google Play Services for AR) displays GLB
models in 3D and in AR. Web pages built with Google's `<model-viewer>` hand the
model to Scene Viewer when you tap the AR button.

## Opening a GLB on iPhone and iPad

This is where most people get stuck. iOS **AR Quick Look** does not use GLB: it
expects **USDZ**, Apple's format. A GLB will display in a web viewer on an iPhone,
but to place it in your room in AR you need a USDZ version of the same model.

## Sharing a GLB so it opens everywhere

If the goal is for other people to see the model, the most reliable route is a
hosted viewer that handles every device for you. Upload the `.glb` to AcademicAR
and you get:

- an optimized GLB (Draco geometry compression, webp textures) that loads fast on
  mobile,
- a **USDZ companion generated automatically**, so **View in AR** works on iPhone
  and iPad as well as Android,
- a **viewer link** that opens in any browser, and a **QR code** for print,
- for multi-part GLBs, a **Layers panel** with one layer per part.

GLB units are kept as authored, so AR shows the model at the size it was modelled.

## Common GLB problems

- **The model is huge or tiny in AR:** the exporter used the wrong unit. glTF is
  defined in metres, so a model exported in millimetres appears 1000 times too
  large. Fix the scale in the source application and re-export.
- **Textures are missing:** a `.gltf` file references external images, while a
  `.glb` embeds them. Export as GLB (binary) to keep everything in one file.
- **The file is too large to share:** compress geometry with Draco and textures
  with webp or KTX2, or let the upload optimizer do it.

*[Upload a GLB](/auth/register) and get a link that opens in 3D and AR on every
device.*
""",
    },
    {
        "slug": "3d-slicer-export-share",
        "title": "3D Slicer: Export Models and Share Them as Interactive 3D",
        "description": "Export segmentations from 3D Slicer as STL, OBJ, or .seg.nrrd and share them as a layered 3D viewer link with AR and a QR code, step by step.",
        "date": "2026-10-13",
        "author": "AcademicAR Team",
        "tags": ["3D Slicer", "segmentation", "medical imaging", "how-to"],
        "persona": "Medical imaging researchers & clinicians",
        "read_minutes": 7,
        "body": """\
3D Slicer is one of the most widely used free tools for segmenting CT and MRI
data. Getting a segmentation **out** of Slicer and in front of reviewers, students,
or colleagues is a separate problem. This guide covers the export options and how
to turn the result into a 3D link anyone can open.

## Two routes out of Slicer

There are two good ways to move a Slicer segmentation to a shareable viewer:

1. **Export surface models (STL or OBJ).** Slicer builds the 3D surfaces and you
   upload the meshes.
2. **Save the segmentation itself (`.seg.nrrd`).** You upload the label map and
   the surfaces are built on upload, one layer per segment.

Route 2 is usually better for multi-structure segmentations, because segment names
and colours travel with the file. Route 1 is useful when you have already
smoothed, decimated, or edited the surfaces in Slicer.

## Route 1: export STL or OBJ from Slicer

1. Open the **Segmentations** module and select your segmentation.
2. Expand **Export/import models and labelmaps**.
3. Choose **Export to files**, pick the output folder, and set the format to
   **STL** or **OBJ**.
4. Tick **Merge into single file** if you want one file for all segments. Leave
   it unticked for one file per segment.
5. Check the **size scale** and **coordinate system** settings. Slicer works in
   millimetres, so keep that unit in mind for the next step.

Then upload the file to AcademicAR and set the **source unit to millimetres**. A
single STL becomes one mesh. If readers should toggle structures separately,
prefer OBJ or the `.seg.nrrd` route below.

## Route 2: save the segmentation as .seg.nrrd

1. In Slicer, use **File → Save** (or **Save Data**).
2. Make sure the segmentation node is ticked and saved as **`.seg.nrrd`**.
3. Upload that file to AcademicAR as a **medical segmentation**.

On upload, each non-empty segment becomes its own named, coloured layer, with up
to 64 layers per model. NIfTI (`.nii`/`.nii.gz`), DICOM-SEG, and a ZIP of masks
work the same way if your pipeline produces those instead (see our post on
[segmentation formats](/blog/medical-image-segmentation-to-3d-viewer)).

Medical uploads need an extra confirmation that the data is anonymised and that
you have the right to share it. The raw file is deleted after conversion.

## What your readers get

The result is a viewer link and a QR code. Readers can:

- **show, hide, fade, or recolour** each structure in the Layers panel,
- cut through the model with the **section plane** and read the cut in the
  colour-coded **slice view**,
- see **structure dimensions and distances between layers** where layer
  measurements are enabled,
- open the model in **AR** on iPhone or Android at its real size.

You can also save a **default layer view** and **scenes** that walk readers through
the anatomy step by step.

## Starting from the DICOM instead

If you haven't segmented yet and only need bone or skin surfaces, you can skip
Slicer and upload the DICOM series as a ZIP with a threshold preset or a custom
Hounsfield range. See [From DICOM to Interactive 3D](/blog/dicom-to-3d-model-guide)
and [Hounsfield units explained](/blog/hounsfield-units-ct-thresholding-3d).

## Before you publish

- Remove faces and identifying features from head and neck reconstructions.
- Confirm consent and ethics approval cover publication of the 3D data.
- Name your segments clearly in Slicer: those names become the legend your readers
  see.

*[Create a free account](/auth/register) and share your first Slicer segmentation
as an interactive 3D model.*
""",
    },
    {
        "slug": "embed-3d-model-website",
        "title": "How to Embed a 3D Model on a Website, LMS, or Lab Page",
        "description": "Two ways to embed an interactive 3D model on a web page: self-host with model-viewer, or paste a hosted viewer iframe into WordPress, Moodle, or Canvas.",
        "date": "2026-10-14",
        "author": "AcademicAR Team",
        "tags": ["embedding", "3D viewer", "teaching", "how-to"],
        "persona": "Lab website owners & educators",
        "read_minutes": 6,
        "body": """\
A lab website, a course page, or a project site is a natural home for the 3D
models behind your research. There are two practical ways to put one there: build
the viewer yourself, or embed a hosted viewer. Here's how each works and when to
pick which.

## Option 1: self-host with model-viewer

Google's open-source **`<model-viewer>`** web component is the standard way to show
a GLB on a web page. The minimal version is two lines:

```html
<script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.3.0/model-viewer.min.js"></script>
<model-viewer src="model.glb" camera-controls ar alt="A 3D model"></model-viewer>
```

This works well if you control the site and are comfortable with HTML. You are
then responsible for:

- **Converting** your source files to an optimized GLB (and compressing them),
- **Producing a USDZ** if you want AR on iPhones (`ios-src`),
- **Hosting** the files with the right content types and caching,
- **Updating** every page by hand when the model changes.

## Option 2: embed a hosted viewer

If your site is WordPress, Squarespace, Google Sites, a university CMS, or an LMS
such as **Moodle** or **Canvas**, you often can't add scripts, but you can paste an
**iframe**. A hosted viewer gives you an iframe that already handles conversion,
compression, AR, and updates.

With AcademicAR:

1. Upload the model (GLB, STL, OBJ, FBX, STEP, or a medical scan or segmentation).
2. On the project page, click **Embed** next to the model. This copies an iframe
   snippet like:

```html
<iframe src="https://academicar.com/view/<model-id>?embed=true"
        width="100%" height="480" style="border:0;border-radius:8px"
        allow="xr-spatial-tracking; fullscreen" allowfullscreen loading="lazy"></iframe>
```

3. Paste it into an HTML or embed block on your page.

Visitors can rotate and zoom the model in the page, open it full screen, and open
it in AR on their phones. If you later **replace the model**, every page that
embeds it shows the new version, with no edits on your side.

## Which should you choose?

| | Self-hosted model-viewer | Hosted embed |
|---|---|---|
| Setup | HTML and file hosting | Paste an iframe |
| Works in LMS / CMS without scripts | Rarely | Yes |
| Conversion from STL, STEP, DICOM | Do it yourself | Automatic |
| iPhone AR (USDZ) | Generate it yourself | Automatic |
| Layers, section plane, slice view | Build it yourself | Included |
| Updating the model | Re-upload on every site | Replace once |

Self-hosting gives full control over look and code. A hosted embed is faster and
easier to maintain, especially when the same model also appears in a paper, a
poster QR code, and a course page.

## Tips for embeds that work well

- **Give the iframe a descriptive `title`** for screen readers. The copied snippet
  includes the model name.
- **Use `loading="lazy"`** so pages with several models don't load them all at
  once.
- **Keep the height at 400–600 px** on desktop. On phones, the full-screen button
  gives readers more room.
- **Add a short caption** with what the reader should look for, just like a figure.

*[Create a free account](/auth/register), upload a model, and copy its embed code.
See also [AR for STEM education](/ar-for-stem-education).*
""",
    },
]


def get_all_posts() -> list[dict]:
    """All built-in (code) posts, newest first."""
    return sorted(POSTS, key=lambda p: p["date"], reverse=True)


def get_post(slug: str) -> dict | None:
    for post in POSTS:
        if post["slug"] == slug:
            return post
    return None


def code_post_slugs() -> set[str]:
    """Slugs reserved by the built-in code posts (avoid DB slug collisions)."""
    return {p["slug"] for p in POSTS}


_COVER_RE = re.compile(
    r"!\[[^\]]*\]\((/(?:static|blog-images)/[^)\s]+\.(?:webp|png|jpe?g))\)", re.IGNORECASE
)


def cover_image_path(body: str) -> str | None:
    """Site path of the post's first raster image (its cover), for og:image and
    the BlogPosting schema: a repo image under /static/ or an admin upload under
    /blog-images/. SVG diagrams are skipped: link previews don't render them."""
    match = _COVER_RE.search(body or "")
    return match.group(1) if match else None


def related_posts(post: dict, posts: list[dict], limit: int = 3) -> list[dict]:
    """Up to ``limit`` other posts sharing the most tags with ``post`` (newest
    first on ties), for internal links under an article."""
    tags = {t.casefold() for t in post.get("tags") or []}
    scored = [
        (len(tags & {t.casefold() for t in p.get("tags") or []}), p["date"], p)
        for p in posts
        if p["slug"] != post["slug"]
    ]
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [p for _score, _date, p in scored[:limit]]
