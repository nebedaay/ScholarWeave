<%/* zotlit-annotation.eta.md — renders ONE annotation as a callout block.
     Merged annotations arrive pre-combined from zotlit-content.eta.md
     (text / comment / pageLabel / tags already merged, "+" marker
     stripped), so this template needs no merge logic of its own. */-%>
<% const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
const colorRaw = zt.colorName ?? "Yellow";
const colorCap = cap(colorRaw);
const typeCap = cap(zt.type);
const esc = s => (s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const mdComment = s => (s ?? "").replace(/<i>/g, "*").replace(/<\/i>/g, "*").replace(/<b>/g, "**").replace(/<\/b>/g, "**").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escText = s => esc(s).replace(/\[/g, "\\[").replace(/\]/g, "\\]");
// Callout-safe multi-line content: EVERY line (including blank lines) gets a
// "> " prefix so paragraphs stay inside the callout. Obsidian callouts break
// on any line lacking the prefix; comments with several paragraphs or blank
// lines otherwise leak their later lines outside the [!ann-…] block.
const calloutLines = s => (s ?? "").split(/\r?\n/).map(l => l.trim() ? `> ${l}` : ">").join("\n");
-%>
<% bq(() => { -%>
[!<%= colorRaw %>-<%= zt.type %>-annotation] <%= colorCap %> <%= typeCap %>
<% if (zt.comment || (zt.tags && zt.tags.length > 0)) { -%>
> [!ann-comment]
<% if (zt.comment) { -%>
<%= calloutLines(mdComment(zt.comment)) %>
<% } -%>
<% for (const tag of (zt.tags ?? [])) { -%>
> - [[<%= tag.name %>]]
<% } -%>
<% } -%>

<% if (zt.type === "highlight" && zt.text) { -%>
> [!ann-highlight-text-<%= colorRaw %>]
> <%= escText(zt.text) %>
<% } else if (zt.type === "underline" && zt.text) { -%>
> [!ann-underline-text-<%= colorRaw %>]
> <%= escText(zt.text) %>
<% } else if (zt.type === "image") { -%>
> [!ann-image-<%= colorRaw %>]
> <%= embed(typeof zt.imgLink === "function" ? zt.imgLink : () => zt.imgLink) %>
> - <%= typeof zt.imgLink === "function" ? zt.imgLink("view image") : zt.imgLink %>
> - [[image annotations|images]]
<% } else if (zt.type === "text" || zt.type === "note") { -%>
> [!ann-text-<%= colorRaw %>]Text comment—click to view in context:
<% if (zt.comment) { -%>
<%= calloutLines(mdComment(zt.comment)) %>
<% } -%>
<% } else if (zt.type === "ink") { -%>
> [!ann-ink-<%= colorRaw %>]
> <%= embed(typeof zt.imgLink === "function" ? zt.imgLink : () => zt.imgLink) %>
> - <%= typeof zt.imgLink === "function" ? zt.imgLink("view ink image") : zt.imgLink %>
<% } -%>
- [[<%= colorCap %> annotations|<%= colorCap %>]]
- (<% if (zt.pageLabel) { %>[<%= zt.pageLabel.includes("–") ? "pp. " : "p. " %><%= zt.pageLabel %>](<%= zt.backlink %>)<% } else { %>[View](<%= zt.backlink %>)<% } %>, <%= zt.dateAdded %>)
<% }) %>
