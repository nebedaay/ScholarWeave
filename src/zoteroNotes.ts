import { App, TFile, requestUrl } from 'obsidian';

/** Zotero's local REST API port (same default as `bib/helpers.ts`; inlined so
 *  this module doesn't pull the whole bib graph in for one string). */
const DEFAULT_ZOTERO_PORT = '23119';

/**
 * Fetch a Zotero item's child notes and insert them into the "## Notes"
 * section of the matching literature note — the equivalent of the old Zotero
 * Integration behaviour, which ZotLit does not support (ZotLit imports notes
 * as separate files and only exposes them to templates as links; it never
 * gives a template the note's text).
 *
 * Design:
 *  - Content is inserted directly under `## Notes`, as regular Markdown
 *    paragraphs, ABOVE the `%%zt-managed%%` region, so ZotLit's re-render
 *    (which only owns the text between its own markers) never touches it.
 *  - We only touch notes whose `## Notes` section is otherwise EMPTY, so a
 *    section the user has written in (or already had filled) is never
 *    overwritten.
 *  - A marker comment (`<!-- sw-zn -->`) is appended at the very END of the
 *    file (below the managed region) recording which Zotero notes were
 *    inserted, so a second run is a no-op even though the section is no
 *    longer empty.
 */

const SW_ZN_MARK = '<!-- sw-zn -->';
export const ZOTERO_NOTE_MARK = SW_ZN_MARK;

export interface InsertResult {
  inserted: number;
  /** Notes skipped because the "## Notes" section already had content. */
  skipped: string[];
  /** Notes whose Zotero item has no child notes. */
  noNotes: string[];
  /** Notes whose Zotero lookup failed (network / not found) — surfaced, never
   *  silently counted as "no notes". */
  failed: string[];
}

interface ZoteroNote {
  key: string;
  html: string;
}

/** Zotero note HTML → Markdown. Zotero notes are "plain text flavoured with a
 *  few inline tags"; we convert block tags to line breaks and inline tags to
 *  Markdown equivalents, then strip anything left. */
export function zoteroHtmlToMarkdown(html: string): string {
  let s = html ?? '';
  s = s.replace(/<\s*br\s*\/?\s*>/gi, '\n');
  s = s.replace(/<\/(p|div|h[1-6]|li|tr|blockquote)\s*>/gi, '\n\n');
  s = s.replace(/<\s*(p|div|h[1-6]|li|tr|blockquote)(\s[^>]*)?>/gi, '');
  s = s.replace(/<\s*b\s*>|<\s*strong\s*>/gi, '**').replace(/<\s*\/\s*(b|strong)\s*>/gi, '**');
  s = s.replace(/<\s*i\s*>|<\s*em\s*>/gi, '*').replace(/<\s*\/\s*(i|em)\s*>/gi, '*');
  s = s.replace(/<\s*sub\s*>/gi, '<sub>').replace(/<\s*\/\s*sub\s*>/gi, '</sub>');
  s = s.replace(/<\s*sup\s*>/gi, '<sup>').replace(/<\s*\/\s*sup\s*>/gi, '</sup>');
  // Strip any remaining tag EXCEPT the sub/sup we just kept (they are valid
  // Markdown-in-Obsidian and Zotero uses them).
  s = s.replace(/<(?!\/?(?:sub|sup)\b)[^>]+>/gi, '');
  s = s.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
  s = s.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return s;
}

async function getJson(port: string, path: string): Promise<any> {
  const resp = await requestUrl({
    url: `http://127.0.0.1:${port}${path}`,
    method: 'GET',
    // Zotero 10 drops browser-looking requests (any Origin header — which
    // requestUrl always sends) unless this header is present.
    headers: {
      Accept: 'application/json',
      'Zotero-Allowed-Request': '1',
    },
    throw: false,
  });
  if (resp.status !== 200) throw new Error(`Zotero: HTTP ${resp.status}`);
  return resp.json;
}

