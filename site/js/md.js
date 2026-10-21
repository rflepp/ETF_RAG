// Minimal, safe markdown for model replies: paragraphs, lists, bold/italic, inline code, citation chips.
const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function inline(s, cite) {
  let h = esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*(?!\s)(.+?)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  return h.replace(/\[([A-Z]{2}[A-Z0-9]{9}\d-[a-z]+(?:#d?\d+)?)\]/g, (m, id) => cite(id));
}

export function renderMarkdown(text, cite) {
  const out = [];
  let list = null;
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (li) {
      if (!list) { list = []; }
      list.push(`<li>${inline(li[1], cite)}</li>`);
      continue;
    }
    if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; }
    const hd = line.match(/^#{1,4}\s+(.*)$/);
    if (hd) out.push(`<h4>${inline(hd[1], cite)}</h4>`);
    else if (line.trim()) out.push(`<p>${inline(line, cite)}</p>`);
  }
  if (list) out.push(`<ul>${list.join('')}</ul>`);
  return out.join('');
}
