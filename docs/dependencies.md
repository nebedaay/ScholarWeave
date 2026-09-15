# Dependencies

ScholarWeave reads and formats citations, shows the reference sidebar, and manages literature notes **with no external tools at all**. Only *document import, compilation, and export* require external programs. This page lists every dependency, what needs it, and where to get it.

Install a dependency only when you need the feature it enables. The plugin detects what is installed and greys out options that need something missing, with an explanation and a link back here.

## Summary

| Dependency                             | Needed for                                                                                                                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Python 3** (+ `lxml`, `python-docx`) | Compiling/exporting documents (every format); importing DOCX/ODT                                                                                                                                 |
| **Pandoc**                             | Exporting to DOCX / ODT / LaTeX / PDF; importing DOCX / ODT                                                                                                                                      |
| **Zotero**                             | Live, refreshable citation fields; citekey lookup; importing citation fields — *unless* you use a bibliography file instead                                                                      |
| **Better BibTeX** (Zotero add-on)      | Only needed for Zotero 6: The BBT endpoint needed for exporting live Zotero citations. Not required for export using Zotero 7/8, but still needed inside Zotero for automatic citekey generation |
| **LibreOffice**                        | PDF export through an ODT/DOCX template                                                                                                                                                          |
| **A LaTeX distribution with LuaLaTeX** | PDF export through a `.tex` template                                                                                                                                                             |
| **ZotLit** (recommended)               | Richer literature-note creation and `@@` full-text search                                                                                                                                        |

## Python 3

The compiler, exporter, and importer are Python scripts, used for **any** document compilation/export or import. They need `lxml` and `python-docx`:

```bash
pip install lxml python-docx
```

If Obsidian resolves the wrong Python (for example macOS's Command Line Tools build, which lacks those packages), set **Path to Python 3** in Settings.

Download: <https://www.python.org/downloads/>

## Node.js (command line only)

The plugin converts citation wikilinks **in-process**, with its own parser, so a normal plugin install needs no Node.js. Only if you run the bundled `DocumentCompiler.py` yourself from a terminal does the standalone converter (`convert-citations.mjs`) need a `node` executable. (Obsidian bundles a Node runtime but does not expose it to plugins, which is why the scripts can't simply borrow it.)

Download (only if you use the scripts from the command line): <https://nodejs.org/en/download>

## Pandoc

Pandoc converts the compiled markdown to DOCX, ODT, or LaTeX, and converts imported Word/ODT documents to markdown. Required for every export and for import. Set **Path to Pandoc** in Settings if auto-detection fails.

Download: <https://pandoc.org/installing.html>

## Zotero

Zotero is a source of references: it is used to look up citation metadata, to insert live citation fields in exported DOCX/ODT, to generate citekeys, and to read citation fields on import.

**Zotero is not strictly required if you keep your references in a bibliography file**: those citations are rendered statically (as plain text) and need nothing running. Live, refreshable fields in Word/LibreOffice do require Zotero. See [Bibliography](./bibliography.md) and [Zotero](./zotero.md).

Download: <https://www.zotero.org/download/>

## Better BibTeX

A Zotero add-on that generates automatic, stable citekeys (e.g. `smithTitleYear`) and provides the JSON-RPC endpoint the plugin uses on Zotero 6. With Zotero 7/8 the plugin can use Zotero's native API instead, so BBT is optional there — although it remains the easiest way to generate citekeys inside Zotero.

Download: <https://retorque.re/zotero-better-bibtex/installation/>

## LibreOffice

Used headlessly to convert a generated ODT/DOCX into PDF. Required only for **PDF export through an ODT/DOCX template**.

Download: <https://www.libreoffice.org/download/>

## LaTeX (LuaLaTeX)

Required only for **PDF export through a `.tex` template**. The engine used is **LuaLaTeX** (included in every TeX distribution); XeLaTeX is not supported for Arabic content.

- macOS: [MacTeX](https://tug.org/mactex/) (or the smaller BasicTeX)
- Windows: [MiKTeX](https://miktex.org/download)
- Linux: [TeX Live](https://tug.org/texlive/)

## ZotLit (optional)

An Obsidian plugin that creates literature notes from Zotero with rich annotation formatting and powers the `@@` full-text title/author search. ScholarWeave works without it.

Download: <https://github.com/PKM-er/obsidian-zotlit>

## What the plugin does when something is missing

ScholarWeave probes for the tools above when you open the export or import dialogue. Options that need a missing tool are greyed out with an explanation, rather than failing part-way through. If Zotero is not running but the note cites works that can't be resolved from a bibliography file, you are warned and offered **Try connecting again**, **Proceed**, or **Cancel** before anything is exported.
