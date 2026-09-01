---
created: 2026-08-26 12:46
up:
  - "[[Unassigned]]"
related:
aliases:
---
# ScholarWeave

An Obsidian plugin that weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow.

ScholarWeave's signature feature is **linked citations**: `[[@citekey|see @, p. 25]]` is simultaneously a formatted inline citation _and_ a real wikilink to the literature note for that source. Citations stay connected to your note network — they're not just text.

This is a fork of **Bripey Citation Suite** (which itself descends from Pandoc Reference List), renamed to reflect its distinct identity.

## Features

- **Linked citations** — `[[@key|alias]]` wikilinks render as formatted citations while linking to literature notes, making them part of your Obsidian thought universe; `[@citekey]` pandoc-style citations work too
- **Bare-`@` proxy** — inside a wikilink alias, `@` expands to the link's own citekey: `[[@smith1992|see @, p. 6]]` → (Smith 1992, 6)
- **Live reference sidebar** — filterable list of all citations in the current note, with copy and jump-to buttons
- **No Pandoc required for citation formatting** — pure-JS BibTeX/BibLaTeX parser built in; Pandoc is opt-in. (Pandoc is required for document import and export commands.)
- **Multiple bibliography sources** — any number of `.bib` files plus Zotero, all merged; Zotero wins on conflicts
- **Native Zotero 7/8 API** — no Better BibTeX required (BBT still supported for Zotero 6)
- **Mobile support** — works on iOS and Android; tap citations in reading mode for a bottom-sheet card; long-press in editor mode to view a citation without interrupting editing
- **Citekey autocomplete** — typing `@` or `[[@…` opens citekey-first search over your Zotero/bibliography index: prefix matches first, then substring, then fuzzy title/author — so `[@pickus` finds `pickusImmigrationCitizenship1998` immediately, including references not yet imported into Obsidian; `@@` triggers full-text title/author search
- **Smart bracket insertion** — `⌘↵` wraps citations in `[@key]`, detects existing brackets so it never double-wraps
- **Diacritic-insensitive search** — "Muller" finds "Müller", "Cezanne" finds "Cézanne"
- **Citation decoration** — subtly color-coded citation decorations help you distinguish between unlinked citations, citations that have literature notes, and citations that link to yet-to-be-created literature notes.
- **Citekey tooltips** — hover over any citekey for a formatted citation preview, a link to view or add the literature note, and a link to open the item in Zotero. Links are inserted for both linked and unlinked citations.
- **Bibliography snapshot** — save a note's citations as a `.bib` file; colour-coded to show sync status
- **Literature note creation** — create and open literature notes directly from the sidebar or tooltip
- **Insert bibliography at cursor** — dump the full formatted reference list into the note
- **Convert between citation formats** — commands to convert pandoc citations (`[@key]`) to linked citations (`[[@key]]`) and back, for the current note or the entire vault
- **Document Compiler** — compile a bulleted outline of notes into a single markdown article or book, then export to Word `.docx` or `.odt` (TOC, per-chapter footnotes, figure captions, Zotero citation fields) via one command
- **Import literature notes for all cited works** in the note or vault. Requires ZotLit.
- **Automatically sync/update stale citekeys and literature note names** when citekeys change in Zotero, making sure the links in your Obsidian thought universe still work.
- **Import DOCX and ODT files containing Zotero references** into Obsidian markdown notes with standard pandoc citations or the plugin’s signature linked citations.

## Install via BRAT

