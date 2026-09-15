# Mobile

ScholarWeave works on iOS and Android. The citation features need no external tools; document import/export is desktop-only.

## Bibliography files

Use a vault-relative path (e.g. `references.bib`) — absolute paths work on desktop only. The browse button opens a vault file search instead of the OS file picker.

## Tapping citations

**Reading mode:** tapping a rendered citation triggers your configured **Mobile tap action**:

- **Show citation info** (default) — opens a bottom-sheet card with the formatted reference. Tap outside or press × to dismiss.
- **Copy to clipboard** — copies the formatted citation as rich text and markdown.
- **Open link** — follows the best available link in order (Zotero select → attached PDF → URL/DOI), falling back to the card when nothing is available.

**Editor (live preview):** tapping places the cursor normally; **long-press** (~500 ms) triggers the action. Long-pressing when the cursor is already on the citekey performs native word selection. Moving your finger during the hold cancels it.

## Reference sidebar

The sidebar opens on startup; if it doesn't appear, run **Show reference list** from the command palette.

## Zotero

The native Zotero API works on mobile when Zotero is running and reachable on the local network. Better BibTeX is not supported on mobile.

## Limitations

- Autocomplete inside table cells is broken on all platforms (an Obsidian `EditorSuggest` limitation).
- Pandoc is unavailable on mobile, so the built-in BibTeX parser is used.
- Absolute bibliography paths don't work on mobile.
- Document import/export is desktop-only (it needs Python, Pandoc, and so on).

See [Dependencies](./dependencies.md).
