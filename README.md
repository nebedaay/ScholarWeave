# ScholarWeave

An Obsidian plugin that weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow.

ScholarWeave's signature feature is **linked citations**: `[[@citekey|see @, p. 25]]` is simultaneously a formatted inline citation *and* a real wikilink to the literature note for that source. Citations stay connected to your note network — they're not just text.

This is a fork and expansion of **[Bripey Citation Suite](https://github.com/112345brian/bripey-citation-suite)** (which itself descends from [Pandoc Reference List](https://github.com/community-archive/obsidian-pandoc-reference-list)), renamed to reflect its distinct and more comprehensive functionality.

## Features

- **Linked citations** — `[[@key|alias]]` wikilinks render as formatted citations while linking to literature notes, making them part of your Obsidian thought universe; `[@citekey]` pandoc-style citations work too. 
- **Convenient syntax for representing pandoc-style citations in link aliases** — inside the alias of a wikilink containing a `@citekey`, the citekey can be shortened to `@`: `[[@smith1992|see @, p. 6]]` → (Smith 1992, 6)
- **Live reference sidebar** — filterable list of all citations in the current note, with copy and jump-to buttons
- **No Pandoc required for citations** — pure-JS BibTeX/BibLaTeX parser built in; Pandoc is opt-in for bibliography processing. (Pandoc is required for document import and export commands.)
- **Multiple bibliography sources** — any number of `.bib` files plus Zotero, all merged; Zotero wins on conflicts
- **Native Zotero 7/8 API** — no Better BibTeX required (BBT still supported for Zotero 6)
- **Mobile support** — works on iOS and Android; tap citations in reading mode for a bottom-sheet card; long-press in editor mode to view a citation without interrupting editing.
- **Citekey autocomplete** — typing `@` or `[[@…` opens citekey-first search over your Zotero/bibliography index: prefix matches first, then substring, then fuzzy title/author — so `[@smith` finds `smithVeryImportant1998` immediately, even if its note has not yet been imported into Obsidian.
- **Full-text search with `@@`** — typing `@@` switches to title/author full-text search over your entire library. Uses ZotLit's SQLite database when ZotLit is installed; falls back to the plugin's own title-biased index when ZotLit is absent.
- **Smart bracket insertion** — `⌘↵` wraps citations in `[@key]`, detects existing brackets so it never double-wraps.
- **Diacritic-insensitive search** — "Muller" finds "Müller", "Cezanne" finds "Cézanne".
- **Citation decoration** — subtly color-coded citation decorations help you distinguish between unlinked citations, citations that have literature notes, and citations that link to yet-to-be-created literature notes
- **Citekey tooltips** — hover over any citekey for a formatted citation preview, a link to view or add the literature note, and a link to open the item in Zotero; links are inserted for both linked and unlinked citations
- **Bibliography snapshot** — save a note's citations as a `.bib` file; colour-coded by sync status
- **Literature note creation** — create literature notes from the sidebar or tooltip, using ZotLit's templates when available or the plugin's own basic template otherwise; commands available for the current note or the whole vault
- **Insert bibliography at cursor** — dump the full formatted reference list into the note
- **Link citations to literature notes** — rendered `[@citekey]` citations become clickable links when a matching note exists
- **Convert between formats** — commands to convert pandoc citations (`[@key]`) to linked citations (`[[@key]]`) and back, for the current note or the entire vault
- **Citekey sync** — when a Zotero citekey changes, update all citations across the vault with one command; optional sync of literature note filenames to match
- **Document Compiler** — compile a bulleted outline of notes into a single markdown document, then export to Word `.docx` or `.odt` (TOC, per-chapter footnotes, figure captions, Zotero citation fields) via one command with a modal for all options (desktop only)

## Install via BRAT

1. Install [BRAT](https://github.com/TfTHacker/obsidian42-brat)
2. In BRAT settings, add: `nebedaay/ScholarWeave`

## Companion plugins

ScholarWeave works alongside [ZotLit](https://github.com/PKM-er/obsidian-zotlit): when ZotLit is present, literature note creation uses ZotLit's templates, and `@@` autocomplete draws on ZotLit's full-text SQLite database for richer results. Neither plugin requires the other.

## Document Compiler (outline → markdown → Word)

This plugin includes a command that can export a markdown note or outline that links to notes as a publication-ready `.docx` or `.odt` book or article with live Zotero citations. This is designed to allow you to compose longer works using shorter notes. It is designed to be more flexible than other long-form and export plugins to accommodate the more complex requirements of academic documents.

Run **"Compile and export a book, article, or other document (outline or markdown)"** from the command palette (desktop only) with a markdown note open. A dialogue box appears with:

- **TOC checkbox** — on by default for `book*` templates, off for `article*` and `document`
- **Global footnotes checkbox** — per-chapter numbering (default for `book*`) or continuous numbering across the document (default for `article*`)
- **Output folder** — vault-relative, absolute, or `~/…`; blank uses the source file's own folder
- **Two buttons** — "Compile to markdown" or "Compile & export to docx"

The input note's `template` property can specify an included or user-defined template. Notes with a `book*` template are processed as books with TOC and chapters. Notes with an `article*` template are treated as shorter works with sections.

### Outline grammar

The same rules apply at every bullet level:

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

Inline linked `[[@key|alias]]` and pandoc-style `[see @key, p. 25]` citations become Zotero citation fields in the exported docx.

### Available templates

The template used for `.docx` export is set by the `template:` YAML property:

| Template name | Description |
|---|---|
| `book2` | Book with TOC, chapter headings, per-chapter footnotes |
| `article2` | Article with continuous footnotes, no TOC |
| `document` | General-purpose document |

Set `template: compile-book2` (or `compile-article2`, etc.) in the YAML of an outline note to mark it as an outline — after compilation the prefix is stripped and the compiled file carries `template: book2`.

Template lookup order: your configured templates directory → `<vault>/Export Templates/` → the templates bundled with the plugin.

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

### Dependencies

The Document Compiler requires:

- **Pandoc** — for markdown-to-docx conversion; set its path in settings if auto-detection fails
- **Python 3** with `lxml` and `python-docx` (`pip install lxml python-docx`) — for template merging
- **Node.js** — for citation conversion; bundled with Obsidian Desktop, so this requirement is automatically met on desktop
- **Zotero** with Better BibTeX or native citation-key support

Because the Compile command is only available on desktop (where Node.js is present), no manual Node.js installation is needed. Pandoc and Python 3 must be installed separately.

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

## Linked citation syntax

| Wikilink form | Rendered as | Pandoc equivalent |
|---|---|---|
| `[[@key]]` | (Author Year) | `[@key]` |
| `[[@key\|@]]` | (Author Year) | `[@key]` |
| `[[@key\|@ -]]` | Author (Year) | `@key` (narrative) |
| `[[@key\|-@]]` | (Year) | `[-@key]` (suppress author) |
| `[[@key\|see @, p. 6]]` | (see Author Year, p. 6) | `[see @key, p. 6]` |
| `[[@key\|-@, p. 6]]` | (Year, p. 6) | `[-@key, p. 6]` |
| `[ [[@a]]; [[@b]] ]` | (Author A Year; Author B Year) | `[@a; @b]` (multi-work) |

Inside an alias, `@` is a proxy for the link's own citekey. The convert commands translate between linked and pandoc forms losslessly.

## Credits

- **Bripey Citation Suite** by [112345brian](https://github.com/112345brian) — the direct upstream fork
- Original plugin by [mgmeyers](https://github.com/mgmeyers/obsidian-pandoc-reference-list), maintained by [obsidian-community](https://github.com/obsidian-community/obsidian-pandoc-reference-list)

This fork incorporates changes from:
- [astroHaoPeng/alp-obsidian-pandoc-reference-list](https://github.com/astroHaoPeng/alp-obsidian-pandoc-reference-list) — file-relative bib paths, multiple bibliography files, auto-update on rename
- [wjvg-gif/obsidian-pandoc-reference-list-zotero8](https://github.com/wjvg-gif/obsidian-pandoc-reference-list-zotero8) — native Zotero 7/8 API mode
- [sjelms/obsidian-pandoc-inline-citations](https://github.com/sjelms/obsidian-pandoc-inline-citations) — DOM fallback fixes, wikilink alias parsing

Diacritic normalization approach credited to [akhmialeuski/obsidian-citation-extended](https://github.com/akhmialeuski/obsidian-citation-extended) (MIT).

See [NOTICE.md](NOTICE.md) for full license attributions.
