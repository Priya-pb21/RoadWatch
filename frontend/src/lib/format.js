export function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  const number = Number(value);
  return `${number > 1 ? number.toFixed(1) : (number * 100).toFixed(1)}%`;
}

export function formatConfidence(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "N/A";
  const number = Number(value);
  return `${(number > 1 ? number : number * 100).toFixed(2)}%`;
}

export function formatDate(value) {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

export function validCoordinate(record) {
  if (record.latitude === null || record.latitude === undefined || record.longitude === null || record.longitude === undefined) return false;
  if (typeof record.latitude === "string" && !record.latitude.trim()) return false;
  if (typeof record.longitude === "string" && !record.longitude.trim()) return false;
  const lat = Number(record.latitude);
  const lon = Number(record.longitude);
  return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
}

export function recordKey(record) {
  return record._id ?? record.pothole_id;
}

export function locationLabel(record) {
  const location = record.location;
  if (typeof location === "string") return location;
  if (location && typeof location === "object") {
    if (location.name || location.address) return location.name || location.address;
    if (Array.isArray(location.coordinates) && location.coordinates.length >= 2) return `${location.coordinates[1]}, ${location.coordinates[0]}`;
  }
  return record.location_name || record.address || record.road_name || "—";
}

export function hasReliableMapAccuracy(record, threshold = 100) {
  if (record.location_verification_method === "MANUAL" && validCoordinate(record)) return true;
  if (record.gps_accuracy_m == null) return false;
  if (typeof record.gps_accuracy_m === "string" && !record.gps_accuracy_m.trim()) return false;
  const accuracy = Number(record.gps_accuracy_m);
  return Number.isFinite(accuracy) && accuracy >= 0 && accuracy <= threshold;
}
