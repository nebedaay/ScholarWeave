/**
 * Helpers for co-existing and integrating with ZotLit (plugin ID "zotlit").
 * All functions degrade gracefully when ZotLit is absent.
 */

import { App, TFile } from 'obsidian';

// ZotLit 1.x set globalThis.zoteroAPI in onload(); 2.x does not. The reliable
// "is ZotLit loaded right now" check for both is whether the plugin is present
// in app.plugins.plugins AND its onload has completed (the instance exists).
// Fall back to the v1 global for older installs.
export function isZotLitLoaded(app?: App): boolean {
  if ((globalThis as any).zoteroAPI) return true;
  const appInstance = app ?? (globalThis as any).app;
  return !!(appInstance?.plugins?.plugins?.['zotlit']);
}

// The ZotLit plugin instance, or null when not loaded.
export function getZotLitPlugin(app: App): any {
  if (!isZotLitLoaded(app)) return null;
  return (app as any).plugins?.plugins?.['zotlit'] ?? null;
}

// Returns true when ZotLit is loaded AND its citation editor suggester is
// enabled (the default). Use this to decide whether to yield the bracketed
// [@key context to ZotLit's suggestion panel.
export function isZotLitSuggestActive(app: App): boolean {
  if (!isZotLitLoaded(app)) return false;
  const zotlit = (app as any).plugins?.plugins?.['zotlit'];
  if (!zotlit) return false;
  // ZotLit checks this path in its own onTrigger; mirror it exactly.
  const setting = zotlit.settings?.current?.citationEditorSuggester;
  // Default is true — only return false when the user has explicitly disabled it.
  return setting !== false;
}

// Find the literature note for a citekey, preferring ZotLit's frontmatter-based
// index over our filename-guessing approach.
export function getLitNoteForCitekey(
  citekey: string,
  sourcePath: string,
  app: App
): { file: TFile; linkText: string } | null {
  // ZotLit maintains NoteIndex.citekeyCache: Map<citekey, Set<notePath>>
  // derived from each note's frontmatter. Prefer this — it catches notes that
  // use any filename, not just @citekey.md.
  if (isZotLitLoaded(app)) {
    const zotlit = (app as any).plugins?.plugins?.['zotlit'];
    const cache = zotlit?.noteIndex?.citekeyCache;
    if (cache instanceof Map) {
      const paths: Set<string> | undefined = cache.get(citekey);
      if (paths?.size) {
        const notePath = paths.values().next().value as string;
        const file = app.vault.getAbstractFileByPath(notePath);
        if (file instanceof TFile) return { file, linkText: notePath };
      }
    }
  }

  // Filename fallback: try @citekey then plain citekey.
  for (const linkText of [`@${citekey}`, citekey]) {
    const file = app.metadataCache.getFirstLinkpathDest(linkText, sourcePath);
    if (file instanceof TFile) return { file, linkText };
  }

  return null;
}

/**
 * Create (or open) a literature note for the given Zotero item via ZotLit.
 *
 * ZotLit's `createNote(item)` requires a FULL DB-hydrated `Item` (fields,
 * creators, itemType relations) — not something we can reconstruct from our
 * bibCache. Instead, we invoke ZotLit's OWN protocol handler
 * (`obsidian://zotlit/open?item=ITEMID&source-id=…`), which resolves the item
 * internally, creates the note with ZotLit's template, and opens it.
 *
 * We obtain the Zotero itemID from our citekey map (_zoteroKey) and the
 * source-id from `zotlit.services.zoteroPref.sourceId`. On any failure we
 * return null so the caller falls back to the plugin's own template.
 */
export async function createLitNoteViaZotLit(
  app: App,
  item: { indexedKey: string; itemID?: number }
): Promise<string | null> {
  const zotlit = getZotLitPlugin(app);
  if (!zotlit) return null;

  try {
    // ZotLit 2.x: services on the plugin instance.
    const services = zotlit.services ?? zotlit.api?.services ?? null;
    if (!services) return null;

    let itemID = item.itemID;
    if (itemID === undefined) {
      const db = services?.db ?? null;
      itemID = await lookupItemID(db, item.indexedKey);
      if (itemID === undefined) return null;
    }

    const sourceId = services?.zoteroPref?.sourceId ?? null;
    const url =
      `obsidian://zotlit/open?item=${itemID}` +
      (sourceId ? `&source-id=${encodeURIComponent(sourceId)}` : '');
    // _self avoids popup blockers; Obsidian's URI handler intercepts
    // obsidian:// and routes to the registered protocol handler.
    window.open(url, '_self');
    // The protocol handler creates + opens the note; we can't await the result
    // path synchronously. Returning a sentinel tells the caller NOT to fall
    // back to its own template (the protocol flow is in charge).
    return url;
  } catch (e) {
    console.warn('[lc] createLitNoteViaZotLit: error', e);
    return null;
  }
}

// Find the Zotero itemID for an item key (indexedKey) via ZotLit's DB client.
async function lookupItemID(db: any, indexedKey: string): Promise<number | undefined> {
  if (!db?.client) return undefined;
  try {
    const client = db.client;
    // Drizzle node-sqlite client: raw query helper.
    const raw = client.$queryRawUnsafe ?? client.$client?.$queryRawUnsafe;
    if (typeof raw === 'function') {
      const rows = await raw(
        'SELECT itemID FROM items WHERE key = ? LIMIT 1',
        indexedKey
      );
      return rows?.[0]?.itemID;
    }
    // Raw node:sqlite handle.
    const sqlite = client.$client ?? client;
    if (typeof sqlite?.prepare === 'function') {
      const stmt = sqlite.prepare('SELECT itemID FROM items WHERE key = ?');
      const row = stmt.get(indexedKey);
      return row?.itemID;
    }
    return undefined;
  } catch {
    return undefined;
  }
}

