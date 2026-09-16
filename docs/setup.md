# Setup

## 1. Install the plugin

Install via [BRAT](https://github.com/TfTHacker/obsidian42-brat):

1. Disable Restricted Mode, then install and enable **BRAT** from the Community Plugins list.
2. In BRAT's settings, add `nebedaay/ScholarWeft` to the **Beta plugin list**.
3. Enable **ScholarWeft** in Obsidian's Community Plugins. BRAT keeps it updated.

## 2. Install what you need

ScholarWeft's citation features work with no external tools. Document import/export needs a few programs — the short version:

- Compile/export/import documents → **Python 3** and **Pandoc**
- PDF via an ODT/DOCX template → also **LibreOffice**
- PDF via a `.tex` template → also a **LaTeX** distribution with LuaLaTeX
- Live, refreshable Zotero citations → **Zotero** (plus **Better BibTeX** for automatic citekeys)

See **[Dependencies](./dependencies.md)** for exactly what needs what and where to download it. The plugin detects what is installed and greys out options that can't run, so you can explore safely.

## 3. Connect your references

Use Zotero, a bibliography file, or both:

- **Zotero** — see [Zotero](./zotero.md).
- **Bibliography files** (`.bib`, CSL-JSON, CSL-YAML) — add them in Settings → Bibliography. See [Bibliography](./bibliography.md).

## 4. Start writing

Citations are Obsidian wikilinks with the pandoc citation after a `|`: `[[@smith1992|see @, p. 6]]` renders as *(see Smith 1992, 6)* **and** links to the literature note. Plain pandoc citations (`[@key]`) work too, and commands convert between the two. See [Linked Citations](./linked-citations.md) and [Citations](./citations.md).

## 5. Import and export

Compile a single note or a bullet-list outline of notes into markdown, DOCX, ODT, or PDF, and import Word/ODT documents. See [Document Import and Export](./import-export.md).

## Settings

Settings are reached from **Settings → ScholarWeft** and organised into four pages:

- **Bibliography** — where your sources come from. See [Bibliography](./bibliography.md) and [Zotero](./zotero.md).
- **Citation and reference formatting** — how citations and the reference list look in Obsidian. See [Citations](./citations.md).
- **Literature note import** — where literature notes live and how they are created. See [Literature Notes](./literature-notes.md).
- **Document import/export and compilation** — the tools, templates, and defaults for compiling and exporting. See [Document Import and Export](./import-export.md).

Each option notes anything it needs, with a link to [Dependencies](./dependencies.md).