1. Install the [BRAT](https://github.com/TfTHacker/obsidian42-brat) plugin from the Obsidian community plugins repository.
2. In BRAT settings, add `nebedaay/ScholarWeave` to the **Beta plugin list**.

## Companion plugins

ScholarWeave works alongside [ZotLit](https://github.com/PKM-er/obsidian-zotlit): when ZotLit is present, literature note creation uses ZotLit's templates, and `@@` autocomplete uses ZotLit's full-text database. Neither plugin requires the other.

## Document Compiler (outline → markdown → Word)

This plugin includes a command that can export a markdown note or outline that links to notes as a publication-ready `.docx` or `.odt` book or article with live Zotero citations. This is designed to allow you to compose longer works using shorter notes. It is designed to be more flexible than other long-form and export plugins to accommodate the more complex requirements of academic documents.

Run **"Compile and export a book or article (outline or markdown)"** from the command palette (desktop only) with a markdown note open. A modal appears with:

- **TOC checkbox** — on by default for `book*` templates, off for `article*` and `document`
- **Global footnotes checkbox** — per-chapter or continuous numbering
- **Output folder** — vault-relative, absolute, or `~/…`; blank = source file's folder
- **Two buttons** — "Compile to markdown" or "Compile & export to docx"

The input note’s `template` property can specify an included or user-defined template. Notes with a `book*` template are processed as books with TOC and chapters. Notes with `article*` template are assumed to be shorter works with sections.

### Outline grammar

- `- Chapter/Section Title` — an unnumbered chapter of a book or main section of an article
- `- @@ Chapter Title` — a numbered chapter (`Chapter 1: Chapter Title`) or article section
- `- [[Note]]` — inserts the note’s "title" property or base filename as a chapter or section header, includes the linked note's content.
- `- Any other text` — a heading (nested bullets become sub-headings)
- Inline linked `[[@key|alias]]` and pandoc-style `[see @key, p. 25]` citations become Zotero citation fields in the exported docx.

### YAML frontmatter keys

|Key|Purpose|
|---|---|
|`template`|`.docx` template name (e.g. `book2`, `article2`, `document`)|
|`title`|Cover title (markdown formatting supported)|
|`subtitle`|Cover subtitle|
|`shorttitle`|Even-page header (falls back to `title` before `:`, then filename)|
|`abstract`|Cover abstract block|
|`note`|Cover note block|
|`author`|Cover author (string or `- Name` list)|

### Dependencies

- **Python 3** with `lxml` and `python-docx` (`pip install lxml python-docx`)
- **Node.js**
- **Pandoc** (set its path in settings if auto-detection fails)
- **Zotero** with Better BibTeX or native citation-key support


## Plugin API

```ts
const plugin = app.plugins.plugins["scholar-weave"] as { api?: ScholarWeaveApi } | undefined;
if (plugin?.api?.version === 1) {
  await plugin.api.focusReferenceListView();
  const citekeys = await plugin.api.getCitekeysForFile(app.workspace.getActiveFile() ?? undefined);
}
```

## Documentation

- [Setup guide](https://claude.ai/cowork/docs/setup.md) — installation, bibliography formats, frontmatter keys, CSL styles
- [Zotero integration](https://claude.ai/cowork/docs/zotero.md) — native API, Better BibTeX, library selection, multi-source merge
- [Mobile](https://claude.ai/cowork/docs/mobile.md) — tap/long-press behaviour, file picker, limitations

## Changelog

See [release-notes.md](https://claude.ai/cowork/release-notes.md) for a full version history.

## Credits

- **Bripey Citation Suite** by [112345brian](https://github.com/112345brian) — the direct upstream fork
- Original plugin by [mgmeyers](https://github.com/mgmeyers/obsidian-pandoc-reference-list), maintained by [obsidian-community](https://github.com/obsidian-community/obsidian-pandoc-reference-list)

This fork incorporates changes from:

- [astroHaoPeng/alp-obsidian-pandoc-reference-list](https://github.com/astroHaoPeng/alp-obsidian-pandoc-reference-list) — file-relative bib paths, multiple bibliography files, auto-update on rename
- [wjvg-gif/obsidian-pandoc-reference-list-zotero8](https://github.com/wjvg-gif/obsidian-pandoc-reference-list-zotero8) — native Zotero 7/8 API mode
- [sjelms/obsidian-pandoc-inline-citations](https://github.com/sjelms/obsidian-pandoc-inline-citations) — DOM fallback fixes, wikilink alias parsing

Diacritic normalization approach credited to [akhmialeuski/obsidian-citation-extended](https://github.com/akhmialeuski/obsidian-citation-extended) (MIT).

See [NOTICE.md](https://claude.ai/cowork/NOTICE.md) for full license attributions.