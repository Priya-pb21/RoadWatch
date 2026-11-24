import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Camera,
  ClipboardList,
  Download,
  FileText,
  Home,
  Loader2,
  MapPin,
  LocateFixed,
  Calendar,
  Pause,
  Play,
  Printer,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
  UserRound,
  Users,
  X,
  Trash2,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import L from "leaflet";
import { CircleMarker, MapContainer, Marker, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import "./App.css";
import AppLayout from "./components/layout/AppLayout";
import FilterBar from "./components/FilterBar";
import AuthPage from "./pages/AuthPage";
import { AccessDenied, DataTable, EmptyState, LoadingState, Panel, StatCard as MetricCard, StatusBadge, ThemeToggle } from "./components/ui";
import { authErrorMessage } from "./lib/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const USER_NAVIGATION = [
  ["Overview", Home, "/user/dashboard", "Overview Dashboard"],
  ["Report Pothole", Camera, "/user/report", "Live Detection"],
  ["My Potholes", ClipboardList, "/user/my-potholes", "Pothole Registry"],
  ["My Pothole Tracking", ClipboardList, "/user/tracking", "Pothole Tracking"],
  ["My Detection History", Activity, "/user/detections", "Detection History"],
  ["My Profile", UserRound, "/user/profile", "My Profile"],
  ["Reports & Exports", FileText, "/user/reports", "Reports and Exports"],
];
const ADMIN_NAVIGATION = [
  ["Global Overview", Home, "/admin/dashboard", "Overview Dashboard"],
  ["Pothole Management", ClipboardList, "/admin/potholes", "Pothole Registry"],
  ["My Notifications", Bell, "/admin/notifications", "Notifications"],
  ["Detection Monitoring", Activity, "/admin/detections", "Detection Monitoring"],
  ["Global Analytics", BarChart3, "/admin/analytics", "Analytics"],
  ["Global Map", MapPin, "/admin/map", "GIS Map"],
  ["Reports & Exports", FileText, "/admin/reports", "Reports and Exports"],
  ["My Profile", UserRound, "/admin/profile", "My Profile"],
];
const ADMINISTRATOR_NAVIGATION = [
  ["Administrator Overview", Home, "/administrator/dashboard", "Overview Dashboard"],
  ["Users & Admins", Users, "/administrator/users", "User Management"],
  ["Admin Approval Requests", ShieldCheck, "/administrator/requests", "Admin Requests"],
  ["Pothole Management", ClipboardList, "/administrator/potholes", "Pothole Registry"],
  ["My Notifications", Bell, "/administrator/notifications", "Notifications"],
  ["Detection Monitoring", Activity, "/administrator/detections", "Detection Monitoring"],
  ["Global Analytics", BarChart3, "/administrator/analytics", "Analytics"],
  ["Global Map", MapPin, "/administrator/map", "GIS Map"],
  ["Reports & Exports", FileText, "/administrator/reports", "Reports and Exports"],
  ["My Profile", UserRound, "/administrator/profile", "My Profile"],
];
const PAGE_DESCRIPTIONS = {
  "Overview Dashboard": "Review pothole totals, detection activity, and account access for your role.",
  "Live Detection": "Capture road conditions with image, video, or live camera detection.",
  "Pothole Registry": "Review only the potholes submitted by your account.",
  "Pothole Tracking": "Track verification, repair progress, administrator remarks, and the complete status timeline for your reports.",
  "Detection History": "Review your individual image, video, and live-camera events.",
  "My Profile": "Review your account and registration information.",
  Analytics: "Explore detection trends and road condition patterns.",
  "GIS Map": "Inspect only your verified pothole locations.",
  "Reports and Exports": "Filter and export records from your available data.",
  "User Management": "View and manage User and Admin accounts; Administrator access is reserved for initial setup.",
  "Admin Requests": "Approve or reject User requests for Admin access.",
  Notifications: "Review persistent account notifications and mark them as read.",
  "Detection Monitoring": "Review image, video, and live-camera events from all accounts.",
};
const REFRESH_SECONDS = 5;
const PAGE_SIZE = 25;
const THEME_STORAGE_KEY = "roadwatch-theme";
const MAP_CLUSTER_CELL_SIZE = 56;
const MAX_LOCATE_ME_ACCURACY_M = 100;

function getInitialTheme() {
  try {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
  } catch (error) {
    console.warn("RoadWatch could not read the saved theme preference.", error);
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getApiErrorMessage(payload, status, fallback = "Something went wrong.") {
  const detail = payload?.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (detail && typeof detail === "object") {
    const messages = [];

    if (detail.message) messages.push(detail.message);
    if (detail.reason) messages.push(detail.reason);

    if (detail.predicted_scene) {
      messages.push(`Detected scene: ${detail.predicted_scene}`);
    }

    if (detail.error) messages.push(detail.error);

    if (messages.length) {
      return messages.join(" · ");
    }

    return JSON.stringify(detail);
  }

  if (payload?.message) return payload.message;

  return `${fallback} (HTTP ${status})`;
}
function adminRequestNotice(status) {
  if (status === "approved") return "Your Admin access request was approved. Admin permissions are now active.";
  if (status === "rejected") return "Your Admin access request was rejected. Your account remains available with User access.";
  if (status === "revoked") return "Your Admin privileges were revoked. Your account remains available with User access.";
  return "";
}
function dashboardPathForRole(role) {
  if (role === "administrator") return "/administrator/dashboard";
  if (role === "admin") return "/admin/dashboard";
  return "/user/dashboard";
}
function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  return fetch(url, { credentials: "include", ...options, headers });
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  const number = Number(value);
  return `${number > 1 ? number.toFixed(1) : (number * 100).toFixed(1)}%`;
}

function formatConfidence(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  const number = Number(value);
  return `${(number > 1 ? number : number * 100).toFixed(2)}%`;
}

function formatDate(value) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function formatAuditValues(values) {
  const relevantValues = Object.entries(values || {}).filter(([key]) => (
    key.endsWith("_status")
    || key === "status"
    || key === "latitude"
    || key === "longitude"
    || key === "notes"
    || key === "rejection_reason"
  ));
  return relevantValues.length
    ? relevantValues.map(([key, value]) => `${key.replaceAll("_", " ")}: ${value ?? "—"}`).join(" · ")
    : "—";
}

function EvidenceImage({ reference, alt, className = "record-thumbnail" }) {
  const [unavailable, setUnavailable] = useState(false);
  if (!reference || unavailable) return <span>Evidence unavailable</span>;
  const source = /^https?:\/\//i.test(reference)
    ? reference
    : `${API_BASE}${reference.startsWith("/") ? reference : `/${reference}`}`;
  return <img className={className} src={source} alt={alt} loading="lazy" onError={() => setUnavailable(true)} />;
}

function validCoordinate(record) {
  if (record.latitude === null || record.latitude === undefined || record.longitude === null || record.longitude === undefined) return false;
  if (typeof record.latitude === "string" && !record.latitude.trim()) return false;
  if (typeof record.longitude === "string" && !record.longitude.trim()) return false;
  const lat = Number(record.latitude);
  const lon = Number(record.longitude);
  return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}

function formatReportDate(value) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Unavailable";
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

function recordKey(record) {
  return record._id ?? record.pothole_id;
}

function locationLabel(record) {
  const location = record.location;
  if (typeof location === "string") return location;
  if (location && typeof location === "object") {
    if (location.name || location.address) return location.name || location.address;
    if (Array.isArray(location.coordinates) && location.coordinates.length >= 2) return `${location.coordinates[1]}, ${location.coordinates[0]}`;
  }
  return record.location_name || record.address || record.road_name || "—";
}

function useRoadwatchData(filters, page, includeMap, isAdmin) {
  const [state, setState] = useState({
    potholes: [],
    total: 0,
    detectionEvents: [],
    detectionEventTotal: 0,
    mapRecords: [],
    mapCount: 0,
    mapReviewRecords: [],
    mapReviewCount: 0,
    mapTruncated: false,
    summary: null,
    trends: [],
    monthlyTrends: [],
    loading: true,
    refreshing: false,
    error: "",
    lastSync: null,
    stale: true,
  });
  const abortRef = useRef(null);
  const requestIdRef = useRef(0);

  const refresh = useCallback(async ({ silent = false, forceMap = false } = {}) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = ++requestIdRef.current;
    setState((previous) => ({ ...previous, loading: !silent && !previous.lastSync, refreshing: true }));
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (filters.search) params.set("search", filters.search);
      if (filters.verification !== "all") params.set("verification_status", filters.verification);
      if (filters.repair !== "all") params.set("repair_status", filters.repair);

      const mapParams = new URLSearchParams();
      if (filters.search) mapParams.set("search", filters.search);
      if (filters.verification !== "all") mapParams.set("verification_status", filters.verification);
      if (filters.repair !== "all") mapParams.set("repair_status", filters.repair);
      const [recordsResponse, detectionEventsResponse, summaryResponse, trendsResponse, mapResponse] = await Promise.all([
        apiFetch(`${API_BASE}/api/potholes?${params}`, { signal: controller.signal }),
        apiFetch(`${API_BASE}/api/detections?page=1&page_size=50`, { signal: controller.signal }),
        apiFetch(`${API_BASE}${isAdmin ? "/api/analytics/summary" : "/api/user/dashboard"}`, { signal: controller.signal }),
        isAdmin ? apiFetch(`${API_BASE}/api/analytics/trends`, { signal: controller.signal }) : Promise.resolve(null),
        includeMap || (isAdmin && forceMap) ? apiFetch(`${API_BASE}/api/potholes/map?${mapParams}`, { signal: controller.signal }) : Promise.resolve(null),
      ]);
      for (const [name, response] of [
        ["Potholes", recordsResponse],
        ["Detection events", detectionEventsResponse],
        [isAdmin ? "Analytics summary" : "Dashboard totals", summaryResponse],
        ...(trendsResponse ? [["Analytics trends", trendsResponse]] : []),
        ...(mapResponse ? [["Pothole map", mapResponse]] : []),
      ]) {
        if (!response.ok) throw new Error(`${name} API returned ${response.status}`);
      }
      const records = await recordsResponse.json();
      const [detectionEventData, summary, trends, mapData] = await Promise.all([
        detectionEventsResponse.json(),
        summaryResponse.json(),
        trendsResponse ? trendsResponse.json() : Promise.resolve({ trends: [], monthly_trends: [] }),
        mapResponse ? mapResponse.json() : Promise.resolve(null),
      ]);
      if (!Array.isArray(records.potholes)) throw new Error("Invalid /api/potholes response.");
      if (!Array.isArray(detectionEventData.detections)) throw new Error("Invalid /api/detections response.");
      if (mapData && (!Array.isArray(mapData.records) || !Array.isArray(mapData.review_records || []))) {
        throw new Error("Invalid /api/potholes/map response.");
      }
      if (requestId !== requestIdRef.current) return;
      setState((previous) => ({
        ...previous,
        potholes: records.potholes,
        total: records.total ?? records.count ?? records.potholes.length,
        detectionEvents: detectionEventData.detections,
        detectionEventTotal: detectionEventData.total ?? detectionEventData.detections.length,
        mapRecords: mapData ? mapData.records : previous.mapRecords,
        mapCount: mapData ? mapData.count : previous.mapCount,
        mapReviewRecords: mapData ? mapData.review_records || [] : previous.mapReviewRecords,
        mapReviewCount: mapData ? mapData.review_count || 0 : previous.mapReviewCount,
        mapTruncated: mapData ? mapData.truncated : previous.mapTruncated,
        summary,
        trends: trends.trends || [],
        monthlyTrends: trends.monthly_trends || [],
        loading: false,
        refreshing: false,
        error: "",
        lastSync: new Date(),
        stale: false,
      }));
    } catch (error) {
      if (error.name !== "AbortError" && requestId === requestIdRef.current) {
        setState((previous) => ({ ...previous, loading: false, refreshing: false, error: error.message || "Backend unavailable", stale: true }));
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setState((previous) => ({ ...previous, refreshing: false }));
      }
    }
  }, [filters, includeMap, isAdmin, page]);

  useEffect(() => {
    let timer;
    let active = true;
    let initial = true;
    const poll = async () => {
      await refresh({ silent: !initial });
      initial = false;
      if (active) {
        timer = window.setTimeout(poll, REFRESH_SECONDS * 1000);
      }
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
      abortRef.current?.abort();
    };
  }, [refresh]);

  return { ...state, refresh };
}

function FitMap({ records }) {
  const map = useMap();
  const fittedCoordinates = useRef("");
  useEffect(() => {
    const points = records.filter(validCoordinate).map((record) => [Number(record.latitude), Number(record.longitude)]);
    const signature = points.map((point) => point.join(",")).sort().join("|");
    if (!signature || signature === fittedCoordinates.current) return;
    fittedCoordinates.current = signature;
    if (points.length === 1) map.setView(points[0], 15);
    if (points.length > 1) map.fitBounds(points, { padding: [34, 34], maxZoom: 15 });
  }, [map, records]);
  return null;
}

function FocusSelectedMapRecord({ records, selectedId, markerRefs }) {
  const map = useMap();
  const lastFocusRef = useRef("");
  useEffect(() => {
    const record = records.find((item) => recordKey(item) === selectedId);
    if (!record || !validCoordinate(record)) return;
    const focusKey = `${selectedId}:${record.latitude}:${record.longitude}`;
    if (focusKey === lastFocusRef.current) return;
    lastFocusRef.current = focusKey;
    map.flyTo([Number(record.latitude), Number(record.longitude)], Math.max(map.getZoom(), 15), { duration: 0.5 });
    markerRefs.current.get(selectedId)?.openPopup();
  }, [map, markerRefs, records, selectedId]);
  return null;
}

function MapResizer() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    const resizeObserver = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    resizeObserver.observe(container);
    const frame = window.requestAnimationFrame(() => map.invalidateSize({ pan: false }));
    return () => {
      window.cancelAnimationFrame(frame);
      resizeObserver.disconnect();
    };
  }, [map]);
  return null;
}

