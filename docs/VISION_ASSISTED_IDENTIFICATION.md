# Vision-Assisted Identification (Phase 2A)

Photograph a medicine, let the server recognise it **locally** and confirm the
result by hand. The feature proposes; a person decides.

---

## 1. What it does

| Step | Behaviour |
|---|---|
| 1. Capture | The browser takes a frame with the rear camera (`getUserMedia`, `facingMode: environment`) or the user picks a file. |
| 2. Upload | The photo is sent to `POST /api/vision/captures` (multipart, limit 5 MiB). |
| 3. Validate | The content type is determined by magic bytes, the pixel count is limited, the EXIF metadata is removed and the picture is re-encoded. |
| 4. Recognise | The local tesseract process reads the text (`stdout` + TSV, so a confidence per word is available). |
| 5. Match | The text is turned into ranked candidates: exact barcode, partial barcode, name. |
| 6. Confirm | The user picks one candidate (or rejects the picture). Only then is the capture closed. |
| 7. Clean up | The photo is deleted, the OCR text and the candidates are kept in the database for the audit trail. |

## 2. What it deliberately does **not** do

* It never creates a medicine and never writes, adjusts or counts stock.
* It never decides on its own — every capture needs a human confirmation.
* It never sends the picture anywhere: the recognition runs in a local
  tesseract process, no external service is contacted.
* It never stores the original file: the picture is re-encoded (EXIF removed)
  and stored with mode `0600` in a directory with mode `0700`.

## 3. Matching rules

| Match | Score | Example |
|---|---|---|
| `barcode_exact` | 100 | the read text contains the barcode of the medicine |
| `barcode_partial` | 75 | the read text contains a prefix/suffix of the barcode (≥ 6 characters) |
| `name` | 90 | the normalised medicine name is contained in the read text |
| `name` (words) | 40 + 10 per matched word | "mifupen 500" matches "Mifupen 500mg" |

Only medicines with a score > 0 are proposed — a medicine that merely shares a
word with the text is not a candidate. The candidates are a proposal; the
confirmation is what makes the result usable.

## 4. API

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `GET` | `/api/vision/status` | `medicine:view` | is the feature enabled, is the OCR engine available |
| `POST` | `/api/vision/captures` | `medicine:view` | upload a picture, returns the capture with candidates |
| `GET` | `/api/vision/captures/{id}` | `medicine:view` | read one capture |
| `POST` | `/api/vision/captures/{id}/confirm` | `count:perform` | `{"decision":"confirm","medicine_id":…}` or `{"decision":"reject"}` |
| `POST` | `/api/vision/scan-text` | `medicine:view` | manual fallback, no camera and no OCR needed |

Error codes: `ocr_unavailable` (no engine), `ocr_failed` (unreadable picture),
`image_support_unavailable` (no Pillow), `capture_already_closed`,
`medicine_required`, `migration_pending` (migration not applied yet).

Audit actions: `vision.capture_created`, `vision.capture_confirmed`,
`vision.capture_rejected`.

## 5. Data model

`vision_captures` (revision `c8f2a4b91d7e`, additive table with a downgrade):

| Column | Meaning |
|---|---|
| `id` | capture id (uuid) |
| `user_id` | who uploaded the picture |
| `status` | `pending` / `confirmed` / `rejected` / `expired` |
| `image_path` | path of the photo, `NULL` as soon as it is deleted |
| `image_sha256`, `content_type`, `size_bytes` | integrity and type of the upload |
| `ocr_text`, `ocr_confidence`, `engine` | result of the recognition |
| `candidates` | JSON list of the proposed medicines |
| `matched_medicine_id`, `confirmed_at` | the human decision |
| `expires_at`, `created_at`, `updated_at` | retention and timestamps |

## 6. Operations

```bash
# is the engine available?
curl -s http://127.0.0.1:8000/api/vision/status

# delete the photos that are older than the retention window
cd backend && python -m app.cli prune-vision-captures --dry-run
cd backend && python -m app.cli prune-vision-captures
```

The deployment (migration, optional dependencies, OCR configuration, nightly
cleanup, rollback) is described in `docs/DEPLOYMENT_RUNBOOK.md` § 8.

## 7. Tests

| Suite | Content |
|---|---|
| `backend/tests/test_vision.py` | upload limits and types, EXIF removal, file permissions, no orphan file when the engine is missing, no medicine/stock writes, audit entries, confirm/reject/double-confirm, permissions, matching, pruning, the real tesseract binary |
| `frontend/tests/camera-capture.test.tsx` | camera start, upload, candidate list, confirmation, error handling |
| `frontend/tests/vision-scan-page.test.tsx` | status handling, the "engine missing" and "feature not deployed" cases, the API client error contract |

```bash
cd backend  && PHARMACY_TEST_DB_URL=postgresql+psycopg://…/pharmacy_test_pytest python -m pytest
cd frontend && npm test
```
