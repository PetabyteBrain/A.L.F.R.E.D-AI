export function Reactor({ small = false }) {
  return <div className={`reactor${small ? " sm" : ""}`} aria-hidden="true"><span /></div>;
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }) {
  return error ? <div className="empty error">{error}</div> : null;
}

export function Tag({ kind = "", children }) {
  return <span className={`tag ${kind}`}>{children}</span>;
}
