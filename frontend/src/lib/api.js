export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export function getApiErrorMessage(payload, status, fallback = "Something went wrong.") {
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

export function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  return fetch(url, { credentials: "include", ...options, headers });
}

// App.jsx called authErrorMessage() but no definition was in the pasted file.
// If you already have one somewhere else, delete this and import yours instead.
export function authErrorMessage(error) {
  if (error instanceof TypeError) {
    return "Cannot reach the server. Check that the backend is running.";
  }
  return error?.message || "Something went wrong. Please try again.";
}
