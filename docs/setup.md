# Setup

## Install

Install via [BRAT](https://github.com/TfTHacker/obsidian42-brat):

1. Install BRAT from the Obsidian community plugin list
2. In BRAT settings → "Add Beta Plugin", enter: `112345brian/linked-citations`
3. Enable the plugin in Obsidian settings

## Bibliography files

In **Settings → Linked Citations → Bibliography files**, add one or more bibliography files. Use the **Add file** button to add entries; each has a browse button and a trash icon to remove it.

**Supported formats:**
- `.bib` — BibTeX / BibLaTeX
- `.json` — CSL-JSON
- `.yaml` / `.yml` — CSL-YAML

**Path formats:**
- **Vault-relative** (recommended, works everywhere): `references.bib`, `assets/refs.bib`
- **Absolute** (desktop only): `/Users/you/references.bib`

If you enter an absolute path that lives inside the vault, it is automatically shortened to vault-relative when you leave the field. All configured files are merged into one library; Zotero wins on conflict between any source.

Parsed `.bib` files are cached in `.pandoc/bib-parsed.json` and only re-parsed when the source file changes, so startup stays fast even with large bibliographies.

## Citation style

Set a CSL style in **Settings → Citation style**. You can:
- Pick from the built-in list (downloaded automatically and cached in `.pandoc/` in your vault)
- Enter a path to a local `.csl` file (vault-relative or absolute)

## Per-note overrides

Any setting can be overridden in a note's YAML frontmatter:

```yaml
---
bibliography: ./references.bib        # path relative to this note, or vault-relative
csl: ./chicago-author-date.csl        # local path or URL
lang: fr-FR                           # citation language
---
```

Multiple bibliography files:

```yaml
---
bibliography:
  - ./primary.bib
  - ./secondary.bib
---
```

Paths resolve relative to the note file first, then fall back to vault root.

## Pandoc (optional)

Pandoc is not required. The built-in pure-JS parser handles `.bib`, `.json`, and `.yaml` files on all platforms including mobile.

If you have Pandoc installed and prefer it for edge cases, set its path in **Settings → Path to Pandoc**. The plugin will auto-detect common install locations (Homebrew, winget, Scoop, Chocolatey) if you leave the field blank and click Auto-detect.

## Book Compiler (desktop only)

The **Compile and export a book (outline or markdown)** command runs the
bundled `scripts/BookCompiler.py` with Python 3. It needs three external
tools, which the plugin resolves itself (Obsidian's Electron process doesn't
inherit your shell PATH, so it probes known install locations and passes the
resolved paths to the script):

- **Python 3** with `lxml` and `python-docx` — set **Path to Python 3** if
  auto-detection picks a build without those modules (e.g. macOS's CLT
  python). Install with `pip install lxml python-docx`.
- **Node.js** — for the citation converter script
- **Pandoc** — for markdown → docx

Settings:
- **Path to Python 3 (for Book Compiler)** — blank auto-detects (verifying
  `lxml`/`python-docx` import)
- **Docx export templates directory** — optional; blank uses
  `<vault>/Export Templates/`, then the plugin's bundled `templates/`
- **Default output folder** — pre-fills the modal's output-folder field;
  blank defaults to the source file's own folder

See the README's [Book Compiler section](../README.md#book-compiler-outline--markdown--word) for the outline grammar, YAML properties, and template resolution order.

## Citekey autocomplete

Typing `@` in a note opens a fuzzy autocomplete popup biased toward citekeys. Typing `@@` opens a full-text search biased toward titles and authors (spaces allowed; a period closes the popup). When ZotLit is installed, `@@` searches via ZotLit's database directly.

Search behaviour for a single `@` (PRL-style, citekey-first):

1. Citekeys that **start with** what you typed — `pickus` → `pickusImmigrationCitizenship1998`
2. Citekeys **containing** it
3. Fuse fuzzy on title/author as a last resort

This keeps results predictable and surfaces references that haven't been imported into Obsidian yet (no literature note exists for them). The same search works inside `[[@key…` wikilinks — the plugin claims the `@`-trigger before Obsidian's link search, so you see library results rather than only already-linked notes.

`⌘↵` (or `ctrl↵`) wraps the selected key in brackets: `[@citekey]`. It detects bracket context automatically — if you're already inside `[@...]`, it appends without double-wrapping.

Searches are diacritic-insensitive: "Muller" matches "Müller".

## Bibliography snapshot

The **Save bibliography snapshot** command (also available as a camera icon in the reference panel header) exports all citations in the current note to a `.bib` file. A dialog lets you set the filename; it defaults to `{note-name}-bibliography.bib` in the same folder.

The saved path is added to the note's `bibliography` frontmatter key, which Pandoc and other tools can use directly.

After a snapshot, the plugin colour-codes each citekey in the editor:

| Colour | Meaning |
|---|---|
| Blue | In your global library and in the snapshot (synced) |
| Yellow / dashed | In your global library but not yet in the snapshot |
| Red | Not found anywhere |

Run the snapshot command again at any time to update the `.bib` with any new citations.

## Showing the reference sidebar

Run **Linked Citations: Show reference list** from the command palette (`Cmd/Ctrl+P`). The sidebar updates automatically as you edit.