/**
 * Build a minimal ZotLit Item from our CSL entry + resolved itemID.
 *
 * ZotLit's `noteFeature.createNote(item)` creates a literature note WITHOUT
 * opening a tab (only the `zotlit/open` protocol action calls openLinkText
 * after createNote). It re-derives tags/attachments/collections from the DB by
 * itemID, so the item only needs the fields/creators used for the filename
 * template + frontmatter:
 *   fields: {itemType, title, …}, creators: [{firstName,lastName,creatorType,
 *   fieldMode}], key, indexedKey, libraryID, groupID, dateAdded, dateModified.
 */
function minimalItemFor(
  entry: any,
  itemID: number,
  indexedKey: string
): Record<string, unknown> | null {
  if (!entry) return null;
  const fields: Record<string, string> = { itemType: entry.type ?? 'book' };
  if (entry.title) fields.title = entry.title;
  // The citekey drives BOTH the literature-note filename (@citekey.md) and the
  // note's frontmatter `citekey:` field. Our CSL entry carries it as `id`;
  // map it to the field ZotLit's template reads (lx: fields.citationKey).
  const citekey = entry.id ?? entry.citationKey;
  if (citekey) fields.citationKey = citekey;
  const year = entry.issued?.['date-parts']?.[0]?.[0];
  if (year) fields.date = String(year);
  const container = entry['container-title'] ?? entry['container-title-short'];
  if (container) fields.publicationTitle = container;
  if (entry.volume) fields.volume = String(entry.volume);
  if (entry.issue) fields.issue = String(entry.issue);
  if (entry.page) fields.pages = entry.page;
  if (entry.publisher) fields.publisher = entry.publisher;
  if (entry.edition) fields.edition = String(entry.edition);
  if (entry.DOI) fields.DOI = entry.DOI;

  const creators = (entry.author ?? []).map((a: any) => {
    if (a.literal) {
      return { firstName: null, lastName: a.literal, creatorType: 'author', fieldMode: 1 };
    }
    return {
      firstName: a.given ?? null,
      lastName: a.family ?? '',
      creatorType: 'author',
      fieldMode: 0,
    };
  });

  const groupId = entry.groupID ?? 1;
  return {
    itemID,
    indexedKey,
    key: entry._zoteroKey ?? indexedKey,
    libraryID: groupId,
    groupID: groupId,
    dateAdded: entry.dateAdded ?? new Date().toISOString(),
    dateModified: entry.dateModified ?? new Date().toISOString(),
    itemType: entry.type ?? 'book',
    primaryCreatorType: 'author',
    fields,
    creators,
    customFields: [],
  };
}

/**
 * Bulk-create literature notes via ZotLit WITHOUT opening a tab per note.
 *
 * Calls `noteFeature.createNote(minimalItem)` directly (create-only; no
 * openLinkText). Falls back to the `obsidian://zotlit/open` protocol (the
 * tooltip path, which opens the note) per item if createNote fails — so a
 * bulk run never silently skips a note.
 *
 * `items` carries the CSL entry (for the minimal item) plus itemID/indexedKey.
 * Returns the number of notes created/attempted.
 */
export async function createLitNotesViaZotLitBulk(
  app: App,
  items: { indexedKey: string; itemID?: number; entry?: any }[],
  onProgress?: (done: number, total: number) => void
): Promise<number> {
  const zotlit = getZotLitPlugin(app);
  if (!zotlit) return 0;
  const services = zotlit.services ?? zotlit.api?.services ?? null;
  if (!services) return 0;
  const db = services?.db ?? null;
  const noteFeature = services?.noteFeature ?? null;
  const sourceId = services?.zoteroPref?.sourceId ?? null;

  const total = items.length;
  let attempted = 0;
  for (const it of items) {
    let itemID = it.itemID;
    if (itemID === undefined) {
      itemID = await lookupItemID(db, it.indexedKey);
    }
    if (itemID === undefined) continue;

    // Preferred: createNote directly (no tab opened).
    let created = false;
    if (noteFeature?.createNote) {
      try {
        const minimal = minimalItemFor(it.entry, itemID, it.indexedKey);
        if (minimal) {
          const note = await noteFeature.createNote(minimal);
          created = !!note;
        }
      } catch (e) {
        console.warn('[lc] createNote failed for', it.indexedKey, e);
      }
    }

    // Fallback: the tooltip protocol (opens the note, but always works).
    if (!created) {
      const url =
        `obsidian://zotlit/open?item=${itemID}` +
        (sourceId ? `&source-id=${encodeURIComponent(sourceId)}` : '');
      try {
        window.open(url, '_self');
        created = true;
      } catch (e) {
        console.warn('[lc] protocol create failed for', it.indexedKey, e);
      }
    }

    if (created) attempted++;
    onProgress?.(attempted, total);
    // Small delay so ZotLit's noteIndex/template keep up on large backfills.
    await new Promise((r) => setTimeout(r, 300));
  }
  return attempted;
}
