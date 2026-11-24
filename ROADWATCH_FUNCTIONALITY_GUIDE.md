# RoadWatch AI: Functionality and Demonstration Guide

This guide describes behavior found in the current frontend and FastAPI source. It is intended as a beginner-friendly reference for explaining the project and its data flow.

> **Scope note:** MongoDB collection names and access patterns were verified in source. A live database query, real account login, and camera/video hardware test were not performed during this documentation and visual-update work. No MongoDB records were changed by this work. The existing backend startup migration `migrate_pothole_ids()` can normalize legacy pothole IDs and associated event links when the backend starts; that behavior was not changed.

## 1. What the application does

RoadWatch AI combines role-based road reporting and pothole management with image, video, and local-camera detection. React renders the website, FastAPI validates requests and enforces permissions, the road-scene classifier and YOLO model analyze supported media, and MongoDB stores account, report, event, and notification data.

The detection path is:

```text
USER / ADMIN / ADMINISTRATOR
        |
        v
React website (role-specific pages and controls)
        |
        v
FastAPI (session validation, input checks, authorization)
        |
        +----> Places365 road-scene check
        |              |
        |              v
        +----------> YOLO11 pothole inference
                           |
                           v
                 detections and report processing
                           |
                           v
        MongoDB events/reports and protected media files
                           |
                           v
        role-scoped dashboards, tracking, analytics, exports
```

The website's **Report Pothole** navigation opens the detection workspace, where users can run image/video inference or use the local live-camera workflow. The backend also retains the legacy `POST /api/potholes` endpoint for compatible clients; the current React report screen does not call that endpoint directly.

## 2. Roles and access

| Role | Main pages | Capabilities |
|---|---|---|
| **USER** | Overview, Report Pothole, My Potholes, My Pothole Tracking, My Detection History, My Profile, Reports & Exports | Submit reports, run available detection workflows, review only their own report/event data, track status, and export their permitted records. |
| **ADMIN** | Global Overview, Pothole Management, My Notifications, Detection Monitoring, Global Analytics, Global Map, Reports & Exports, My Profile | Review and manage pothole reports, verify report/location data, update repair progress, and inspect system-level operational data. Cannot approve Admin access or manage account roles. |
| **ADMINISTRATOR** | Administrator Overview, Users & Admins, Admin Approval Requests, plus operational pages | Has Admin operational capabilities and can approve/reject Admin requests, promote Users, revoke Admin access, and manage account activation/roles. |

The backend derives the effective role from the stored account, not a role value supplied by the browser. Normal registration creates a `user` account. Selecting an Admin access request records a pending request; it does not grant Admin permissions. Backend dependencies enforce Admin-only and Administrator-only operations.

### Authentication flow

| Function | User interaction | API | Data flow and result |
|---|---|---|---|
| Initial Administrator setup | The setup link is shown only when the configured backend secret is valid and there is no Administrator. Submit name, email, password, confirmation, and the secret. | `GET /api/auth/admin-setup/status`, then `POST /api/auth/admin-setup` | FastAPI validates inputs and the secret, rechecks MongoDB, hashes the password with bcrypt, and creates the first `administrator` account. The website then returns to normal login. |
| Registration | Use the shared registration form and optionally request Admin access. | `POST /api/auth/register` | A bcrypt password hash and a `user` role are stored. An Admin request is marked pending for Administrator review. |
| Login/session | Enter email and password on the common login form. | `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` | FastAPI verifies the password and account status, sets the `roadwatch_access_token` HttpOnly cookie, and returns the database-backed role. The frontend routes to the matching dashboard. |
| Role refresh | Keep the website open after an account is approved, revoked, or deactivated. | Repeated `GET /api/auth/me` | The frontend refreshes the session and role, then redirects when the account's role changes. |

The setup secret, MongoDB credentials, and JWT signing secret belong in backend configuration only. They are not frontend settings. The initial setup endpoint rejects invalid secrets and duplicate setup attempts.

## 3. User pages and workflows

