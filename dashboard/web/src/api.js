// Thin fetch wrapper. Every call is same-origin with the HttpOnly session cookie; state-changing
// calls carry the CSRF header the backend requires (see dashboard/api/security.py).

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "alfred";
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && path !== "/login") {
    onUnauthorized();
    throw new ApiError(401, "Signed out");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

// Only ever build audio URLs from numeric IDs returned by the API.
export const audioUrl = (id) => `/api/recordings/${Number(id)}/audio`;
