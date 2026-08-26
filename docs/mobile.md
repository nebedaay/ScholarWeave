# Mobile

The plugin works on iOS and Android without Pandoc or Better BibTeX.

## Bibliography file

Use a vault-relative path (e.g. `references.bib`) — absolute paths are desktop only. The browse button on mobile opens a vault file search modal instead of the OS file picker.

## Tapping citations

### Reading mode

Tapping a rendered citation immediately triggers your configured **Mobile tap action** (Settings → Tooltip):

- **Show citation info** (default) — opens a bottom-sheet card with the formatted reference. Tap outside or press × to dismiss.
- **Copy to clipboard** — copies the formatted citation as rich text and markdown.
- **Open link** — follows the best available link in order: Zotero select → attached PDF → URL/DOI. Falls back to showing the card if nothing is available.

### Editor (live-preview) mode

In the editor, citations are interactive but tapping should still let you edit:

- **Tap** — places the cursor normally, no citation action
- **Long press** (~500 ms) — triggers your configured tap action
- **Long press when cursor is already on the citekey** — native word selection (the plugin steps aside)

Scrolling or moving your finger during a hold cancels the long press cleanly.

## Reference sidebar

The sidebar opens automatically on startup. If it doesn't appear, run **Linked Citations: Show reference list** from the command palette.

## Limitations

- Autocomplete in table cells is broken on all platforms (Obsidian `EditorSuggest` API limitation)
- Pandoc is not available on mobile; the built-in BibTeX parser is used instead
- Absolute bibliography paths don't work on mobile — use vault-relative paths