| Page/function | What the user does | API and data flow | Result |
|---|---|---|---|
| Overview | Opens the User dashboard. | `GET /api/user/dashboard` and role-scoped report/event reads | Shows the signed-in user's available totals and activity, not global account data. |
| Report Pothole | Opens the detection workspace. The user can look up a saved record, upload images or video, start the local camera, or provide optional image/video coordinates. | `POST /api/detections/image`, `POST /api/detections/video`, and authenticated `/api/camera/*` controls. The legacy `POST /api/potholes` endpoint remains available to compatible clients but is not called by this React page. | FastAPI returns inference results and saves detection events; detection processing associates or creates pothole records where applicable. Location may use image metadata, browser geolocation when requested, or manual map selection. |
| My Potholes | Searches and pages through submitted potholes. | `GET /api/potholes` | Shows only records owned by the signed-in User. |
| My Pothole Tracking | Reviews verification and repair progress. | `GET /api/potholes` and linked report/audit data | Shows current statuses, remarks, timestamps, evidence/location state, and recorded status history when present. |
| My Detection History | Reviews personal image, video, and live-camera observations. | `GET /api/detections` | Lists only the signed-in user's saved detection events. |
| Reports & Exports | Chooses a start/end date and uses CSV or Printable Report controls. | Date-filtered `GET /api/potholes`; `GET /api/reports/export.csv` for CSV; browser print dialog for printable output | The report table and statistics reflect MongoDB `created_at` values in the selected range. Printing uses the browser's print/save-as-PDF dialog; the website does not generate a server-side PDF. |
| My Profile | Reviews account details and signs out through the profile control. | `GET /api/auth/me`, `POST /api/auth/logout` | Shows the authenticated account information and ends the session. |

The User navigation intentionally has no separate GPS Details, Map, or Notifications page. This is a UI restriction; it does not remove existing backend endpoints or location handling used by report/detection workflows.

## 4. Detection and GPS data flow

### Image inference

1. The user selects an image in the detection workspace.
2. The frontend sends the file and any supported location metadata to `POST /api/detections/image`.
3. FastAPI validates the upload and checks the scene with the Places365 road-scene classifier.
4. The configured YOLO11 model runs pothole inference on an accepted road image.
5. The backend returns detection metadata and an annotated image preview, and records the observation data in MongoDB. Original/annotated media is stored under the backend uploads directory and served through an authenticated route.

The model's default path is `runs/pothole_yolo11n/weights/best.pt`. The backend host must have the configured model and road-scene resources for inference to work.

### Video inference

1. The user selects a video in the detection workspace.
2. The frontend uploads it to `POST /api/detections/video`.
3. FastAPI processes video frames, applies road-scene validation and YOLO inference, and returns a result summary and annotated-video reference.
4. The annotated result is available through `GET /api/detections/video/{video_id}/annotated`; detected observations are processed through the detection/event flow.

Video GPS is not inferred from arbitrary video pixels. Any coordinates supplied by the operator are metadata and should only be used when they describe the footage.

### Local live camera

The live-camera workflow uses the camera attached to the backend host, not a remote browser webcam. The dashboard controls the authenticated local camera agent using:

- `POST /api/camera/start`
- `POST /api/camera/stop`
- `GET /api/camera/status`
- `GET /api/camera/stream`

`camera_stream.py` captures frames, runs the configured model, and sends saved detections to `POST /api/detections`. When available, Windows GPS is acquired by the local agent. Camera, Windows location permissions, Python dependencies, and model resources are required. The stream and status APIs require an authenticated session.

### Reports, detections, and locations in MongoDB

The configured MongoDB database defaults to `road_pothole_db`. Source configuration uses the `potholes`, `detection_events`, `system_events`, `users`, and `notifications` collections; collection names may be configured with backend environment variables. `id_sequences` is also used for generated identifiers.

Pothole records represent the maintained report/repair state. Detection events represent individual observations and are stored separately. Backend processing can associate observations with report records using the existing location/deduplication logic. Poor or missing coordinates affect map eligibility and association; they do not make a detection event a verified map location.

GPS can come from supported image metadata, explicit browser location permission, a manual map selection, or the local Windows agent. Coordinates are not proof of verification: map markers are restricted to records whose report and location status meet the backend/frontend display rules. Administrators can review and verify locations through the protected workflow.

## 5. Admin and Administrator pages

| Page/function | What it does | API and protection |
|---|---|---|
| Global Overview | Displays role-appropriate global operational totals and activity. | `GET /api/admin/dashboard`; requires Admin or Administrator access. |
| Pothole Management | Lists global pothole records and supports report review, verification, repair status, and permitted updates. | `GET /api/potholes`; `GET /api/potholes/{pothole_id}`; `PATCH /api/potholes/{pothole_id}`; `POST /api/potholes/{pothole_id}/verify`; `POST /api/potholes/{pothole_id}/repair`. Management endpoints require Admin or Administrator access. |
| Detection Monitoring | Reviews saved image, video, and live-camera events across accounts. | `GET /api/detections`; backend returns global data only to Admin/Administrator sessions and user-scoped data to Users. |
| Global Map and location review | Displays eligible global records and supports the permitted manual location review flow. | `GET /api/potholes/map`; location verification uses `/api/potholes/{pothole_id}/verify-location` or `/api/detections/{detection_id}/verify-location`. Write actions require Admin or Administrator access. |
| Notifications | Lists account-scoped notifications and marks them read. | `GET /api/notifications`, `POST /api/notifications/{notification_id}/read`, and `POST /api/notifications/read-all`; authenticated and scoped to the current account. |
| Global Analytics | Displays stored-record counts, severity information, and detection trends. | `GET /api/analytics/summary`, `GET /api/analytics/trends`, and `GET /api/admin/analytics`; Admin/Administrator access. |
| Reports & Exports | Filters visible report data and exports CSV or uses browser printing. | `GET /api/potholes` with date filters and `GET /api/reports/export.csv`; results are scoped by the current role. |
| Admin Approval Requests | Lists pending User requests and records approve/reject decisions. | `GET /api/admin/users`, `POST /api/admin/users/{user_id}/approve-admin`, and `POST /api/admin/users/{user_id}/reject-admin`; Administrator-only. |
| Users & Admins | Lists accounts and provides the available role/activation management actions. | `GET /api/admin/users`, `PUT /api/admin/users/{user_id}/status`, `POST /api/admin/users/{user_id}/promote-admin`, and `POST /api/admin/users/{user_id}/revoke-admin`; Administrator-only. |

