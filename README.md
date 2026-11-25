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

# FPUE — 평면도 이해 엔진 (Floor Plan Understanding Engine)

**멀티모달 AI 기반 건축 평면도 이해 및 변경 감지**

FPUE는 기준 평면도(사업승인도)와 대상 평면도(실시설계도)를 비교합니다. 실질적인 목표는 지원되는 도면의 변경 사항을 찾아 설명하고, 변경 사항이 표시된 대상(Target) 이미지와 CSV 리포트를 내보내는 것입니다.

건축적 이해는 비교를 위한 기하학적 구조, 의미, 관계적 근거를 제공합니다. 국소적인 차이를 평가하기 전에 모든 실(방) 경계, 호스트(벽체 등), 또는 관계를 완전히 규명해야 하는 것은 아닙니다.

**상태: 연구용 프로토타입.** 완전한 건축적 이해, 물리적 객체의 자동 식별, 전반적인 변경 감지 정확도는 아직 확립되지 않았습니다. 원본 도면, 정답 데이터(Ground Truth), 모델 가중치 및 내부 연구 문서는 이 저장소에 배포되지 않습니다.

## 현재 웹 워크플로우

1. 기준(Reference) 및 대상(Target) PDF를 각각 업로드합니다.
2. 각 PDF에서 페이지를 선택하고 도면을 렌더링합니다.
3. 확대/축소(Zoom), 이동(Pan), 화면 맞춤(Fit-to-screen) 기능을 사용하여 도면을 검토합니다.
4. 원본 이미지 픽셀 좌표계에서 직사각형 대상 ROI(관심 영역)를 그리거나 편집하고, 해당 ROI JSON을 저장/불러오기 합니다.
5. 사용 가능한 로컬 OCR, 기하학 및 이해 컴포넌트를 사용하여 비교 워크플로우를 실행합니다.
6. 지원되는 변경 사항 및 보류(Deferred) 후보를 검토한 후, 대상 PNG 및 변경 리포트 CSV를 다운로드합니다.

애플리케이션은 빈 PDF 입력 상태로 시작합니다. 고정된 4페이지 데모를 미리 불러오거나 새로운 업로드에 과거 결과를 적용하지 않습니다. 현재 공개 PDF 작업 공간 서버는 로그인을 요구하지 않습니다.

PDF 업로드, 렌더링 및 ROI 편집은 OCR/VLM 가중치 없이도 작동합니다. 분석의 완전성은 설치된 OCR 환경, 원본 도면 품질, 기하학 지원 여부 및 검증된 비교 범위에 따라 달라집니다. 누락된 컴포넌트나 불충분한 근거는 명시적인 실패 또는 보류(Deferred) 결과로 남습니다.

## 비교 과정에서 이해(Understanding)가 활용되는 방식

```text
기준(Reference) PDF → 자체 프레임 관측 → 기준 이해 ─┐
                                                   ├→ 구조적 정합 (Structural alignment)
대상(Target) PDF    → 자체 프레임 관측 → 대상 이해 ─┘
                            ↓
               기하학 및 인쇄 텍스트 비교 채널
                            ↓
             근거 기반 대응 관계 및 차이 평가
                            ↓
           검토 → 표시된 대상 PNG + CSV 리포트
```

- **관측 (Observations):** PDF 벡터 경로, 래스터 기하학 및 사용 가능한 OCR 텍스트 영역.
- **이해 (Understanding):** 공간 경계 후보, 벽체, 개구부, 설비 후보, 심볼 및 관계 가설.
- **공유 온톨로지 계약 (Shared ontology contracts):** 분석 및 비교 코드에서 사용되는 건축 유형, 관계 시그니처, 근거 및 주장 상태(Claim-state)의 의미.
- **대응 관계 (Correspondence):** 정합된 기하학, 원본 가시성, 독립적인 구조적 검증 및 경합 후보 확인. 단순한 상호 최적 매칭(Mutual best matching)만으로는 문(Door)의 대응 관계를 확정하지 않습니다.
- **범위 내 변경 사항 (Scoped changes):** 치수 텍스트, 인쇄된 문/창호 코드, 지원되는 벽체 끝단 관측, 개구부 프로파일 및 측정된 문 도면 기하학.
- **주장 종속성 (Claim dependencies):** 도면/텍스트 판단은 물리적 시공, 제품 사양 및 공간 연결 판단과 분리됩니다. 해결되지 않은 관계가 곧 모순을 의미하지는 않습니다.

`PROPOSED`(제안됨), `SUPPORTED`(근거 있음), `VERIFIED`(검증됨)는 서로 구분되는 건축적 주장 상태입니다. `SUPPORTED_CHANGE` 또는 `DEFERRED`와 같은 비교 종속성 평가는 특정 판단의 범위를 설명할 뿐, 원래의 온톨로지 주장을 상위 상태로 승격시키지 않습니다.