function MapClusterMarkers({ records, setSelectedId, markerRefs, onReviewRecord, showOwner }) {
  const map = useMap();
  const [zoom, setZoom] = useState(map.getZoom());
  useMapEvents({
    zoomend() {
      setZoom(map.getZoom());
    },
  });
  const groups = useMemo(() => {
    const buckets = new Map();
    records.forEach((record) => {
      const projected = map.project([Number(record.latitude), Number(record.longitude)], zoom);
      const key = `${Math.floor(projected.x / MAP_CLUSTER_CELL_SIZE)}:${Math.floor(projected.y / MAP_CLUSTER_CELL_SIZE)}`;
      const bucket = buckets.get(key) || { records: [], x: 0, y: 0 };
      bucket.records.push(record);
      bucket.x += projected.x;
      bucket.y += projected.y;
      buckets.set(key, bucket);
    });
    return [...buckets.values()].map((bucket) => {
      const center = map.unproject(
        L.point(bucket.x / bucket.records.length, bucket.y / bucket.records.length),
        zoom,
      );
      return { records: bucket.records, center: [center.lat, center.lng] };
    });
  }, [map, records, zoom]);

  return groups.map((group) => {
    if (group.records.length > 1) {
      const severityPriority = { high: 3, critical: 3, medium: 2, low: 1 };
      const strongestSeverity = group.records.reduce((strongest, record) => (
        (severityPriority[String(record.severity || "").toLowerCase()] || 0)
          > (severityPriority[strongest] || 0)
          ? String(record.severity || "").toLowerCase()
          : strongest
      ), "other");
      const icon = L.divIcon({
        className: "roadwatch-cluster-icon-container",
        html: `<span class="roadwatch-cluster-icon ${strongestSeverity}">${group.records.length}</span>`,
        iconSize: [38, 38],
        iconAnchor: [19, 19],
      });
      return (
        <Marker
          key={`cluster-${group.records.map(recordKey).sort().join("-")}`}
          position={group.center}
          icon={icon}
          eventHandlers={{
            click: () => map.flyTo(group.center, Math.min(zoom + 2, 18), { duration: 0.35 }),
          }}
        />
      );
    }

    const record = group.records[0];
    const id = recordKey(record);
    const displayId = record.pothole_id ?? record._id ?? "ID unavailable";
    const severity = String(record.severity || "").toLowerCase();
    const severityClass = ["high", "critical", "medium", "low"].includes(severity) ? severity : "other";
    const isVerified = record.verification_status === "verified";
    const location = record.location && typeof record.location === "object" ? record.location : {};
    const locationDetails = [
      record.location_name,
      record.road_name,
      record.address,
      record.region,
      location.name,
      location.road,
      location.address,
      location.region,
    ].filter((value) => typeof value === "string" && value.trim());
    const icon = L.divIcon({
      className: "roadwatch-severity-icon-container",
      html: `<span class="roadwatch-severity-icon ${severityClass}${isVerified ? "" : " pending"}"></span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    return (
      <Marker
        ref={(marker) => {
          if (marker) markerRefs.current.set(id, marker);
          else markerRefs.current.delete(id);
        }}
        key={id}
        position={group.center}
        icon={icon}
        eventHandlers={{ click: () => setSelectedId(id) }}
      >
        <Popup>
          <div className="popup">
            <strong>{displayId}</strong>
            {showOwner && <span>Submitted by: {record.reported_by_display || record.reported_by_id || "Legacy / unassigned"}</span>}
            <span>Severity: {record.severity || "Unavailable"}</span>
            <span>Detected: {formatDate(record.last_seen_at || record.created_at || record.first_detected_at)}</span>
            <span>Status: {record.verification_status?.replaceAll("_", " ") || "Unknown"} · {record.repair_status?.replaceAll("_", " ") || "Repair status unavailable"}</span>
            {locationDetails.length > 0 && <span>Location: {[...new Set(locationDetails)].join(", ")}</span>}
            <span>Latitude: {record.latitude}</span>
            <span>Longitude: {record.longitude}</span>
            <span>GPS accuracy: {record.gps_accuracy_m == null ? "Manually verified" : `${Number(record.gps_accuracy_m).toFixed(1)} m`}</span>
            <a href={`https://www.google.com/maps?q=${record.latitude},${record.longitude}`} target="_blank" rel="noreferrer">Open coordinates</a>
            {onReviewRecord && <button type="button" onClick={(event) => { event.stopPropagation(); onReviewRecord(record); }}>Review pothole</button>}
          </div>
        </Popup>
      </Marker>
    );
  });
}

function LocateMeControl() {
  const map = useMap();
  const [status, setStatus] = useState("Requesting device location…");
  const [locating, setLocating] = useState(false);
  const [position, setPosition] = useState(null);
  const receiveLocation = useCallback((result) => {
    const accuracy = Number(result.coords.accuracy);
    if (!Number.isFinite(accuracy) || accuracy > MAX_LOCATE_ME_ACCURACY_M) {
      setPosition(null);
      setStatus(Number.isFinite(accuracy)
        ? `Location accuracy is too low (${Math.round(accuracy)} m). Try again in a clearer area.`
        : "Location accuracy is unavailable. Try again in a clearer area.");
      setLocating(false);
      return;
    }
    const nextPosition = {
      latitude: result.coords.latitude,
      longitude: result.coords.longitude,
      accuracy,
    };
    setPosition(nextPosition);
    map.flyTo([nextPosition.latitude, nextPosition.longitude], Math.max(map.getZoom(), 14), { duration: 0.5 });
    setStatus(`Located · accuracy ±${Math.round(accuracy)} m`);
    setLocating(false);
  }, [map]);
  const receiveLocationError = useCallback((error) => {
    setPosition(null);
    setStatus(error.code === error.PERMISSION_DENIED
      ? "Location permission denied. Allow location access in browser settings to use this feature."
      : "Your device location is unavailable right now. You can still explore verified pothole markers.");
    setLocating(false);
  }, []);
  const locate = useCallback(() => {
    if (!navigator.geolocation) {
      setStatus("This browser does not provide location access.");
      return;
    }
    setLocating(true);
    setStatus("Requesting device location…");
    navigator.geolocation.getCurrentPosition(
      receiveLocation,
      receiveLocationError,
      { enableHighAccuracy: true, maximumAge: 60_000, timeout: 12_000 },
    );
  }, [receiveLocation, receiveLocationError]);

  useEffect(() => {
    if (!navigator.geolocation) {
      const timer = window.setTimeout(() => setStatus("This browser does not provide location access."), 0);
      return () => window.clearTimeout(timer);
    }
    navigator.geolocation.getCurrentPosition(
      receiveLocation,
      receiveLocationError,
      { enableHighAccuracy: true, maximumAge: 60_000, timeout: 12_000 },
    );
    return undefined;
  }, [receiveLocation, receiveLocationError]);

  return (
    <>
      {position && <CircleMarker center={[position.latitude, position.longitude]} radius={7} pathOptions={{ color: "#2563eb", fillColor: "#60a5fa", fillOpacity: 0.9, weight: 2 }} />}
      <div className="map-location-control">
        <button type="button" className="secondary" onClick={locate} disabled={locating}>
          <LocateFixed size={15} />{locating ? "Locating…" : "Locate Me"}
        </button>
        <span role="status">{status}</span>
      </div>
    </>
  );
}

function UnverifiedLocationList({ records, events, onVerify, onReviewRecord, canVerify = false, limit = 0 }) {
  const allUnverifiedRecords = records.filter((record) => record.location_verification_status !== "VERIFIED");
  const allUnverifiedEvents = events.filter((event) => (event.location_verification_status || "UNVERIFIED") !== "VERIFIED");
  const unverifiedRecords = limit ? allUnverifiedRecords.slice(0, limit) : allUnverifiedRecords;
  const unverifiedEvents = limit ? allUnverifiedEvents.slice(0, limit) : allUnverifiedEvents;
  if (!unverifiedRecords.length && !unverifiedEvents.length) return null;
  return (
    <div className="unverified-list">
      <h4>Unverified detections and locations</h4>
      <p>These detections remain saved even when their location is unavailable or too imprecise to map.</p>
      <div className="unverified-items">
        {unverifiedEvents.map((event) => (
          <article key={`event-${event.event_id || event._id}`}>
            <strong>{event.pothole_id || `Detection #${event.display_id ?? "—"}`}</strong>
            <span>{event.source_id || "Source unavailable"} · {formatDate(event.timestamp)}</span>
            <span>{event.location_verification_reason || "Location is unverified"} · {validCoordinate(event) ? `${Number(event.latitude).toFixed(6)}, ${Number(event.longitude).toFixed(6)}` : "Coordinates unavailable"} · GPS accuracy {event.gps_accuracy_m == null ? "unavailable" : `${Number(event.gps_accuracy_m).toFixed(1)} m`}</span>
            <span>Confidence: {formatConfidence(event.confidence_percent ?? event.confidence_normalized)}</span>
            {canVerify && <button onClick={() => onVerify(event)}>Verify Location</button>}
          </article>
        ))}
        {unverifiedRecords.map((record) => (
          <article key={`record-${recordKey(record)}`}>
            <strong>{record.pothole_id ?? "ID unavailable"} · {record.record_origin === "historical_unknown" ? "Historical / unknown" : record.source || "Source recorded"}</strong>
            <span>{validCoordinate(record) ? `${Number(record.latitude).toFixed(6)}, ${Number(record.longitude).toFixed(6)}` : "Coordinates unavailable"} · {record.location_verification_reason || "Location is unverified"} · GPS accuracy {record.gps_accuracy_m == null ? "unavailable" : `${Number(record.gps_accuracy_m).toFixed(1)} m`}</span>
            <span>Last recorded: {formatDate(record.last_seen_at || record.created_at || record.first_detected_at)}</span>
            {onReviewRecord && <button type="button" onClick={() => onReviewRecord(record)}>Review pothole</button>}
          </article>
        ))}
      </div>
      {limit > 0 && (allUnverifiedEvents.length > unverifiedEvents.length || allUnverifiedRecords.length > unverifiedRecords.length) && <p className="unverified-more">Showing the {Math.min(limit, allUnverifiedEvents.length)} latest events and {Math.min(limit, allUnverifiedRecords.length)} latest records. Open GIS Map to review the full list.</p>}
    </div>
  );
}

function PotholeMap({ records, reviewRecords = [], previewRecord = null, events = [], unverifiedRecords = [], selectedId, setSelectedId, onReviewRecord, refresh = () => {}, compactUnverified = false, canVerify = false, showLocationReview = false, showOwner = false }) {
  const [tileStatus, setTileStatus] = useState("loading");
  const [selected, setSelected] = useState(null);
  const markerRefs = useRef(new Map());
  const isVerifiedMarker = (record) => record.location_verification_status === "VERIFIED" && record.verification_status === "verified";
  const verifiedRecords = records.filter((record) => validCoordinate(record) && isVerifiedMarker(record));
  const candidates = [
    ...verifiedRecords,
    ...(canVerify ? reviewRecords : []),
    ...(!canVerify && previewRecord ? [previewRecord] : []),
  ];
  const valid = [...new Map(candidates
    .filter((record) => validCoordinate(record) && record.location_verification_status === "VERIFIED")
    .map((record) => [recordKey(record), record])).values()];
  const initialCenter = valid.length
    ? [Number(valid[0].latitude), Number(valid[0].longitude)]
    : [0, 0];
  return (
    <div className="map-content">
      <div className="map-shell">
          <MapContainer center={initialCenter} zoom={valid.length ? 13 : 2} zoomControl scrollWheelZoom className="leaflet-map">
            <MapResizer />
            <TileLayer
              attribution='&copy; OpenStreetMap contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              eventHandlers={{
                tileload: () => setTileStatus("loaded"),
                tileerror: () => setTileStatus("error"),
              }}
            />
            <FitMap records={valid} />
            <FocusSelectedMapRecord records={valid} selectedId={selectedId} markerRefs={markerRefs} />
            <MapClusterMarkers records={valid} setSelectedId={setSelectedId} markerRefs={markerRefs} onReviewRecord={onReviewRecord} showOwner={showOwner} />
            <LocateMeControl />
          </MapContainer>
          {tileStatus === "loading" && <div className="map-overlay">Loading OpenStreetMap tiles...</div>}
          {tileStatus === "error" && <div className="map-error" role="alert">Some map tiles could not be loaded. Check your network connection; markers remain based on stored records.</div>}
          {!valid.length && <div className="map-empty-overlay">No verified pothole coordinates are available. Missing or imprecise GPS locations remain in the review list instead of being mapped.</div>}
          <div className="map-legend">
            <span><i className="high" />High severity</span>
            <span><i className="medium" />Medium severity</span>
            <span><i className="low" />Low severity</span>
            <span><i className="other" />Unclassified</span>
            <span><i className="cluster" />Numbered marker: nearby records</span>
            <span><i className="current-location" />Your location</span>
            {canVerify && <span><i className="unverified" />Dashed outline: report verification pending</span>}
          </div>
      </div>
      {showLocationReview && <UnverifiedLocationList records={unverifiedRecords} events={events} onVerify={setSelected} onReviewRecord={onReviewRecord} canVerify={canVerify} limit={compactUnverified ? 3 : 0} />}
      {selected && <LocationVerificationDialog event={selected} onClose={() => setSelected(null)} onVerified={() => { setSelected(null); refresh({ silent: true, forceMap: true }); }} />}
    </div>
  );
}

function MapSelection({ point, setPoint }) {
  const map = useMap();
  const latitude = point?.latitude;
  const longitude = point?.longitude;
  useMapEvents({ click(event) { setPoint({ latitude: event.latlng.lat, longitude: event.latlng.lng }); } });
  useEffect(() => {
    if (latitude != null && longitude != null) map.setView([latitude, longitude], Math.max(map.getZoom(), 15));
  }, [map, latitude, longitude]);
  return point ? <Marker position={[point.latitude, point.longitude]} /> : null;
}

function LocationPickerMap({ point, setPoint, heightClass = "location-picker-map" }) {
  const center = point ? [point.latitude, point.longitude] : [0, 0];
  return <div className={heightClass}><MapContainer center={center} zoom={point ? 15 : 2} zoomControl scrollWheelZoom className="leaflet-map">
    <MapResizer />
    <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
    <MapSelection point={point} setPoint={setPoint} />
  </MapContainer></div>;
}

function ReportLocationPicker({ latitude, longitude, gpsAccuracy, locationMethod, onSelect }) {
  const point = validCoordinate({ latitude, longitude })
    ? { latitude: Number(latitude), longitude: Number(longitude) }
    : null;
  const statusText = locationMethod === "DEVICE"
    ? `Device GPS captured${gpsAccuracy ? ` · reported accuracy ${Number(gpsAccuracy).toFixed(1)} m` : " · accuracy unavailable"}. The server will apply its verification threshold.`
    : locationMethod === "USER_SUPPLIED"
      ? "Manually selected or entered coordinates · unverified until an Admin explicitly confirms them."
      : "No coordinates selected. Click the map to select a location, or continue without GPS.";
  return <section className="report-location-picker">
    <div className="report-location-heading"><strong>Report location</strong><span>{statusText}</span></div>
    <LocationPickerMap point={point} setPoint={onSelect} />
    <p>With no saved location, the map opens at world view without a marker. Selecting a point saves it only as an unverified user location.</p>
  </section>;
}

function LocationVerificationDialog({ event, onClose, onVerified }) {
  const [point, setPoint] = useState(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const image = event.annotated_image_reference || event.image_reference;
  const hasSavedLocation = validCoordinate(event);
  const mapCenter = hasSavedLocation ? [Number(event.latitude), Number(event.longitude)] : [0, 0];
  async function confirm() {
    if (!point) { setError("Click the map to place the pothole marker before confirming."); return; }
    setSaving(true); setError("");
    try {
      const response = await apiFetch(`${API_BASE}/api/detections/${encodeURIComponent(event.event_id)}/verify-location`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(point),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(
          getApiErrorMessage(payload, response.status, "Verification failed.")
        );
      }
      onVerified();
    } catch (failure) { setError(failure.message || "Could not save the verified location."); }
    finally { setSaving(false); }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}><section className="location-dialog" role="dialog" aria-modal="true" aria-labelledby="verify-title">
    <div className="dialog-heading"><div><h3 id="verify-title">Verify Location · {event.pothole_id || `Detection #${event.display_id || "—"}`}</h3><p>{hasSavedLocation ? "Click the pothole’s location on the map, then confirm." : "No GPS location was saved. Select the location on the map; no coordinates will be assumed."}</p></div><button className="secondary" onClick={onClose}>Close</button></div>
    <div className="verification-review">{image && <img src={`${API_BASE}${image}`} alt="Uploaded road image with YOLO detection overlay" />}<div><strong>YOLO confidence: {formatConfidence(event.confidence_percent)}</strong><span>Detection time: {formatDate(event.timestamp)}</span><span>Source: {event.source_id || "image_upload"}</span><span>YOLO confidence describes detection certainty; it is separate from GPS precision.</span></div></div>
    <div className="verification-map"><MapContainer center={mapCenter} zoom={hasSavedLocation ? 15 : 2} scrollWheelZoom className="leaflet-map"><TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" /><MapSelection point={point} setPoint={setPoint} /></MapContainer></div>
    {point && <div className="detail-strip">Selected: {point.latitude.toFixed(6)}, {point.longitude.toFixed(6)}</div>}
    {error && <div className="alert" role="alert">{error}</div>}<div className="button-row"><button onClick={confirm} disabled={saving || !point}>{saving ? "Saving…" : "Confirm location"}</button></div>
  </section></div>;
}

function RegistryTable({ records, total, page, setPage, pageSize, selectedId, setSelectedId, isAdmin = false, onLocateRecord, onReviewRecord }) {
  if (!records.length) return <EmptyState icon={ClipboardList} title="No pothole records available" body="Try changing the filters, or check back after detections are saved." />;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <>
      <DataTable>
          <thead><tr><th>Pothole ID</th><th>Evidence</th>{isAdmin && <th>Submitted by</th>}<th>Detected</th><th>Confidence</th><th>Latitude</th><th>Longitude</th><th>GPS accuracy</th><th>GPS status</th><th>Pothole status</th><th>Repair status</th><th>Verification details</th><th>Location</th>{onLocateRecord && <th>Map</th>}{onReviewRecord && <th>Admin action</th>}</tr></thead>
          <tbody>
            {records.map((record) => {
              const id = record.pothole_id ?? "ID unavailable";
              const key = recordKey(record);
              return (
                <tr key={key} className={selectedId === key ? "selected" : ""} onClick={() => setSelectedId(key)}>
                  <td><strong>{id}</strong></td>
                  <td><EvidenceImage key={record.annotated_image_reference || record.image_reference || key} reference={record.annotated_image_reference || record.image_reference} alt={`Pothole ${id}`} /></td>
                  {isAdmin && <td>{record.reported_by_display || record.reported_by_id || "Legacy / unassigned"}</td>}
                  <td>{formatDate(record.last_seen_at || record.created_at || record.first_detected_at)}</td>
                  <td>{formatPercent(record.confidence_percent)}</td>
                  <td>{record.latitude ?? "—"}</td><td>{record.longitude ?? "—"}</td>
                  <td>{record.gps_accuracy_m == null ? "Unavailable" : `${Number(record.gps_accuracy_m).toFixed(1)} m`}</td>
                  <td><StatusBadge value={record.location_verification_status || "UNVERIFIED"} /></td>
                  <td><StatusBadge value={record.verification_status || "unknown"} /></td>
                  <td><StatusBadge value={record.repair_status || "unknown"} /></td>
                  <td>{record.verification_status === "verified"
                    ? `${record.verified_by_display || record.verified_by || "Unavailable"} · ${formatDate(record.verified_at)}`
                    : record.verification_status === "rejected"
                      ? `${record.rejected_by_display || record.rejected_by || "Unavailable"} · ${formatDate(record.rejected_at)} · ${record.rejection_reason || "No reason recorded"}`
                      : record.verification_status === "pending_verification" ? "Awaiting Admin review" : "No verification history"}</td>
                  <td>{locationLabel(record)}</td>
                  {onLocateRecord && <td><button className="secondary" type="button" disabled={!validCoordinate(record) || record.location_verification_status !== "VERIFIED" || record.verification_status !== "verified"} onClick={(event) => { event.stopPropagation(); onLocateRecord(record); }}>Locate</button></td>}
                  {onReviewRecord && <td><button className="secondary" type="button" onClick={(event) => { event.stopPropagation(); onReviewRecord(record); }}>Review &amp; Update</button></td>}
                </tr>
              );
            })}
          </tbody>
      </DataTable>
      <div className="pagination">
        <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>
    </>
  );
}