`ADMIN` and `ADMINISTRATOR` are different stored roles. Admins can operate on pothole data but cannot approve/promote/revoke Admin accounts. Administrator-only APIs use a separate backend authorization dependency; hiding a page in React is not the security boundary.

## 6. API and data responsibilities

- **Authentication and role resolution:** `/api/auth/*`; passwords are bcrypt hashes. Authenticated requests resolve the account from the token subject and current MongoDB user record.
- **Reports:** `/api/potholes*`; query and mutation access is role-scoped and protected. Report date filtering is based on stored creation dates.
- **Detection events:** `/api/detections*`; supports listing, image/video inference, event ingestion, and location verification.
- **Analytics:** `/api/analytics/*` and `/api/admin/analytics`; aggregates stored MongoDB records/events, not frontend demo data.
- **Notifications:** `/api/notifications*`; reads and updates are scoped to the signed-in account.
- **System checks:** `/health` and `/api/system/status`; report backend/database and locally detectable service status.
- **Uploads:** `/uploads/{filename}`; backend checks record ownership or administrative access before serving media.
- **Camera agent:** `/api/camera/*`; conditionally mounted when the camera module's dependencies can be imported.

FastAPI response helpers convert MongoDB/BSON values to JSON-compatible values. IDs are represented as strings in API payloads; dates are serialized for the frontend. User serialization excludes password hashes and secret fields.

## 7. Implemented, conditional, and intentionally absent

### Implemented in the source

- Shared login and registration, bcrypt password hashing, HttpOnly cookie session, and three distinct roles.
- One-time initial Administrator setup guarded by a backend environment secret and duplicate checks.
- User-scoped records, Admin operational access, and Administrator-only account approvals/role management.
- Image, video, and local live-camera detection paths, subject to model and host requirements.
- MongoDB-backed pothole registry, detection history, status tracking, notifications for authorized account views, analytics, and report exports.
- UTC date-range report filtering, CSV export, browser-print layout, light/dark theme persistence, responsive layouts, and reduced-motion support.

### Conditional or limited

- Detection requires the YOLO model and road-scene classifier resources to be installed at the backend host.
- Live camera and Windows GPS require the local host, compatible dependencies, attached hardware, and operating-system permissions.
- Browser geolocation requires the browser's permission and a secure context such as localhost or HTTPS.
- Map rendering relies on public OpenStreetMap tiles and network access. The User role has no standalone map page.
- Printable reports use the browser print dialog rather than a server-generated PDF.
- Historical MongoDB records may not contain the source, image, ownership, or GPS verification metadata that newer workflows record. Such fields should not be inferred from absent data.

### Intentionally not exposed as User pages

- Dedicated User GPS Details, Map, and Notifications navigation/pages remain removed by design.
- Public registration cannot create Admin or Administrator accounts. Admin access is requested and requires an Administrator decision; only the one-time setup can create the first Administrator.
- Users cannot gain Admin/Administrator permissions by changing frontend state or manually opening a protected URL; backend API checks remain authoritative.

## 8. Suggested demonstration sequence

1. Register a User and show that the account receives User access immediately.
2. Show that requesting Admin access stays pending; approval requires an Administrator account.
3. Log in with separate User, Admin, and Administrator accounts and show the distinct dashboards and navigation.
4. Submit a pothole report and/or run an image or video detection; explain that the API returns results while the database stores reports/events.
5. Show My Potholes, My Pothole Tracking, and My Detection History as user-scoped data.
6. As Admin, verify a report, update repair status, inspect events, and open Analytics.
7. As Administrator, approve/reject an access request and demonstrate that Admin account management is not available to ordinary Admins.
8. Apply a report date range, download CSV, use the print dialog, switch theme, refresh, and confirm the theme preference persists.

