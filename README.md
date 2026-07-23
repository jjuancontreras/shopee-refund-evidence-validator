# Refund Evidence Validation System

**Top 5 in a Shopee hackathon. I was the main developer.**

When a buyer files a "return/refund" claim, an ops agent has to check the photo
or unboxing video against the stated reason: Is it the right item? Was the
parcel already opened? Has the image been edited? This tool does that first pass
automatically and hands a human reviewer an **explainable report**, with a
verdict, a confidence score and the reasoning from each inspector, instead of a
black-box yes/no.

## At a glance

```
Google Sheet row ──(Apps Script webhook)──▶ FastAPI ──▶ queue worker
                                                         │
  Stage 0  Intake: Sheet CSV + Drive proof media         │
  Stage 1  Preprocess: OpenCV / Pillow / EXIF, ffmpeg + PySceneDetect
  Stage 2  Evidence quality gate: blur, brightness, resolution, continuity
  Stage 3  4 inspectors in parallel:
             • Visual match (CLIP: proof vs. listing photo)
             • Parcel / unboxing check (vision-language model)
             • AI-editing forensics (open-source detector)
             • Return-reason validation (VLM, separate prompt)
  Stage 4  Aggregate evidence (Pydantic)
  Stage 5  Decision rules (an explicit, editable rules table, no hidden ML)
  Stage 6  Report ──▶ Streamlit reviewer UI (approve / reject / keep for review)
                  ──▶ Ops dashboard (queue, processing, history)
```

**Result on the demo case:** the pipeline classified the claim as **Invalid**
for a pre-opened parcel, matching the ground truth.

**Checking the AI's work** was a big part of the build:
- The AI-editing detector gave **85–99.8% "artificial" false positives** on
  frames from a known-genuine video, so it is now limited to native images.
- CLIP thresholds were recalibrated after a confirmed same-item match scored
  only 0.58.
- Flaky free-tier model calls retry with backoff and fall back to
  "inconclusive" instead of crashing the run.

**Stack:** Python 3.11 · FastAPI · SQLite/SQLAlchemy · OpenCV · ffmpeg ·
PySceneDetect · OpenCLIP · Hugging Face Transformers · OpenRouter (free VLM) ·
Google Sheets + Apps Script · Streamlit · Playwright

Built with AI-assisted development (Claude Code). Hackathon order data is not
included. Order IDs below are placeholders.

---

## Technical details

AI-assisted decision-support tool for Shopee Ops. Implements the 7-stage pipeline:

0. Intake (Google Sheets + Apps Script webhook, listing photos from a manually-
   curated Drive folder -- see "Known constraints" below for why this isn't
   live Shopee scraping)
1. Preprocessing (OpenCV/Pillow/exifread for images, ffmpeg + PySceneDetect for video)
2. Evidence Quality Inspector (rule-based: blur/brightness/resolution/continuity)
3. Inspection Router - 4 parallel inspectors: Visual Match (CLIP), Parcel/Unboxing
   (free-tier VLM via OpenRouter), AI-Editing Forensics (open-source HF classifier,
   native images only), Return Reason Validation (same VLM, different prompt)
4. Evidence Aggregator (plain Pydantic model, no ML)
5. Decision Engine (explicit, editable rules table in `app/stage5_decision/rules.py`)
6. Explainable Report + human reviewer (FastAPI JSON -> Streamlit UI)

