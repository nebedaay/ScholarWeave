<%/* zotlit-content.eta.md — annotations region (Eta, JS templates).
     Groups annotations by attachment and renders each through the
     "annotation" template. Zotero-Integration-style "+" concatenation:
     an annotation whose comment begins with "+" is appended to the
     PREVIOUS annotation on the same attachment (joined with " ... "),
     chaining across multiple "+" annotations. The merged group keeps
     the first annotation's links and date; the page label becomes a
     range ("pp. 4–6") when pages differ; comments and tags combine.
     Display-only — Zotero data is never modified, and re-updates
     reproduce the same merge. */-%>
<% if (zt.annotations && zt.annotations.length > 0) { -%>
## Annotations

<% const merged = [];
let group = null;
for (let i = 0; i < zt.annotations.length; i++) {
  const a = { ...zt.annotations[i] };
  const plus = typeof a.comment === "string" && /^\+\s*/.test(a.comment);
  if (plus && group && a.parentAttachment?.key === group.parentAttachment?.key && a.text) {
    a.comment = a.comment.replace(/^\+\s*/, "");
    group.text = [(group.text ?? "").trim(), a.text.trim()].filter(Boolean).join(" ... ");
    group.comment = [group.comment, a.comment].filter(c => c && c.trim()).join(" ... ") || null;
    if (a.pageLabel && group.pageLabel && group.pageLabel !== a.pageLabel) {
      group.pageLabel = `${group.pageLabel.split("–")[0]}–${a.pageLabel}`;
    }
    if (a.tags?.length) {
      const seen = new Set((group.tags ?? []).map(t => t.name));
      group.tags = [...(group.tags ?? []), ...a.tags.filter(t => !seen.has(t.name))];
    }
    continue;
  }
  if (plus && a.comment) a.comment = a.comment.replace(/^\+\s*/, "");
  merged.push(a);
  group = a;
} -%>
<% for (const attachment of zt.attachments) { -%>
<% const anns = merged.filter(a => a.parentAttachment?.key === attachment.key);
if (anns.length === 0) continue; -%>
### [<%= attachment.filename ?? attachment.key %>](<%= attachment.backlink %>)

<% for (const annotation of anns) { -%>
<%~ include("annotation", annotation) %>

<% } -%>
<% } -%>
<% } -%>
