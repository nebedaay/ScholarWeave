# Document Import and Export

Two desktop-only commands move documents between the outside world and your vault:

- **Import a Word or ODT document with Zotero citations** — turn a `.docx`/`.odt` into a markdown note.
- **Compile and export a book, article, or other document (outline or markdown)** — compile a note or a bullet-list outline of notes into markdown, DOCX, ODT, or PDF.

Both call external tools. The plugin probes for them when the dialogue opens and greys out options that can't run, with an explanation and a link to [Dependencies](./dependencies.md). The short version:

| | Python 3 | Pandoc | LibreOffice | LaTeX | Zotero |
|---|---|---|---|---|---|
| Compile to Markdown | ✓ | | | | |
| Export DOCX / ODT | ✓ | ✓ | | | optional |
| PDF via ODT/DOCX template | ✓ | ✓ | ✓ | | optional |
| PDF via `.tex` template | ✓ | ✓ | | ✓ | optional |
| Import DOCX / ODT | ✓ | ✓ | | | ✓ |

“Zotero optional” means static citations can be rendered from a bibliography file; live, refreshable fields still need Zotero. See [Dependencies](./dependencies.md) for download links.

## Document importer

Command: **Import a Word or ODT document with Zotero citations**.

Converts a Word/ODT document — including its Zotero citation fields — into a markdown note, turning the citations into linked (or plain pandoc) citations and optionally creating literature notes for cited works that lack them. Citations inside **footnotes and endnotes** are converted too (Word stores those in separate parts of the file, which ScholarWeave processes alongside the body).

Requires **Python 3, Pandoc, and Zotero** (running).

Every imported note gets frontmatter drawn from the document (mirroring the *Basic note template*), which you can edit afterwards:

| Key | Filled from |
|---|---|
| `created` | the note's creation date and time |
| `up` | `[[sw imports]]` |
| `related` | left empty |
| `aliases` | the document title, plus the short title before its first `:` |
| `title` | the document's Title/Subtitle paragraph(s) — max two, joined with `:`; else the first paragraph |
| `author` | the document's Author-styled paragraph(s), blank-line separated |
| `abstract` | up to three paragraphs after an `Abstract` line, stopping at a heading (omitted when the document has none) |
| `original-created` | the document's own creation date from its metadata (`meta:creation-date`, else the last-modified `dc:date`), omitted when unavailable |
| `original-filename` | the imported file's name |

`title`, `author` and `abstract` are **moved out of the body** into the frontmatter (keeping them in both would duplicate them if the note is exported again); every other paragraph is preserved. Two other generated elements are dropped:

- **Zotero's generated bibliography** (the `CSL_BIBLIOGRAPHY` field/section) and its `Bibliography` heading — the citations are already converted, so the list is regenerated on export. A hand-written bibliography has no such field and is left untouched.
- **Date/time fields** (`DATE`, `CREATEDATE`, `<text:modification-date>`, …) — they render today's date, which `created` already records, and a date is never part of an author's name.