/** The child notes of an item, by its Zotero key.
 *
 *  `indexedKey` may carry a group suffix (`KEYg6667607` — ZotLit's form for a
 *  group library). A group item is only reachable at
 *  `/api/groups/<id>/items/<key>`, so we try the item's own library first and
 *  fall back to the personal library. */
export async function fetchChildNotes(
  port: string,
  key: string
): Promise<ZoteroNote[]> {
  const m = /^([A-Za-z0-9]+?)(?:g(\d+))?$/.exec(key.trim());
  const bareKey = m?.[1] ?? key.trim();
  const groupId = m?.[2];

  const paths: string[] = [];
  if (groupId) paths.push(`/api/groups/${groupId}/items/${bareKey}/children`);
  paths.push(`/api/users/0/items/${bareKey}/children`);

  let lastErr = '';
  for (const base of paths) {
    let data: any;
    try {
      data = await getJson(
        port,
        `${base}?format=json&itemType=note&limit=100`
      );
    } catch (e) {
      lastErr = (e as Error).message;
      continue;
    }
    const arr: any[] = Array.isArray(data) ? data : [];
    return arr
      .filter(
        (d) => d?.data?.itemType === 'note' && typeof d.data.note === 'string'
      )
      .map((d) => ({ key: d.key ?? d.data.key, html: d.data.note as string }));
  }
  throw new Error(lastErr || 'Zotero: could not fetch child notes');
}

interface NoteSlot {
  /** Offset just after the "## Notes" heading line (start of the section body). */
  sectionStart: number;
  /** Offset of the `%%zt-managed%%` opening marker, or end of the section. */
  sectionEnd: number;
  /** Text between the heading and the marker (trimmed). */
  body: string;
}

/** Locate the "## Notes" section of a ZotLit literature note. */
export function findNotesSection(content: string): NoteSlot | null {
  const heading = /^##[ \t]+Notes[ \t]*$/m.exec(content);
  if (!heading) return null;
  const sectionStart = heading.index + heading[0].length;
  const rest = content.slice(sectionStart);
  const mark = /^%%zt-managed%%[ \t]*$/m.exec(rest);
  const sectionEnd = mark ? sectionStart + mark.index : content.length;
  return {
    sectionStart,
    sectionEnd,
    body: content.slice(sectionStart, sectionEnd).trim(),
  };
}

/** Zotero keys already recorded by a previous run (from `<!-- sw-zn: … -->`). */
export function recordedKeys(content: string): Set<string> {
  const out = new Set<string>();
  for (const m of content.matchAll(/<!--\s*sw-zn(?::\s*([^>]*))?\s*-->/g)) {
    const keys = (m[1] ?? '').trim();
    if (!keys) continue;
    for (const k of keys.split(/[,\s]+/)) if (k) out.add(k);
  }
  return out;
}

async function zoteroKeyOf(app: App, file: TFile): Promise<string | null> {
  const cache = app.metadataCache.getFileCache(file);
  const fm = cache?.frontmatter;
  const k = fm?.['zotero-key'] ?? fm?.zoteroKey;
  return typeof k === 'string' && k.trim() ? k.trim() : null;
}

