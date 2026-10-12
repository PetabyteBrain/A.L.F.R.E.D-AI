// Permissions come from /api/me (orchestrator/permissions.py); the server enforces them, this only hides UI.
export const can = (me, permission) => Boolean(me?.permissions?.includes(permission));

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }) {
  return error ? <div className="empty error">{error}</div> : null;
}

export function Tag({ kind = "", children }) {
  return <span className={`tag ${kind}`}>{children}</span>;
}
