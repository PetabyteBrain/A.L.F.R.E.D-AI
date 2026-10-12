// Thin fetch wrapper. Every call is same-origin with the HttpOnly session cookie; state-changing
// calls carry the CSRF header the backend requires (see dashboard/api/security.py).

export class ApiError extends Error {
  constructor(status, message, data = null) {
    super(message);
    this.status = status;
    this.data = data;          // structured detail, e.g. the CRM's possible duplicates on a 409
  }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(path, { method = "GET", body, accept = "application/json" } = {}) {
  const headers = { Accept: accept };
  if (method !== "GET") headers["X-Requested-With"] = "alfred";
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/login")) {
    onUnauthorized();
    throw new ApiError(401, "Signed out");
  }
  if (!res.ok) {
    let detail = res.statusText;
    let data = null;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
      else if (j.detail && typeof j.detail.message === "string") { detail = j.detail.message; data = j.detail; }
    } catch { /* not JSON */ }
    throw new ApiError(res.status, detail, data);
  }
  return res;
}

export async function api(path, options) {
  return (await request(path, options)).json();
}

// POST that answers with Server-Sent Events; calls onEvent(obj) for each `data:` line and
// resolves when the server closes the stream.
export async function apiStream(path, body, onEvent) {
  const res = await request(path, { method: "POST", body, accept: "text/event-stream" });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      const data = frame.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

// Multipart upload (files): same CSRF header, the browser sets the multipart boundary itself.
export async function apiUpload(path, formData) {
  const res = await fetch(`/api${path}`, {
    method: "POST", headers: { Accept: "application/json", "X-Requested-With": "alfred" },
    credentials: "same-origin", cache: "no-store", body: formData,
  });
  if (res.status === 401) { onUnauthorized(); throw new ApiError(401, "Signed out"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

// Only ever build audio URLs from numeric IDs returned by the API.
export const audioUrl = (id) => `/api/recordings/${Number(id)}/audio`;