**Status:** full pipeline verified end-to-end against the scoped demo order
(see below) via both `scripts/run_demo.py` and the live FastAPI + Streamlit
path. Final classification matches the ground truth (`Invalid`) with a
reasoning-equivalent explanation ("pre-opened packaging" / discontinuous
unboxing vs. the ground truth's "Parcel was already opened").

## Setup

```powershell
cd refund-evidence-validator
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

Credentials already in `.env` (git-ignored): OpenRouter API key, the Google
Sheet ID, the shared proof-media Drive folder ID, and the manually-collected
listing-images Drive folder ID. All Sheet/Drive resources only need to be
shared as "Anyone with the link can view" -- no OAuth/service account is
required, since Stage 0 reads them via the public CSV export endpoint
(Sheets) and `gdown` (Drive).

## Run the demo order end-to-end (no server needed)

```powershell
python scripts/run_demo.py
```

Runs the single scoped demo case: Order `DEMO-ORDER-001`, Return Reason
"Suspicious Parcel", proof `DEMO-ORDER-001.mp4`. Ground truth to check
against: **Invalid — Parcel was already opened**.

## Run the full live pipeline (FastAPI + Apps Script)

```powershell
uvicorn app.main:app --reload
```

Then, to let the Apps Script webhook reach your local server, tunnel it.
**ngrok now requires a free account + authtoken to start a tunnel**, so this
project uses cloudflared's account-free quick-tunnel mode instead:

```powershell
winget install --id Cloudflare.cloudflared -e
cloudflared tunnel --url http://localhost:8000
```

Look for a line like `https://<random-words>.trycloudflare.com` in the
output -- that's your public URL. It changes every time the tunnel process
restarts (quick tunnels aren't pinned to a fixed subdomain), so re-check it
and update the Script Property below whenever you restart the tunnel.

**Wiring up Apps Script** (one-time, inside the Google Sheet itself --
there's no pre-existing Apps Script project, you create it from the Sheet's UI):
1. Open the Sheet -> Extensions -> Apps Script.
2. Paste in `apps_script/Code.gs`.
3. Project Settings -> Script Properties -> add `WEBHOOK_URL` =
   `https://<your-cloudflared-subdomain>.trycloudflare.com/refund-requests`.
4. Triggers (clock icon) -> Add Trigger -> function `checkAndProcessRows`,
   event "On edit" (or time-driven, every few minutes, if you'd rather poll).
5. Make sure the sheet has a column containing "Processed" so rows aren't
   re-sent after their first successful POST -- **without one, `checkAndProcessRows`
   will re-POST every row whose Status column still reads "Ready" on
   *every* future edit anywhere in the sheet**, not just the row you meant
   to trigger. `Code.gs`'s `READY_VALUE` matches on the literal value
   `"Ready"` (case-insensitive) in whichever column header contains "status".

## Ops dashboard (queue / currently-processing / history)

Once `uvicorn app.main:app` is running, open **http://localhost:8000/** --
FastAPI serves a small Shopee-themed static dashboard (`frontend/`) at the
root path, polling `GET /api/queue-status` every ~1.5s. It shows:

- **Currently Processing**: the one order the single sequential worker is
  on right now, with a live stage stepper (Fetching evidence ->
  Preprocessing -> Quality check -> Running inspectors -> Aggregating &
  deciding -> Report ready).
- **Queue**: orders waiting their turn (both `POST /refund-requests` and
  `POST /refund-requests/{order_id}/run` now enqueue onto this same worker
  instead of firing independent background tasks -- exactly one order
  processes at a time).
- **History**: the last 20 completed/failed runs (a new `pipeline_runs`
  table, appended to on every run so reprocessing an order doesn't erase
  its prior entry), each with a "View full report ->" link that opens the
  Streamlit reviewer pre-loaded to that order.

## Review UI

```powershell
streamlit run streamlit_app/review_ui.py
```

Enter an order ID, click "Run pipeline for this order" (or just refresh if
it's already been processed via the webhook), and use the Approve/Reject/
Keep-as-review buttons to record a human decision alongside the automated one.
Supports `?order_id=...` in the URL to prefill the input -- this is what the
ops dashboard's "View full report" links use.

## Notes / known constraints

- **Shopee scraping is blocked, by design not by bug**: Playwright hits
  Shopee's anti-bot check, which redirects any headless/automated request to
  a `verify/traffic/error` login-required page before the real listing ever
  renders -- confirmed by inspecting the actual response (title "Login
  Required"), not just a generic timeout. There's no realistic fix short of
  deliberately evading Shopee's bot detection (stealth plugins, residential
  proxies), which this project intentionally does not do. `app/stage0_intake/scraper.py`
  is kept for reference but is **not called** by the pipeline. Per project
  decision, listing photos for the Visual Match inspector were instead
  manually collected into a second shared Drive folder
  (`GOOGLE_DRIVE_LISTING_IMAGES_FOLDER_ID` in `.env`) and are read locally by
  `app/stage0_intake/listing_images.py`. That folder currently isn't
  organized per item_id (every image in it is used for whichever item_id is
  requested) since it's scoped to the single demo listing -- if you add more
  orders, organize the folder per item_id and update `get_listing_images`.
- **AI-editing/forensics inspector is scoped to native image proofs only**:
  the classifier (`Organika/sdxl-detector`) was verified to produce a
  systematic false-positive bias (85-99.8% "artificial") on every
  ffmpeg-extracted frame of the known-genuine demo video -- it wasn't
  trained to tell video-compression artifacts apart from SDXL renders. The
  training pack's "Edited using AI" ground-truth rows are always `.jpg`/`.png`
  proofs, never videos, which matches this scoping. See
  `app/pipeline.py::_gather_proof_frames` (`native_image_frames`) and the
  module docstring in `app/stage3_inspectors/forensics.py`.
- **Parcel/Unboxing prompt uses the scene-cut count from Stage 1**: PySceneDetect's
  cut count (previously computed but never fed anywhere past the Stage 2
  quality report) is now passed into the VLM prompt so it can weigh whether
  a jump cut plausibly hides the moment the inner packaging was first
  opened, alongside exterior tamper signs. See `app/stage3_inspectors/parcel_unboxing.py`.
- **Visual Match thresholds recalibrated**: comparing a clean catalog photo
  against a handheld unboxing-video frame (hands, packaging, motion blur,
  different framing) scores meaningfully lower via CLIP than two clean
  same-item photos would -- a confirmed same-item comparison on the demo
  order landed at 0.58 cosine similarity. `PASS_THRESHOLD`/`FLAG_THRESHOLD`
  in `app/stage3_inspectors/visual_match.py` were lowered so that range reads
  as "uncertain, needs review" rather than a confident (and wrong) "item
  mismatch" call.
- **OpenRouter's free-tier VLM endpoint is flaky**: transient `502`s and
  "upstream idle timeout" responses are common. `app/stage3_inspectors/openrouter_client.py`
  retries with backoff and falls back to an `inconclusive` result rather than
  raising and killing the whole pipeline run.
- **Multi-listing orders**: some orders have multiple item IDs under one
  order (e.g. `DEMO-ORDER-002`, `DEMO-ORDER-003`). `RefundRequest.listing_item_ids`
  carries all of them and `get_all_listing_images` returns images keyed per
  listing, not just the first.
- **Multi-proof orders**: order `DEMO-ORDER-004` has both a `.jpg` and an
  `.mp4` as separate proof rows. `RefundRequest.proof_filenames` is a list and
  `pipeline._gather_proof_frames` concatenates frames from every proof file
  rather than assuming a 1:1 order-to-file mapping.
- **No ground truth in the test pack** -- the report is built to be
  eyeballed (confidence + reasoning per inspector, not just Valid/Invalid) so
  judges/reviewers can sanity-check it manually.
- **Queue**: uses FastAPI `BackgroundTasks` (an in-process asyncio queue) per
  the plan's "skip Celery/Redis for hackathon scope" decision. No Redis
  required. Swap for RQ if you outgrow single-process background handling.
- **AI-editing/forensics detector**: open-source HF image classifier
  (`Organika/sdxl-detector`), runs on CPU, no paid API keys -- keeps the whole
  pipeline free-tier except the demo's own OpenRouter usage.
- **VLM model**: the build plan's original pick (`qwen/qwen-2.5-vl-7b-instruct:free`)
  was retired from OpenRouter's free tier by the time this was built, so
  `OPENROUTER_MODEL` now defaults to `nvidia/nemotron-nano-12b-v2-vl:free`
  (verified working against a real demo frame). OpenRouter's free-tier lineup
  rotates -- if this one also disappears, check `GET /api/v1/models` and swap
  in another `:free` model whose `architecture.input_modalities` includes
  `image`, and update `OPENROUTER_MODEL` in `.env`.
