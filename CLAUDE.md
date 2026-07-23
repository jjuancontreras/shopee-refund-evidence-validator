# CLAUDE.md

Guidance for Claude Code when working in this repo. See `README.md` for
setup/run instructions and the full list of known constraints -- this file
is about *why* the code looks the way it does, not how to run it.

## What this is

A 7-stage AI-assisted decision-support pipeline for Shopee Ops to triage
refund-evidence proof (photos/videos) against the customer's stated return
reason. Full spec/rationale for the stack choices lives in the original
build-plan doc the user provided (Python 3.11, FastAPI, SQLite, Streamlit
reviewer UI, per-stage tool choices) -- ask the user if you need that
context again, it isn't duplicated here.

Demo scope is intentionally one order: `DEMO-ORDER-001` (Shop `DEMO-SHOP`,
Item `DEMO-ITEM`, Return Reason "Suspicious Parcel", proof
`DEMO-ORDER-001.mp4`, ground truth **Invalid — Parcel was already
opened**). `scripts/run_demo.py` runs exactly this case with no server
needed.

## Current status

Full pipeline verified end-to-end (both via `scripts/run_demo.py` and via
FastAPI + Streamlit) as of 2026-07-23. Final classification matches ground
truth (`Invalid`) with a reasoning-equivalent explanation. See "Session
findings" below for everything that had to change to get there -- if you're
touching Stage 0 or Stage 3, read that section first, it documents real
verified behavior, not speculation.

## Architecture map

- `app/pipeline.py` — orchestrates all 7 stages for one order.
  `_gather_proof_frames` returns both `all_frames` (video + native images,
  used by most inspectors) and `native_image_frames` (used only by the
  forensics inspector — see below for why). `run_pipeline_for_order` takes
  an optional `on_stage(stage_name)` callback (default no-op) called at 6
  checkpoints — this is what the queue worker uses to track live stage.
