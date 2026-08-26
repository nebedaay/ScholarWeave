# Zotero Integration

The plugin supports two Zotero connection modes and can merge Zotero data with a local `.bib` file simultaneously.

## Native API (Zotero 7/8) — recommended

Enable **Settings → Use native Zotero API** to query Zotero directly without the Better BibTeX plugin. This uses Zotero's built-in local REST API, available in Zotero 7 and later.

Requirements:
- Zotero 7 or 8 running on the same machine (desktop) or same local network (mobile via IP)
- Zotero's local API enabled (it is by default)

## Better BibTeX (Zotero 6 and earlier)

Leave native API disabled to use the Better BibTeX (BBT) JSON-RPC endpoint. The BBT plugin must be installed in Zotero.

## Port

The default port is **23119**. Change it in settings if you use Juris-M (24119) or a custom port.

## Library selection

When connected, you can choose which Zotero libraries to include. Personal library and group libraries are both supported. If the same citekey appears in multiple libraries, the most recently modified version wins.

## Merging with a .bib file

You can use Zotero and a `.bib` file at the same time. Both sources load on startup:

- If a citekey exists in both, **Zotero wins**
- Entries that exist in both show a conflict indicator (⚠) in the sidebar
- If Zotero is unavailable at startup, the `.bib` file covers whatever it can

This means you can keep a `.bib` export as a fallback without having to choose between sources.

## Mobile

The native Zotero API works on mobile as long as Zotero is running and reachable by IP on the local network. Better BibTeX is not supported on mobile.

## Full-text search with @@

When ZotLit is installed and active, typing `@@` in the editor searches via ZotLit's SQLite database — the same engine powering ZotLit's own `[@` suggester. This gives title/author-biased full-text search with spaces allowed. If ZotLit is not installed, the plugin falls back to its own title-biased Fuse index.

## Refreshing

The bibliography refreshes automatically when you switch notes. You can also force a refresh from the sidebar menu.
