# RoadWatch AI

Intelligent Road Pothole Detection and Maintenance Management System.

This project preserves the existing FastAPI, MongoDB Atlas, YOLO11, OpenCV, Windows GPS, React, Vite, Leaflet, and React Leaflet workflow. The dashboard calculates metrics from actual database records and does not insert demo data into the real `road_pothole_db.potholes` collection.

For the verified role/page/API/data-flow map, see the beginner-friendly [RoadWatch AI functionality guide](./ROADWATCH_FUNCTIONALITY_GUIDE.md).

The backend dependency pins Pydantic to a release compatible with Python 3.10, which is used by the Windows virtual environment.

## Project Structure

```text
Road_pothole/
  backend/
    main.py                 FastAPI API, MongoDB access, analytics, health, exports
    create_admin.py         Optional controlled local bootstrap tool
    .env                    Local secrets, not used by frontend
  frontend/
    src/
      App.jsx               RoadWatch AI dashboard application
      App.css               Dashboard styling
      index.css             Global reset
    package.json
  camera_stream.py          Local Windows webcam/GPS/YOLO streaming agent
  live_pothole_detection.py Local standalone YOLO/GPS ingestion script
  detect_video.py           Offline video annotation; database ingestion is opt-in
  runs/pothole_yolo11n/weights/best.pt
```

## Run Commands

Backend:

```powershell
cd C:\Users\priya\Desktop\Road_pothole
backend\venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```powershell
cd C:\Users\priya\Desktop\Road_pothole\frontend
npm.cmd run dev
```

Open `http://localhost:5173`.

## Role setup and first Administrator

Public registration always creates a User account. Users may request Admin access; only an Administrator can approve or reject those requests. Admins manage pothole reports and analytics but cannot manage account roles.

To set up the first Administrator through the existing authentication page:

1. Configure `MONGODB_URL` (and, if needed, `MONGODB_DATABASE` / `USERS_COLLECTION`) in `backend\.env`.
2. Set `ROADWATCH_INITIAL_ADMIN_SETUP_SECRET` in `backend\.env` to a private, randomly generated secret with at least 32 bytes. Keep it on the backend; do not add it to frontend environment variables or source.
3. Start the backend and website, then choose **Set up the first Administrator** on the login screen. This option appears only while the configured database has no Administrator; existing Admin accounts are preserved.
4. Enter the setup secret along with the Administrator's name, email, and a password of at least 12 characters. The setup endpoint validates the secret, rechecks the database, and creates the account with a bcrypt password hash. After setup, sign in through the normal login page.

The setup secret is submitted only to the backend endpoint and is never embedded in frontend code. The backend permanently records completion in the first Administrator's user record. Existing roles are not rewritten during startup.

## Environment Variables

Backend only:

```text
MONGODB_URL=<MongoDB Atlas connection string>
MONGODB_DATABASE=road_pothole_db
POTHOLES_COLLECTION=potholes
DETECTIONS_COLLECTION=detection_events
EVENTS_COLLECTION=system_events
AUTH_TOKENS_COLLECTION=auth_tokens
AUTH_RATE_LIMITS_COLLECTION=auth_rate_limits
REPORT_SCHEDULES_COLLECTION=report_schedules
REPORT_RUNS_COLLECTION=report_runs
ROADWATCH_JWT_SECRET=<random server-side secret of at least 32 bytes>
ROADWATCH_INITIAL_ADMIN_SETUP_SECRET=<private random server-side secret of at least 32 bytes>
ROADWATCH_INGEST_API_KEY=<optional ingestion key for /api/detections>
ROADWATCH_PAGE_SIZE=25
ROADWATCH_MAX_PAGE_SIZE=100
ROADWATCH_GPS_ACCURACY_THRESHOLD_M=100
ROADWATCH_PUBLIC_BASE_URL=https://your-roadwatch-site.example
ROADWATCH_EMAIL_SMTP_HOST=<SMTP hostname>
ROADWATCH_EMAIL_SMTP_PORT=587
ROADWATCH_EMAIL_SMTP_USERNAME=<SMTP username, if required>
ROADWATCH_EMAIL_SMTP_PASSWORD=<SMTP password, if required>
ROADWATCH_EMAIL_FROM=<verified sender address>
ROADWATCH_EMAIL_STARTTLS=true
ROADWATCH_EMAIL_USE_SSL=false
ROADWATCH_EMAIL_TIMEOUT_SECONDS=10
ROADWATCH_EMAIL_VERIFICATION_TTL_MINUTES=1440
ROADWATCH_PASSWORD_RESET_TTL_MINUTES=30
ROADWATCH_REPORTS_DIRECTORY=<private backend-only report storage directory>
ROADWATCH_REPORT_RETENTION_DAYS=30
```

