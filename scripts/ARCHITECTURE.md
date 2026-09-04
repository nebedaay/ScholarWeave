# Export pipeline architecture (`scripts/`)

The DOCX/ODT export & import pipeline is Python, invoked by `src/exportCompiler.ts`
/ `src/importCompiler.ts` via `child_process` (desktop only; needs `python3` with
`lxml` + `python-docx`, plus `pandoc`).

```
outline / note.md
   │  DocumentCompiler.compile_book / compile_note   (outline → one markdown doc)
   ▼
compiled.md
   │  convert-citations.mjs        (citation wikilinks → native pandoc citations)
   │  DocumentCompiler markdown pre-processing:
   │     rewrite_poetry_callouts · preprocess_md_syntax · resolve_embed_links
   │     (image embeds + Obsidian |WIDTH size syntax) · strip_wikilinks
   │     (code-span aware) · linkify_bare_urls
   │  pandoc  -t docx|odt  + sw-*.lua filters
   ▼
clean.docx / clean.odt        (styled content, no template structure)
   │  sw_export_merge.merge()          sw_export_odt_merge.merge_odt()
   ▼
final.docx / final.odt
   │  DocumentCompiler.inject_missing_{docx,odt}_styles   (belt-and-braces)
   ▼
output
```

## The three-way split

| file | role |
|---|---|
| `DocumentCompiler.py` | orchestrator: outline compilation, markdown pre-processing, pandoc invocation, template lookup, merge-script invocation. `export_document(fmt, …)` is the single entry point for both formats. |
| `sw_export_merge.py` (DOCX), `sw_export_odt_merge.py` (ODT) | **serializers.** Take pandoc's clean output + the export template, produce the final file. Contain only format XML mechanics — building `<w:p>` vs `<text:p>`, sectPr vs master pages, OOXML fields vs `<text:sequence>`. |
| `sw_merge_helpers.py` | **the shared layer.** Every decision about *what content appears, in what order, with what numbering / labels / structure* lives here and is consumed by both merge scripts. |

## The rule (this is why the shared layer exists)

Past work repeatedly forked DOCX and ODT into divergent logic — one format would
gain a feature the other silently lacked. So:

- A substantive content/structure decision goes in `sw_merge_helpers.py`, **never**
  in a single merge script.
- Before adding a function to a merge script, check whether the other format
  already implements the same idea. If it does, lift the shared logic into
  `sw_merge_helpers.py` first, then call it from both.
- Shared functions stay format-neutral: they take **accessor callbacks**
  (`get_style` / `set_style` / `get_text`, element builders) rather than importing
  OOXML or ODF constants.

## Shared functions and their per-format callers

| `sw_merge_helpers` | DOCX caller | ODT caller |
|---|---|---|
| `resolve_cover`, `title_case`, `strip_markdown` | direct | direct |
| `is_main_start` / `is_toc_heading` / `is_tof_heading` | `classify_blocks`, `extract_template_layout` | `_looks_like_toc_heading` |
| `STYLE_REMAP['docx' \| 'odt']` | inline in `build_body` | `_STYLE_REMAPS` |
| `process_figures(…, accessors)` — the figure-caption walk (find the vault "Figure. …" line after an image, drop pandoc's filename caption, strip the old number, compute the real one, consume `Alt-text:`, track chapter, `has_figures`) | `transform_figures` | inline in `merge_odt` |
| `strip_figure_prefix`, `parse_chapter_number`, `strip_chapter_prefix` | `apply_chapter_numbering` | `apply_chapter_numbering_odt` |
| `append_extra_sections(…, make_heading, make_body)` | `_append_extra_sections` (AKH + BodyText) | `_append_extra_sections` (AKH + Text_20_body) |
| `resize_images(root, fmt)` | `merge` | `merge_odt` |
| `ensure_docx_styles` / `ensure_odt_styles` | ToF style borrow in `_write_docx` | ToF style borrow in `merge_odt` |
| `bundled_template(name)` | ToF fallback source | ToF fallback source |
| `split_paragraphs`, `find_bibliography_range`, `strip_bibliography`, `ZOTERO_BIBL_INSTR` | direct | direct |

## Deliberately per-format (irreducible)

- **`_fill_title_block`** — DOCX detects title/author/date by placeholder text +
  position; ODT by style name (+ book.odt's P1/P2 convention). The *abstract*
  policy (drop every placeholder slot, re-inject via `append_extra_sections`) is
  now identical.
- **Chapter numbering** — DOCX adds `<w:numPr>` (template numId); ODT wraps the
  heading in `<text:list style="WWNum13">`. Same trigger (`Chapter N:` prefix,
  stripped by the shared helper), same gate (template must define the mechanism).
- **Page-break / section-break mechanics**, **footnote restart**, **field XML**.

## Open feature work (not consolidation)

- Format-agnostic page numbering: roman frontmatter → arabic at
  Introduction/Chapter 1, driven by settings + document structure, independent of
  the template. book.odt does this with per-heading `style:master-page-name`.
- ODT book figure captions render "Figure 1" not "Figure C.N" — `display-outline-level`
  can't see the `<text:list>` chapter numbers.