인쇄된 코드의 변경만으로 제품 교체가 입증되는 것은 아닙니다. 그려진 개구부/프로파일의 차이만으로 물리적 시공의 변경이 입증되는 것은 아닙니다. 미탐지가 곧 삭제를 의미하지 않습니다. 불완전하거나 추론된 실 경계는 검증된 물리적 벽체가 아닙니다.

## 저장소 구조

| 경로 | 목적 |
|---|---|
| `backend/` | FastAPI 작업 공간, 관측, 이해, 정합, 비교, 검토 및 내보내기 |
| `frontend/` | React + TypeScript 뷰어 및 비교 UI |
| `phase1_pdf.py` | 공유 PDF 검사 및 렌더링 |
| `phase1_viewer_frontend/geometry.js` | React에서 재사용되는 원본 네이티브 픽셀 ↔ 브라우저 좌표 변환 구현 |
| `integrations/fpue_ontology/` | 공유 계약, 어휘, 버전 관리된 OWL 스키마 생성기 및 RDF 직렬화 |
| `poc/` | 백엔드에서 재사용되는 기하학 함수, OCR 진입점 및 선택적 모델 실험 |
| `scripts/` | 웹 시작, 연결 관리 및 온톨로지 내보내기 |
| `tests/`, `backend/test_*.py`, `frontend/tests/` | 회귀 테스트 제어; 일부 과거 테스트는 비공개 픽스처(Fixture)가 필요함 |

`poc/`와 `phase1_viewer_frontend/geometry.js`는 런타임 의존성 파일입니다. 애플리케이션을 복사할 때 누락해서는 안 됩니다.

## 로컬 설정

저장소 루트 경로에서 명령어를 실행하세요. 현재 개발 환경은 Ubuntu, Python **3.12.14**, Node.js **24.22.3**, npm **11.19.1**을 사용합니다. 다른 버전은 이 릴리스에서 검증되지 않았습니다.

```bash
git clone https://github.com/suk1998/Multimodal-AI-based-Floor-Plan-Understanding-Engine.git
cd Multimodal-AI-based-Floor-Plan-Understanding-Engine

python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
npm --prefix frontend ci

./scripts/run_web.sh
```

**http://127.0.0.1:8020**을 엽니다. 이 스크립트는 React를 빌드하고 동일한 오리진(Origin)을 통해 정적 에셋과 FastAPI를 제공합니다. 루프백(loopback)에 바인딩되며 외부 터널을 생성하지 않습니다.

프론트엔드 개발을 위해서는 웹 서버를 실행한 상태로 두 번째 터미널에서 다음을 실행합니다:

```bash
npm --prefix frontend run dev
```

Vite는 `/api` 요청을 포트 8020의 기존 서버로 프록시합니다. 백엔드는 분석 워커를 위해 `backend/.venv` 환경을 계속 사용합니다.

### 선택적 OCR 설정

웹 OCR 워커는 다음 경로의 파일을 필요로 합니다:

```text
poc/.venv/bin/python
poc/models/det/inference.pdiparams
poc/models/rec/inference.pdiparams
```

기존 OCR 구성은 텍스트 감지에 **PP-OCRv5_server_det**를, 인식에 **korean_PP-OCRv5_mobile_rec**를 사용합니다. 현재 웹 OCR 서브프로세스는 CUDA를 비활성화하므로 웹 뷰어 실행에 RTX GPU가 필요하지 않습니다.

별도의 OCR 가상 환경이 필요합니다. 다음은 로컬에서 테스트된 Paddle 버전을 기준으로 한 설정 시작 가이드이며, 완전히 초기화된 머신에서의 전체 OCR 부트스트랩은 독립적으로 검증되지 않았습니다:

```bash
python3.12 -m venv poc/.venv
poc/.venv/bin/python -m pip install \
  paddlepaddle==3.3.1 paddleocr==3.7.0 PyMuPDF==1.28.2 Pillow huggingface_hub

# poc/run.py에 고정된 리비전의 기존 OCR 모델 2개만 다운로드합니다.
poc/.venv/bin/python -B poc/download_models.py det rec
```

`poc/requirements.txt`에는 VLM 라이브러리를 포함한 더 광범위한 연구 환경이 기록되어 있으나, PDF 뷰어를 시작하는 데는 필요하지 않습니다. OCR에 의존하는 완전한 분석을 수행하려면 누락된 OCR 환경이나 가중치를 먼저 해결해야 합니다. 웹 시작 시에는 새로운 모델이 다운로드되지 않습니다.

### 선택적 온톨로지 RDF 검증

일반 백엔드에서는 순수 Python 건축 계약이 사용됩니다. RDF 검증 및 내보내기를 위해서는 격리된 온톨로지 환경과 스키마/내보내기 소스 파일이 추가로 필요합니다:

```bash
python3.12 -m venv integrations/fpue_ontology/.venv
integrations/fpue_ontology/.venv/bin/python -m pip install \
  -r integrations/fpue_ontology/requirements.lock
```

