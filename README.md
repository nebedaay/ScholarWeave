# ScholarWeave

An Obsidian plugin that weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow.

ScholarWeave's signature feature is **linked citations**: `[[@citekey|see @, p. 25]]` is simultaneously a formatted inline citation *and* a real wikilink to the literature note for that source. Citations stay connected to your note network — they're not just text.

Additionally, ScholarWeave links your writing inside Obsidian to the world beyond Obsidian, importing and exporting DOCX and ODT documents while preserving citations. It can compile a note or an outline linking to a series of notes into a publication-ready book, article, or other document with working Zotero references.

This plugin started as a fork of **[Bripey Citation Suite](https://github.com/112345brian/bripey-citation-suite)** (which itself descends from [Pandoc Reference List](https://github.com/community-archive/obsidian-pandoc-reference-list)), renamed to reflect its distinctive and more comprehensive functionality.



## Features

- **Linked citations** — `[[@smith1992|see @, p. 6]]` → (see Smith 1992, 6): a simple, pandoc-derived citation format that solves the heretofore impossible problem of **linking** to literature notes using real Obsidian wikilinks ***and*** rendering **publication-ready formatted citations**. This is the only way to reconcile true links and formatted citations. Conventional `[@citekey]` pandoc-style citations still work and can be converted to and from the linked citation format.
- **Document compiler and exporter (desktop only)** — export a note or multi-note project as compiled markdown, ODT, DOCX, or PDF with one command.
	- Extensive settings and an export dialogue provide fine-grained control over the output format and options (TOC, per-chapter footnotes, figure captions, Zotero citation fields, custom styles).
	- The plugin combines the features of Pandoc export and long-form plugins but with far greater flexibility in converting simple and complex writing projects to publication-ready output.
	- **Custom callouts and markdown → new DOCX/ODT styles** — styles used with plugins such as Markdown Attributes and Extended Markdown Syntax are automatically converted to DOCX/ODT styles. Styles not defined in the template are automatically inserted and given colored highlights for easy recognition and template adaptation.
- **Live reference sidebar** — searchable list of all citations in the current note, with copy and jump-to buttons.
- **No Pandoc required to format citations and references** — built-in pure-JS BibTeX/BibLaTeX parser; Pandoc is opt-in for bibliography processing. (Pandoc and Python are required for document import and export commands.)
- **Multiple bibliography sources** — any number of `.bib` files plus Zotero, all merged; Zotero wins on conflicts
- **Native Zotero 7/8 API** — no Better BibTeX required (BBT still supported for Zotero 6)
- **Mobile support** — works on iOS and Android; tap citations in reading mode for a bottom-sheet card; long-press in editor mode to view a citation without interrupting editing.
- **Citekey autocomplete** — typing `@`, `[@`, or `[[@…` searches citekeys in your Zotero library: prefix matches first, then substring, then fuzzy title/author, even if its note has not yet been imported into Obsidian.
- **Full-text search with `@@`** — typing `@@` switches to title/author full-text search over your entire library. Uses ZotLit's SQLite database when ZotLit is installed; falls back to the plugin's own title-biased index when ZotLit is absent.
- **Smart bracket insertion** — `⌘↵` wraps citations in `[@key]`, detects existing brackets so it never double-wraps.
- **Diacritic-insensitive search** — "Muller" finds "Müller", "Cezanne" finds "Cézanne".
- **Citation decoration** — subtly color-coded citation decorations help you distinguish between unlinked citations, citations that have literature notes, and citations that link to yet-to-be-created literature notes.
- **Citation tooltips** — hover over any citation for a formatted bibliographic reference preview, a link to view or add the literature note, and a link to open the item in Zotero; links are inserted for both linked and unlinked citations.
- **Bibliography snapshot** — save a note's citations as a `.bib` file; colour-coded by sync status
- **Literature note creation** — create literature notes for cited works from the reference sidebar, tooltip, or command palette. Uses ZotLit's templates when available or the plugin's own basic template otherwise. Use commands to import missing notes for all citations in the current note or the whole vault.
- **Insert bibliography at cursor** — dump the full formatted reference list into the note.
- **Convert between formats** — commands to convert pandoc citations (`[see @key, p. 25]`) to linked citations (`[[@key|see @, p. 25]]`) and back, for the current note or the entire vault.
- **Citekey sync** — when a Zotero citekey changes, update all citations across the vault with one command; optional sync of literature note filenames to match.

## Install via BRAT

1. Install [BRAT](https://github.com/TfTHacker/obsidian42-brat)
2. In BRAT settings, add: `nebedaay/ScholarWeave` to the **Beta plugin list**.

## Companion plugins

ScholarWeave works alongside [ZotLit](https://github.com/PKM-er/obsidian-zotlit): when ZotLit is present, literature note creation uses ZotLit's templates, and `@@` autocomplete draws on ZotLit's full-text SQLite database for richer results. Neither plugin requires the other.

## Linked citation syntax

| Wikilink form           | Rendered as                    | Pandoc equivalent           |
| ----------------------- | ------------------------------ | --------------------------- |
| `[[@key]]`              | (Author Year)                  | `[@key]`                    |
| `[[@key\|@]]`           | (Author Year)                  | `[@key]`                    |
| `[[@key\|@ -]]`         | Author (Year)                  | `@key` (narrative)          |
| `[[@key\|-@]]`          | (Year)                         | `[-@key]` (suppress author) |
| `[[@key\|see @, p. 6]]` | (see Author Year, p. 6)        | `[see @key, p. 6]`          |
| `[[@key\|-@, p. 6]]`    | (Year, p. 6)                   | `[-@key, p. 6]`             |
| `[ [[@a]]; [[@b]] ]`    | (Author A Year; Author B Year) | `[@a; @b]` (multi-work)     |

Inside an alias, `@` is a proxy for the link's own citekey. The convert commands translate between linked and pandoc forms losslessly.

See https://pandoc.org/demo/example33/8.20-citation-syntax.html#citation-syntax on pandoc citation formatting.

## Document Compiler and Exporter (outline/markdown → markdown → Word/ODT/PDF)

This plugin includes a command that can export a markdown note or outline that links to a series of notes as a publication-ready `.docx`, `.odt`, or `.pdf` book or article with live Zotero citations. This is designed to allow you to compose longer works using shorter notes, and it can also quickly export a shorter note using the template and options of your choice. It more flexible than other long-form and export plugins to accommodate the more complex requirements of academic documents.

Run **"Compile and export a book, article, or other document (outline or markdown)"** from the command palette (desktop only) with a markdown note open. A dialogue box appears with:

- **Output format**: Markdown, DOCX, ODT, or PDF (requires a pandoc installation; PDF export requires a LibreOffice installation)
- **Template**: Auto-selects the template corresponding to the YAML property, but this can be changed at export time.
- **Document type**: book, article, custom. Applies the defaults for the selected type, which can then be customized
- **Output filename and directory**
- **TOC checkbox** — on by default for `book*` templates, off for `article*` and `document`
- **Restart footnotes per chapter checkbox** — per-chapter numbering (default for `book*`) or continuous numbering across the document (default for `article*`)
- **Chapters/top-level sections start on a new page**: On by default for books.
- **Predefined style mappings** from markdown to the export format (managed in the plugin’s settings dialogue, selectable here)

The input note's `template` property can specify an included or user-defined template. Notes with a `book*` template are by default processed as books with TOC and chapters. Notes with an `article*` template are treated as shorter works with sections. But these settings can be overridden in the export dialogue.

### Outline grammar

When compiling a bullet list of notes, the same rules apply at every bullet level:

| Bullet form | Output |
|---|---|
| `- Heading text` | Heading at that level |
| `- [[Note]]` | Heading (note's title or filename) + note contents |
| `- @@ Heading text` | Numbered heading ("Chapter N: Heading") |
| `- @@[[Note]]` | Numbered heading + note contents |
| `- x [[Note]]` | Note contents only (no heading — appends to the current section) |

**Heading title for linked notes** (in order of preference):
1. The note's YAML `title:` property
2. The base filename, with leading ordering numbers stripped (`1 Introduction` → `Introduction`, `7-1 Details` → `Details`)

**Heading demotion**: When an included note contains section headings, the shallowest heading in the note's body is placed one level below the note's own position in the hierarchy. For example, if a note is included as a `##` section and its body has `# Section` headings, those become `###`, and deeper headings shift by the same offset.

Inline linked `[[@key|alias]]` and pandoc-style `[see @key, p. 25]` citations become live Zotero citation fields in the exported DOCX/ODT, refreshable in Word or LibreOffice.

### PDF export

PDF export goes through an intermediate DOCX or ODT (chosen from the selected template), which LibreOffice converts to PDF — **LibreOffice must be installed**, and an ODT template generally gives better results than DOCX (footnote numbering, figure references). Because a PDF is final, citations are rendered **statically** at export time rather than as live fields: the bibliography and formatted citations appear immediately, using the citation style stored in the template's own Zotero document preferences, or Chicago author-date if none is set. The table of contents, table of figures, figure numbers, and captions are likewise filled in with computed values (chapter-scoped where the template numbers chapters), and TOC/ToF entries are clickable links to their targets — so nothing needs a manual "update fields" pass.

### Available templates

The templates used for `.docx` and `.odt` export is set by the `template:` YAML property, or it can be changed in the export settings dialogue during the export process:

| Template name | Description                                            |
| ------------- | ------------------------------------------------------ |
| `book`        | Book with TOC, chapter headings, per-chapter footnotes |
| `article`     | Article with continuous footnotes, no TOC              |
| `document`    | General-purpose document                               |

Put "compile-" before the template name (`template: compile-book`, `compile-article`, etc.) in an outline note to mark it as an outline — after compilation the prefix is stripped and the compiled file carries `template: book`.

Template lookup order: your configured templates directory → `<vault>/Export Templates/` → the templates bundled with the plugin. Bundled templates are automatically extracted to the plugin's directory on first load and serve as a starting point you can override by placing your own template in either of the earlier locations.

### YAML frontmatter properties

| Key | Purpose |
|---|---|
| `template` | `.docx` template name (e.g. `book2`, `article2`, `document`); `compile-<name>` marks an outline |
| `title` | Cover title (markdown formatting supported) |
| `subtitle` | Cover subtitle |
| `shorttitle` | Even-page header text (falls back to `title` before `:`, then filename) |
| `abstract` | Cover abstract block |
| `note` | Cover note block |
| `author` | Cover author (string or `- Name` list) |

### Automatic style conversion

The export pipeline automatically converts several Obsidian-specific markdown formats into DOCX/ODT paragraph and character styles, preserving your custom styling rather than silently dropping it.

**Callout → paragraph style mapping**: Any Obsidian callout type (e.g. `> [!note]`, `> [!warning]`) can be mapped to a named paragraph style through the plugin's style mapping settings. Poetry callouts (`[!poetry]` and `[!arabic-poetry]`) are handled automatically without configuration. Other callout types remain as block quotes unless explicitly mapped.

**Markdown Attributes and Extended Markdown Syntax**: Inline syntax from the Obsidian Markdown Attributes and Extended Markdown Syntax plugins is automatically recognized and converted to named character styles during export, without any manual configuration:

| Syntax | Plugin | Becomes |
|---|---|---|
| `*text{.cls}*`, `**text{.cls}**`, `***text{.cls}***` | Markdown Attributes | `cls` character style |
| `` `text{.cls}` `` | Markdown Attributes | `cls` character style |
| `==text{.cls}==` | Markdown Attributes | `cls` character style |
| `!!{cls}text!!` | Extended Markdown Syntax | `cls` character style |
| `++text++` | Extended Markdown Syntax | `Inserted` character style |
| `=={color}text==` | Extended Markdown Syntax | `Highlight color` character style |

Style names are derived from the CSS class: the first letter is capitalized and hyphens become spaces, so `.arabic-poetry` becomes "Arabic poetry". You can override the auto-generated name with an explicit mapping in the settings.

**Sentinel styles for undefined styles**: When the exported document references a style not defined in the output template, ScholarWeave automatically injects a sentinel version of that style with a distinctive highlighted background — cycling through yellow, orange, green, blue, and other colours so each undefined style is visually distinct. This makes it easy to locate every instance and define the style properly in your template. Once a style is defined in the template, the highlighting disappears automatically on future exports.

### Dependencies

The Document Compiler requires:

- **Pandoc** — for markdown-to-docx conversion; set its path in settings if auto-detection fails
- **Python 3** with `lxml` and `python-docx` (`pip install lxml python-docx`) — for template merging
- **Node.js (bundled with Obsidian Desktop)** — for citation conversion, automatically met on desktop
- **Zotero** with Better BibTeX or native citation-key support if using Zotero for references.

Because the Compile command is only available on desktop (where Node.js is present), no manual Node.js installation is needed. Pandoc and Python 3 must be installed separately. All export scripts and templates are bundled inside the plugin and extracted automatically on first load — no manual download from GitHub is required.

The plugin resolves these paths itself and passes them to the script via environment variables, so it works even though Obsidian's Electron process doesn't inherit your shell PATH.

## Commands

| Command | Scope | Notes |
|---|---|---|
| Show reference list | — | Open the reference sidebar |
| Insert bibliography at cursor | Current note | Dump formatted reference list into the note |
| Save bibliography snapshot | Current note | Save citations as a `.bib` file |
| Create literature notes for citations lacking notes | Current note | Uses ZotLit templates if available; falls back to plugin template |
| Create literature notes for citations lacking notes | Vault | Same, for all notes |
| Compile and export a document | Current note | Desktop only; opens compile/export modal |
| Convert pandoc citations to linked citations | Current note | `[@key]` → `[[@key]]` |
| Convert pandoc citations to linked citations | Vault | Same, for all notes |
| Revert linked citations to pandoc-style | Current note | `[[@key]]` → `[@key]` |
| Revert linked citations to pandoc-style | Vault | Same, for all notes |
| Update stale citekeys and literature note filenames | Vault | Applies accumulated citekey renames |
| Purge citekey rename history | — | Clear the stored rename records |

## Plugin API

```ts
const plugin = app.plugins.plugins["scholar-weave"] as { api?: ScholarWeaveApi } | undefined;
if (plugin?.api?.version === 1) {
  await plugin.api.focusReferenceListView();
  const citekeys = await plugin.api.getCitekeysForFile(app.workspace.getActiveFile() ?? undefined);
}
```

## Credits

- **Bripey Citation Suite** by [112345brian](https://github.com/112345brian) — the direct upstream fork
- Original plugin by [mgmeyers](https://github.com/mgmeyers/obsidian-pandoc-reference-list), maintained by [obsidian-community](https://github.com/obsidian-community/obsidian-pandoc-reference-list)

This fork incorporates changes from:
- [astroHaoPeng/alp-obsidian-pandoc-reference-list](https://github.com/astroHaoPeng/alp-obsidian-pandoc-reference-list) — file-relative bib paths, multiple bibliography files, auto-update on rename
- [wjvg-gif/obsidian-pandoc-reference-list-zotero8](https://github.com/wjvg-gif/obsidian-pandoc-reference-list-zotero8) — native Zotero 7/8 API mode
- [sjelms/obsidian-pandoc-inline-citations](https://github.com/sjelms/obsidian-pandoc-inline-citations) — DOM fallback fixes, wikilink alias parsing

Diacritic normalization approach credited to [akhmialeuski/obsidian-citation-extended](https://github.com/akhmialeuski/obsidian-citation-extended) (MIT).

See [NOTICE.md](NOTICE.md) for full license attributions.