Frontend:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Do not put MongoDB credentials or private API keys in frontend environment variables.

SMTP credentials are optional for starting the API, but required to deliver verification and password-reset links. When SMTP is absent, signup still creates an unverified User, and the website explicitly explains that delivery is not configured; no email is claimed as sent. Configure an HTTPS `ROADWATCH_PUBLIC_BASE_URL` for production links and use `ROADWATCH_COOKIE_SECURE=true` when the site is served over HTTPS. The report directory must be writable and must not be exposed as a static web directory. The backend creates indexes for the additional token, rate-limit, schedule, and run collections; it does not delete existing user or pothole records.

## API Endpoints

- `GET /health` checks FastAPI and MongoDB connectivity.
- `GET /api/system/status` checks FastAPI and MongoDB and reports local YOLO, camera, and GPS state only when verified.
- `POST /api/auth/register` always creates a User account. Selecting Admin creates only a pending request; an existing administrator must approve it in the Admin Requests section.
- `POST /api/auth/login` issues an HttpOnly session cookie, and `GET /api/auth/me` returns the database-backed role and any pending decision notification.
- `POST /api/auth/email-verification/confirm` consumes a hashed, expiring, single-use verification token; `/api/auth/email-verification/resend` requests a new link.
- `POST /api/auth/password-reset/request` returns a generic response that does not reveal whether the address exists; `/api/auth/password-reset/confirm` consumes a single-use token, changes the bcrypt password, and invalidates prior sessions.
- Login, registration, reset, verification, resend, and initial setup requests are rate limited using MongoDB-backed counters.
- `POST /api/auth/admin-request/acknowledge` dismisses the signed-in user's approval or rejection notification.
- `GET /api/notifications`, `POST /api/notifications/{notification_id}/read`, and `POST /api/notifications/read-all` provide persistent, account-scoped notifications and unread counts.
- `GET /api/admin/dashboard` and `/api/admin/analytics` require an Admin or Administrator session. User and Admin account management through `/api/admin/users` requires an Administrator session.
- `GET /api/user/dashboard` returns only the authenticated account's pothole total, Low/Medium/High counts, and detection counts by image, video, live-camera, and other source.
- `POST /api/admin/users/{user_id}/approve-admin` or `/reject-admin` decides a pending request; both require an administrator session.
- The administrator dashboard includes global pothole, user, detection, and pending-request counts; its Detection Monitoring view paginates saved image, video, and live-camera events.
- `GET /api/potholes` preserves the existing `{ count, potholes }` shape and adds pagination metadata.
- User pothole and detection queries are scoped to the signed-in user. Administrators see all submitted records, including historical records without an owner.
- `GET /api/potholes/map` returns role-scoped records with valid, verified GPS locations, independent of registry pagination.
- `POST /api/potholes` preserves the legacy request shape and links nearby repeat observations instead of creating another record.
- `GET /api/potholes/{pothole_id}` returns one record plus linked observations.
- `PATCH /api/potholes/{pothole_id}` updates managed fields; requires an administrator session.
- `POST /api/potholes/{pothole_id}/verify` marks a report Verified or Rejected (with a reason); it stores the Admin, timestamp, and audit history and requires an administrator session.
- `POST /api/potholes/{pothole_id}/repair` sets Not Started, Under Maintenance, or Repaired and records the Admin and timestamp; requires an administrator session.
- `GET /api/detections` lists raw detection events.
- `POST /api/detections/image` runs the existing trained YOLO model on a JPEG, PNG, WebP, or BMP upload (maximum 10 MB), checks EXIF GPS/capture time, retains the original and YOLO overlay under `/uploads`, and saves every detection even if GPS is missing or invalid.
- `POST /api/detections/{event_id}/verify-location` records an administrator-selected map location, requires an administrator session, stores method `MANUAL`, and appends a verification audit entry.
- `POST /api/detections` ingests local-agent detection events with idempotent `event_id`; GPS coordinates are optional but must be supplied as a pair.
- `GET /api/detections` returns paginated saved detection events separately from the pothole registry.
- `GET /api/analytics/summary` and `/api/analytics/trends` are administrator-only and return database-backed record and detection metrics. The severity counts group critical records into High; `/api/user/dashboard` returns only the signed-in user's counts.
- `GET /api/reports/export.csv`, `/api/reports/export.pdf`, and `/api/reports/export.xlsx` export only records available to the signed-in account. Date, location, severity, search, verification, and repair filters are applied by the backend to MongoDB records.
- `GET/POST /api/reports/schedules` list or create a daily, weekly, or monthly PDF/Excel schedule with the active report filters. `PATCH /api/reports/schedules/{id}` pauses or resumes a schedule; `DELETE` cancels it. The backend scheduler stores run metadata and generates files from current records. Recent files are available through the authorized `/api/reports/schedules/{schedule_id}/runs/{run_id}/download` endpoint until retention cleanup expires them. Scheduled reports use private filesystem storage and require no email service.
- Existing camera endpoints from `camera_stream.py` are still mounted when local Windows dependencies are available.