- `app/queue_worker.py` — single sequential FIFO worker (`worker` singleton)
  that both `POST /refund-requests` and `POST /refund-requests/{id}/run`
  enqueue onto, replacing the old per-request `BackgroundTasks` pattern.
  Processes exactly one order at a time; persists every run (success or
  failure) to `PipelineRunModel` (`pipeline_runs` table — append-only,
  unlike `FinalReportModel` which is overwritten per order_id). Queue/
  current-job state is in-memory only (lost on restart, matching this
  project's "skip Redis/Celery" posture); history survives restarts.
- `frontend/` — plain HTML/CSS/JS (no build step) ops dashboard, served by
  FastAPI's `StaticFiles` mount at `/`, polling `GET /api/queue-status`
  every ~1.5s. Shopee-orange themed. Shows queue / currently-processing
  (with live stage stepper) / history, with link-outs to the Streamlit
  reviewer (`?order_id=...` query param) for the full report + override.
- `app/stage0_intake/` — `sheets.py` (reads the Sheet via public CSV
  export), `drive.py` (proof-media folder), `listing_images.py` (**the one
  actually used** for listing photos — see below), `scraper.py` (Shopee
  Playwright scraper, **not called by the pipeline**, kept for reference).
- `app/stage1_preprocessing/` — `video_prep.py` (ffmpeg frame extraction +
  PySceneDetect scene cuts), `image_prep.py` (normalize/EXIF, used less
  since most demo proof is video).
- `app/stage2_quality/quality_inspector.py` — pure rule-based blur/
  brightness/resolution/scene-cut thresholds, no model calls.
- `app/stage3_inspectors/` — the four parallel inspectors (`visual_match.py`
  = CLIP, `parcel_unboxing.py` + `return_reason.py` = same OpenRouter VLM
  client with different prompts, `forensics.py` = HF image classifier).
  `openrouter_client.py` is the shared VLM call wrapper (retry/backoff +
  JSON-reply parsing).
- `app/stage4_aggregator/`, `app/stage5_decision/rules.py`,
  `app/stage6_report/` — thin, no surprises; `rules.py` is a first-match-wins
  rule table, read it top to bottom if you need to change decision logic.
- `app/main.py` — FastAPI webhook (`POST /refund-requests`, `POST
  /refund-requests/{order_id}/run`, `GET /reports/{order_id}`), runs the
  pipeline via `BackgroundTasks`.
- `streamlit_app/review_ui.py` — reviewer UI, reads/writes
  `FinalReportModel` directly via SQLAlchemy.
- `apps_script/Code.gs` — pasted into the Sheet's Apps Script editor; POSTs
  to the FastAPI webhook on row edit.

## Live-webhook demo setup (2026-07-23)

The user added a **Status** column (header literally "Status") to the
Training Data sheet, with the trigger value `READY` (not the original
`READY_VALUE = "ready for processing"` — `apps_script/Code.gs` was updated
to match, `READY_VALUE = "ready"`). There is **no "Processed" column** on
that sheet — without one, `checkAndProcessRows` re-POSTs every row whose
Status still reads "Ready" on *every future edit anywhere in the sheet*,
since there's no column to write "Yes" into after a successful send. At
least two rows (the original demo row plus what looks like a duplicate) had
Status=READY simultaneously as of this session — flag this to the user if
duplicate/unexpected pipeline runs show up.

ngrok v3 requires a free account + authtoken to open a tunnel (confirmed by
trying — `ERR_NGROK_4018`); this project uses **cloudflared's account-free
quick-tunnel mode** instead (`cloudflared tunnel --url http://localhost:8000`,
installed via `winget install --id Cloudflare.cloudflared`). The public
`https://*.trycloudflare.com` URL changes every time the tunnel process
restarts — whoever runs the demo needs to re-paste it into the Apps Script
project's `WEBHOOK_URL` script property after any tunnel restart.

Verified end-to-end through an actual cloudflared tunnel (not just
localhost): a POST to `https://<tunnel>/refund-requests` with a full
`RefundRequest` JSON body (mimicking exactly what `Code.gs` sends) was
correctly enqueued, processed by `queue_worker.worker`, and produced
`Invalid` at 95% confidence in the dashboard's history — same result as
every other run of this demo order. The only part of the live-Sheet flow
NOT independently verified this session is the actual Apps Script
trigger firing on a real edit (no Google account access) — that part is on
whoever runs the demo to confirm once the Script Properties/trigger are set.

## Session findings (2026-07-23) — read before touching Stage 0/3

These were all discovered by actually running the pipeline against the demo
order and inspecting real output, not by reading the code in isolation. Each
one changed a specific file; grep the filename below if you need the exact
diff context (git history isn't available -- this isn't a git repo).

1. **OpenRouter free-tier endpoint is flaky.** Unhandled `502`s crashed the
   whole pipeline (a single inspector's exception propagated through
   `asyncio.gather`, killing the run before Stage 4 even started).
   `openrouter_client.call_vlm` now retries 3x with backoff and falls back
   to `inconclusive` on persistent failure. If you add a new VLM-based
   inspector, route it through `call_vlm` rather than calling OpenRouter
   directly, or you'll reintroduce this crash.

2. **The AI-editing/forensics classifier (`Organika/sdxl-detector`) is
   miscalibrated for video frames.** Verified directly: every single
   ffmpeg-extracted frame of the known-genuine demo video scored 85-99.8%
   "artificial" -- not selectively on tampered content, uniformly on all 8
   frames. This is a real domain-shift bug in using a
   SDXL-render-vs-native-photo classifier on video-compression artifacts,
   not a threshold-tuning issue (no threshold separates real video frames
   from real AI edits if the model scores both ~99%). Fix: the inspector is
   now scoped to `native_image_frames` only (see `pipeline.py`), matching
   the fact that every "Edited using AI" ground-truth row in the training
   pack is a `.jpg`/`.png`, never a video. **Do not re-enable this
   inspector on video frames** without new calibration evidence.

3. **Shopee's anti-bot check blocks Playwright entirely**, redirecting to
   `/verify/traffic/error` (a "Login Required" page) before the real listing
   renders -- confirmed via screenshot, not inferred from a timeout. This
   is not fixable without deliberately evading Shopee's bot detection
   (stealth plugins, residential proxies, etc.), which is out of scope for
   this project on principle, not just effort. Per the user's explicit
   instruction, listing images are instead manually collected into a Drive
   folder (`GOOGLE_DRIVE_LISTING_IMAGES_FOLDER_ID`) and read by
   `stage0_intake/listing_images.py`, which `pipeline.py` actually calls.
   `scraper.py` is retained only for reference. **If asked to "fix the
   scraper," clarify with the user whether they mean restoring live
   scraping (likely infeasible per above) or something else** -- don't
   silently attempt bot-detection evasion.

4. **`parcel_unboxing`'s prompt didn't use the scene-cut count Stage 1
   already computes.** The video has 3 detected scene cuts; the inner
   product box appears already-open the first time it's shown, with no
   frame depicting the actual opening -- classic sign of a jump cut hiding
   the real moment of first opening. The original prompt only asked about
   exterior tamper signs (tape/seals) and got this wrong (`pass`,
   confidence 0.95, when ground truth is "already opened"). Fixed by (a)
   passing `scene_cut_count` into the prompt as context, (b) explicitly
   asking the VLM to check whether the inner packaging is already open on
   first appearance. This is what actually flips the final decision to
   match ground truth (`Invalid`, triggered by
   `high_confidence_fail:parcel_unboxing`).

5. **Visual Match thresholds were calibrated for clean-vs-clean photo
   comparisons, not clean-catalog-photo-vs-video-frame.** A *confirmed*
   same-item comparison (verified by eye: the video shows the same UGREEN
   magnetic power bank as the listing photos) scored only 0.58 cosine
   similarity via CLIP ViT-B-32 -- below the original `FLAG_THRESHOLD`
   (0.60), which produced a false "item mismatch" `fail`. Thresholds
   lowered (`PASS_THRESHOLD` 0.85→0.75, `FLAG_THRESHOLD` 0.60→0.45) so this
   range now reads as "uncertain, needs review" instead of a confident wrong
   answer. This is a one-data-point calibration -- revisit if/when more
   labeled examples become available.

## Working in this repo

- Not a git repo (per environment info) -- there's no `git log`/`git blame`
  to check; this file and the README are the persistent record of past
  decisions.
- `data/` holds cached scrape/drive downloads and extracted frames per
  order ID. Safe to delete and let it re-populate, except `data/app.db`
  (SQLite — has any previously run reports/human overrides) and anything
  you're mid-session using as calibration evidence.
- When changing an inspector's prompt or thresholds, actually re-run
  `scripts/run_demo.py` against the demo order and read the JSON output --
  every fix in this file was found that way, not by code review alone. Free
  API keys are in `.env` and don't need to be re-requested from the user.
