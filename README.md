# ScholarWeft

<img src="./images/scholarweft-illustration.png" width="300" alt="ScholarWeft logo">

Weaving your ideas, Obsidian, Zotero, and word processor output into a connected scholarly workflow. 

ScholarWeft's unique **linked citations** format weaves every work you cite into your interconnected Obsidian thought universe. The citation `[[@sanchez2009|see @, p. 25]]` is simultaneously a formatted inline citation — "(see Sanchez 2009, 25)" — *and* an Obsidian wikilink to that source’s literature note. Other citation plugins can either link to a literature note (`[[@sanchez2009]]`) or render pandoc-formatted citations (`[see @sanchez2009, p. 25]`), preventing you from integrating publication-ready citations as nodes in Obsidian's note network visualized in backlinks and graphs.

Beyond linking your scholarly notes and references, ScholarWeft links your writing inside Obsidian to the world beyond Obsidian. Import DOCX and ODT documents as Obsidian notes, converting their Zotero citations to linked citations and importing a literature note for each cited work. Export an Obsidian note or compile a series of notes as a publication-ready DOCX, ODT, or PDF document with functioning citations — so you can do all your academic writing inside manageable, interlinked Obsidian notes, even long book projects.

This plugin started as a fork of [Bripey Citation Suite](https://github.com/112345brian/bripey-citation-suite), which itself descends from [Pandoc Reference List](https://github.com/community-archive/obsidian-pandoc-reference-list). It was renamed to reflect its distinctive and more comprehensive functionality.

## Documentation

- [Setup](./docs/setup.md) — install and first steps
- [Dependencies](./docs/dependencies.md) — what each feature needs, and where to get it
- [Linked Citations](./docs/linked-citations.md) — the citation syntax
- [Citations and References](./docs/citations.md) — formatting, autocomplete, tooltips, the reference sidebar
- [Bibliography](./docs/bibliography.md) — `.bib`/CSL sources, per-note overrides
- [Zotero](./docs/zotero.md) — connection modes, port, libraries
- [Literature Notes](./docs/literature-notes.md) — where notes live and how they are created
- [Document Import and Export](./docs/import-export.md) — compile/export to markdown, DOCX, ODT, PDF
- [Commands](./docs/commands.md) — the full command list
- [ZotLit Import Templates](./docs/zotlit-import-templates.md) — rich annotation templates
- [Mobile](./docs/mobile.md) — iOS/Android behaviour

## Features

Most features work with **no external tools** (no Pandoc, no Zotero) when you use a bibliography file. The ones that don't note it inline; see [Dependencies](./docs/dependencies.md) for details.

### Citations

- **Linked citations** — `[[@smith1992|see @, p. 6]]` → (see Smith 1992, 6): real Obsidian wikilinks *and* publication-ready formatted citations. See [Linked Citations](./docs/linked-citations.md).
- **Conventional pandoc citations** — `[@key]`, `[see @key, p. 25]` render too, and commands convert between formats losslessly.
- **Live reference sidebar** — a searchable list of every citation in the current note, with copy and jump buttons.
- **Insert bibliography at cursor** and **bibliography snapshot** (save a note's citations as a `.bib`, colour-coded by sync status).
- **Citekey autocomplete and full-text search** — `@` searches citekeys (prefix → substring → fuzzy), `@@` searches titles/authors across your library.
- **Smart bracket insertion** — `⌘↵` wraps the selection in `[@key]` without double-wrapping.
- **Diacritic-insensitive search** — "Muller" finds "Müller".
- **Citation decoration and tooltips** — colour-coded status; hover for a formatted preview, literature-note link, and Zotero link.
- **Mobile support** — tap citations in reading mode, long-press in the editor. See [Mobile](./docs/mobile.md).

### References and literature notes

- **Multiple bibliography sources** — any number of `.bib`/CSL-JSON/CSL-YAML files plus Zotero, merged; Zotero wins on conflicts. See [Bibliography](./docs/bibliography.md).
- **Native Zotero 7/8 API** — no Better BibTeX needed to resolve and format citations (BBT still required for Zotero 6, and still the easiest way to auto-generate citekeys). See [Zotero](./docs/zotero.md).
- **Literature note creation** — create notes for cited works from the sidebar, tooltip, or command palette; uses ZotLit when available. See [Literature Notes](./docs/literature-notes.md).
- **Citekey sync** — update citations across the vault and rename literature notes when a Zotero citekey changes.

### Document import and export (desktop only)

- **Compile and export** a note or a multi-note outline as markdown, DOCX, ODT, or PDF with one command, with fine-grained options (TOC, per-chapter footnotes, figure captions, custom styles). See [Document Import and Export](./docs/import-export.md).
- **Import DOCX/ODT** documents as markdown notes, converting their Zotero citation fields to linked citations.
- **Custom callouts and markdown → DOCX/ODT styles** — Markdown Attributes / Extended Markdown Syntax styles are converted automatically; undefined styles get a highlighted sentinel so you can define them.

### Requirements at a glance

Basic citation work needs nothing installed. Document import/export and live Zotero fields require installing some combination of Python 3, Pandoc, Zotero/Better BibTeX, LibreOffice, and/or a LaTeX distribution — see **[Dependencies](./docs/dependencies.md)** for the exact mapping and download links. The plugin detects what is installed and greys out options that can't run.

## Install via BRAT

1. Disable Restricted Mode, then install and enable [BRAT](https://github.com/TfTHacker/obsidian42-brat) from the Community Plugins list.
2. In BRAT's settings, add `nebedaay/ScholarWeft` to the **Beta plugin list**.
3. Enable **ScholarWeft** in Community Plugins. BRAT keeps it updated.

See [Setup](./docs/setup.md) for first steps.

## Companion plugins

ScholarWeft works alongside [ZotLit](https://github.com/PKM-er/obsidian-zotlit): when ZotLit is present, literature note creation uses ZotLit's templates and `@@` autocomplete draws on ZotLit's full-text database. Neither plugin requires the other.

**One-click ZotLit templates:** *Settings → ScholarWeft → "Install and use ScholarWeft's ZotLit import templates"* copies a curated set of templates into `sw-zotlit-templates/` and points ZotLit's *Template folder* setting there (ZotLit reloads automatically), leaving your own templates untouched. See [ZotLit Import Templates](./docs/zotlit-import-templates.md).

## Plugin API

```ts
const plugin = app.plugins.plugins["scholar-weft"] as { api?: ScholarWeftApi } | undefined;
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
