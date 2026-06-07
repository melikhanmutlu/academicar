"""Programmatic discipline landing pages ("AR for <field>").

Each entry powers a single SEO page at ``/ar-for-<slug>`` plus the hub at
``/ar-for``. Content is intentionally distinct per discipline (no boilerplate
duplication) so the pages read as genuine resources rather than thin
doorway pages. Adding a field is a data-only change here — the route, template,
sitemap, and FAQPage JSON-LD all read from this dict.
"""

from __future__ import annotations

# Order here is the order shown on the hub page.
DISCIPLINES: dict[str, dict] = {
    "anatomy": {
        "name": "Anatomy",
        "title": "AR for Anatomy: Interactive 3D Models | AcademicAR",
        "meta_description": (
            "Turn anatomical scans and dissections into interactive 3D and AR models "
            "students can rotate, zoom, and place life-size — in any browser, no app."
        ),
        "h1": "Interactive 3D & AR for anatomy education and research",
        "subhead": (
            "Give students a structure they can rotate in their hand instead of a flat "
            "diagram on a slide — straight from a browser or a QR code on a handout."
        ),
        "intro": [
            "Anatomy is inherently three-dimensional, but most teaching material still "
            "flattens it into labelled illustrations and fixed-angle photographs. "
            "AcademicAR lets you upload a segmented CT/MRI mesh, a photogrammetry scan, "
            "or a textbook model and publish it as an interactive viewer with tap-to-place "
            "augmented reality — so a heart, skull, or brachial plexus can be examined from "
            "every angle and placed at true scale on a desk.",
            "Every model gets a permanent QR code and short link you can drop into a "
            "lecture slide, lab handout, dissection guide, or paper. No headset, no install, "
            "and the link keeps working even after you recolor or replace the model.",
        ],
        "why": [
            {"lead": "Spatial relationships click faster.", "text": "Rotating a real mesh makes "
             "the course of a nerve or the layering of fascia obvious in a way a 2D cross-section never can."},
            {"lead": "Life-size AR builds intuition for scale.", "text": "Placing a femur or a fetal "
             "heart at actual size on the bench turns abstract measurements into something students can feel."},
            {"lead": "It scales beyond the cadaver lab.", "text": "Specimen access is limited and "
             "expensive; a shared 3D model is available to every student, every term, on the device in their pocket."},
        ],
        "use_cases": [
            {"title": "Lecture & flipped classroom", "body": "Embed a QR code on the slide so students "
             "open the exact structure you're describing and explore it during or after class."},
            {"title": "Dissection & lab guides", "body": "Pair each printed station with a model that "
             "shows the idealised anatomy next to the real specimen on the table."},
            {"title": "Publications & theses", "body": "Attach an interactive figure to a morphology paper "
             "so reviewers and readers can inspect the geometry, not just a rendered screenshot."},
        ],
        "faqs": [
            {"q": "Can I upload a segmentation from CT or MRI?",
             "a": "Yes. Export your segmentation as STL, OBJ, GLB, or FBX and AcademicAR converts and "
                  "optimizes it for the web automatically. Large meshes are compressed without you doing anything."},
            {"q": "Do students need an app to see it in AR?",
             "a": "No. The viewer runs in any modern mobile browser, and tap-to-place AR works on most recent "
                  "iPhones, iPads, and Android phones with no install."},
            {"q": "Is this suitable for patient-derived data?",
             "a": "Uploading requires confirming anonymization and rights. Remove identifiers and surface "
                  "detail that could re-identify a patient before publishing, exactly as you would for any figure."},
            {"q": "Can I label specific structures?",
             "a": "You can add annotation hotspots to point out named structures, and set a real-world scale so the "
                  "AR placement matches life size."},
            {"q": "Will the QR code still work if I update the model?",
             "a": "Yes. The QR code and link are stable — replacing, recoloring, or upgrading the model keeps the same code."},
        ],
    },
    "chemistry": {
        "name": "Chemistry",
        "title": "AR for Chemistry: 3D Molecular Models | AcademicAR",
        "meta_description": (
            "Publish 3D molecular and crystal-structure models students can rotate and place in "
            "AR. Share interactive figures from any browser with a permanent QR code."
        ),
        "h1": "Interactive 3D & AR for molecular and materials chemistry",
        "subhead": (
            "Stereochemistry, binding sites, and crystal packing make sense when you can turn the "
            "molecule in your hands instead of squinting at a wedge-and-dash drawing."
        ),
        "intro": [
            "Chemistry lives in three dimensions — chirality, conformation, orbital overlap, and "
            "crystal packing are all spatial — yet most of it is taught and published as flat 2D "
            "structures. AcademicAR takes a 3D model exported from your modelling software and publishes "
            "it as an interactive viewer with augmented reality, so a protein active site or a unit cell "
            "can be rotated, zoomed, and placed on a real desk.",
            "Generate the geometry in PyMOL, VMD, Avogadro, Mercury, or any tool that exports GLB/OBJ/STL, "
            "upload it, and share a QR code on your poster, slide, or supplementary materials.",
        ],
        "why": [
            {"lead": "Stereochemistry stops being abstract.", "text": "R/S configuration and axial vs. "
             "equatorial positions are obvious when the model turns; they're guesswork on paper."},
            {"lead": "Show binding, not just structure.", "text": "Rotate a ligand inside its pocket to "
             "convey shape complementarity and key contacts that a single rendered view hides."},
            {"lead": "Make crystallography tangible.", "text": "Place a unit cell or a packing diagram at a "
             "graspable size so symmetry and layering read instantly."},
        ],
        "use_cases": [
            {"title": "Conference posters", "body": "A QR code next to your 2D scheme lets reviewers open the "
             "real 3D structure and inspect the geometry you're arguing about."},
            {"title": "Teaching organic & inorganic", "body": "Hand students a model of the exact molecule on "
             "the slide so they can check stereochemistry themselves."},
            {"title": "Supplementary information", "body": "Deposit interactive structures alongside a paper so "
             "readers explore conformers and complexes instead of static figures."},
        ],
        "faqs": [
            {"q": "Which formats can I upload from chemistry software?",
             "a": "Export to GLB, OBJ, STL, or FBX from PyMOL, VMD, Avogadro, Blender, or Mercury and AcademicAR "
                  "converts and optimizes it for the web. (Raw PDB/CIF files should be turned into a mesh first.)"},
            {"q": "Can I keep my atom colors and materials?",
             "a": "Yes — colors and materials baked into your exported GLB are preserved, and you can recolor the "
                  "model later without breaking the QR code."},
            {"q": "Is AR useful for small molecules?",
             "a": "Very — placing even a small molecule at a graspable scale on a desk makes conformation and "
                  "symmetry far easier to reason about than a flat drawing."},
            {"q": "Can readers view it without installing anything?",
             "a": "Yes. The viewer is a standard web page; AR placement works in the browser on most modern phones."},
            {"q": "Can I add labels to atoms or sites?",
             "a": "You can place annotation hotspots to call out an active site, a substituent, or a coordination center."},
        ],
    },
    "biology": {
        "name": "Biology",
        "title": "AR for Biology: 3D Specimens & Cells | AcademicAR",
        "meta_description": (
            "Share 3D scans of specimens, cells, and microstructures as interactive AR models. "
            "Publish from any browser with a stable QR code for papers and teaching."
        ),
        "h1": "Interactive 3D & AR for biology and life sciences",
        "subhead": (
            "From a confocal reconstruction to a whole-organism micro-CT scan, let students and "
            "reviewers explore the specimen instead of a single chosen camera angle."
        ),
        "intro": [
            "Biological structure spans scales — organelles, cells, tissues, whole organisms — and almost "
            "all of it is volumetric. AcademicAR turns a confocal stack reconstruction, a micro-CT scan, or "
            "a photogrammetry model of a specimen into an interactive viewer with augmented reality, so the "
            "geometry can be examined from any angle and placed at a chosen scale.",
            "Upload from STL, OBJ, GLB, or FBX, and publish a QR code for your figure, poster, field guide, "
            "or museum-style label — no app and no specialised viewer required.",
        ],
        "why": [
            {"lead": "Morphology is the message.", "text": "Form and structure carry the meaning in much of "
             "biology; an interactive mesh shows it without forcing one fixed viewpoint."},
            {"lead": "Bridge micrographs and volume.", "text": "A rotatable reconstruction connects 2D imaging "
             "to the 3D object it came from, which static panels rarely manage."},
            {"lead": "Reuse rare specimens.", "text": "A scanned holotype or fragile specimen can be shared and "
             "studied widely without risking the physical sample."},
        ],
        "use_cases": [
            {"title": "Morphology & systematics papers", "body": "Attach interactive type-specimen models so "
             "reviewers can assess characters directly from the geometry."},
            {"title": "Teaching cell & developmental biology", "body": "Let students walk around a cell or embryo "
             "reconstruction at a scale they can grasp."},
            {"title": "Natural-history outreach", "body": "Put a QR code beside a specimen so visitors open a 3D "
             "model and rotate what's behind the glass."},
        ],
        "faqs": [
            {"q": "Can I publish a holotype or type specimen?",
             "a": "Yes. Many researchers share scanned specimens as interactive figures; confirm you hold the rights "
                  "and follow your repository's policy before publishing."},
            {"q": "What if my reconstruction is very large?",
             "a": "Large meshes are automatically compressed and optimized for the web on upload, so they load "
                  "smoothly even on phones."},
            {"q": "Does AR work for microscopic structures?",
             "a": "Yes — you set a display scale, so a cell or organelle can be placed on a desk at any size that "
                  "makes the structure clear."},
            {"q": "Can I link the model back to my paper?",
             "a": "Each public model page can show the publication title, authors, and DOI, so the model and the "
                  "article stay connected."},
            {"q": "Do viewers need an account?",
             "a": "No. Anyone with the link or QR code can view and place the model; only uploading requires an account."},
        ],
    },
    "archaeology": {
        "name": "Archaeology",
        "title": "AR for Archaeology: 3D Artifacts | AcademicAR",
        "meta_description": (
            "Publish 3D scans of artifacts and reconstructions as interactive AR models. Share "
            "finds and heritage objects from any browser with a permanent QR code."
        ),
        "h1": "Interactive 3D & AR for archaeology and heritage",
        "subhead": (
            "Let readers handle the artifact — rotate a vessel, inspect tool marks, place a "
            "reconstruction at full scale — without the object ever leaving the store."
        ),
        "intro": [
            "Photogrammetry and structured-light scanning have made high-fidelity 3D records of "
            "artifacts routine, but those models too often sit unseen in a data repository. AcademicAR "
            "publishes a scanned find or a digital reconstruction as an interactive viewer with augmented "
            "reality, so a ceramic, lithic, or architectural fragment can be rotated, measured by eye, and "
            "placed at true scale.",
            "Drop a QR code into an excavation report, museum label, site guide, or paper, and the object "
            "becomes explorable to anyone with a phone — no proprietary viewer, no download.",
        ],
        "why": [
            {"lead": "Document at a glance.", "text": "Surface detail, tool marks, and form read far better on a "
             "rotatable scan than on a plate of fixed photographs."},
            {"lead": "Protect fragile originals.", "text": "Share and study a digital surrogate widely while the "
             "physical artifact stays safe in storage."},
            {"lead": "Make reconstructions legible.", "text": "Place a reconstructed structure or vessel at full "
             "scale so its size and proportion land immediately."},
        ],
        "use_cases": [
            {"title": "Excavation reports & papers", "body": "Replace a plate of photographs with an interactive "
             "model readers can turn and inspect."},
            {"title": "Museum & site interpretation", "body": "A QR code on the label opens a 3D model so visitors "
             "explore an object they can't physically handle."},
            {"title": "Teaching material culture", "body": "Give students typological examples they can rotate and "
             "compare instead of slides."},
        ],
        "faqs": [
            {"q": "Can I upload a photogrammetry model?",
             "a": "Yes. Export your textured mesh to GLB, OBJ, STL, or FBX and AcademicAR converts and optimizes it "
                  "for fast web viewing, textures included."},
            {"q": "Will textures and color survive upload?",
             "a": "Baked textures and vertex colors in your GLB are preserved through conversion."},
            {"q": "Can I set the real-world size for AR?",
             "a": "Yes — set a scale reference so the AR placement matches the artifact's true dimensions."},
            {"q": "Is it suitable for unpublished or sensitive finds?",
             "a": "You control whether a model is public. Confirm you hold the rights and follow any permit or "
                  "repository restrictions before sharing."},
            {"q": "Does the link stay stable for citation?",
             "a": "Yes. The QR code and short link are permanent and survive model replacement, so they're safe to cite."},
        ],
    },
    "geology": {
        "name": "Geology",
        "title": "AR for Geology: 3D Samples & Outcrops | AcademicAR",
        "meta_description": (
            "Share 3D scans of hand samples, fossils, and outcrops as interactive AR models. "
            "Bring field and lab specimens to any browser with a permanent QR code."
        ),
        "h1": "Interactive 3D & AR for geology and earth science",
        "subhead": (
            "Bring the outcrop into the classroom — rotate a hand sample, walk around a folded "
            "structure, place a core at scale, all from a browser."
        ),
        "intro": [
            "Earth science depends on three-dimensional observation — the geometry of a fold, the texture "
            "of a hand sample, the structure of a fossil — but field access is seasonal, costly, and "
            "sometimes impossible. AcademicAR turns a scanned specimen or a photogrammetry model of an "
            "outcrop into an interactive viewer with augmented reality, so structure and texture can be "
            "examined from every side and placed at a workable scale.",
            "Upload from GLB, OBJ, STL, or FBX and share a QR code in a field guide, lab manual, poster, or "
            "paper, so anyone can explore the specimen on a phone.",
        ],
        "why": [
            {"lead": "Structure is spatial.", "text": "Folds, faults, and bedding relationships are obvious when "
             "you can turn the model; they're ambiguous in a single photo."},
            {"lead": "Extend the field season.", "text": "A scanned outcrop or sample is available to study and "
             "teach long after — and far from — the field site."},
            {"lead": "Standardise specimens.", "text": "Every student gets the same reference specimen to rotate "
             "and compare, regardless of what's in the teaching drawer."},
        ],
        "use_cases": [
            {"title": "Field & lab teaching", "body": "Pair each station with a scanned hand sample students can "
             "rotate and zoom on their own device."},
            {"title": "Structural geology papers", "body": "Show a folded or faulted structure as an interactive "
             "model so reviewers can assess the geometry."},
            {"title": "Core & sample archives", "body": "Publish scanned cores and specimens with stable links for "
             "reference and citation."},
        ],
        "faqs": [
            {"q": "Can I publish an outcrop photogrammetry model?",
             "a": "Yes. Export the textured mesh to GLB, OBJ, STL, or FBX; large models are compressed and optimized "
                  "on upload so they load on phones."},
            {"q": "Do textures and surface detail survive?",
             "a": "Baked textures and vertex colors in the GLB are preserved through conversion."},
            {"q": "Can I set a real-world scale?",
             "a": "Yes — set a scale reference so a hand sample or core is placed at a realistic size in AR."},
            {"q": "Will it run on a student's phone in the field?",
             "a": "Yes. The viewer runs in any modern mobile browser; an internet connection is all that's needed."},
            {"q": "Can I cite the model?",
             "a": "Each model has a permanent link and QR code that survive replacement, so they're stable to cite."},
        ],
    },
    "engineering": {
        "name": "Engineering",
        "title": "AR for Engineering: 3D CAD Models | AcademicAR",
        "meta_description": (
            "Publish CAD parts and assemblies as interactive 3D and AR models. Share mechanisms "
            "and designs from any browser with a permanent QR code — no CAD seat required."
        ),
        "h1": "Interactive 3D & AR for engineering and design",
        "subhead": (
            "Let a reviewer, student, or client turn the part and place the assembly on a real "
            "bench — without a CAD license or a heavy file download."
        ),
        "intro": [
            "Engineering work is created in 3D but shared as 2D drawings, screenshots, and PDFs that lose "
            "the spatial information that matters. AcademicAR publishes a CAD export as an interactive viewer "
            "with augmented reality, so a part, mechanism, or assembly can be rotated, examined, and placed "
            "at true scale on a desk or in a room — without anyone needing a CAD seat.",
            "Export from SolidWorks, Fusion 360, Onshape, Blender, or any tool that writes GLB/OBJ/STL/FBX, "
            "upload, and share a QR code on a report, poster, datasheet, or course handout.",
        ],
        "why": [
            {"lead": "Drawings lose the third dimension.", "text": "An interactive model conveys fit, clearance, "
             "and form in a way orthographic views and renders can't."},
            {"lead": "AR validates real-world scale.", "text": "Placing a part or assembly at full size in the "
             "actual space catches size and ergonomics issues early."},
            {"lead": "No CAD seat to review.", "text": "Stakeholders open the model in a browser instead of "
             "installing software or wrangling a STEP file."},
        ],
        "use_cases": [
            {"title": "Design reviews & reports", "body": "Share an interactive assembly so reviewers explore the "
             "geometry instead of flipping through rendered views."},
            {"title": "Teaching mechanisms & CAD", "body": "Give students a rotatable model of the exact part on the "
             "slide to inspect features and tolerances."},
            {"title": "Posters & capstone showcases", "body": "A QR code lets visitors open and place your design at "
             "full scale at a conference or expo."},
        ],
        "faqs": [
            {"q": "Which CAD exports work?",
             "a": "Export to GLB, OBJ, STL, or FBX from your CAD or 3D tool. (Tessellate solids to a mesh first — "
                  "AcademicAR works with meshes, not native CAD or STEP files.)"},
            {"q": "Can I show an assembly with multiple parts?",
             "a": "Yes — export the assembly as a single mesh with its materials and it views as one interactive model."},
            {"q": "Do reviewers need any software?",
             "a": "No. The viewer is a web page and AR placement runs in the browser on most modern phones and tablets."},
            {"q": "Can I set the model to real dimensions?",
             "a": "Yes — set a scale reference so AR placement matches the part's true size."},
            {"q": "Are my files kept private?",
             "a": "You choose whether a model is public. Private models are only reachable by you and the people you share the link with."},
        ],
    },
    "museums": {
        "name": "Museums & Heritage",
        "title": "AR for Museums: 3D Collections | AcademicAR",
        "meta_description": (
            "Open your collection with interactive 3D and AR. Put a QR code on the label so "
            "visitors rotate and place objects — from any browser, no app to install."
        ),
        "h1": "Interactive 3D & AR for museums and collections",
        "subhead": (
            "Turn a case full of objects-behind-glass into things visitors can rotate, zoom, and "
            "place in the gallery — with nothing but the phone in their pocket."
        ),
        "intro": [
            "Museums hold far more than they can display, and even objects on show are usually fixed behind "
            "glass at a single angle. AcademicAR publishes a scanned object as an interactive viewer with "
            "augmented reality, so visitors and researchers can rotate it, zoom into detail, and place it at "
            "true scale — extending the collection beyond the gallery wall.",
            "Add a QR code to a label, catalogue entry, or web page and the object opens on any phone, with no "
            "app to install and a link that stays stable for citation and reuse.",
        ],
        "why": [
            {"lead": "Show what's in storage.", "text": "Most of a collection is never on display; a 3D model "
             "brings hidden objects to the public without moving them."},
            {"lead": "Access from any angle.", "text": "Visitors explore the back, base, and interior of an object "
             "they could never handle in person."},
            {"lead": "One scan, many channels.", "text": "The same model serves the gallery label, the website, the "
             "education program, and a research catalogue."},
        ],
        "use_cases": [
            {"title": "Gallery labels", "body": "A QR code beside the case opens a model visitors can turn and place, "
             "deepening engagement without extra hardware."},
            {"title": "Online collections", "body": "Embed interactive objects in catalogue pages so remote audiences "
             "explore the collection in 3D."},
            {"title": "Education & outreach", "body": "Hand classrooms a rotatable model of an object for lessons and "
             "activities built around the real thing."},
        ],
        "faqs": [
            {"q": "Do visitors need to install an app?",
             "a": "No. The viewer opens in any modern mobile browser and AR placement works without an install."},
            {"q": "Can we keep our scan's textures and color?",
             "a": "Yes — textures and vertex colors baked into the uploaded GLB are preserved, so the object looks right."},
            {"q": "Can the model be embedded on our website?",
             "a": "Each model has its own public page and stable link you can place behind a QR code or share directly."},
            {"q": "Is the link safe to print on a permanent label?",
             "a": "Yes. The QR code and link are permanent and survive replacing or updating the model, so reprints aren't needed."},
            {"q": "Can we control which objects are public?",
             "a": "Yes — you decide whether each model is public, so works in copyright or under embargo stay private."},
        ],
    },
    "stem-education": {
        "name": "STEM Education",
        "title": "AR for STEM Education: 3D Models | AcademicAR",
        "meta_description": (
            "Make STEM lessons interactive with 3D and AR. Put a QR code on a slide or worksheet "
            "so students rotate and place models — in any browser, no app, no headset."
        ),
        "h1": "Interactive 3D & AR for STEM teaching",
        "subhead": (
            "Drop a QR code on a worksheet and a static diagram becomes something every student "
            "can rotate, zoom, and stand next to — on the device already in their hand."
        ),
        "intro": [
            "Across science, technology, engineering, and maths, the hardest ideas are usually the spatial "
            "ones — and they're the ones flat diagrams serve worst. AcademicAR lets a teacher publish any 3D "
            "model as an interactive viewer with augmented reality, so students rotate it, zoom in, and place "
            "it in the room, turning a slide into something they can explore on their own device.",
            "It works on the phones and tablets students already carry: no headset, no install, and a QR code "
            "or link you can paste into a slide, worksheet, or learning-management system.",
        ],
        "why": [
            {"lead": "Spatial reasoning needs spatial tools.", "text": "Letting students turn a model themselves "
             "builds intuition that a fixed diagram can't."},
            {"lead": "Hardware-free AR.", "text": "It runs on the devices a class already has, so AR reaches every "
             "student without a lab budget or headsets."},
            {"lead": "Reusable across lessons.", "text": "Build a small library of models once and a QR code drops "
             "the right one into any future slide or worksheet."},
        ],
        "use_cases": [
            {"title": "Slides & worksheets", "body": "A QR code opens the exact model you're teaching so students "
             "explore it during or after the lesson."},
            {"title": "Stations & flipped learning", "body": "Set up self-paced stations where students rotate and "
             "place a model and answer prompts about it."},
            {"title": "Student projects", "body": "Have students publish their own 3D work and share it with a QR code "
             "for presentations and assessment."},
        ],
        "faqs": [
            {"q": "What devices do students need?",
             "a": "Any modern phone, tablet, or computer with a browser. Tap-to-place AR works on most recent phones "
                  "and tablets with no app to install."},
            {"q": "Where do I get 3D models to use?",
             "a": "Upload models you or your students create, or models you have the rights to use, in GLB, STL, OBJ, "
                  "or FBX. AcademicAR converts and optimizes them for the web."},
            {"q": "Can I share a model in my LMS?",
             "a": "Yes — paste the model's link into Canvas, Moodle, Google Classroom, or any platform, or share the QR code."},
            {"q": "Is there a free way to try it?",
             "a": "Yes. You can create a free account and publish a model to see the full viewer, AR, and QR workflow."},
            {"q": "Do students need accounts to view?",
             "a": "No. Anyone with the link or QR code can view and place a model; only the teacher or student publishing it needs an account."},
        ],
    },
}


def all_disciplines() -> list[dict]:
    """Disciplines in hub display order, each augmented with its ``slug``."""
    return [{"slug": slug, **data} for slug, data in DISCIPLINES.items()]


def get_discipline(slug: str) -> dict | None:
    """Return one discipline (with ``slug``) or ``None`` if the slug is unknown."""
    data = DISCIPLINES.get((slug or "").strip().lower())
    if not data:
        return None
    return {"slug": slug, **data}


def related_disciplines(slug: str, limit: int = 3) -> list[dict]:
    """Sibling disciplines for internal linking, excluding ``slug``."""
    siblings = [d for d in all_disciplines() if d["slug"] != slug]
    return siblings[:limit]


def discipline_slugs() -> list[str]:
    """All valid discipline slugs (used by the sitemap)."""
    return list(DISCIPLINES.keys())
