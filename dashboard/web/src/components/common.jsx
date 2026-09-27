export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }) {
  return error ? <div className="empty error">{error}</div> : null;
}

export function Tag({ kind = "", children }) {
  return <span className={`tag ${kind}`}>{children}</span>;
}