function DetectionEventTable({ events, total, isAdmin = false }) {
  if (!events.length) {
    return <EmptyState icon={Activity} title="No saved detection events" body="Events appear here after the backend confirms saving YOLO observations. Historical pothole records are not shown as new events." />;
  }
  return (
    <>
      <DataTable>
          <thead><tr><th>Detection ID</th>{isAdmin && <th>Reported by</th>}<th>Source</th><th>Evidence</th><th>Timestamp</th><th>Confidence</th><th>Coordinates</th><th>GPS accuracy</th><th>Location status</th><th>Linked pothole</th></tr></thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.event_id || event._id}>
                <td><strong>Detection #{event.display_id ?? "—"}</strong></td>
                {isAdmin && <td>{event.reported_by_display || event.reported_by_id || "Legacy / unassigned"}</td>}
                <td>{detectionSourceLabel(event.source_id)}</td>
                <td><EvidenceImage key={event.annotated_image_reference || event.image_reference || event.image_url || event.event_id || event._id} reference={event.annotated_image_reference || event.image_reference || event.image_url} alt={`Evidence for detection ${event.display_id ?? "—"}`} /></td>
                <td>{formatDate(event.timestamp)}</td>
                <td>{formatConfidence(event.confidence_percent ?? event.confidence_normalized)}</td>
                <td>{validCoordinate(event) ? `${Number(event.latitude).toFixed(5)}, ${Number(event.longitude).toFixed(5)}` : "Unavailable"}</td>
                <td>{event.gps_accuracy_m == null ? "Unavailable" : `${Number(event.gps_accuracy_m).toFixed(1)} m`}</td>
                <td>{event.location_verification_status || "UNVERIFIED"}{event.location_verification_reason ? ` · ${event.location_verification_reason}` : ""}</td>
                <td>{event.pothole_id ?? "Event only"}</td>
              </tr>
            ))}
          </tbody>
      </DataTable>
      <div className="detail-strip">Detection events are individual observations. Linked pothole IDs refer to corresponding registry records; unverified locations are excluded from map markers. Showing {events.length} of {total} events.</div>
    </>
  );
}

function detectionSourceLabel(source) {
  if (source === "image_upload") return "Image upload";
  if (source === "video_upload" || source === "video_upload_frame") return "Video upload";
  if (typeof source === "string" && source.startsWith("local-windows")) return "Live camera";
  return source || "Source unavailable";
}

function Overview({ data, selectedId, setSelectedId, selectedRecord, setReviewRecord, adminStats, isAdmin, isAdministrator }) {
  const summary = data.summary || {};
  const potholeTotal = isAdmin ? summary.total_pothole_records : summary.total_potholes;
  const detectionTotal = isAdmin ? summary.total_detection_events : summary.total_detections;
  const detectionCounts = summary.detection_counts || {};
  return (
    <>
      <div className="page-section-heading">
        <div><span className="section-eyebrow">{isAdmin ? "NETWORK SNAPSHOT" : "YOUR FIELD ACTIVITY"}</span><h2>{isAdministrator ? "Administrator Dashboard" : isAdmin ? "Admin Dashboard" : "User Dashboard"}</h2><p>{isAdministrator ? "Manage account access and review road operations across the network." : isAdmin ? "Manage pothole reports, verification, repair status, and analytics." : "Only your saved detections and pothole records are included here."}</p></div>
        <span className="data-source-chip"><span />MongoDB backed</span>
      </div>
      <section className="metrics-grid overview-metrics">
        <MetricCard title={isAdmin ? "Total potholes" : "Your potholes"} value={potholeTotal ?? 0} detail={isAdmin ? "All saved MongoDB pothole records" : "Pothole records submitted by you"} icon={ClipboardList} tone="blue" />
        {adminStats && <MetricCard title="Registered users" value={adminStats.total_users ?? 0} detail="MongoDB user accounts" icon={Users} tone="blue" />}
        <MetricCard title="Detection records" value={detectionTotal ?? 0} detail={isAdmin ? "Saved detection events" : "Your saved detection events"} icon={Activity} tone="blue" />
        {isAdministrator && adminStats && <MetricCard title="Pending Admin requests" value={adminStats.pending_admin_requests ?? 0} detail="Awaiting your review" icon={ShieldCheck} tone="amber" />}
        {isAdmin && <MetricCard title="Verified locations" value={summary.verified_locations ?? 0} detail="GPS verified or manually verified" icon={ShieldCheck} tone="green" />}
        {isAdmin && <MetricCard title="Unverified locations" value={summary.unverified_locations ?? 0} detail="Detection events needing location review" icon={MapPin} tone="amber" />}
        {["image", "video", "live", "other"].map((kind) => <MetricCard key={kind} title={`${isAdmin ? "All" : "My"} ${kind === "live" ? "live camera" : kind} detections`} value={detectionCounts[kind] ?? 0} detail="Saved detection events" icon={Activity} tone="blue" />)}
      </section>
      <section className={`dashboard-grid overview-dashboard-grid ${isAdmin ? "" : "user-overview-dashboard-grid"}`}>
        {isAdmin && <Panel title="Verified pothole locations" subtitle={`${data.mapCount} verified locations${data.mapReviewCount ? ` · ${data.mapReviewCount} locations need review` : ""}${data.mapTruncated ? " (map limit reached)" : ""}`}><PotholeMap records={data.mapRecords} reviewRecords={data.mapReviewRecords} previewRecord={selectedRecord} unverifiedRecords={data.potholes} events={data.detectionEvents} selectedId={selectedId} setSelectedId={setSelectedId} onReviewRecord={setReviewRecord} refresh={data.refresh} compactUnverified canVerify showLocationReview showOwner /></Panel>}
        <Panel title={isAdmin ? "Overall Severity Trends" : "My Severity Counts"} subtitle="Saved pothole records grouped by severity"><SeverityTrendChart counts={summary.severity_counts || {}} /></Panel>
        {isAdmin && <Panel title="Detection Trends" subtitle="Based only on timestamped detection events"><TrendChart trends={data.trends} /></Panel>}
      </section>
    </>
  );
}

function TrendChart({ trends = [] }) {
  if (!trends.length) return <EmptyState icon={BarChart3} title="No trend data" body="Time series analytics will update from database timestamps." />;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={trends} margin={{ bottom: 16 }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" label={{ value: "Date", position: "insideBottom", offset: -8 }} /><YAxis allowDecimals={false} label={{ value: "Detections", angle: -90, position: "insideLeft" }} /><Tooltip /><Legend /><Area type="monotone" dataKey="observations" name="Detection events" stroke="var(--color-accent)" fill="var(--color-chart-fill)" /><Area type="monotone" dataKey="unique_potholes" name="Linked potholes" stroke="var(--color-primary)" fill="var(--color-primary-soft)" /></AreaChart>
    </ResponsiveContainer>
  );
}

