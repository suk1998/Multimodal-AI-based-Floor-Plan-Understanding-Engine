# FPUE — Floor Plan Understanding Engine

**Multimodal AI-based Architectural Floor Plan Understanding and Change Detection**

FPUE compares a Reference floor plan (사업승인도) with a Target floor plan (실시설계도). Its practical goal is to locate and explain supported drawing changes and export a marked Target image and CSV report.

Architectural understanding supplies geometry, meaning and relationship evidence for comparison. It is not a prerequisite that every room boundary, host or relationship must be completed before a local difference can be assessed.

**Status: research prototype.** Complete architectural understanding, automatic physical object identity and general change-detection accuracy have not been established. Original drawings, ground truth, model weights and internal research documents are not distributed in this repository.

## Current web workflow

1. Upload independent Reference and Target PDFs.
2. Select a page in each PDF and render the drawings.
3. Inspect the drawings with zoom, pan and fit-to-screen controls.
4. Draw or edit a rectangular Target ROI in original image pixel coordinates; save/load its ROI JSON.
5. Run the comparison workflow using the available local OCR, geometry and understanding components.
6. Review supported changes and deferred candidates, then download the Target PNG and change-report CSV.

The application starts with empty PDF inputs. It does not preload a fixed Page 4 demonstration or apply historical results to new uploads. No login is required by the current public PDF workspace server.

PDF upload, rendering and ROI editing work without OCR/VLM weights. Analysis completeness depends on the installed OCR environment, source drawing quality, geometry support and verified comparison scope. Missing components and insufficient evidence remain explicit failures or deferred results.

## How comparison uses understanding

```text
Reference PDF → own-frame observations → Reference understanding ─┐
                                                               ├→ structural alignment
Target PDF    → own-frame observations → Target understanding ──┘
                            ↓
        geometric and printed-text comparison channels
                            ↓
      evidence-scoped correspondence and difference assessment
                            ↓
           review → marked Target PNG + CSV report
```

- **Observations:** PDF vector paths, raster geometry and available OCR text regions.
- **Understanding:** space-boundary candidates, walls, openings, installation candidates, symbols and relationship hypotheses.
- **Shared ontology contracts:** architectural types, relationship signatures, evidence and claim-state meanings used by the analysis and comparison code.
- **Correspondence:** registered geometry, source visibility, independent structural validation and competing-candidate checks. Mutual best matching alone does not establish door correspondence.
- **Scoped changes:** dimension text, printed door/window codes, supported wall-end observations, opening profiles and measured door drawing geometry.
- **Claim dependencies:** drawing/text judgments are separated from physical installation, product specification and space-connection judgments. An unresolved relationship does not imply a contradiction.

`PROPOSED`, `SUPPORTED` and `VERIFIED` are distinct architectural claim states. Comparison dependency assessments such as `SUPPORTED_CHANGE` or `DEFERRED` describe a particular judgment's scope; they do not promote the original ontology claims.

A printed code change does not by itself prove product replacement. A drawn opening/profile difference does not by itself prove a change in physical installation. Missing detection does not establish deletion. Incomplete or inferred room boundaries are not verified physical walls.

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI workspace, observations, understanding, alignment, comparison, review and exports |
| `frontend/` | React + TypeScript viewer and comparison UI |
| `phase1_pdf.py` | Shared PDF inspection and rendering |
| `phase1_viewer_frontend/geometry.js` | Original native-pixel ↔ browser-coordinate implementation reused by React |
| `integrations/fpue_ontology/` | Shared contracts, vocabulary, versioned OWL schema generators and RDF serialization |
| `poc/` | Geometry functions reused by the backend, OCR entry points and optional model experiments |
| `scripts/` | Web startup, connection management and ontology export |
| `tests/`, `backend/test_*.py`, `frontend/tests/` | Regression controls; some historical tests require private fixtures |

`poc/` and `phase1_viewer_frontend/geometry.js` are runtime dependencies. They must not be omitted when copying the application.

## Local setup

Run commands from the repository root. The current development environment uses Ubuntu, Python **3.12.14**, Node.js **24.22.3** and npm **11.19.1**. Other versions have not been validated by this release.

```bash
git clone https://github.com/suk1998/Multimodal-AI-based-Floor-Plan-Understanding-Engine.git
cd Multimodal-AI-based-Floor-Plan-Understanding-Engine

python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
npm --prefix frontend ci

./scripts/run_web.sh
```

Open **http://127.0.0.1:8020**. The script builds React and serves its static assets and FastAPI through the same origin. It binds to loopback and does not create an external tunnel.

