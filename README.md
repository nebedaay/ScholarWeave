# ScholarWeave

An Obsidian plugin that weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow.

ScholarWeave's signature feature is **linked citations**: `[[@citekey|see @, p. 25]]` is simultaneously a formatted inline citation _and_ a real wikilink to the literature note for that source. Citations stay connected to your note network — they're not just text.

This is a fork of **Bripey Citation Suite** (which itself descends from Pandoc Reference List), renamed to reflect its distinct identity.

## Features

- **Linked citations** — `[[@key|alias]]` wikilinks render as formatted citations and stay live links to literature notes; `[@citekey]` pandoc-style citations work too
- **Bare-`@` proxy** — inside a wikilink alias, `@` expands to the link's own citekey: `[[@smith1992|see @, p. 6]]` → (Smith 1992, 6)
- **Live reference sidebar** — filterable list of all citations in the current note, with copy and jump-to buttons
- **No Pandoc required** — pure-JS BibTeX/BibLaTeX parser built in; Pandoc is opt-in
- **Multiple bibliography sources** — any number of `.bib` files plus Zotero, all merged; Zotero wins on conflicts
- **Native Zotero 7/8 API** — no Better BibTeX required (BBT still supported for Zotero 6)
- **Mobile support** — works on iOS and Android; tap citations in reading mode for a bottom-sheet card; long-press in editor mode to view a citation without interrupting editing
- **Citekey autocomplete** — typing `@` or `[[@…` opens citekey-first search over your Zotero/bibliography index: prefix matches first, then substring, then fuzzy title/author — so `[@pickus` finds `pickusImmigrationCitizenship1998` immediately, including references not yet imported into Obsidian; `@@` triggers full-text title/author search
- **Smart bracket insertion** — `⌘↵` wraps citations in `[@key]`, detects existing brackets so it never double-wraps
- **Diacritic-insensitive search** — "Muller" finds "Müller", "Cezanne" finds "Cézanne"
- **Citation decoration** — citekeys highlighted in accent color in the editor; customizable via Style Settings
- **Citekey tooltips** — hover over any citekey for a formatted citation preview
- **Bibliography snapshot** — save a note's citations as a `.bib` file; colour-coded to show sync status
- **Literature note creation** — create and open literature notes directly from the sidebar or tooltip
- **Insert bibliography at cursor** — dump the full formatted reference list into the note
- **Convert between formats** — commands to convert pandoc citations (`[@key]`) to linked citations (`[[@key]]`) and back, for the current note or the entire vault
- **Link citations to literature notes** — rendered `[@citekey]` citations become clickable links when a matching note exists
- **Book Compiler** — compile a bulleted outline of notes into a single markdown document, then export to Word `.docx` (TOC, per-chapter footnotes, figure captions, Zotero citation fields) via one command

## Install via BRAT

1. Install [BRAT](https://github.com/TfTHacker/obsidian42-brat)
2. In BRAT settings, add: `nebedaay/ScholarWeave`

## Companion plugins

ScholarWeave works alongside [ZotLit](https://github.com/PKM-er/obsidian-zotlit): when ZotLit is present, literature note creation uses ZotLit's templates, and `@@` autocomplete uses ZotLit's full-text database. Neither plugin requires the other.

## Book Compiler (outline → markdown → Word)

Run **"Compile and export a book (outline or markdown)"** from the command palette (desktop only) with a markdown note open. A modal appears with:

- **TOC checkbox** — on by default for `book*` templates, off for `article*` and `document`
- **Global footnotes checkbox** — per-chapter or continuous numbering
- **Output folder** — vault-relative, absolute, or `~/…`; blank = source file's folder
- **Two buttons** — "Compile to markdown" or "Compile & export to docx"

### Outline grammar

- `- @@ Chapter Title` — a numbered chapter (`Chapter 1: Chapter Title`)
- `- [[Note]]` — includes the linked note's content
- `- Any other text` — a heading (nested bullets become sub-headings)
- Inline `[[@key|alias]]` citations become Zotero citation fields in the exported docx

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