/** Insert one literature note's Zotero child notes. */
async function insertIntoNote(
  app: App,
  file: TFile,
  notes: ZoteroNote[]
): Promise<'inserted' | 'skipped' | 'no-notes'> {
  const content = await app.vault.read(file);
  const slot = findNotesSection(content);
  if (!slot) return 'skipped';
  if (!notes.length) return 'no-notes';
  if (slot.body) return 'skipped'; // user content (or a previous insert) present
  if (recordedKeys(content).size > 0) return 'no-notes'; // already handled a run

  const parts: string[] = [];
  for (const n of notes) {
    const md = zoteroHtmlToMarkdown(n.html);
    if (md) parts.push(md);
  }
  if (!parts.length) return 'no-notes';

  // Layout: a blank line under the heading, the notes, then a blank line
  // before ZotLit's managed region (so the region is clearly separated).
  const block = '\n' + parts.join('\n\n') + '\n\n';
  const before = content.slice(0, slot.sectionEnd).replace(/\s*$/, '\n');
  const after = content.slice(slot.sectionEnd).replace(/^\s*/, '');
  const marker = `\n${SW_ZN_MARK}: ${notes.map((n) => n.key).join(' ')}\n`;
  const updated =
    before + block + after.replace(/\s*$/, '') + '\n' + marker;
  if (updated === content) return 'no-notes';
  await app.vault.modify(file, updated);
  return 'inserted';
}

export interface InsertOptions {
  onProgress?: (done: number, total: number) => void;
  zoteroPort?: string;
}

/**
 * Vault-wide: for every literature note (has `zotero-key`) whose "## Notes"
 * section is empty, insert that item's Zotero child notes. Returns counts and
 * the list of skipped notes (so the user can review them).
 */
export async function insertZoteroNotesVaultWide(
  app: App,
  opts: InsertOptions = {}
): Promise<InsertResult> {
  const port = opts.zoteroPort || DEFAULT_ZOTERO_PORT;
  const result: InsertResult = { inserted: 0, skipped: [], noNotes: [], failed: [] };
  const files = app.vault.getMarkdownFiles();
  const candidates: { file: TFile; key: string }[] = [];
  for (const f of files) {
    const content = await app.vault.cachedRead(f);
    if (!findNotesSection(content) || !/%%zt-managed%%/.test(content)) continue;
    const key = await zoteroKeyOf(app, f);
    if (key) candidates.push({ file: f, key });
  }

  let done = 0;
  for (const { file, key } of candidates) {
    try {
      const notes = await fetchChildNotes(port, key);
      const r = await insertIntoNote(app, file, notes);
      if (r === 'inserted') result.inserted++;
      else if (r === 'skipped') result.skipped.push(file.path);
      else result.noNotes.push(file.path);
    } catch (e) {
      // A failed lookup must be visible: this is what hid the dynamic-import
      // bug that reported "0 inserted" while 18 notes were actually waiting.
      console.warn('ScholarWeft: Zotero note lookup failed for', file.path, e);
      result.failed.push(file.path);
    }
    opts.onProgress?.(++done, candidates.length);
  }
  return result;
}

/**
 * A single literature note (used right after ScholarWeft asks ZotLit to
 * create notes, so freshly created notes get their Zotero notes at once).
 * Waits briefly for ZotLit to finish writing the file.
 */
export async function insertZoteroNotesForFiles(
  app: App,
  files: TFile[],
  opts: InsertOptions = {}
): Promise<InsertResult> {
  const port = opts.zoteroPort || DEFAULT_ZOTERO_PORT;
  const result: InsertResult = { inserted: 0, skipped: [], noNotes: [], failed: [] };
  // Give ZotLit a moment to create/render the notes it was just asked for.
  if (files.length) await new Promise((r) => setTimeout(r, 800));
  let done = 0;
  for (const f of files) {
    try {
      const fresh = app.vault.getAbstractFileByPath(f.path);
      const file = fresh instanceof TFile ? fresh : f;
      const key = await zoteroKeyOf(app, file);
      if (!key) {
        result.noNotes.push(file.path);
        continue;
      }
      const notes = await fetchChildNotes(port, key);
      const r = await insertIntoNote(app, file, notes);
      if (r === 'inserted') result.inserted++;
      else if (r === 'skipped') result.skipped.push(file.path);
      else result.noNotes.push(file.path);
    } catch (e) {
      console.warn('ScholarWeft: Zotero note lookup failed for', f.path, e);
      result.failed.push(f.path);
    }
    opts.onProgress?.(++done, files.length);
  }
  return result;
}
