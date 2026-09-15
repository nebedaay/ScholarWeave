# Bibliography

ScholarWeave can read references from one or more bibliography files, from Zotero, or both at once. This page covers the file-based sources and how sources are merged; the Zotero connection has its own page ([Zotero](./zotero.md)). No external tools are needed to read a bibliography file.

## Bibliography files

In **Settings → Bibliography → Bibliography files**, add files with **Add file**; each row has a browse button and a delete button.

Supported formats:

- `.bib` — BibTeX / BibLaTeX
- `.json` — CSL-JSON
- `.yaml` / `.yml` — CSL-YAML

Paths:

- **Vault-relative** (recommended; works on mobile too): `references.bib`, `assets/refs.bib`
- **Absolute** (desktop only): `/Users/you/references.bib`

An absolute path that lives inside the vault is shortened to vault-relative automatically. Parsed `.bib` files are cached in `.pandoc/bib-parsed.json` and re-parsed only when the file changes, so startup stays fast with large bibliographies.

## Merging sources

All configured files plus Zotero are merged into one library. When the same citekey exists in more than one source, **Zotero wins**. A conflict indicator (⚠) appears in the sidebar for entries found in multiple sources.

If a bibliography file is your only source, everything still works: citations are rendered statically and need nothing running — no Zotero, no Pandoc.

## Per-note bibliography

Override the global sources in a note's frontmatter:

```yaml
---
bibliography: ./references.bib        # relative to this note, or vault-relative
---
```

Or several files:

```yaml
---
bibliography:
  - ./primary.bib
  - ./secondary.bib
---
```

Paths resolve relative to the note first, then to the vault root. For the citation *style* overrides (`csl:` / `citation-style:` / `lang:`), see [Citations](./citations.md).
