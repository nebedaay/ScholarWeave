# Citations and References in Obsidian

How ScholarWeave renders citations and the reference list inside Obsidian, and the settings that control it. For the citation *syntax*, see [Linked Citations](./linked-citations.md). For the style used in *exported* documents, see [Document Import and Export](./import-export.md).

No external tools are needed for anything on this page — Pandoc is not required to format citations.

## Citation style vs. custom citation style

Two different settings, often confused:

- **Citation style** — the CSL style used for inline citations and the reference list, chosen from your installed Zotero styles (set the *Zotero data folder* if it isn't `~/Zotero`) or selected from the built-in list. Defaults to Chicago author-date.
- **Custom citation style** — a path or URL to a specific `.csl` file that overrides the selected style.

A note can override both with frontmatter:

```yaml
---
csl: ./my-style.csl        # a Zotero style name, a .csl path, or a URL
citation-style: apa        # synonym for csl
lang: fr-FR                # citation language
---
```

The export dialogue can additionally apply a style to a single export without changing these defaults.

## Rendering

- **Process linked citations** — format `[[@key|…]]` wikilinks as citations. Turn off to leave them as ordinary links.
- **Render live preview inline citations** — format citations in the editor's live preview.
- **Render reading mode inline citations** — format citations in reading view.
- **Link citations to literature notes** — make rendered citations link to the `@citekey` literature note.
- **Hide links in references** — replace the link text in the reference list with a compact icon to save space.
- **Show PDF links in references** — add an "open PDF" button per reference (off by default; opening the item in Zotero already reveals all attachments).

## Decoration and tooltips

- **Citation decoration** — colour-codes citations so you can tell at a glance whether a work has a literature note; sub-options choose the colours.
- **Show citekey tooltips** — hover a citation for a formatted reference preview, a link to view or create the literature note, and a link to open the item in Zotero.
- **Tooltip delay** — how long to hover before the tooltip appears.
- **Mobile tap action** — what tapping a citation does on mobile. See [Mobile](./mobile.md).

## Autocomplete and search

- Typing `@` (or `[@`, `[[@…`) opens citekey autocomplete: prefix matches first, then substring, then fuzzy title/author — including references with no literature note yet.
- Typing `@@` switches to full-text title/author search (spaces allowed; a period closes it). With ZotLit installed this uses ZotLit's database; otherwise the plugin's own index.
- Search is diacritic-insensitive ("Muller" finds "Müller").
- `⌘↵` / `Ctrl+↵` wraps the selected key in `[@key]` unless you are already inside brackets.

## Reference sidebar and bibliography commands

- **Show reference list** opens the sidebar: every citation in the current note, searchable, with copy and jump buttons.
- **Insert bibliography at cursor** inserts the formatted reference list into the note.
- **Save bibliography snapshot for this note** writes the note's citations to a `.bib` file and records it in the note's `bibliography` frontmatter. The editor then colour-codes citekeys by sync status: blue = in your library and the snapshot, yellow = in your library but not the snapshot, red = not found.

See [Commands](./commands.md) for the full command list.
