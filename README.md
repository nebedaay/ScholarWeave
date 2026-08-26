# ScholarWeave

<<<<<<< HEAD
This Obsidian plugin seamlessly weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow. Using **linked citations**, it transforms your literature notes into **nodes** in your note network. From there, it allows you to shuttle your work between your linked thought universe and your audience out there through converting between linked Obsidian notes and formats such as DOCX and ODT.
=======
This Obsidian plugin seamlessly weaves together your universe of interlinked Obsidian notes, your Zotero library, and your publication workflow. Using **linked citations**, it transforms your literature notes into **nodes** in your note network. From there, it lets you shuttle your work between your linked thought universe and your audience through converting between linked Obsidian notes and publication formats such as DOCX and ODT.
>>>>>>> 2e85742 (Rename to ScholarWeave; add revert-to-pandoc commands)

It provides essential functions for integrating references, notes, and writing output into a single process:

- Like many plugins, it allows you to **locate and insert Zotero citations** using pandoc-style formatting.
<<<<<<< HEAD
- What’s unique is that **fully formatted citations** are literal **links** in your Obsidian thought universe, not dead ends. While other plugins allow you to choose betweeen full pandoc-style citation formatting—[see, for example, @citekey, p. 25]—and simple [[@citekey]] wikilinks, this plugin allows you to combine **both** **citation linking** and **pandoc-style formatting**. In short, your writing can now be **fully at home in the linked Obsidian thought universe** and **ready for sharing and publication outside Obsidian**. To reconcile standard Obsidian wikilinks with tailored citation formatting, this plugin uses a modified pandoc syntax: [see @citekey, p. 25] becomes [[@citekey|see @, p. 25]].
- **Import DOCX and ODT files containing Zotero references** into Obsidian markdown notes with standard pandoc citations or the plugin’s signature linked citations.
- **Export a note** into an article or book in DOCX or ODT format, converting standard pandoc and linked citations to **live Zotero citations**.
- **Compile a simple bullet list of notes** into a publication-ready article or book in markdown, DOCX, or ODT format.
- **Sync/update all citekeys** in a file or vault, including the filenames of imported Zotero notes, whenever citekeys change in Zotero. No more stale citekeys.
- **Convert** all standard pandoc citations in a note or vault to the new linked format.
- **Revert** linked citations to standard pandoc-style citations for use in contexts where the plugin is not in use.
- **Import all references cited** in a note or vault from Zotero (this requires the ZotLit plugin).
=======
- What's unique is that **fully formatted citations** are literal **links** in your Obsidian thought universe, not dead ends. While other plugins make you choose between full pandoc-style citation formatting — `[see, for example, @citekey, p. 25]` — and simple `[[@citekey]]` wikilinks, this plugin gives you **both at once**. Your writing can be **fully at home in the linked Obsidian thought universe** and **ready for sharing and publication outside Obsidian**. To reconcile standard Obsidian wikilinks with tailored citation formatting, it uses a modified pandoc syntax: `[see @citekey, p. 25]` becomes `[[@citekey|see @, p. 25]]`.
- **Import DOCX and ODT files containing Zotero references** into Obsidian markdown notes with standard pandoc citations or the plugin's signature linked citations.
- **Export a note** into an article or book in DOCX or ODT format, converting standard pandoc and linked citations to **live Zotero citations**.
- **Compile a simple bullet list of notes** into a publication-ready article or book in markdown, DOCX, or ODT format.
- **Sync/update all citekeys** in a file or vault, including the filenames of literature notes imported from Zotero, whenever citekeys change in Zotero. No more stale citekeys.
- **Convert** all standard pandoc citations in a note or vault to the linked format.
- **Revert** linked citations to standard pandoc-style citations for use in contexts where the plugin is not in use.
- **Import all references cited** in a note or vault from Zotero (requires the ZotLit plugin).

Additional capabilities:

- **Live reference sidebar** — filterable list of all citations in the current note, with copy and jump-to buttons
- **No Pandoc required for bibliography** — pure-JS BibTeX/BibLaTeX parser built in; Pandoc is still used for document export
- **Multiple bibliography sources** — any number of `.bib` files plus Zotero, all merged; Zotero wins on conflicts
- **Native Zotero 7/8 API** — no Better BibTeX plugin required (BBT still supported for Zotero 6)
- **Mobile support** — works on iOS and Android; tap citations in reading mode for a bottom-sheet card
- **Citekey autocomplete** — typing `@` (or `[[@…`) opens citekey-first search over your Zotero/bibliography index; `@@` triggers full-text title/author search
- **Diacritic-insensitive search** — "Muller" finds "Müller", "Cezanne" finds "Cézanne"
- **Bibliography snapshot** — save a note's citations as a `.bib` file with one click
- **Citation decoration** — citekeys highlighted in the editor; customizable via Style Settings
>>>>>>> 2e85742 (Rename to ScholarWeave; add revert-to-pandoc commands)

# Companion files/plugins