ODT and DOCX share this one flow (a format-specific walker supplies each paragraph's text and style chain; everything else is common), so a Word document and its ODT twin import to the same note.

## Document compiler and exporter

Command: **Compile and export a book, article, or other document (outline or markdown)**.

Accepts a single markdown note or a bullet-list *outline* of notes, and outputs:

- **Markdown** — the compiled document only (no Pandoc needed).
- **DOCX / ODT** — a document built on a template, with live, refreshable Zotero citation fields.
- **PDF** — via an intermediate ODT/DOCX (LibreOffice) or `.tex` (LuaLaTeX), with citations rendered statically.

### Which input is an outline?

A note is treated as an **outline** (and compiled) only when its body is made up **entirely of list items** — no ordinary prose paragraphs — and it contains **at least one note include**: a bullet that is a single `[[wikilink]]`, possibly nested beneath plain-text heading bullets. A plain topic/task bullet list inside an ordinary note is rendered as-is, not compiled.

Put `compile-` before the template name (`template: compile-book`) to mark an outline explicitly, even when it doesn't match the structural rule.

### Outline grammar

Outline notes contain only frontmatter and bullets; nesting follows bullet depth:

| Bullet form | Output |
|---|---|
| `- Heading text` | Heading at that level |
| `- [[Note]]` | Heading (note title / filename) + note contents |
| `- @@ Heading text` | Numbered heading ("Chapter N: Heading") |
| `- @@[[Note]]` | Numbered heading + note contents |
| `- x [[Note]]` | Note contents only, no heading |

The heading text for a linked note comes from the note's `title:` property, else the filename with leading ordering numbers stripped (`1 Introduction` → `Introduction`). A linked note's own headings are demoted so its shallowest heading sits one level below the note's position in the outline.

### Export dialogue

- **Output format** — Markdown, DOCX, ODT, PDF.
- **Template** — auto-selected from the note's `template:` property; changeable per export.
- **Document type** — Book, Article, or Markdown; sets the other checkboxes (TOC, per-chapter footnotes, new-page headings, roman frontmatter), which can then be customised.
- **Output filename and folder.**
- **Table of contents / table of figures**, **restart footnote and figure numbering per chapter**, **top-level headings start on a new page**.
- **Apply a citation style, overriding the template's** — pick an installed Zotero style; used for PDF and written into the exported DOCX/ODT's Zotero document preferences so a later "Refresh" in Word/LibreOffice uses it.
- **Keep intermediate files** — the compiled markdown and, for PDF, the intermediate ODT/DOCX/TeX.

### Citation style resolution (export)

Highest priority first: the dialogue's style override → the note's `csl:`/`citation-style:` frontmatter → the template's stored Zotero document preferences → the plugin's configured style (see [Citations](./citations.md)) → Chicago author-date.

### Templates

| Template | Description |
|---|---|
| `book` | Book: TOC, chapter headings, per-chapter footnotes |
| `article` | Article: continuous footnotes, no TOC |
| `document` | General-purpose document |

Lookup order: your configured templates directory → `<vault>/Export Templates/` → the templates bundled with the plugin (extracted automatically on load). Bundled templates are a starting point you can override by placing your own copy earlier in that order.

### YAML frontmatter properties

| Key | Purpose |
|---|---|
| `template` | Template name (`book`, `article`, `document`, or your own); `compile-<name>` marks an outline |
| `title` | Cover title (markdown formatting supported) |
| `subtitle` | Cover subtitle |
| `shorttitle` | Even-page header (falls back to `title` before `:`, then the filename) |
| `abstract` | Cover/near-cover abstract block |
| `note` | Cover/near-cover note block |
| `author` | Cover author (a string, or a `- Name` list) |
| `csl` / `citation-style` | Citation style for this note (a Zotero style name, `.csl` path, or URL) |
| `bibliography` | Override the bibliography source(s) for this note |

### Images and figures

- Transcluded images (`![[image|400]]`) are inserted, with the alias used as a width. A following `Figure. …` paragraph becomes a numbered caption.
- **Excalidraw** drawings (`![[Drawing.excalidraw]]`) use the drawing's auto-exported sidecar image (enable *Auto-export PNG* in Excalidraw) so they render consistently.

### Automatic style conversion

ScholarWeave converts Obsidian-specific markdown into DOCX/ODT styles rather than dropping it.

**Callouts** (`> [!note]`) can be mapped to named paragraph styles in Settings; poetry callouts (`[!poetry]`, `[!arabic-poetry]`) are handled automatically.

**Markdown Attributes** and **Extended Markdown Syntax** are recognised without configuration:

| Syntax | Plugin | Becomes |
|---|---|---|
| `*text{.cls}*`, `**text{.cls}**`, `***text{.cls}***` | Markdown Attributes | `cls` character style |
| `` `text{.cls}` `` | Markdown Attributes | `cls` character style |
| `==text{.cls}==` | Markdown Attributes | `cls` character style |
| `!!{cls}text!!` | Extended Markdown Syntax | `cls` character style |
| `++text++` | Extended Markdown Syntax | `Inserted` character style |
| `=={color}text==` | Extended Markdown Syntax | `Highlight color` character style |

Style names come from the CSS class or callout type (first letter capitalised, hyphens → spaces, so `.arabic-poetry` → "Arabic poetry"); override with an explicit mapping in Settings.

**Undefined styles:** when the output references a style the template doesn't define, ScholarWeave injects a sentinel copy with a distinctive highlighted background (cycling colours) so you can spot it and define the style in your template; defining it removes the highlight on future exports.

### PDF export

PDF goes through an intermediate chosen by the selected template:

- **ODT/DOCX template → LibreOffice**, which must be installed. ODT usually handles footnote numbering and figure references more reliably than DOCX.
- **`.tex` template → LuaLaTeX**, from a LaTeX distribution.

Because a PDF is final, citations, the bibliography, the TOC/ToF, figure numbers, and captions are all rendered to fixed values at export time — no "update fields" pass is needed. TOC/ToF entries are clickable links.

### How paths and tools are resolved

All scripts and templates are bundled in the plugin and extracted automatically on load — nothing is downloaded from GitHub by hand. The plugin resolves each external tool's path itself and passes it to the script via environment variables, so it works even though Obsidian's Electron process doesn't inherit your shell `PATH`.

See [Dependencies](./dependencies.md) and [Commands](./commands.md).
