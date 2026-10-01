/* Safe Markdown -> React (no HTML injection, only React elements): headings, paragraphs, line
   breaks, bullet / numbered lists, > quotes, tables, --- rules, fenced code, **bold**, *italic*,
   ~~strike~~, `code` and [links](https://…) (http/https only). */

// Underscore italics only at word boundaries, so names like ANTHROPIC_API_KEY stay intact.
const INLINE = /(\*\*[^*]+\*\*|~~[^~]+~~|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|(?<![A-Za-z0-9])_[^_\s][^_]*_(?![A-Za-z0-9]))/g;

function inline(text, keyBase) {
  const out = [];
  let last = 0, i = 0, m;
  const re = new RegExp(INLINE.source, "g");   // own instance: inline() recurses (bold can contain italics)
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0], key = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={key}>{inline(tok.slice(2, -2), key)}</strong>);
    else if (tok.startsWith("~~")) out.push(<s key={key}>{tok.slice(2, -2)}</s>);
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

const BULLET = /^\s*[-*•+]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
const startsBlock = (l) => !l.trim() || l.trim().startsWith("```") || BULLET.test(l) || NUMBERED.test(l)
  || HEADING.test(l) || RULE.test(l) || l.trim().startsWith(">") || TABLE_ROW.test(l);

export default function Markdown({ text }) {
  const blocks = [];
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {                         // fenced code
      const code = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={k++}><code>{code.join("\n")}</code></pre>);
    } else if (HEADING.test(line)) {                              // # heading -> h3..h5 (chat-sized)
      const [, hashes, title] = line.match(HEADING);
      const Tag = `h${Math.min(5, hashes.length + 2)}`;
      blocks.push(<Tag key={k++}>{inline(title, `h${k}`)}</Tag>);
      i++;
    } else if (RULE.test(line)) {
      blocks.push(<hr key={k++} />);
      i++;
    } else if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {   // table
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) rows.push(cells(lines[i++]));
      blocks.push(
        <div className="md-table" key={k++}>
          <table>
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `th${k}-${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `td${k}-${ri}-${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>);
    } else if (line.trim().startsWith(">")) {                      // > quote
      const quote = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) quote.push(lines[i++].trim().replace(/^>\s?/, ""));
      blocks.push(<blockquote key={k++}>{withBreaks(quote, `q${k}`)}</blockquote>);
    } else if (BULLET.test(line)) {                               // bullet list
      const items = [];
      while (i < lines.length && BULLET.test(lines[i])) items.push(lines[i++].replace(BULLET, ""));
      blocks.push(<ul key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ul>);
    } else if (NUMBERED.test(line)) {                             // numbered list
      const items = [];
      while (i < lines.length && NUMBERED.test(lines[i])) items.push(lines[i++].replace(NUMBERED, ""));
      blocks.push(<ol key={k++}>{items.map((t, j) => <li key={j}>{inline(t, `${k}-${j}`)}</li>)}</ol>);
    } else if (!line.trim()) {
      i++;
    } else {                                                       // paragraph (until blank line / other block)
      const para = [];
      while (i < lines.length && !startsBlock(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      blocks.push(<p key={k++}>{withBreaks(para, `p${k}`)}</p>);
    }
  }
  return <div className="md">{blocks}</div>;
}