The Admin and Administrator GIS pages cluster nearby verified pothole records in the browser, color markers by severity, and provide marker details plus a Locate Me control. Browser location is centered only after permission is granted and a sufficiently accurate reading is returned. The User navigation remains unchanged and does not include Map, GPS Details, or Notifications.

The User Dashboard navigation contains Overview, Report Pothole, My Potholes, My Pothole Tracking, My Detection History, My Profile, and Reports & Exports. Dedicated GPS Details, Map, and Notifications pages are intentionally absent from User navigation; report/detection forms may still use optional location capture. My Pothole Tracking displays verification and repair changes, the Admin actor, timestamps, remarks, GPS status, evidence availability, and the saved audit timeline. The Admin Dashboard separates the global pothole registry from the individual detection-event monitor; Admin rows identify their submitter by name or account ID. Legacy records without ownership remain labeled as unassigned rather than being attributed to a user.

## MongoDB Compatibility

The primary database remains `road_pothole_db`. The primary collection remains `potholes`.

Legacy records containing only `pothole_id`, `latitude`, `longitude`, and `confidence` are normalized at the API boundary. Coordinates can also be read from GeoJSON Point locations. The existing startup `migrate_pothole_ids()` does not delete or merge records, but it can backfill, uppercase, or reassign legacy pothole IDs and update linked detection-event IDs. This migration behavior predates the visual refresh; no backend startup or live MongoDB writes were run for that work. New detection events are stored in `detection_events` and linked to nearby pothole records when GPS accuracy is usable. Events with GPS accuracy worse than 250 metres are retained as events but do not create a new mapped pothole record.

Offline video analysis does not write to MongoDB by default. To explicitly enable event ingestion for video with known GPS metadata, set `ROADWATCH_VIDEO_INGEST_ENABLED=true`, `ROADWATCH_VIDEO_LATITUDE`, `ROADWATCH_VIDEO_LONGITUDE`, and `ROADWATCH_VIDEO_GPS_ACCURACY_M`. The script rate-limits uploads and sends them through `/api/detections`; it never uses embedded sample coordinates.

The legacy `POST /api/potholes` route accepts the previous payload shape, but treats it as a pothole-record upsert by nearby coordinates. Use `POST /api/detections` when the caller has a stable event ID and needs the individual observation tracked.

Records without `source` or `source_id` metadata are returned as `source: "historical/unknown"` and `record_origin: "historical_unknown"`; the backend does not alter or delete those MongoDB documents. Missing verification and repair states are reported as `unknown`, not inferred as awaiting verification or reported. For those records, `historical_unknown_average_confidence_percent` is the mean of their stored confidence fields, not a newly run model score and not model accuracy. The dashboard excludes historical records from detection-event trends and reports the last detection from the actual event collection.

Image upload checks EXIF GPS and capture timestamps. EXIF GPS without reported accuracy remains unverified. If EXIF GPS is absent, a user may explicitly request browser geolocation; that is labeled as upload-time device location and never silently substituted for capture location. Original and annotated images persist in the backend `uploads` directory. Pothole detections remain saved as events when coordinates are missing or invalid. Map markers require valid coordinates and a verified GPS/manual location state; unverified locations stay visible in the appropriate records and GPS details but are not rendered as verified markers. Browser geolocation requires localhost or HTTPS.

The dashboard's Live Detection section starts and stops a single local camera worker, displays its annotated MJPEG stream from `/api/camera/stream`, and polls `/api/camera/status` for the current frame's detection count, confidence, GPS, and backend-confirmed save state. The worker submits one event per YOLO bounding box on the first frame of a continuous detection episode, then suppresses repeat frames until detection has cleared for three seconds. Valid coordinates and reported accuracy are included when available; missing GPS or accuracy no longer blocks event persistence. GPS accuracy over the configured threshold is shown as unverified and excluded from verified map markers until an administrator manually verifies it. Events without valid coordinates remain in the event registry and GPS details. Failed submissions remain explicitly unconfirmed and are not retried until detection clears and a new episode begins. A confirmed camera save triggers a dashboard data refresh without creating a record merely to refresh.

Windows GPS is acquired by the local FastAPI camera agent using WinRT while the camera agent is active; a browser cannot access Windows location services directly. The local agent requests high sensor accuracy but always preserves the actual reported accuracy. `live_gps_test.py` is a standalone diagnostic script and does not feed the dashboard. The backend does not call WinRT `RequestAccessAsync`: Windows requires that permission prompt to run on a foreground UI thread, which the camera worker is not. Enable Windows Location Services and desktop-app location access in system privacy settings; acquisition failures are surfaced as permission/unavailable status. The camera status endpoint returns fresh coordinates, reported accuracy, and update time without substituting a previous reading after an acquisition failure.