<<<<<<< HEAD
For full functionality, such as bulk-importing Zotero references and importing/exporting documents with citations, you’ll need to install the ZotLit plugin, pandoc, and python.
=======
1. Install [BRAT](https://github.com/TfTHacker/obsidian42-brat)
2. In BRAT settings, add: `josephhill/scholar-weave`

## Companion plugins and files

For full functionality — bulk-importing Zotero references, importing and exporting documents with citations — you'll also need the **ZotLit** plugin, **Pandoc**, and **Python 3**.

A comprehensive companion **ZotLit import template** is included that creates literature notes optimized for the links-over-tags note style, converting Zotero tags and annotations into Obsidian links.

## Book Compiler (outline → markdown → Word)

Run **"Compile and export a book (outline or markdown)"** from the command palette (desktop only) with a markdown note open. A modal appears with:

- **TOC checkbox** — on by default for `book*` templates, off for `article*` and `document`
- **Global footnotes checkbox** — per-chapter numbering by default for `book*` templates, continuous for `article*`
- **Output folder** — where the compiled `.md` and exported `.docx` land (vault-relative, absolute, or `~/…`; blank = same folder as source)
- **Two buttons** — "Compile to markdown" or "Compile & export to docx"

### Outline grammar

- `- @@ Chapter Title` — a numbered chapter (`Chapter 1: Chapter Title`)
- `- [[Note]]` — a bare link bullet includes the linked note's content
- `- Any other text` — a heading (nested bullets become sub-headings)
- Inline `[[@key|alias]]` citations anywhere in prose become Zotero citation fields in the exported docx; footnotes are renumbered per chapter or globally

### YAML frontmatter properties

| Key | Purpose |
| --- | --- |
| `template` | `.docx` template name (e.g. `book2`, `article2`, `document`). `compile-<name>` marks an outline and is rewritten to `<name>` in the compiled file |
| `title` | Cover title |
| `subtitle` | Cover subtitle |
| `shorttitle` | Even-page header (falls back to `title` before `:`, then the filename) |
| `abstract` | Cover abstract block |
| `author` | Cover author (string or `- Name` list) |

### Dependencies

- **Python 3** with `lxml` and `python-docx` (`pip install lxml python-docx`)
- **Pandoc** (set its path in settings if auto-detection fails)
- **Zotero** with Better BibTeX or native citation-key support, so citation fields render in Word

Template lookup order: your configured templates directory → `<vault>/Export Templates/` → templates bundled with the plugin.
>>>>>>> 2e85742 (Rename to ScholarWeave; add revert-to-pandoc commands)

I’ve also created a comprehensive companion ZotLit import template that imports everything you need to create a literature note that is a living, breathing part of your thought universe. It’s optimized for the links-over-tags note style, converting all tags in Zotero references and annotations as links.

# Credits

<<<<<<< HEAD
This plugin started as a fork of the [Bripey Citation Suite](https://github.com/112345brian/bripey-citation-suite), itself a form of [Pandoc Reference List](https://github.com/community-archive/obsidian-pandoc-reference-list) by [mgmeyers](https://github.com/mgmeyers) and the Obsidian community. I incorporated parts of the [Better BibTex’s pandoc export lua filter](https://github.com/retorquere/zotero-better-bibtex/blob/master/pandoc/zotero.lua) by [retorquere](https://github.com/retorquere).
=======
## Plugin API

Other plugins can access ScholarWeave's public API:

```ts
const plugin = app.plugins.plugins["scholar-weave"] as { api?: ScholarWeaveApi } | undefined;
if (plugin?.api?.version === 1) {
  await plugin.api.focusReferenceListView();
  const citekeys = await plugin.api.getCitekeysForFile(app.workspace.getActiveFile() ?? undefined);
}
```

## Credits

This plugin started as a fork of the [Bripey Citation Suite](https://github.com/112345brian/bripey-citation-suite), itself a fork of [Pandoc Reference List](https://github.com/community-archive/obsidian-pandoc-reference-list) by [mgmeyers](https://github.com/mgmeyers) and the Obsidian community. It also incorporates:

- [astroHaoPeng/alp-obsidian-pandoc-reference-list](https://github.com/astroHaoPeng/alp-obsidian-pandoc-reference-list) — file-relative bib paths, multiple bibliography files, auto-update on rename
- [wjvg-gif/obsidian-pandoc-reference-list-zotero8](https://github.com/wjvg-gif/obsidian-pandoc-reference-list-zotero8) — native Zotero 7/8 API mode
- [sjelms/obsidian-pandoc-inline-citations](https://github.com/sjelms/obsidian-pandoc-inline-citations) — DOM fallback fixes, wikilink alias parsing
- Better BibTeX's [pandoc Lua filter](https://github.com/retorquere/zotero-better-bibtex/blob/master/pandoc/zotero.lua) by [retorquere](https://github.com/retorquere) (AGPL-3.0)

See [NOTICE.md](NOTICE.md) for full third-party license attributions.
>>>>>>> 2e85742 (Rename to ScholarWeave; add revert-to-pandoc commands)