For frontend development, keep the web server running and use a second terminal:

```bash
npm --prefix frontend run dev
```

Vite proxies `/api` to the existing server on port 8020. The backend continues to use the environment at `backend/.venv` for analysis workers.

### Optional OCR

The web OCR worker expects:

```text
poc/.venv/bin/python
poc/models/det/inference.pdiparams
poc/models/rec/inference.pdiparams
```

The existing OCR configuration uses **PP-OCRv5_server_det** for text detection and **korean_PP-OCRv5_mobile_rec** for recognition. The current web OCR subprocess disables CUDA; the web viewer does not require an RTX GPU.

A separate OCR environment is required. The following is a setup starting point using the locally exercised Paddle versions; a complete fresh-machine OCR bootstrap has not been independently validated:

```bash
python3.12 -m venv poc/.venv
poc/.venv/bin/python -m pip install \
  paddlepaddle==3.3.1 paddleocr==3.7.0 PyMuPDF==1.28.2 Pillow huggingface_hub

# Downloads only the two existing OCR models at revisions pinned in poc/run.py.
poc/.venv/bin/python -B poc/download_models.py det rec
```

`poc/requirements.txt` records a broader research environment, including VLM libraries; it is not needed to start the PDF viewer. Missing OCR environments or weights must be resolved before expecting complete OCR-dependent analysis. No new models are downloaded by web startup.

### Optional ontology RDF validation

Pure-Python architectural contracts are used by the normal backend. RDF validation/export additionally requires the isolated ontology environment and the schema/export source files:

```bash
python3.12 -m venv integrations/fpue_ontology/.venv
integrations/fpue_ontology/.venv/bin/python -m pip install \
  -r integrations/fpue_ontology/requirements.lock
```

`scripts/export_reference_ontology.py` validates native graph contracts and exports Turtle. Contract or RDF round-trip success does not establish architectural ground truth or complete physical understanding.

### Optional VLM experiments

Qwen3.5-9B and Qwen3-VL-8B-Instruct are retained as research experiment configurations. **VLM inference is not enabled in the current web comparison workflow.** Model weights, quantization dependencies and GPU setup are separate from web startup. VLM output remains a proposal until supported by appropriate geometry or independent validation.

## Coordinates and storage

- Ref and Target retain separate PDF/image Frame IDs, source hashes, page numbers, extraction scales and native pixel coordinates.
- Browser zoom/pan changes display coordinates only.
- Target ROI coordinates are not copied directly into Reference coordinates. Mapping requires a supported transform and comparison scope.
- Uploaded PDFs are processed on the server. The current anonymous workspace and comparison snapshots are held in memory and expire after inactivity; a server restart loses those temporary sessions.
- CSV/PNG downloads are saved through the user's browser. Runtime logs and temporary processing files are not repository data or public static assets.
- This workflow does not provide durable multi-user project storage.

## Validation

```bash
npm --prefix frontend test
npm --prefix frontend run build

# 36 synthetic regressions for door correspondence, claim dependencies and exports.
backend/.venv/bin/python -B -m unittest \
  backend.test_door_correspondence backend.test_comparison_claims
```

The synthetic controls cover absolute geometry and competition requirements, host HOLD versus verified relationship conflict, changed drawing geometry, claim-state preservation and CSV/PNG delivery. They are not an accuracy benchmark on arbitrary floor plans.

Some additional historical tests reference original PDFs, ROI files, assessments or GT that are intentionally not published. Running all discovered tests without those fixtures is not a supported public benchmark. Real-drawing evidence and synthetic test results must be reported separately. No precision, recall, F1 or GT-image IoU is claimed here.

## External access

For an already configured Cloudflare Quick Tunnel installation:

```bash
./fpue start
./fpue status
```

The manager expects `cloudflared` at `~/.local/bin/cloudflared`, or the path in `FPUE_CLOUDFLARED_BIN`. It reuses matching server/tunnel processes and prints the active HTTPS address. Quick Tunnel addresses are temporary and can change after restart. The Ubuntu host and tunnel must remain running. A fixed hostname requires separately configured domain/account and Named Tunnel settings.

## Public distribution scope

The repository excludes `docs/`, original PDFs/PNGs, GT, model weights, virtual environments, internal experimental outputs, private databases and credentials. Versioned schema generators and synthetic test source are included; generated project-specific RDF and analysis results are not.

Current limitations include partial room reconstruction, incomplete installation/host grounding, limited handling of some transformations, OCR failures and deferred correspondence where structural support is insufficient. A successful execution is not a declaration that the entire drawing was understood or that every real change was detected.