function SeverityTrendChart({ counts = {} }) {
  const data = [
    { name: "Low", count: Number(counts.low) || 0 },
    { name: "Medium", count: Number(counts.medium) || 0 },
    { name: "High", count: Number(counts.high) || 0 },
  ];
  if (!data.some((item) => item.count > 0)) {
    return <EmptyState icon={BarChart3} title="No severity data" body="Severity counts will appear after pothole records are saved." />;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="name" />
        <YAxis allowDecimals={false} />
        <Tooltip />
        <Bar dataKey="count" name="Potholes" fill="var(--color-primary)" radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function LiveDetection({ camera, setCamera, refresh }) {
  const [recordId, setRecordId] = useState("");
  const [lookup, setLookup] = useState({ loading: false, error: "", record: null });
  const [files, setFiles] = useState([]);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [gpsAccuracy, setGpsAccuracy] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [batchSummary, setBatchSummary] = useState("");
  const [deviceLocation, setDeviceLocation] = useState(false);
  const [locationMethod, setLocationMethod] = useState("");
  const [locationMessage, setLocationMessage] = useState("");
  const [streamError, setStreamError] = useState("");
  const lastSavedRefreshRef = useRef("");
  const streamRetryTimerRef = useRef(null);

  useEffect(() => () => window.clearTimeout(streamRetryTimerRef.current), []);

  function handleStreamLoad() {
    window.clearTimeout(streamRetryTimerRef.current);
    streamRetryTimerRef.current = null;
    setStreamError("");
  }

  function handleStreamError() {
    setStreamError("The live camera stream stopped or could not be reached. Reconnecting…");
    window.clearTimeout(streamRetryTimerRef.current);
    streamRetryTimerRef.current = window.setTimeout(() => {
      setCamera((current) => current.running
        ? { ...current, streamKey: current.streamKey + 1 }
        : current);
    }, 1500);
  }

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    apiFetch(`${API_BASE}/api/camera/status`, { signal: controller.signal })
      .then(async (response) => {
        const status = await response.json();
        if (!response.ok) {
          throw new Error(getApiErrorMessage(status, response.status, "Camera status request failed."));
        }
        if (!active) return;
        setCamera((current) => ({
          ...current,
          running: Boolean(status.camera?.running),
          status,
          error: status.camera?.running
            ? status.camera?.error || status.yolo_inference?.error || ""
            : "",
        }));
      })
      .catch((error) => {
        if (error.name !== "AbortError" && active) {
          setCamera((current) => ({ ...current, error: error.message || "Could not read camera status." }));
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [setCamera]);

  useEffect(() => {
    if (!camera.running) return undefined;
    const controller = new AbortController();
    let active = true;
    const pollCameraStatus = async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/camera/status`, {
          signal: controller.signal,
        });
        const status = await response.json();
        if (!response.ok) {
          throw new Error(getApiErrorMessage(status, response.status, "Camera status request failed."));
        }
        if (!active) return;
        setCamera((current) => ({
          ...current,
          running: Boolean(status.camera?.running),
          status,
          error: status.camera?.running
            ? status.camera?.error || status.yolo_inference?.error || ""
            : "",
        }));
        const savedAt = status.detection?.saved_at;
        if (savedAt && savedAt !== lastSavedRefreshRef.current) {
          lastSavedRefreshRef.current = savedAt;
          refresh({ silent: true, forceMap: true });
        }
      } catch (error) {
        if (error.name !== "AbortError" && active) {
          setCamera((current) => ({ ...current, error: error.message || "Could not read camera status." }));
        }
      }
    };
    pollCameraStatus();
    const interval = window.setInterval(pollCameraStatus, 1000);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(interval);
    };
  }, [camera.running, refresh, setCamera]);

  function selectImage(event) {
    const selectedFiles = Array.from(event.target.files || []);
    setFiles(selectedFiles);
    setAnalysis(null);
    setUploadError("");
    setBatchSummary("");
  }

  async function analyzeImage(event) {
    event.preventDefault();
    if (!files.length) return;
    setUploading(true);
    setAnalysis(null);
    setUploadError("");
    setBatchSummary("");
    try {
      const results = [];
      const failures = [];
      for (const imageFile of files) {
        const form = new FormData();
        form.append("file", imageFile);
        if (latitude.trim()) form.append("latitude", latitude.trim());
        if (longitude.trim()) form.append("longitude", longitude.trim());
        if (gpsAccuracy.trim()) form.append("gps_accuracy_m", gpsAccuracy.trim());
        if (deviceLocation) form.append("device_location", "true");
        try {
          const response = await apiFetch(`${API_BASE}/api/detections/image`, { method: "POST", body: form });
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(
              getApiErrorMessage(payload, response.status, "Image inference failed.")
            );
          }
          results.push(payload);
        } catch (failure) {
          failures.push(`${imageFile.name}: ${failure.message || "Upload failed."}`);
        }
      }
      if (!results.length) {
        throw new Error(failures.join("; ") || "No images could be analyzed.");
      }
      const saved = results.reduce((sum, item) => sum + (item.saved_detection_events || 0), 0);
      const saveFailures = results.reduce((sum, item) => sum + (item.save_failures || 0), 0);
      setAnalysis(results[results.length - 1]);
      setBatchSummary(
        `Processed ${results.length} of ${files.length} selected images; ` +
        `${saved} detection events saved` +
        (saveFailures ? `, ${saveFailures} saves unconfirmed` : "") +
        (failures.length ? `. Failed uploads: ${failures.join("; ")}` : ".")
      );
      if (saved > 0 || saveFailures > 0) {
        refresh({ silent: true, forceMap: true });
      }
    } catch (error) {
      setUploadError(error.message || "Image inference failed.");
    } finally {
      setUploading(false);
    }
  }

  async function controlCamera(action) {
    setCamera((c) => ({ ...c, loading: true, error: "" }));
    try {
      const response = await apiFetch(`${API_BASE}/api/camera/${action}`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok || payload.status === "error") {
        throw new Error(getApiErrorMessage(payload, response.status, "Camera command failed."));
      }
      setStreamError("");
      setCamera((c) => ({
        ...c,
        running: action === "start" || payload.status === "stopping",
        loading: false,
        error: "",
        streamKey: action === "start" ? c.streamKey + 1 : c.streamKey,
      }));
    } catch (error) {
      setCamera((c) => ({ ...c, loading: false, running: false, error: error.message }));
    }
  }
  const detection = camera.status?.detection || {};
  const gps = camera.status?.gps || {};
  const inferenceStatus = camera.status?.yolo_inference || {};
  const roadScene = camera.status?.road_scene || {};
  const gpsStatus = {
    ready_to_save_without_gps: "Detection found; it will be saved as an event without a mapped location because GPS is unavailable.",
    gps_accuracy_unavailable: "Detection found; it will be saved, but location accuracy is unavailable.",
    ready_to_save_low_accuracy: "Detection found; it will be saved, but GPS accuracy is over 100 m and will not be shown as a precise map marker.",
    cooldown: "Detection found; waiting for the save cooldown.",
    already_saved_for_current_detection: "This continuous detection has already been submitted; waiting until it leaves view before submitting another.",
    awaiting_backend_confirmation: "Detection submitted once; waiting for backend confirmation.",
    ready_to_save: "Detection found; waiting for backend confirmation.",
    partially_saved: "Some detections were saved; at least one save was not confirmed. See the backend error below.",
    save_failed: detection.save_error || "Detection found, but the backend did not confirm saving it. It will not be resubmitted until the detection clears.",
  }[detection.save_status];
  const gpsStatusLabel = {
    connected: "Available",
    low_accuracy: "Low accuracy",
    acquiring: "Acquiring location",
    permission_error: "Permission error",
    disconnected: "Unavailable",
    unavailable: "Unavailable",
    unknown: "Unavailable / not yet checked",
  }[gps.status] || "Unavailable / not yet checked";
  async function searchRecord(event) {
    event.preventDefault();
    if (!recordId.trim()) return;
    setLookup({ loading: true, error: "", record: null });
    try {
      const raw = recordId.trim();
      const detectionNumber = raw.match(/^(?:detection\s*#\s*|#)?(\d+)$/i)?.[1];
      const paths = detectionNumber
        ? [`/api/detections/${detectionNumber}`, `/api/potholes/${encodeURIComponent(raw)}`, `/api/detections/${encodeURIComponent(raw)}`]
        : [`/api/potholes/${encodeURIComponent(raw)}`, `/api/detections/${encodeURIComponent(raw)}`];
      for (const path of paths) {
        const response = await apiFetch(`${API_BASE}${path}`);
        const payload = await response.json();
        if (response.status === 404) continue;
        if (!response.ok) {
          throw new Error(
            getApiErrorMessage(payload, response.status, "Record lookup failed.")
          );
        }
        setLookup({ loading: false, error: "", record: payload.pothole || payload.detection });
        return;
      }
      setLookup({ loading: false, error: "No detection record found.", record: null });
    } catch (error) { setLookup({ loading: false, error: error.message || "Could not retrieve this record.", record: null }); }
  }
  return (
    <><div className="page-section-heading"><div><span className="section-eyebrow">FIELD OPERATIONS</span><h2>Capture and validate detections</h2><p>Run YOLO against images, videos, or the connected Windows camera agent.</p></div><span className="data-source-chip"><span />Detection tools</span></div><Panel title="Find a stored record" subtitle="Search by PH-### ID or simple detection number"><form className="filters" onSubmit={searchRecord}><label className="search"><Search size={16} /><input value={recordId} onChange={(e) => setRecordId(e.target.value)} placeholder="Pothole ID (PH-001) or Detection #" /></label><button type="submit" disabled={lookup.loading}>{lookup.loading ? "Searching…" : "Search"}</button></form>{lookup.error && <div className="detail-strip">{lookup.error}</div>}{lookup.record && <div className="lookup-card">{lookup.record.image_url || lookup.record.annotated_image_reference ? <img src={lookup.record.image_url || lookup.record.annotated_image_reference} alt="Stored pothole detection" /> : null}<div><strong>{lookup.record.event_id ? `Detection #${lookup.record.display_id}${lookup.record.pothole_id ? ` · ${lookup.record.pothole_id}` : ""}` : lookup.record.pothole_id ?? "ID unavailable"}</strong><span>{lookup.record.event_id && !lookup.record.pothole_id ? "No pothole record is linked to this detection." : `Pothole ID: ${lookup.record.pothole_id ?? "Unavailable"}`}</span><span>Confidence: {formatPercent(lookup.record.confidence_percent)}</span><span>GPS: {lookup.record.latitude ?? "—"}, {lookup.record.longitude ?? "—"}</span><span>Detected: {formatDate(lookup.record.last_seen_at || lookup.record.timestamp || lookup.record.created_at || lookup.record.first_detected_at)}</span></div></div>}</Panel><div className="two-column">
      <Panel title="Live Camera Detection" subtitle={`YOLO model: ${inferenceStatus.model_file_exists ? "available" : "not found"} at runs/pothole_yolo11n/weights/best.pt`}>
        <div className="camera-stage">
          {camera.running ? <img key={camera.streamKey} src={`${API_BASE}/api/camera/stream?session=${camera.streamKey}`} alt="Live YOLO annotated camera feed" onLoad={handleStreamLoad} onError={handleStreamError} /> : <div><Camera size={46} /><strong>Camera is not streaming</strong><span>Start the local camera agent to see real annotated frames.</span></div>}
        </div>
        {roadScene.status === "not_road" && <div className="alert"><AlertTriangle size={17} />{"Invalid Image! Please upload a valid road image containing a road scene."}</div>}
        {roadScene.status === "unavailable" && <div className="alert"><AlertTriangle size={17} />Road-scene validation is unavailable. Detection is paused until validation recovers.</div>}
        {(camera.error || streamError || detection.save_error || inferenceStatus.error || gps.error) && <div className="alert"><AlertTriangle size={17} />{camera.error || streamError || detection.save_error || inferenceStatus.error || gps.error}</div>}
        <div className="live-metrics">
          <article><span>Detections in latest frame</span><strong>{detection.count ?? 0}</strong></article>
          <article><span>Backend-confirmed events in latest episode</span><strong>{detection.saved_count ?? 0}</strong></article>
          <article><span>Latest confidence</span><strong>{detection.confidence == null ? "N/A" : `${(detection.confidence * 100).toFixed(2)}%`}</strong></article>
          <article><span>Latest detection</span><strong>{formatDate(detection.detected_at)}</strong></article>
          <article><span>Backend-confirmed save</span><strong>{formatDate(detection.saved_at)}</strong></article>
          <article><span>GPS coordinates</span><strong>{gps.latitude == null || gps.longitude == null ? "Unavailable" : `${Number(gps.latitude).toFixed(6)}, ${Number(gps.longitude).toFixed(6)}`}</strong></article>
          <article><span>GPS accuracy</span><strong>{gps.accuracy_m == null ? "Unavailable" : `${Number(gps.accuracy_m).toFixed(1)} m`}</strong></article>
        </div>
        <div className={`detail-strip gps-status ${gps.status === "low_accuracy" || gps.status === "permission_error" ? "warning" : ""}`}>
          GPS status: {gpsStatusLabel}
          {gps.accuracy_m == null ? "" : ` · Reported accuracy ${Number(gps.accuracy_m).toFixed(1)} m`}
          {gps.last_updated_at ? ` · Updated ${formatDate(gps.last_updated_at)}` : ""}
          {gpsStatus ? ` · ${gpsStatus}` : ""}
        </div>
        <div className="button-row"><button onClick={() => controlCamera("start")} disabled={camera.running || camera.loading}><Camera size={16} />{camera.loading ? "Starting..." : "Start Camera"}</button><button className="secondary" onClick={() => controlCamera("stop")} disabled={!camera.running || camera.loading}><X size={16} />{camera.loading ? "Stopping..." : "Stop Camera"}</button></div>
      </Panel>
      <Panel title="Image Upload Detection" subtitle="Runs the existing trained YOLO model on this uploaded image">
        <form className="image-upload-form" onSubmit={analyzeImage}>
          <label className="file-picker"><Upload size={22} /><span>{files.length ? `${files.length} image${files.length === 1 ? "" : "s"} selected` : "Choose one or more JPEG, PNG, WebP, or BMP images (max 10 MB each)"}</span><input type="file" accept="image/jpeg,image/png,image/webp,image/bmp" multiple onChange={selectImage} /></label>
          <div className="gps-input-grid">
            <label>Latitude (optional)<input type="number" min="-90" max="90" step="any" value={latitude} onChange={(event) => { setLatitude(event.target.value); setDeviceLocation(false); setLocationMethod("USER_SUPPLIED"); setGpsAccuracy(""); }} placeholder="e.g. 16.02" /></label>
            <label>Longitude (optional)<input type="number" min="-180" max="180" step="any" value={longitude} onChange={(event) => { setLongitude(event.target.value); setDeviceLocation(false); setLocationMethod("USER_SUPPLIED"); setGpsAccuracy(""); }} placeholder="e.g. 74.69" /></label>
            <label>GPS accuracy in metres (optional)<input type="number" min="0" step="any" value={gpsAccuracy} onChange={(event) => setGpsAccuracy(event.target.value)} placeholder="Leave blank if unknown" /></label>
          </div>
          <div className="button-row"><button className="secondary" type="button" onClick={() => { setDeviceLocation(false); setLocationMethod("USER_SUPPLIED"); setGpsAccuracy(""); setLocationMessage("Enter coordinates or select a point on the map. This location will remain unverified until an Admin confirms it."); }}><MapPin size={16} />Select location manually</button></div>
          <ReportLocationPicker
            latitude={latitude}
            longitude={longitude}
            gpsAccuracy={gpsAccuracy}
            locationMethod={locationMethod}
            onSelect={(point) => {
              setLatitude(String(point.latitude));
              setLongitude(String(point.longitude));
              setGpsAccuracy("");
              setDeviceLocation(false);
              setLocationMethod("USER_SUPPLIED");
              setLocationMessage("Manually selected coordinates are saved as unverified until an Admin confirms the location.");
            }}
          />
          <button className="secondary" type="button" onClick={() => {
            setLocationMessage("");
            if (!navigator.geolocation) { setLocationMessage("Browser geolocation is unavailable."); return; }
            navigator.geolocation.getCurrentPosition((position) => {
              setLatitude(String(position.coords.latitude)); setLongitude(String(position.coords.longitude)); setGpsAccuracy(String(position.coords.accuracy)); setDeviceLocation(true); setLocationMethod("DEVICE");
              setLocationMessage("Upload-time device location captured with your permission. This may differ from where the image was taken.");
            }, (failure) => {
              setDeviceLocation(false); setLocationMethod("");
              setLatitude(""); setLongitude(""); setGpsAccuracy("");
              setLocationMessage(failure.code === failure.PERMISSION_DENIED ? "Location permission was denied. You can still upload the image without GPS." : failure.code === failure.TIMEOUT ? "Location request timed out. You can retry or upload without GPS." : "Could not obtain device location. You can still upload without GPS.");
            }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
          }}><MapPin size={16} />Use current device location</button>
          {locationMessage && <div className="detail-strip" role="status">{locationMessage}</div>}
          <button type="submit" disabled={!files.length || uploading}>{uploading ? <Loader2 className="spin" size={16} /> : <Activity size={16} />}{uploading ? `Analyzing ${files.length} image${files.length === 1 ? "" : "s"}...` : "Run pothole detection"}</button>
        </form>
        <div className="explain">Images are checked for EXIF GPS and capture time. If no GPS is embedded, you may explicitly request your current device location; this is the upload-time location, not necessarily where the image was captured. Missing or imprecise GPS never prevents a pothole detection from being saved.</div>
        {uploadError && <div className="alert"><AlertTriangle size={17} />{uploadError}</div>}
        {analysis && <div className="upload-result">
          {batchSummary && <div className="detail-strip">{batchSummary}. The preview below shows the last successfully processed image.</div>}
          <div className="upload-result-heading"><strong>{analysis.detection_count} pothole detection{analysis.detection_count === 1 ? "" : "s"}</strong><span>{analysis.filename}</span></div>
          <img className="annotated-upload" src={`data:image/jpeg;base64,${analysis.annotated_image_base64}`} alt={`YOLO annotated result for ${analysis.filename}`} />
          {analysis.detections.length > 0 ? <ul className="upload-detections">{analysis.detections.map((detection, index) => <li key={`${detection.class_id}-${index}`}><strong>{detection.class_name}</strong><span>Confidence: {detection.confidence_percent}%</span></li>)}</ul> : <div className="detail-strip">YOLO returned zero detections for this image.</div>}
          <div className="detail-strip">{analysis.persistence_note}{analysis.saved_detection_events > 0 && ` Backend-confirmed saved events: ${analysis.saved_detection_events}.`}</div>
          <div className="detail-strip">Location: {analysis.location_verification_status || "UNVERIFIED"}{analysis.location_source ? ` via ${analysis.location_source}` : ""}{analysis.location_verification_reason ? ` · ${analysis.location_verification_reason}` : ""}{analysis.capture_timestamp ? ` · Image captured ${formatDate(analysis.capture_timestamp)}` : ""}</div>
          {analysis.save_failures > 0 && <div className="alert"><AlertTriangle size={17} />YOLO analysis succeeded, but MongoDB did not confirm {analysis.save_failures} detection save(s). The annotated result is shown above; check backend/database health before retrying.</div>}
          {analysis.persistence?.length > 0 && <ul className="upload-detections">{analysis.persistence.map((item) => <li key={item.event_id}><strong>{item.error ? "Save not confirmed" : item.message || (item.pothole_id ? `Saved as ${item.pothole_id}` : "Event saved; no mapped record")}</strong><span>{item.error || (item.linked_existing ? "Linked to an existing nearby record." : "Backend confirmed the event.")}</span></li>)}</ul>}
        </div>}
      </Panel>
      <VideoUpload refresh={refresh} latitude={latitude} longitude={longitude} gpsAccuracy={gpsAccuracy} />
    </div></>
  );
}

function VideoUpload({ refresh, latitude, longitude, gpsAccuracy }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    if (latitude.trim()) form.append("latitude", latitude.trim());
    if (longitude.trim()) form.append("longitude", longitude.trim());
    if (gpsAccuracy.trim()) form.append("gps_accuracy_m", gpsAccuracy.trim());
    try {
      const response = await apiFetch(`${API_BASE}/api/detections/video`, { method: "POST", body: form });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          getApiErrorMessage(payload, response.status, "Video detection failed.")
        );
      }
      setResult(payload);
      if (payload.detection_count) refresh({ silent: true, forceMap: true });
    } catch (err) {
      setError(err.message || "Video detection failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel title="Video Detection" subtitle="Validate road scenes, run YOLO on accepted frames, and review the annotated output.">
      <form className="image-upload-form" onSubmit={submit}>
        <label className="file-picker">
          <Upload size={22} />
          <span>{file?.name || "Choose MP4, MOV, WebM, or AVI (max 50 MB)"}
            <input
              type="file"
              accept="video/mp4,video/quicktime,video/webm,video/x-msvideo,.mp4,.mov,.webm,.avi"
              onChange={(event) => {
                setFile(event.target.files?.[0] || null);
                setResult(null);
                setError("");
              }}
            />
          </span>
        </label>
        <button disabled={!file || busy}>{busy ? "Analyzing video…" : "Analyze video"}</button>
      </form>
      {busy && <LoadingState label="Validating frames and generating the annotated video…" />}
      {error && <div className="alert" role="alert">{error}</div>}
      {result && (
        <div className="video-result">
          {result.annotated_video_url && (
            <video
              controls
              preload="metadata"
              playsInline
              crossOrigin="use-credentials"
              src={`${API_BASE}${result.annotated_video_url}`}
              aria-label="YOLO pothole detection results in the annotated video"
            >
              Your browser does not support MP4 video playback.
            </video>
          )}
          <div className="detail-strip">
            Processed {result.frames_processed ?? 0} frames ({result.road_frames ?? 0} validated segments); saved {result.detection_count ?? 0} unique detection events.
          </div>
          {result.results?.length > 0 ? (
            <div className="video-detection-results" aria-label="Video detection results">
              {result.results.map((item, index) => {
                const event = item.event || item;
                const confidence = item.confidence ?? event.confidence;
                const potholeId = item.pothole_id ?? item.pothole?.pothole_id ?? event.pothole_id;
                return (
                  <article key={item.event_id || event.event_id || index}>
                    <strong>Detection {index + 1}{potholeId ? ` · ${potholeId}` : ""}</strong>
                    <span>Confidence: {formatConfidence(confidence)}</span>
                    <span>GPS: {event.latitude == null || event.longitude == null ? "Unavailable" : `${Number(event.latitude).toFixed(6)}, ${Number(event.longitude).toFixed(6)}`}</span>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyState icon={Activity} title="No potholes detected" body="The validated road frames were processed, but the model found no potholes in this video." />
          )}
        </div>
      )}
    </Panel>
  );
}

function Reports({ filters }) {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [location, setLocation] = useState("");
  const [severity, setSeverity] = useState("all");
  const [printTimestamp, setPrintTimestamp] = useState("");
  const [exportError, setExportError] = useState("");
  const [scheduleError, setScheduleError] = useState("");
  const [scheduleSuccess, setScheduleSuccess] = useState("");
  const [scheduleFrequency, setScheduleFrequency] = useState("weekly");
  const [scheduleFormat, setScheduleFormat] = useState("pdf");
  const [schedules, setSchedules] = useState([]);
  const [scheduleLoading, setScheduleLoading] = useState(true);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [scheduleActionId, setScheduleActionId] = useState("");
  const [scheduleReload, setScheduleReload] = useState(0);
  const [reload, setReload] = useState(0);
  const [reportData, setReportData] = useState({
    query: "",
    reports: [],
    total: 0,
    statistics: null,
    truncated: false,
    error: "",
  });
  const invalidDateRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);
  const reportQuery = useMemo(() => {
    const params = new URLSearchParams({
      all_records: "true",
      include_statistics: "true",
    });
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (location.trim()) params.set("location", location.trim());
    if (severity !== "all") params.set("severity", severity);
    if (filters.search) params.set("search", filters.search);
    if (filters.verification !== "all") params.set("verification_status", filters.verification);
    if (filters.repair !== "all") params.set("repair_status", filters.repair);
    return params.toString();
  }, [dateFrom, dateTo, filters, location, severity]);
  const dateRangeLabel = !dateFrom && !dateTo
    ? "All available dates"
    : `${dateFrom || "Beginning"} – ${dateTo || "Present"}`;
  const activeFilters = [
    filters.search && `Search: ${filters.search}`,
    location.trim() && `Region / location: ${location.trim()}`,
    severity !== "all" && `Severity: ${severity}`,
    filters.verification !== "all" && `Verification: ${filters.verification.replaceAll("_", " ")}`,
    filters.repair !== "all" && `Repair: ${filters.repair.replaceAll("_", " ")}`,
  ].filter(Boolean);

  useEffect(() => {
    if (invalidDateRange) return undefined;
    const controller = new AbortController();
    let active = true;
    async function loadReports() {
      try {
        const response = await apiFetch(`${API_BASE}/api/potholes?${reportQuery}`, { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load reports."));
        if (!Array.isArray(payload.potholes)) throw new Error("Invalid /api/potholes response.");
        if (active) {
          setReportData({
            query: reportQuery,
            reports: payload.potholes,
            total: payload.total ?? payload.potholes.length,
            statistics: payload.statistics || null,
            truncated: Boolean(payload.truncated),
            error: "",
          });
        }
      } catch (error) {
        if (error.name !== "AbortError" && active) {
          setReportData({
            query: reportQuery,
            reports: [],
            total: 0,
            statistics: null,
            truncated: false,
            error: error.message || "Could not load reports.",
          });
        }
      }
    }
    loadReports();
    return () => {
      active = false;
      controller.abort();
    };
  }, [invalidDateRange, reload, reportQuery]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function loadSchedules() {
      try {
        const response = await apiFetch(`${API_BASE}/api/reports/schedules`, { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load scheduled reports."));
        if (!Array.isArray(payload.schedules)) throw new Error("Invalid scheduled reports response.");
        if (active) {
          setSchedules(payload.schedules);
          setScheduleError("");
        }
      } catch (error) {
        if (error.name !== "AbortError" && active) {
          setScheduleError(error.message || "Could not load scheduled reports.");
        }
      } finally {
        if (active) setScheduleLoading(false);
      }
    }
    loadSchedules();
    return () => {
      active = false;
      controller.abort();
    };
  }, [scheduleReload]);

  const isCurrentQuery = !invalidDateRange && reportData.query === reportQuery;
  const loading = !invalidDateRange && !isCurrentQuery;
  const reports = isCurrentQuery ? reportData.reports : [];
  const statistics = isCurrentQuery ? reportData.statistics : null;
  const reportError = invalidDateRange
    ? "Start date must be on or before end date."
    : isCurrentQuery ? reportData.error : "";

  async function downloadExport(format) {
    setExportError("");
    try {
      const response = await apiFetch(`${API_BASE}/api/reports/export.${format}?${reportQuery}`);
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(getApiErrorMessage(payload, response.status, "Could not export records."));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `roadwatch-potholes.${format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(error.message || `Could not export ${format.toUpperCase()} records.`);
    }
  }

  function currentScheduleFilters() {
    return {
      ...(dateFrom ? { date_from: dateFrom } : {}),
      ...(dateTo ? { date_to: dateTo } : {}),
      ...(location.trim() ? { location: location.trim() } : {}),
      ...(severity !== "all" ? { severity } : {}),
      ...(filters.search ? { search: filters.search } : {}),
      ...(filters.verification !== "all" ? { verification_status: filters.verification } : {}),
      ...(filters.repair !== "all" ? { repair_status: filters.repair } : {}),
    };
  }

  async function createSchedule() {
    setScheduleSaving(true);
    setScheduleError("");
    setScheduleSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/reports/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          frequency: scheduleFrequency,
          format: scheduleFormat,
          ...currentScheduleFilters(),
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not create scheduled report."));
      setScheduleSuccess(payload.message || "Scheduled report created.");
      setScheduleReload((value) => value + 1);
    } catch (error) {
      setScheduleError(error.message || "Could not create scheduled report.");
    } finally {
      setScheduleSaving(false);
    }
  }

  async function changeSchedule(schedule) {
    setScheduleActionId(schedule.id);
    setScheduleError("");
    setScheduleSuccess("");
    try {
      const response = schedule.status === "active"
        ? await apiFetch(`${API_BASE}/api/reports/schedules/${encodeURIComponent(schedule.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "pause" }),
        })
        : await apiFetch(`${API_BASE}/api/reports/schedules/${encodeURIComponent(schedule.id)}`, { method: "DELETE" });
      const payload = response.status === 204 ? {} : await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update scheduled report."));
      setScheduleSuccess(payload.message || "Scheduled report updated.");
      setScheduleReload((value) => value + 1);
    } catch (error) {
      setScheduleError(error.message || "Could not update scheduled report.");
    } finally {
      setScheduleActionId("");
    }
  }

  async function resumeSchedule(schedule) {
    setScheduleActionId(schedule.id);
    setScheduleError("");
    setScheduleSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/reports/schedules/${encodeURIComponent(schedule.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "resume" }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not resume scheduled report."));
      setScheduleSuccess(payload.message || "Scheduled report resumed.");
      setScheduleReload((value) => value + 1);
    } catch (error) {
      setScheduleError(error.message || "Could not resume scheduled report.");
    } finally {
      setScheduleActionId("");
    }
  }

  async function downloadScheduledRun(run) {
    setScheduleError("");
    try {
      const response = await apiFetch(`${API_BASE}${run.download_url}`);
      if (!response.ok) {
        const payload = await response.json();
        throw new Error(getApiErrorMessage(payload, response.status, "Could not download scheduled report."));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `roadwatch-scheduled-report.${run.format}`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setScheduleError(error.message || "Could not download scheduled report.");
    }
  }

  function printReport() {
    setPrintTimestamp(new Date().toLocaleString());
    window.setTimeout(() => window.print(), 0);
  }

  const verificationCounts = statistics?.verification || {};
  const repairCounts = statistics?.repair || {};
  const controlsDisabled = loading || Boolean(reportError);
  return (
    <div className="reports-page">
      <div className="page-section-heading reports-screen-only"><div><span className="section-eyebrow">DATA DELIVERY</span><h2>Reports and exports</h2><p>Filter saved MongoDB reports, review their status, and print or export the filtered results.</p></div><span className="data-source-chip"><span />Authorized data</span></div>
      <Panel title="Report criteria" subtitle="Dates are interpreted in UTC and filter by the report creation timestamp stored in MongoDB.">
        <div className="report-controls reports-screen-only">
          <label>Start date<input type="date" value={dateFrom} max={dateTo || undefined} aria-invalid={invalidDateRange} onChange={(event) => setDateFrom(event.target.value)} /></label>
          <label>End date<input type="date" value={dateTo} min={dateFrom || undefined} aria-invalid={invalidDateRange} onChange={(event) => setDateTo(event.target.value)} /></label>
          <label>Region or location<input type="search" value={location} maxLength={120} placeholder="Filter by region, road, or address" onChange={(event) => setLocation(event.target.value)} /></label>
          <label>Severity<select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="all">All severities</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select></label>
        </div>
        {invalidDateRange && <div className="alert" role="alert">Start date must be on or before end date.</div>}
        <div className="report-grid reports-screen-only">
          <button className="download-card" type="button" disabled={controlsDisabled} onClick={() => downloadExport("csv")}><Download size={22} /><strong>CSV export</strong><span>Download reports matching this date range and the active filters.</span></button>
          <button className="download-card" type="button" disabled={controlsDisabled} onClick={() => downloadExport("pdf")}><FileText size={22} /><strong>PDF report</strong><span>Download a formatted PDF with summary statistics and report details.</span></button>
          <button className="download-card" type="button" disabled={controlsDisabled} onClick={() => downloadExport("xlsx")}><Download size={22} /><strong>Excel workbook</strong><span>Download a filter-matched .xlsx workbook with summary and report sheets.</span></button>
          <button className="download-card" type="button" disabled={controlsDisabled} onClick={printReport}><Printer size={22} /><strong>Printable report</strong><span>Print this filtered report or save it as a PDF.</span></button>
          <div className="download-card muted"><SlidersHorizontal size={22} /><strong>Active filters</strong><span>{activeFilters.length ? activeFilters.join(" · ") : "No additional filters"}</span></div>
        </div>
      </Panel>
      {exportError && <div className="alert reports-screen-only" role="alert">{exportError}</div>}
      {reportError && <div className="alert reports-screen-only" role="alert">{reportError}{isCurrentQuery && <button type="button" onClick={() => setReload((value) => value + 1)}>Retry</button>}</div>}
      <Panel title="Scheduled reports" subtitle="The backend generates each report from the latest matching MongoDB records. Files remain available for the configured retention period.">
        <div className="scheduled-report-create reports-screen-only">
          <label>Frequency<select value={scheduleFrequency} onChange={(event) => setScheduleFrequency(event.target.value)}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
          <label>File format<select value={scheduleFormat} onChange={(event) => setScheduleFormat(event.target.value)}><option value="pdf">PDF</option><option value="xlsx">Excel (.xlsx)</option></select></label>
          <button type="button" disabled={controlsDisabled || scheduleSaving} onClick={createSchedule}><Calendar size={16} />{scheduleSaving ? "Creating…" : "Schedule filtered report"}</button>
          <button type="button" className="secondary schedule-refresh-button" disabled={scheduleLoading} onClick={() => setScheduleReload((value) => value + 1)}>Refresh status</button>
          <span>Current date, location, severity, and status filters will be saved with this schedule.</span>
        </div>
        {scheduleError && <div className="alert reports-screen-only" role="alert">{scheduleError}</div>}
        {scheduleSuccess && <div className="alert success reports-screen-only" role="status">{scheduleSuccess}</div>}
        {scheduleLoading ? <LoadingState label="Loading scheduled reports…" /> : schedules.length ? (
          <div className="scheduled-report-list">
            {schedules.map((schedule) => (
              <article key={schedule.id} className="scheduled-report-card">
                <div className="scheduled-report-summary">
                  <strong>{schedule.frequency} · {schedule.format.toUpperCase()}</strong>
                  <span>Status: {schedule.status}</span>
                  <span>Next run: {schedule.next_run_at ? formatDate(schedule.next_run_at) : "Not scheduled"}</span>
                  {schedule.last_run_at && <span>Last run: {formatDate(schedule.last_run_at)} · {schedule.last_run_status || "unknown"}</span>}
                  {schedule.last_error && <span className="scheduled-report-error">{schedule.last_error}</span>}
                  <span>Filters: {Object.entries(schedule.filters || {}).map(([key, value]) => `${key.replaceAll("_", " ")}: ${String(value)}`).join(" · ") || "None"}</span>
                </div>
                {schedule.status !== "cancelled" && (
                  <div className="scheduled-report-actions reports-screen-only">
                    {schedule.status === "active" ? (
                      <button type="button" className="secondary" disabled={scheduleActionId === schedule.id} onClick={() => changeSchedule(schedule)}><Pause size={14} />Pause</button>
                    ) : schedule.status === "paused" ? (
                      <button type="button" className="secondary" disabled={scheduleActionId === schedule.id} onClick={() => resumeSchedule(schedule)}><Play size={14} />Resume</button>
                    ) : <span>Currently generating…</span>}
                    <button type="button" className="secondary danger-button" disabled={scheduleActionId === schedule.id} onClick={() => changeSchedule({ ...schedule, status: "paused" })}><Trash2 size={14} />Cancel</button>
                  </div>
                )}
                {schedule.runs?.length > 0 && <div className="scheduled-report-runs"><strong>Recent files</strong>{schedule.runs.map((run) => (
                  <div key={run.id}><span>{formatDate(run.created_at)} · {run.record_count} records · {run.status}</span>{run.download_url && <button type="button" className="secondary reports-screen-only" onClick={() => downloadScheduledRun(run)}><Download size={14} />Download {run.format.toUpperCase()}</button>}</div>
                ))}</div>}
              </article>
            ))}
          </div>
        ) : <EmptyState icon={Calendar} title="No scheduled reports" body="Create a daily, weekly, or monthly report schedule using the filters above." />}
      </Panel>
      <section className="reports-print-area">
        <header className="reports-print-heading">
          <span className="section-eyebrow">ROADWATCH AI · DATA REPORT</span>
          <h1>Pothole Reports</h1>
          <p>Report creation date (UTC): {dateRangeLabel}</p>
          {activeFilters.length > 0 && <p>Additional filters: {activeFilters.join(" · ")}</p>}
          <p>Generated: {printTimestamp || "Generated when printing"}</p>
        </header>
        <div className="report-stat-grid">
          <article><span>Total reports</span><strong>{isCurrentQuery ? reportData.total.toLocaleString() : "—"}</strong></article>
          <article><span>Verified</span><strong>{isCurrentQuery ? (verificationCounts.verified ?? 0).toLocaleString() : "—"}</strong></article>
          <article><span>Pending verification</span><strong>{isCurrentQuery ? (verificationCounts.pending ?? 0).toLocaleString() : "—"}</strong></article>
          <article><span>Rejected</span><strong>{isCurrentQuery ? (verificationCounts.rejected ?? 0).toLocaleString() : "—"}</strong></article>
          <article><span>Under maintenance</span><strong>{isCurrentQuery ? (repairCounts.under_maintenance ?? 0).toLocaleString() : "—"}</strong></article>
          <article><span>Repaired</span><strong>{isCurrentQuery ? (repairCounts.repaired ?? 0).toLocaleString() : "—"}</strong></article>
        </div>
        {reportData.truncated && isCurrentQuery && <p className="report-truncation-note">The table contains the first {reports.length.toLocaleString()} of {reportData.total.toLocaleString()} matching reports. Statistics reflect the full filtered result set.</p>}
        {loading ? <LoadingState label="Loading filtered reports…" /> : reportError ? null : reports.length ? (
          <div className="table-wrap">
            <table className="report-table">
              <thead><tr><th>Report ID</th><th>Created (UTC)</th><th>Location</th><th>Source</th><th>Severity</th><th>Detection status</th><th>Repair status</th><th>Coordinates</th></tr></thead>
              <tbody>{reports.map((report) => <tr key={report._id || report.pothole_id}>
                <td>{report.pothole_id || "ID unavailable"}</td>
                <td>{formatReportDate(report.created_at)}</td>
                <td>{report.location_name || report.road_name || report.region || (typeof report.location === "string" ? report.location : report.location?.name || report.location?.address || report.location?.region) || "Unavailable"}</td>
                <td>{detectionSourceLabel(report.source)}</td>
                <td>{report.severity || "unknown"}{report.confidence_percent != null ? ` · ${formatConfidence(report.confidence_percent)}` : ""}</td>
                <td>{String(report.verification_status || "unknown").replaceAll("_", " ")}</td>
                <td>{String(report.repair_status || "unknown").replaceAll("_", " ")}</td>
                <td>{validCoordinate(report) ? `${report.latitude}, ${report.longitude}` : "Unavailable"}</td>
              </tr>)}</tbody>
            </table>
          </div>
        ) : !reportError && <EmptyState icon={FileText} title="No reports in this date range" body="Choose another date range or clear the dates to view all reports available to your account." />}
      </section>
    </div>
  );
}

function DetectionMonitoring({ isAdmin = false }) {
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    apiFetch(`${API_BASE}/api/detections?page=${page}&page_size=${PAGE_SIZE}`, { signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load detection events."));
        if (!Array.isArray(payload.detections)) throw new Error("Invalid /api/detections response.");
        if (active) {
          setEvents(payload.detections);
          setTotal(payload.total ?? payload.detections.length);
          setError("");
        }
      })
      .catch((failure) => {
        if (failure.name !== "AbortError" && active) setError(failure.message || "Could not load detection events.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [page, reload]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">{isAdmin ? "DETECTION OPERATIONS" : "MY ACTIVITY"}</span><h2>{isAdmin ? "Detection monitoring" : "My detection history"}</h2><p>{isAdmin ? "Review saved image-upload, video-upload, and live-camera events across all accounts." : "Review only your saved image-upload, video-upload, and live-camera events."} Evidence and GPS verification are shown when available.</p></div><span className="data-source-chip"><span />MongoDB detection events</span></div>
    {error && <div className="alert" role="alert">{error}<button onClick={() => setReload((value) => value + 1)}>Retry</button></div>}
    <Panel title={isAdmin ? "All Detection Events" : "My Detection Events"} subtitle={isAdmin ? `${total.toLocaleString()} events across all registered users` : `${total.toLocaleString()} events submitted by your account`}>
      {loading ? <LoadingState label="Loading detection events…" /> : events.length ? <><DetectionEventTable events={events} total={total} isAdmin={isAdmin} /><div className="pagination"><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>Next</button></div></> : <EmptyState icon={Activity} title="No saved detection events" body="Image, video, and live-camera events will appear after the backend confirms they were saved." />}
    </Panel>
  </>;
}

function PotholeReviewDialog({ record, onClose, onSaved }) {
  const [currentRecord, setCurrentRecord] = useState(record);
  const [rejectionReason, setRejectionReason] = useState(record.rejection_reason || "");
  const [repairStatus, setRepairStatus] = useState(record.repair_status || "unknown");
  const [repairNotes, setRepairNotes] = useState("");
  const [locationNotes, setLocationNotes] = useState(record.location_verification_notes || "");
  const [locationPoint, setLocationPoint] = useState(validCoordinate(record)
    ? { latitude: Number(record.latitude), longitude: Number(record.longitude) }
    : null);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const id = currentRecord.pothole_id ?? currentRecord._id;
  const evidence = currentRecord.annotated_image_reference || currentRecord.image_reference;

  async function saveVerification(status) {
    if (status === "rejected" && !rejectionReason.trim()) {
      setError("Enter a reason before rejecting this report.");
      return;
    }
    setSaving(status);
    setError("");
    setSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/potholes/${encodeURIComponent(id)}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, rejection_reason: status === "rejected" ? rejectionReason.trim() : null }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update verification status."));
      const updated = payload.pothole || payload;
      setCurrentRecord(updated);
      setSuccess(status === "verified" ? "Pothole marked Verified." : "Pothole report rejected.");
      await onSaved();
    } catch (failure) {
      setError(failure.message || "Could not update verification status.");
    } finally {
      setSaving("");
    }
  }

  async function saveRepairStatus() {
    if (repairStatus === "unknown") {
      setError("Select a repair status before saving.");
      return;
    }
    setSaving("repair");
    setError("");
    setSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/potholes/${encodeURIComponent(id)}/repair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: repairStatus, notes: repairNotes.trim() || null }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update repair status."));
      const updated = payload.pothole || payload;
      setCurrentRecord(updated);
      setRepairStatus(updated.repair_status || repairStatus);
      setSuccess("Repair progress updated.");
      await onSaved();
    } catch (failure) {
      setError(failure.message || "Could not update repair status.");
    } finally {
      setSaving("");
    }
  }

  async function saveLocation() {
    if (!locationPoint || !validCoordinate(locationPoint)) {
      setError("Select valid coordinates on the map before confirming the location.");
      return;
    }
    setSaving("location");
    setError("");
    setSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/potholes/${encodeURIComponent(id)}/verify-location`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...locationPoint, notes: locationNotes.trim() || null }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not verify the pothole location."));
      const updated = payload.pothole || payload;
      setCurrentRecord(updated);
      setLocationPoint({ latitude: Number(updated.latitude), longitude: Number(updated.longitude) });
      setSuccess("Location verified and audit history saved.");
      await onSaved();
    } catch (failure) {
      setError(failure.message || "Could not verify the pothole location.");
    } finally {
      setSaving("");
    }
  }

  const statusHistory = [...(currentRecord.audit_history || [])].reverse();
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose(); }}>
    <section className="location-dialog pothole-review-dialog" role="dialog" aria-modal="true" aria-labelledby="pothole-review-title">
      <div className="dialog-heading">
        <div><h3 id="pothole-review-title">Review Pothole · {currentRecord.pothole_id ?? currentRecord._id ?? "ID unavailable"}</h3><p>Verify the report and update repair progress. Status changes are saved with an Admin audit entry.</p></div>
        <button className="secondary" type="button" onClick={onClose} disabled={Boolean(saving)}>Close</button>
      </div>
      <div className="pothole-review-details">
        <div className="review-evidence"><EvidenceImage reference={evidence} alt={`Evidence for pothole ${currentRecord.pothole_id ?? id}`} className="review-evidence-image" /></div>
        <div className="review-facts">
          <strong>Detection details</strong>
          <span>Confidence: {formatPercent(currentRecord.confidence_percent ?? currentRecord.confidence_normalized)}</span>
          <span>Source: {currentRecord.source || "Source unavailable"}</span>
          <span>Detected: {formatDate(currentRecord.last_seen_at || currentRecord.created_at || currentRecord.first_detected_at)}</span>
          <span>Coordinates: {validCoordinate(currentRecord) ? `${currentRecord.latitude}, ${currentRecord.longitude}` : "Unavailable"}</span>
          <span>GPS accuracy: {currentRecord.gps_accuracy_m == null ? "Unavailable" : `${Number(currentRecord.gps_accuracy_m).toFixed(1)} m`}</span>
          <span>GPS verification: {currentRecord.location_verification_status || "UNVERIFIED"}</span>
        </div>
      </div>
      <div className="pothole-status-grid">
        <article><span>Verification status</span><StatusBadge value={currentRecord.verification_status || "unknown"} /></article>
        <article><span>Repair status</span><StatusBadge value={currentRecord.repair_status || "unknown"} /></article>
        <article><span>GPS location status</span><StatusBadge value={currentRecord.location_verification_status || "UNVERIFIED"} /></article>
        {currentRecord.verification_status === "verified" && <article className="verification-audit"><span>Verified by</span><strong>{currentRecord.verified_by_display || currentRecord.verified_by || "Unavailable"}</strong><span>{formatDate(currentRecord.verified_at)}</span></article>}
        {currentRecord.verification_status === "rejected" && <article className="verification-audit"><span>Rejection reason</span><strong>{currentRecord.rejection_reason || "No reason recorded"}</strong><span>{currentRecord.rejected_by_display || currentRecord.rejected_by || "Unavailable"} · {formatDate(currentRecord.rejected_at)}</span></article>}
        {currentRecord.location_verification_status === "VERIFIED" && <article className="verification-audit"><span>Location verified by</span><strong>{currentRecord.location_verified_by_display || currentRecord.location_verified_by_name || currentRecord.location_verified_by || "Unavailable"}</strong><span>{formatDate(currentRecord.location_verified_at)}</span></article>}
      </div>
      {error && <div className="alert" role="alert">{error}</div>}
      {success && <div className="alert success" role="status">{success}</div>}
      <section className="location-review-controls">
        <h4>Confirm or correct submitted location</h4>
        <p>Click the map to select the verified point. The saved coordinates are not marked verified until you confirm below.</p>
        <LocationPickerMap point={locationPoint} setPoint={setLocationPoint} heightClass="verification-map" />
        {locationPoint && <div className="detail-strip">Selected coordinates: {locationPoint.latitude.toFixed(6)}, {locationPoint.longitude.toFixed(6)}</div>}
        <label>Location remarks (optional)<textarea value={locationNotes} onChange={(event) => setLocationNotes(event.target.value)} maxLength={500} placeholder="Optional verification details" /></label>
        <div className="button-row">
          {validCoordinate(currentRecord) && <button className="secondary" type="button" disabled={Boolean(saving)} onClick={() => setLocationPoint({ latitude: Number(currentRecord.latitude), longitude: Number(currentRecord.longitude) })}>Use submitted coordinates</button>}
          <button type="button" disabled={Boolean(saving) || !locationPoint} onClick={saveLocation}>{saving === "location" ? "Saving…" : "Confirm location"}</button>
        </div>
      </section>
      <div className="review-actions">
        <section>
          <h4>Verification decision</h4>
          <label>Rejection reason<textarea value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} maxLength={500} placeholder="Required when rejecting an invalid report" /></label>
          <div className="button-row"><button type="button" disabled={Boolean(saving)} onClick={() => saveVerification("verified")}>{saving === "verified" ? "Saving…" : "Mark Verified"}</button><button type="button" className="danger-button" disabled={Boolean(saving) || !rejectionReason.trim()} onClick={() => saveVerification("rejected")}>{saving === "rejected" ? "Saving…" : "Reject report"}</button></div>
        </section>
        <section>
          <h4>Repair progress</h4>
          <label>Repair status<select value={repairStatus} onChange={(event) => setRepairStatus(event.target.value)}><option value="unknown" disabled>Unknown (legacy record)</option><option value="not_started">Not Started</option><option value="under_maintenance">Under Maintenance</option><option value="repaired">Repaired</option></select></label>
          <label>Administrator remarks (optional)<textarea value={repairNotes} onChange={(event) => setRepairNotes(event.target.value)} maxLength={500} placeholder="Add a note about this repair update" /></label>
          <button type="button" disabled={Boolean(saving) || repairStatus === "unknown"} onClick={saveRepairStatus}>{saving === "repair" ? "Saving…" : "Update repair status"}</button>
        </section>
      </div>
      <details className="status-audit">
        <summary>Status audit history</summary>
        {statusHistory.length ? <ul>{statusHistory.map((entry, index) => <li key={`${entry.timestamp || index}-${index}`}><strong>{String(entry.action || "update").replaceAll("_", " ")}</strong><span>{entry.administrator_name || entry.administrator_id || entry.submitted_by_id || "Actor unavailable"} · {formatDate(entry.timestamp)}</span>{entry.previous && <span>Previous: {Object.entries(entry.previous).filter(([key]) => key.endsWith("_status")).map(([key, value]) => `${key.replaceAll("_", " ")} ${value || "unknown"}`).join(" · ") || "—"}</span>}{entry.new && <span>Updated: {Object.entries(entry.new).filter(([key]) => key.endsWith("_status") || key === "latitude" || key === "longitude" || key === "notes").map(([key, value]) => `${key.replaceAll("_", " ")} ${value ?? "—"}`).join(" · ") || "—"}</span>}</li>)}</ul> : <p>No status changes have been recorded.</p>}
      </details>
    </section>
  </div>;
}

function PotholeTracking({ records, total, page, setPage, pageSize }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">MY REPORTS</span><h2>My Pothole Tracking</h2><p>Follow verification, location review, repair progress, administrator remarks, and the full activity timeline for your reports.</p></div><span className="data-source-chip"><span />Your account only</span></div>
    {!records.length ? <EmptyState icon={ClipboardList} title="No pothole reports yet" body="Reports created through image, video, or live detection will appear here after MongoDB confirms them." /> : <div className="tracking-cards">
      {records.map((record) => {
        const history = record.audit_history || [];
        return <article className="tracking-card" key={recordKey(record)}>
          <div className="tracking-card-heading">
            <div><span className="section-eyebrow">POTHOLE REPORT</span><h3>{record.pothole_id ?? "ID unavailable"}</h3><span>Reported {formatDate(record.created_at || record.first_detected_at)}</span></div>
            <div className="tracking-card-badges"><StatusBadge value={record.verification_status || "unknown"} /><StatusBadge value={record.repair_status || "unknown"} /></div>
          </div>
          <div className="tracking-card-facts">
              <strong>Verification</strong>
              <span>{record.verification_status === "verified" ? `Verified by ${record.verified_by_display || record.verified_by || "Unavailable"} · ${formatDate(record.verified_at)}` : record.verification_status === "rejected" ? `Rejected by ${record.rejected_by_display || record.rejected_by || "Unavailable"} · ${record.rejection_reason || "No reason recorded"}` : "Pending Admin verification"}</span>
              <strong>Location review</strong>
              <span>{record.location_verification_status || "UNVERIFIED"}{record.location_verified_by_display ? ` · reviewed by ${record.location_verified_by_display}` : ""}{record.location_verified_at ? ` · ${formatDate(record.location_verified_at)}` : ""}</span>
              <strong>GPS accuracy</strong>
              <span>{record.gps_accuracy_m == null ? "Unavailable" : `${Number(record.gps_accuracy_m).toFixed(1)} m`}</span>
              <strong>Repair progress</strong>
              <span>{record.repair_status === "unknown" ? "No repair status recorded" : record.repair_status === "not_started" && !record.repair_updated_at ? "Not Started" : `${String(record.repair_status || "not_started").replaceAll("_", " ")} · updated by ${record.repair_updated_by_display || record.repair_updated_by || "Unavailable"} · ${formatDate(record.repair_updated_at)}`}</span>
              {record.notes && <><strong>Administrator remarks</strong><span>{record.notes}</span></>}
              {record.location_verification_notes && <><strong>Location remarks</strong><span>{record.location_verification_notes}</span></>}
              {record.annotated_image_reference || record.image_reference ? <EvidenceImage reference={record.annotated_image_reference || record.image_reference} alt={`Evidence for ${record.pothole_id}`} /> : <span>Evidence unavailable</span>}
          </div>
          <details className="status-audit tracking-timeline" open>
            <summary>Complete status timeline</summary>
            {history.length ? <ol>{history.map((entry, index) => <li key={`${entry.timestamp || index}-${index}`}>
              <strong>{String(entry.action || "update").replaceAll("_", " ")}</strong>
              <span>{entry.administrator_name || entry.administrator_id ? `Admin ${entry.administrator_name || entry.administrator_id}` : entry.submitted_by_id ? "Submitted by you" : "System"} · {formatDate(entry.timestamp)}</span>
              {entry.previous && <span>Previous: {formatAuditValues(entry.previous)}</span>}
              {entry.new && <span>Updated: {formatAuditValues(entry.new)}</span>}
            </li>)}</ol> : <p>No audit history was stored for this record.</p>}
          </details>
        </article>;
      })}
    </div>}
    {total > pageSize && <div className="pagination"><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}>Next</button></div>}
  </>;
}

function useNotificationData(enabled) {
  const [state, setState] = useState({ notifications: [], total: 0, unreadCount: 0, loading: true, error: "" });
  const controllerRef = useRef(null);
  const refresh = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const response = await apiFetch(`${API_BASE}/api/notifications?limit=100`, { signal: controller.signal });
      const payload = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load notifications."));
      if (Array.isArray(payload.notifications)) {
        setState({
          notifications: payload.notifications,
          total: payload.total ?? payload.notifications.length,
          unreadCount: payload.unread_count ?? 0,
          loading: false,
          error: "",
        });
      } else {
        throw new Error("Invalid notifications response.");
      }
    } catch (failure) {
      if (failure.name !== "AbortError") {
        setState((current) => ({ ...current, loading: false, error: failure.message || "Could not load notifications." }));
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    let timer;
    const poll = async () => {
      await refresh();
      if (active) timer = window.setTimeout(poll, REFRESH_SECONDS * 1000);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
      controllerRef.current?.abort();
    };
  }, [enabled, refresh]);

  const markRead = useCallback(async (notificationId) => {
    const response = await apiFetch(`${API_BASE}/api/notifications/${encodeURIComponent(notificationId)}/read`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not mark notification as read."));
    await refresh();
  }, [refresh]);

  const markAllRead = useCallback(async () => {
    const response = await apiFetch(`${API_BASE}/api/notifications/read-all`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not mark notifications as read."));
    await refresh();
  }, [refresh]);

  return { ...state, refresh, markRead, markAllRead };
}

function NotificationsPage({ data }) {
  const [actionError, setActionError] = useState("");
  const [saving, setSaving] = useState("");
  async function markRead(notificationId) {
    setSaving(notificationId);
    setActionError("");
    try {
      await data.markRead(notificationId);
    } catch (failure) {
      setActionError(failure.message || "Could not mark notification as read.");
    } finally {
      setSaving("");
    }
  }
  async function markAllRead() {
    setSaving("all");
    setActionError("");
    try {
      await data.markAllRead();
    } catch (failure) {
      setActionError(failure.message || "Could not mark notifications as read.");
    } finally {
      setSaving("");
    }
  }
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">ACCOUNT UPDATES</span><h2>Notifications</h2><p>Persistent account and pothole workflow updates. Only notifications for your signed-in account are shown.</p></div><span className="data-source-chip"><span />MongoDB notifications</span></div>
    <div className="metric-grid"><article className="metric-card"><span>Unread notifications</span><strong>{data.unreadCount}</strong></article><article className="metric-card"><span>Total notifications</span><strong>{data.total}</strong></article></div>
    {data.error && <div className="alert" role="alert">{data.error}<button type="button" onClick={data.refresh}>Retry</button></div>}
    {actionError && <div className="alert" role="alert">{actionError}</div>}
    <Panel title="Recent updates" subtitle="Admin access, location verification, report decisions, and repair progress">
      {data.loading ? <LoadingState label="Loading notifications…" /> : data.notifications.length ? <>
        <div className="notification-toolbar"><button className="secondary" type="button" disabled={!data.unreadCount || Boolean(saving)} onClick={markAllRead}>{saving === "all" ? "Saving…" : "Mark all as read"}</button></div>
        <div className="notification-list">{data.notifications.map((notification) => <article className={`notification-card ${notification.is_read ? "read" : "unread"}`} key={notification.id || notification._id}>
          <div className="notification-card-heading"><StatusBadge value={notification.action || notification.type} /><span>{formatDate(notification.created_at)}</span></div>
          <p>{notification.message}</p>
          {notification.pothole_id && <strong>Pothole {notification.pothole_id}</strong>}
          {notification.administrator_name && <span>By Admin {notification.administrator_name}</span>}
          {!notification.is_read && <button className="secondary" type="button" disabled={Boolean(saving)} onClick={() => markRead(notification.id || notification._id)}>{saving === (notification.id || notification._id) ? "Saving…" : "Mark as read"}</button>}
          {notification.is_read && <span className="notification-read-label">Read {formatDate(notification.read_at)}</span>}
        </article>)}</div>
      </> : <EmptyState icon={Bell} title="No notifications" body="Admin decisions and pothole status updates will appear here when they are saved." />}
    </Panel>
  </>;
}

function MyProfile({ user }) {
  const fields = [
    ["Full name", user.full_name],
    ["Email", user.email],
    ["Account role", user.role === "administrator" ? "Administrator" : user.role === "admin" ? "Admin" : "User"],
    ["Account ID", user.id || "Unavailable"],
    ["Registration date", formatDate(user.created_at)],
  ];
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">ACCOUNT</span><h2>My Profile</h2><p>Your registration information and current access level.</p></div><span className="data-source-chip"><span />Authenticated account</span></div>
    <Panel title="Registration information" subtitle="Role changes are controlled by the administrator approval workflow">
      <div className="profile-details">{fields.map(([label, value]) => <article key={label}><span>{label}</span><strong>{value || "Unavailable"}</strong></article>)}</div>
    </Panel>
  </>;
}

function UserManagement({ user }) {
  const [users, setUsers] = useState([]);
  const [totals, setTotals] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    Promise.all([apiFetch(`${API_BASE}/api/admin/users`), apiFetch(`${API_BASE}/api/admin/dashboard`)]).then(async ([u, d]) => {
      if (!u.ok || !d.ok) throw new Error("Administrator access is required.");
      const [userData, dashboard] = await Promise.all([u.json(), d.json()]);
      if (active) { setUsers(userData.users || []); setTotals(dashboard); }
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  async function updateStatus(user) {
    const action = user.is_active ? "deactivate" : "activate";
    if (!window.confirm(`Are you sure you want to ${action} ${user.full_name}?`)) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/admin/users/${user.id}/status`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !user.is_active }) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update this account."));
      setError(""); setSuccess(`${user.full_name} was ${user.is_active ? "deactivated" : "activated"}.`);
      setLoading(true); setRefresh(x => x + 1);
    } catch (failure) {
      setError(failure.message || "Could not update this account.");
    }
  }
  async function changeAdminRole(account) {
    const promoting = account.role === "user";
    const action = promoting ? "promote" : "revoke";
    const verb = promoting ? "promote to Admin" : "revoke Admin privileges from";
    if (!window.confirm(`Are you sure you want to ${verb} ${account.full_name}?`)) return;
    try {
      const response = await apiFetch(`${API_BASE}/api/admin/users/${account.id}/${action}-admin`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update this account's role."));
      setError("");
      setSuccess(promoting ? `${account.full_name} is now an Admin.` : `Admin privileges were revoked for ${account.full_name}.`);
      setLoading(true);
      setRefresh(value => value + 1);
    } catch (failure) {
      setError(failure.message || "Could not update this account's role.");
    }
  }
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">ACCESS CONTROL</span><h2>Users and Admins</h2><p>Manage User and Admin access. Administrator privileges can only be established through initial setup.</p></div><span className="data-source-chip"><span />Administrator view</span></div>
    <div className="metric-grid">{[["Registered users", totals?.total_users],["Active accounts", totals?.active_users],["Pothole records", totals?.total_potholes],["Detection events", totals?.total_detections],["Legacy potholes", totals?.legacy_potholes]].map(([label,value])=><article className="metric-card" key={label}><span>{label}</span><strong>{loading ? "—" : value ?? "—"}</strong></article>)}</div>
    {success && <div className="alert success" role="status">{success}</div>}
    {error && <div className="alert" role="alert">{error}</div>}
    <Panel title="All accounts" subtitle="Public registration always creates User access; Admin requests require Administrator approval">
      {loading ? <LoadingState label="Loading account records…" /> : users.length ? <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Joined</th><th>Status</th><th>Access actions</th></tr></thead><tbody>{users.map(account=><tr key={account.id}><td>{account.full_name}</td><td>{account.email}</td><td><StatusBadge value={account.role === "administrator" ? "Administrator" : account.role === "admin" ? "Admin" : "User"} /></td><td>{formatDate(account.created_at)}</td><td><StatusBadge value={account.is_active ? "Active" : "Inactive"} /></td><td className="button-row user-actions">{account.role === "administrator" ? <span>Protected</span> : <><button className={account.role === "admin" ? "secondary" : ""} disabled={account.id === user.id} onClick={() => changeAdminRole(account)}>{account.role === "admin" ? "Revoke Admin" : "Promote to Admin"}</button>{account.id !== user.id && <button className="secondary" onClick={() => updateStatus(account)}>{account.is_active ? "Deactivate" : "Activate"}</button>}</>}</td></tr>)}</tbody></table></div> : <EmptyState icon={Users} title="No accounts found" body="Registered accounts will appear here." />}
    </Panel>
  </>;
}

function AdminRequests() {
  const [requestData, setRequestData] = useState({ users: [], admin_request_counts: {} });
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [savingId, setSavingId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  useEffect(() => {
    let active = true;
    let timer;
    let controller;
    const loadRequests = async () => {
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await apiFetch(`${API_BASE}/api/admin/users`, { signal: controller.signal });
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load Admin requests."));
        if (active) {
          setRequestData(payload);
          setError("");
        }
      } catch (failure) {
        if (active && failure.name !== "AbortError") setError(failure.message || "Could not load Admin requests.");
      } finally {
        if (active) {
          setLoading(false);
          timer = window.setTimeout(loadRequests, REFRESH_SECONDS * 1000);
        }
      }
    };
    loadRequests();
    return () => {
      active = false;
      window.clearTimeout(timer);
      controller?.abort();
    };
  }, [refresh]);

  async function decide(request, decision) {
    const approved = decision === "approve";
    if (!window.confirm(`${approved ? "Approve" : "Reject"} Admin access for ${request.full_name}?`)) return;
    setSavingId(request.id); setError(""); setSuccess("");
    try {
      const response = await apiFetch(`${API_BASE}/api/admin/users/${request.id}/${approved ? "approve" : "reject"}-admin`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not update this request."));
      setSuccess(`Admin access request ${approved ? "approved" : "rejected"} for ${request.full_name}.`);
      setRefresh(value => value + 1);
    } catch (failure) {
      setError(failure.message || "Could not update this request.");
    } finally {
      setSavingId("");
    }
  }

  const counts = requestData.admin_request_counts || {};
  const pending = requestData.users.filter(user => user.admin_request_status === "pending");
  const decisions = requestData.users.filter(user => ["approved", "rejected", "revoked"].includes(user.admin_request_status));
  return <>
    <div className="page-section-heading"><div><span className="section-eyebrow">ACCESS CONTROL</span><h2>Admin requests</h2><p>Review User requests for Admin access. Approval grants the Admin Dashboard and report-management tools.</p></div><span className="data-source-chip"><span />Administrator review</span></div>
    <div className="metric-grid">{[["Pending", counts.pending],["Approved", counts.approved],["Rejected", counts.rejected],["Revoked", counts.revoked]].map(([label,value])=><article className="metric-card" key={label}><span>{label}</span><strong>{loading ? "—" : value ?? 0}</strong></article>)}</div>
    {success && <div className="alert success" role="status">{success}</div>}
    {error && <div className="alert" role="alert">{error}</div>}
    <Panel title="Pending requests" subtitle={`${loading ? "Loading" : pending.length} requests awaiting a decision`}>
      {loading ? <LoadingState label="Loading Admin requests…" /> : pending.length ? <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Registration date</th><th>Requested role</th><th>Status</th><th>Decision</th></tr></thead><tbody>{pending.map(request=><tr key={request.id}><td>{request.full_name}</td><td>{request.email}</td><td>{formatDate(request.created_at)}</td><td><StatusBadge value={request.requested_role || "admin"} /></td><td><StatusBadge value="pending" /></td><td className="button-row user-actions"><button disabled={savingId === request.id} onClick={() => decide(request, "approve")}>{savingId === request.id ? "Saving…" : "Approve"}</button><button className="secondary" disabled={savingId === request.id} onClick={() => decide(request, "reject")}>Reject</button></td></tr>)}</tbody></table></div> : <EmptyState icon={ShieldCheck} title="No pending requests" body="New Admin access requests will appear here." />}
    </Panel>
    <Panel title="Request history" subtitle="Previously approved or rejected requests">
      {loading ? <LoadingState label="Loading request history…" /> : decisions.length ? <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Registration date</th><th>Requested role</th><th>Status</th></tr></thead><tbody>{decisions.map(request=><tr key={request.id}><td>{request.full_name}</td><td>{request.email}</td><td>{formatDate(request.created_at)}</td><td><StatusBadge value={request.requested_role || "admin"} /></td><td><StatusBadge value={request.admin_request_status} /></td></tr>)}</tbody></table></div> : <EmptyState icon={ShieldCheck} title="No decisions yet" body="Approved and rejected requests will appear here." />}
    </Panel>
  </>;
}
function UserContributions() {
  const [accounts, setAccounts] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    apiFetch(`${API_BASE}/api/admin/analytics`).then(async response => {
      if (!response.ok) throw new Error("Could not load user contributions.");
      const data = await response.json(); setAccounts(data.user_contributions || []);
    }).catch(err => setError(err.message)).finally(() => setLoading(false));
  }, []);
  return <Panel title="User contributions" subtitle="Counts from owned MongoDB pothole and detection records">{loading ? <LoadingState label="Loading contribution data…" /> : error ? <div className="alert">{error}</div> : !accounts.length ? <EmptyState icon={Users} title="No registered users" body="User contribution data will appear after accounts are created." /> : <div className="table-wrap"><table><thead><tr><th>User</th><th>Email</th><th>Reported potholes</th><th>Detections</th></tr></thead><tbody>{accounts.map(account=><tr key={account.user_id}><td>{account.full_name}</td><td>{account.email}</td><td>{account.potholes}</td><td>{account.detections}</td></tr>)}</tbody></table></div>}</Panel>;
}

function Dashboard({ user, pathname, navigate, onLogout, accountNotice, accountNoticeError, onDismissAccountNotice, theme, onToggleTheme }) {
  const [filters, setFilters] = useState({ search: "", verification: "all", repair: "all" });
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState(null);
  const [reviewRecord, setReviewRecord] = useState(null);
  const [camera, setCamera] = useState({ running: false, loading: false, error: "", streamKey: 0, status: null });
  const [adminStats, setAdminStats] = useState(null);
  const [adminStatsError, setAdminStatsError] = useState("");
  const isAdministrator = user.role === "administrator";
  const isAdmin = user.role === "admin" || isAdministrator;
  const rolePrefix = isAdministrator ? "/administrator" : isAdmin ? "/admin" : "/user";
  const navigation = isAdministrator ? ADMINISTRATOR_NAVIGATION : isAdmin ? ADMIN_NAVIGATION : USER_NAVIGATION;
  const notificationData = useNotificationData(isAdmin);
  const activeItem = navigation.find((item) => item[2] === pathname) || navigation[0];
  const active = activeItem[3];
  const includesMap = isAdmin && ["Overview Dashboard", "GIS Map"].includes(active);
  const data = useRoadwatchData(filters, page, includesMap, isAdmin);
  useEffect(() => {
    if (!isAdmin) return;
    let active = true;
    const loadStats = async () => {
      try {
        const response = await apiFetch(`${API_BASE}/api/admin/dashboard`);
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not load administrator overview."));
        if (active) { setAdminStats(payload); setAdminStatsError(""); }
      } catch (failure) {
        if (active) setAdminStatsError(failure.message || "Could not load administrator overview.");
      }
    };
    loadStats();
    const timer = window.setInterval(loadStats, REFRESH_SECONDS * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, [isAdmin]);

  function updateFilters(updater) {
    setFilters(updater);
    setPage(1);
  }
  function setActivePath(path) { navigate(path); }
  function locateRecord(record) {
    setSelectedId(recordKey(record));
    navigate(`${rolePrefix}/map`);
  }

  const selectedRecord = useMemo(() => data.potholes.find((record) => recordKey(record) === selectedId), [data.potholes, selectedId]);
  function renderPage() {
    if (data.loading) return <LoadingState />;
    if (active === "Overview Dashboard") return <Overview data={data} selectedId={selectedId} setSelectedId={setSelectedId} selectedRecord={selectedRecord} setReviewRecord={setReviewRecord} adminStats={adminStats} isAdmin={isAdmin} isAdministrator={isAdministrator} />;
    if (active === "Live Detection") return <LiveDetection camera={camera} setCamera={setCamera} refresh={data.refresh} />;
    if (active === "GIS Map" && isAdmin) return <Panel title="Map monitoring" subtitle={`${data.mapCount} verified markers · ${data.mapReviewCount} other coordinate-bearing reports for review`}><PotholeMap records={data.mapRecords} reviewRecords={data.mapReviewRecords} unverifiedRecords={data.potholes} events={data.detectionEvents} selectedId={selectedId} setSelectedId={setSelectedId} onReviewRecord={setReviewRecord} refresh={data.refresh} canVerify showLocationReview showOwner />{selectedRecord && <div className="detail-strip">Selected {selectedRecord.pothole_id}: {validCoordinate(selectedRecord) ? `${selectedRecord.latitude}, ${selectedRecord.longitude}` : "GPS coordinates unavailable"} · {selectedRecord.location_verification_status || "UNVERIFIED"}.</div>}</Panel>;
    if (active === "Pothole Registry") return <><div className="page-section-heading"><div><span className="section-eyebrow">FIELD RECORDS</span><h2>{isAdmin ? "All Potholes" : "My Potholes"}</h2><p>{isAdmin ? "All saved pothole records, including the account that submitted each one." : "Only the pothole records submitted by your account."}</p></div><span className="data-source-chip"><span />MongoDB pothole records</span></div><Panel title={isAdmin ? "All Potholes" : "My Potholes"} subtitle={`${data.total.toLocaleString()} unique pothole records`}><RegistryTable records={data.potholes} total={data.total} page={page} setPage={setPage} pageSize={PAGE_SIZE} selectedId={selectedId} setSelectedId={setSelectedId} isAdmin={isAdmin} onLocateRecord={isAdmin ? locateRecord : undefined} onReviewRecord={isAdmin ? setReviewRecord : undefined} /></Panel></>;
    if (active === "Pothole Tracking" && !isAdmin) return <PotholeTracking records={data.potholes} total={data.total} page={page} setPage={setPage} pageSize={PAGE_SIZE} />;
    if (active === "Notifications" && isAdmin) return <NotificationsPage data={notificationData} />;
    if (active === "Detection History" || active === "Detection Monitoring") return <DetectionMonitoring isAdmin={isAdmin} />;
    if (active === "My Profile") return <MyProfile user={user} />;
    if (active === "Analytics" && isAdmin) return <><div className="page-section-heading"><div><span className="section-eyebrow">PERFORMANCE & DISTRIBUTION</span><h2>Detection analytics</h2><p>Overall severity counts and detection trends from saved MongoDB records.</p></div><span className="data-source-chip"><span />Live data</span></div><div className="dashboard-grid analytics-dashboard-grid"><Panel title="Severity Trends" subtitle="Overall saved pothole counts by severity"><SeverityTrendChart counts={data.summary?.severity_counts || {}} /></Panel><Panel title="Daily detection trends" subtitle="Saved events by day"><TrendChart trends={data.trends} /></Panel><Panel title="Monthly detection trends" subtitle="Saved events by month"><TrendChart trends={data.monthlyTrends.map(item => ({...item, date:item.month}))} /></Panel><Panel title="Potholes by location" subtitle="Top available location labels"><DistributionBar distribution={data.summary?.location_distribution || {}} /></Panel></div>{isAdministrator && <UserContributions />}</>;
    if (active === "Reports and Exports") return <Reports filters={filters} />;
    if (active === "User Management" && isAdministrator) return <UserManagement user={user} />;
    if (active === "Admin Requests" && isAdministrator) return <AdminRequests />;
    return null;
  }

  return (
    <AppLayout navigation={navigation} activePath={pathname} pageTitle={active} pageDescription={PAGE_DESCRIPTIONS[active]} user={user} notificationUnreadCount={isAdmin ? notificationData.unreadCount : 0} connected={!data.error} lastSync={data.lastSync} refreshing={data.refreshing} onRefresh={data.refresh} onLogout={onLogout} onNavigate={setActivePath} theme={theme} onToggleTheme={onToggleTheme}>
      {accountNotice && <div className="alert success" role="status">{accountNotice}<button type="button" onClick={onDismissAccountNotice}>Dismiss</button></div>}
      {accountNoticeError && <div className="alert" role="alert">{accountNoticeError}</div>}
      {adminStatsError && isAdmin && <div className="alert" role="alert">{adminStatsError}<button onClick={() => setAdminStatsError("")}>Dismiss</button></div>}
      {data.error && <div className="alert"><AlertTriangle size={18} />{data.error}<button onClick={() => data.refresh()}>Retry</button></div>}
      {["Overview Dashboard", "GIS Map", "Pothole Registry", "Pothole Tracking", "Analytics", "Reports and Exports"].includes(active) && <FilterBar filters={filters} setFilters={updateFilters} />}
      {renderPage()}
      {reviewRecord && isAdmin && <PotholeReviewDialog record={reviewRecord} onClose={() => setReviewRecord(null)} onSaved={() => data.refresh({ silent: true })} />}
    </AppLayout>
  );
}

function App() {
  const [theme, setTheme] = useState(getInitialTheme);
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [pathname, setPathname] = useState(window.location.pathname);
  const [authToken, setAuthToken] = useState(() => new URLSearchParams(window.location.search).get("token") || "");
  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [adminSetupAvailable, setAdminSetupAvailable] = useState(null);
  const [adminSetupError, setAdminSetupError] = useState("");
  const [accountNotice, setAccountNotice] = useState("");
  const [accountNoticeError, setAccountNoticeError] = useState("");
  function toggleTheme() {
    setTheme((current) => current === "dark" ? "light" : "dark");
  }
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch (error) {
      console.warn("RoadWatch could not save the theme preference.", error);
    }
  }, [theme]);
  function navigate(path, replace = false) {
    if (replace) window.history.replaceState({}, "", path);
    else window.history.pushState({}, "", path);
    setPathname(path);
    setAuthToken("");
  }
  useEffect(() => {
    const onPopState = () => {
      setPathname(window.location.pathname);
      setAuthToken(new URLSearchParams(window.location.search).get("token") || "");
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  useEffect(() => {
    if (pathname !== "/verify-email" || checking) return undefined;
    if (!authToken) return undefined;
    let active = true;
    fetch(`${API_BASE}/api/auth/email-verification/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: authToken }),
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Email verification failed."));
        if (active) setSuccessMessage(payload.message || "Email verified. You can now sign in.");
      })
      .catch((failure) => {
        if (active) setError(authErrorMessage(failure));
      })
      .finally(() => {
        if (active) {
          window.history.replaceState({}, "", "/login");
          setPathname("/login");
          setAuthToken("");
        }
      });
    return () => { active = false; };
  }, [authToken, checking, pathname]);
  useEffect(() => {
    if (pathname === "/reset-password" && window.location.search) {
      window.history.replaceState({}, "", "/reset-password");
    }
  }, [pathname]);
  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/auth/admin-setup/status`)
      .then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not check Admin setup availability."));
        if (active) setAdminSetupAvailable(payload.available === true);
      })
      .catch(failure => {
        if (active) setAdminSetupError(authErrorMessage(failure) || "Could not check Admin setup availability.");
      });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    apiFetch(`${API_BASE}/api/auth/me`).then(async response => {
      if (response.status === 401) return null;
      if (!response.ok) throw new Error("Could not validate your session.");
      const currentUser = await response.json();
      setUser(currentUser);
      setAccountNotice(adminRequestNotice(currentUser.admin_request_notice));
    }).catch(err => { setError(authErrorMessage(err)); }).finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (!user?.id) return undefined;
    let active = true;
    let timer;
    let controller;
    let knownRole = user.role;
    const refreshSession = async () => {
    controller?.abort();
    controller = new AbortController();
    try {
      const response = await apiFetch(`${API_BASE}/api/auth/me`, { signal: controller.signal });
      if (response.status === 401) {
        if (active) {
          setUser(null);
          setAccountNotice("");
          window.history.replaceState({}, "", "/login");
          setPathname("/login");
        }
        return;
      }
      const refreshedUser = await response.json();
      if (!response.ok) throw new Error(getApiErrorMessage(refreshedUser, response.status, "Could not refresh account access."));
      if (!active) return;
      if (refreshedUser.role !== knownRole) {
        knownRole = refreshedUser.role;
        const destination = dashboardPathForRole(knownRole);
        window.history.replaceState({}, "", destination);
        setPathname(destination);
        setAccountNotice(adminRequestNotice(refreshedUser.admin_request_notice));
      } else {
        const notice = adminRequestNotice(refreshedUser.admin_request_notice);
        if (notice) setAccountNotice(notice);
      }
      setUser(refreshedUser);
      setAccountNoticeError("");
    } catch (failure) {
      if (active && failure.name !== "AbortError") {
        setAccountNoticeError(failure.message || "Could not refresh account access.");
      }
    } finally {
      if (active) timer = window.setTimeout(refreshSession, REFRESH_SECONDS * 1000);
    }
    };
    const refreshWhenVisible = () => {
    if (document.visibilityState === "visible") {
      window.clearTimeout(timer);
      refreshSession();
    }
    };
    refreshSession();
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
    active = false;
    window.clearTimeout(timer);
    controller?.abort();
    document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [user?.id, user?.role]);
  async function dismissAccountNotice() {
    setAccountNoticeError("");
    try {
      const response = await apiFetch(`${API_BASE}/api/auth/admin-request/acknowledge`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Could not dismiss this notification."));
      setAccountNotice("");
    } catch (failure) {
      setAccountNoticeError(failure.message || "Could not dismiss this notification.");
    }
  }
  async function submit(event) {
    event.preventDefault(); setError(""); setSuccessMessage("");
    const registering = pathname === "/register";
    const settingUpAdmin = pathname === "/admin-setup";
    const requestingReset = pathname === "/forgot-password";
    const resendingVerification = pathname === "/resend-verification";
    const resettingPassword = pathname === "/reset-password";
    const form = new FormData(event.currentTarget);
    if (registering || settingUpAdmin || resettingPassword) {
      const password = String(form.get("password") || "");
      const minimumLength = settingUpAdmin || resettingPassword ? 12 : 8;
      const requirementsMet = [...password].length >= minimumLength
        && /\p{Lu}/u.test(password)
        && /\p{Ll}/u.test(password)
        && /\p{N}/u.test(password)
        && /[^\p{L}\p{N}]/u.test(password);
      if (new TextEncoder().encode(password).length > 72) {
        setError("Password must be no more than 72 UTF-8 bytes.");
        return;
      }
      if (!requirementsMet) {
        setError(`Password must be at least ${minimumLength} characters and include uppercase and lowercase letters, a number, and a special character.`);
        return;
      }
    }
    if ((registering || settingUpAdmin || resettingPassword) && form.get("password") !== form.get("confirm_password")) {
      setError("Passwords do not match.");
      return;
    }
    const body = registering
      ? {full_name: form.get("full_name"), email: form.get("email"), password: form.get("password"), requested_role: form.get("requested_role") || "user"}
      : settingUpAdmin
        ? {full_name: form.get("full_name"), email: form.get("email"), password: form.get("password"), confirm_password: form.get("confirm_password"), setup_secret: form.get("setup_secret")}
        : resettingPassword
          ? {token: authToken, password: form.get("password"), confirm_password: form.get("confirm_password")}
          : {email: form.get("email"), password: form.get("password")};
    if (settingUpAdmin && adminSetupAvailable !== true) { setError("Initial Admin setup is unavailable."); return; }
    try {
      if (requestingReset || resendingVerification) {
        const endpoint = requestingReset
          ? "/api/auth/password-reset/request"
          : "/api/auth/email-verification/resend";
        const response = await fetch(`${API_BASE}${endpoint}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: form.get("email") }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Request could not be completed."));
        if (!payload.email_delivery_configured) {
          setSuccessMessage("This request is handled privately, but email delivery is not configured. Configure the backend SMTP settings before an email can be sent.");
        } else {
          setSuccessMessage(payload.message);
        }
        navigate("/login");
        return;
      }
      if (resettingPassword) {
        if (!authToken) throw new Error("This reset link is missing or invalid. Request a new link.");
        const response = await fetch(`${API_BASE}/api/auth/password-reset/confirm`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(getApiErrorMessage(payload, response.status, "Password could not be updated."));
        setUser(null);
        setSuccessMessage(payload.message || "Password updated. Sign in with your new password.");
        navigate("/login");
        return;
      }
      if (registering) {
        const created = await fetch(`${API_BASE}/api/auth/register`, {method:"POST", credentials:"include", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
        const payload = await created.json();
        if (!created.ok) {
          throw new Error(getApiErrorMessage(payload, created.status, "Registration failed."));
        }
        const approvalNotice = payload.admin_request_status === "pending"
          ? " Your Admin access request remains pending approval."
          : "";
        const verificationNotice = !payload.email_delivery_configured
          ? " Email verification is required, but the backend email service is not configured; ask the operator to configure SMTP before requesting a verification link."
          : payload.verification_email_sent
            ? " Check your inbox for the verification link before signing in."
            : " Email delivery could not be confirmed. Use Resend verification after the server email service is configured.";
        setSuccessMessage(`Your account was created with User access.${approvalNotice}${verificationNotice}`);
        navigate("/login");
        return;
      }
      if (settingUpAdmin) {
        const response = await fetch(`${API_BASE}/api/auth/admin-setup`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
        const payload = await response.json();
        if (!response.ok) {
          if (response.status === 409) setAdminSetupAvailable(false);
          throw new Error(getApiErrorMessage(payload, response.status, "Admin setup failed."));
        }
        setAdminSetupAvailable(false);
        setSuccessMessage("Your Administrator account was created. Sign in to continue.");
        navigate("/login");
        return;
      }
      const response = await fetch(`${API_BASE}/api/auth/login`, {method:"POST", credentials:"include", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
      const data = await response.json();
      if (!response.ok) {
        throw new Error(getApiErrorMessage(data, response.status, "Sign in failed."));
      }
      setUser(data.user);
      setAccountNotice(adminRequestNotice(data.user.admin_request_notice));
      navigate(dashboardPathForRole(data.user.role), true);
    } catch (err) { setError(authErrorMessage(err)); }
  }
  function logout() { apiFetch(`${API_BASE}/api/auth/logout`, {method:"POST"}).catch(()=>{}); setUser(null); navigate("/login", true); }
  if (checking) return <main className="auth-page auth-status-page"><ThemeToggle theme={theme} onToggle={toggleTheme} /><div className="auth-status-message">Checking your session…</div></main>;
  if (user) {
    if (["/reset-password", "/verify-email"].includes(pathname)) {
      return <AuthPage pathname={pathname} error={error} successMessage={successMessage} adminSetupAvailable={adminSetupAvailable} adminSetupError={adminSetupError} authToken={authToken} authProcessing={pathname === "/verify-email" && Boolean(authToken)} navigate={navigate} onSubmit={submit} onClearError={() => { setError(""); setSuccessMessage(""); }} theme={theme} onToggleTheme={toggleTheme} />;
    }
    if (["/", "/login", "/register", "/admin-setup", "/forgot-password", "/resend-verification"].includes(pathname)) {
      const home = dashboardPathForRole(user.role);
      navigate(home, true);
      return <main className="auth-page auth-status-page"><ThemeToggle theme={theme} onToggle={toggleTheme} /><div className="auth-status-message">Opening your dashboard…</div></main>;
    }
    const adminPath = pathname.startsWith("/admin/");
    const administratorPath = pathname.startsWith("/administrator/");
    const userPath = pathname.startsWith("/user/");
    const home = dashboardPathForRole(user.role);
    if (user.role === "administrator" && (adminPath || userPath)) {
      return <AccessDenied navigate={navigate} destination={home} theme={theme} onToggleTheme={toggleTheme} />;
    }
    if (user.role === "admin" && (administratorPath || userPath)) {
      return <AccessDenied navigate={navigate} destination={home} theme={theme} onToggleTheme={toggleTheme} />;
    }
    if (user.role !== "admin" && user.role !== "administrator" && (adminPath || administratorPath)) {
      return <AccessDenied navigate={navigate} destination={home} theme={theme} onToggleTheme={toggleTheme} />;
    }
    const menu = user.role === "administrator" ? ADMINISTRATOR_NAVIGATION : user.role === "admin" ? ADMIN_NAVIGATION : USER_NAVIGATION;
    if (!menu.some((item) => item[2] === pathname)) return <AccessDenied navigate={navigate} destination={home} />;
    return <Dashboard user={user} pathname={pathname} navigate={navigate} onLogout={logout} accountNotice={accountNotice} accountNoticeError={accountNoticeError} onDismissAccountNotice={dismissAccountNotice} theme={theme} onToggleTheme={toggleTheme} />;
  }
  if (!["/login", "/register", "/admin-setup", "/forgot-password", "/resend-verification", "/reset-password", "/verify-email"].includes(pathname)) { navigate("/login", true); return <div className="auth-page">Opening sign in…</div>; }
  return <AuthPage pathname={pathname} error={error} successMessage={successMessage} adminSetupAvailable={adminSetupAvailable} adminSetupError={adminSetupError} authToken={authToken} authProcessing={pathname === "/verify-email" && Boolean(authToken)} navigate={navigate} onSubmit={submit} onClearError={() => { setError(""); setSuccessMessage(""); }} theme={theme} onToggleTheme={toggleTheme} />;
}

function DistributionBar({ distribution }) {
  const data = Object.entries(distribution).filter(([, value]) => Number(value) > 0).map(([name, value]) => ({ name: name.replaceAll("_", " "), value }));
  if (!data.length) return <EmptyState icon={BarChart3} title="No distribution data" body="Charts appear after records are available." />;
  return <ResponsiveContainer width="100%" height={260}><BarChart data={data} margin={{ bottom: 16 }}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" interval={0} angle={data.length > 6 ? -25 : 0} textAnchor={data.length > 6 ? "end" : "middle"} height={data.length > 6 ? 64 : 30} label={{ value: "Location", position: "insideBottom", offset: -8 }} /><YAxis allowDecimals={false} label={{ value: "Potholes", angle: -90, position: "insideLeft" }} /><Tooltip /><Bar dataKey="value" name="Potholes" fill="var(--color-primary)" radius={[6, 6, 0, 0]} /></BarChart></ResponsiveContainer>;
}

export default App;