New pothole records begin as Pending Verification and Not Started for repair. Admin verification and repair updates append an audit entry containing the administrator and timestamp; verified records also store `verified_by` and `verified_at`, while rejected records store their reason. The submitter sees the latest status in My Potholes on the normal dashboard refresh and receives a persistent notification. Admin location corrections retain the previous submitted coordinates and corrected coordinates in the audit history; only explicitly confirmed locations are marked verified.

The GIS map uses public OpenStreetMap tiles and displays only potholes whose Admin verification status is Verified and whose GPS location is independently verified. Users see only their own such locations; Admins see the global set. Selecting a registry row's Locate action opens the map on that verified record; its marker popup shows ID, severity, detection date, confidence, and the saved latitude/longitude. Admins can review unverified GPS locations separately; this is distinct from pothole-report verification. Manual GPS verification requires a map selection and an explicit confirmation; repeat verification does not create another pothole record. The GPS accuracy threshold is configurable with `ROADWATCH_GPS_ACCURACY_THRESHOLD_M`.

## Historical Record Provenance

The figures in this section are a historical snapshot from earlier read-only checks, not a live database query performed during the visual refresh. Do not present them as current totals without rechecking the configured database.

The initial read-only audit found 1,299 documents in `potholes` and no documents in `detection_events`. A later integration check confirmed four real YOLO events from a recorded test-image upload; the latest read-only query found 16 image-upload events, zero live-camera events, and the same 1,299 pothole documents. The other 12 upload-source events cannot be tied to a particular filename because upload filenames are not stored in event documents. All 1,299 historical pothole documents lack verifiable source metadata and remain labeled historical/unknown. The displayed 47.35% is the average of stored confidence fields, not model accuracy. The approximate GPS groups are calculated from stored coordinates and cannot identify the original images or prove the origin of individual records.

The strongest matching historical writer was an earlier version of `detect_video.py` that posted every detected video box to the legacy pothole endpoint using a temporary sample coordinate. The current `detect_video.py` has offline ingestion disabled by default, and the current legacy endpoint deduplicates nearby observations. This is a likely explanation for the clustered records, not a verifiable attribution. The project also contains local webcam agents (`camera_stream.py` and `live_pothole_detection.py`) and an offline video input (`videos/road.mp4` by default); dataset files under `Pothole_Clean` are training/evaluation inputs. Because historical documents have no image reference, the dashboard cannot recover which images, if any, produced those records. New image uploads are explicitly selected by the user.

Indexes are created for common dashboard queries, detection idempotency, timestamps, and optional geospatial lookup.

## Local YOLO and GPS Agent

The hosted dashboard cannot directly access a Windows laptop webcam or GPS. Use the local Python agent:

1. Keep the trained model at `runs\pothole_yolo11n\weights\best.pt`.
2. Start the backend locally on `http://127.0.0.1:8000`.
3. Run the frontend on `http://localhost:5173`.
4. Start live detection from the dashboard or run `camera_stream.py`/`live_pothole_detection.py`.
5. The agent runs YOLO11 through OpenCV, reads Windows GPS through `winrt`, and sends validated events to `POST /api/detections`.

GPS uncertainty is stored as `gps_accuracy_m`. Previous readings around 212 metres should be treated as approximate area evidence, not exact pothole coordinates.

## Deduplication and Prioritization

Detection events are separate from persistent pothole records. The dashboard shows their counts separately from the MongoDB record count. Its approximate unique GPS-location statistic groups valid coordinates to four decimal places; this is an approximate coordinate grouping, not a confirmed count of physical potholes. Poor GPS readings are retained as events but do not create new location records.

Severity metadata is retained on records and displayed in the current Admin and Administrator analytics, including severity trends and distributions. Critical records are grouped with High in the summary counts.

## Known Limitations

- Public registration creates User accounts. Admin requests remain pending until an Administrator approves them. Admins and Administrators use separate dashboards; only Administrators can manage account roles.
- Image and video upload inference are available in the detection workspace. Both require the configured YOLO model to exist on the backend host; the local live-camera workflow also requires camera-host dependencies and a camera.
- Map marker clustering and heatmap rendering are represented by filtered GIS views; a dedicated clustering plugin can be added for very large map views.
- PDF generation is currently browser-print based; server-side PDF generation is not implemented.
- The frontend does not create or display demo records; registry and analytics data comes from the API.
- Offline-video GPS must be supplied by the operator and should only be enabled when it accurately describes the analyzed footage.
