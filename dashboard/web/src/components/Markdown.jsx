/* Minimal, safe Markdown -> React (no HTML injection): paragraphs, line breaks, bullet and
   numbered lists, fenced code, **bold**, *italic*, `code` and [links](https://…) (http/https only). */

// Underscore italics only at word boundaries, so names like ANTHROPIC_API_KEY stay intact.
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|(?<![A-Za-z0-9])_[^_\s][^_]*_(?![A-Za-z0-9]))/g;

function inline(text, keyBase) {
  const out = [];
  let last = 0, i = 0, m;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0], key = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={key}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[")) {
      const label = tok.slice(1, tok.indexOf("]("));
      const href = tok.slice(tok.indexOf("](") + 2, -1);
      out.push(/^https?:\/\//i.test(href)
        ? <a key={key} href={href} target="_blank" rel="noopener noreferrer">{label}</a>
        : `${label} (${href})`);
    } else out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function withBreaks(lines, keyBase) {
  return lines.flatMap((line, i) => (i ? [<br key={`${keyBase}-br${i}`} />] : []).concat(inline(line, `${keyBase}-${i}`)));
}

export default function Markdown({ text }) {
  const blocks = [];
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {                       // fenced code
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={k++}><code>{code.join("\n")}</code></pre>);
    } else if (/^\s*[-*•]\s+/.test(line)) {                     // bullet list
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
      blocks.push(<ul key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ul>);
    } else if (/^\s*\d+[.)]\s+/.test(line)) {                   // numbered list
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(<ol key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ol>);
    } else if (!line.trim()) {
      i++;
    } else {                                                     // paragraph (until blank line / other block)
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !lines[i].trim().startsWith("```")) {
        para.push(lines[i++].replace(/^#+\s*/, ""));
      }
      blocks.push(<p key={k++}>{withBreaks(para, `p${k}`)}</p>);
    }
  }
  return <div className="md">{blocks}</div>;
}