`scripts/export_reference_ontology.py`는 네이티브 그래프 계약을 검증하고 Turtle 형식으로 내보냅니다. 계약 또는 RDF 왕복(Round-trip) 검증에 성공했다고 해서 건축적 정답이나 완전한 물리적 이해가 입증되는 것은 아닙니다.

### 선택적 VLM 실험

Qwen3.5-9B 및 Qwen3-VL-8B-Instruct는 연구 실험용 구성으로 유지되어 있습니다. **현재 웹 비교 워크플로우에서는 VLM 추론이 활성화되어 있지 않습니다.** 모델 가중치, 양자화 의존성 및 GPU 설정은 웹 시작과 별개입니다. VLM 출력은 적절한 기하학적 근거 또는 독립적인 검증을 통해 뒷받침되기 전까지는 '제안(Proposal)' 상태로 남습니다.

## 좌표계 및 저장 방식

- 기준(Ref)과 대상(Target)은 별도의 PDF/이미지 프레임 ID, 소스 해시, 페이지 번호, 추출 스케일 및 네이티브 픽셀 좌표를 유지합니다.
- 브라우저 확대/축소 및 이동은 화면 표시 좌표만 변경합니다.
- 대상 ROI 좌표는 기준 좌표로 직접 복사되지 않습니다. 매핑을 위해서는 지원되는 변환(Transform)과 비교 범위가 필요합니다.
- 업로드된 PDF는 서버에서 처리됩니다. 현재 익명 작업 공간과 비교 스냅샷은 메모리에 유지되며 일정 시간 활동이 없으면 만료됩니다. 서버가 재시작되면 이러한 임시 세션은 유실됩니다.
- CSV/PNG 다운로드는 사용자의 브라우저를 통해 저장됩니다. 런타임 로그 및 임시 처리 파일은 저장소 데이터나 공개 정적 에셋이 아닙니다.
- 이 워크플로우는 영구적인 다중 사용자 프로젝트 저장 기능을 제공하지 않습니다.

## 검증 (Validation)

```bash
npm --prefix frontend test
npm --prefix frontend run build

# 문 대응 관계, 주장 종속성 및 내보내기에 대한 36개의 합성 회귀 테스트
backend/.venv/bin/python -B -m unittest \
  backend.test_door_correspondence backend.test_comparison_claims
```

합성 테스트(Synthetic controls)는 절대 기하학 및 경합 요구사항, 호스트 보류(HOLD) 대 검증된 관계 충돌, 변경된 도면 기하학, 주장 상태 보존 및 CSV/PNG 출력을 다룹니다. 이는 임의의 평면도에 대한 정확도 벤치마크가 아닙니다.

일부 추가적인 과거 테스트는 의도적으로 공개되지 않은 원본 PDF, ROI 파일, 평가 자료 또는 정답 데이터(GT)를 참조합니다. 이러한 픽스처 없이 발견된 모든 테스트를 실행하는 것은 지원되는 공개 벤치마크가 아닙니다. 실제 도면 근거와 합성 테스트 결과는 별도로 보고되어야 합니다. 여기서는 정밀도(Precision), 재현율(Recall), F1 또는 GT 이미지 IoU 수치를 주장하지 않습니다.

## 외부 접속

이미 Cloudflare Quick Tunnel이 구성된 환경의 경우 다음 명령어를 사용합니다:

```bash
./fpue start
./fpue status
```

관리 스크립트는 `~/.local/bin/cloudflared` 또는 `FPUE_CLOUDFLARED_BIN` 환경 변수 경로에 `cloudflared`가 있을 것으로 예상합니다. 일치하는 서버/터널 프로세스를 재사용하고 활성 HTTPS 주소를 출력합니다. Quick Tunnel 주소는 임시적이며 재시작 후 변경될 수 있습니다. Ubuntu 호스트와 터널은 계속 실행 중이어야 합니다. 고정 호스트 이름을 사용하려면 별도로 구성된 도메인/계정 및 Named Tunnel 설정이 필요합니다.

## 공개 배포 범위

이 저장소에서는 `docs/`, 원본 PDF/PNG, GT, 모델 가중치, 가상 환경, 내부 실험 출력물, 비공개 데이터베이스 및 인증 정보가 제외되어 있습니다. 버전 관리된 스키마 생성기와 합성 테스트 소스는 포함되어 있으나, 생성된 프로젝트별 RDF 및 분석 결과는 포함되어 있지 않습니다.

현재 한계점으로는 부분적인 실(방) 재구성, 불완전한 설비/호스트 접지(Grounding), 일부 변환에 대한 제한적인 처리, OCR 실패 및 구조적 근거가 불충분한 경우의 대응 보류 등이 있습니다. 실행에 성공했다고 해서 전체 도면을 완전히 이해했거나 모든 실제 변경 사항을 감지했음을 선언하는 것은 아닙니다.