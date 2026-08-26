import { EditorView } from '@codemirror/view';
import CSL from 'citeproc';
import type ReferenceList from 'src/main';
import { PartialCSLEntry } from './types';
import Fuse from 'fuse.js';
import {
  bibPathsToCSL,
  bibToCSL,
  getBibPath,
  getCSLLocale,
  getCSLStyle,
  isAbsolutePath,
  pathBasename,
  DEFAULT_ZOTERO_PORT,
} from './helpers';
import { BBTAdapter, NativeAdapter, ZoteroAdapter } from './zotero';
import { SimpleLRU } from './lru';
import {
  PromiseCapability,
  copyElToClipboard,
  copyTextToClipboard,
  debugLog,
} from 'src/helpers';
import {
  RenderedCitation,
  getCitationSegments,
  getCitations,
} from 'src/parser/parser';
import { FileSystemAdapter, Keymap, MarkdownView, Menu, TFile, TFolder, debounce, normalizePath, setIcon } from 'obsidian';
import {
  createLitNoteViaZotLit,
  createLitNotesViaZotLitBulk,
  getLitNoteForCitekey,
} from 'src/zotlit';
import { cite } from 'src/parser/citeproc';
import { setCiteKeyCache } from 'src/editorExtension';
import equal from 'fast-deep-equal';
import { t } from 'src/lang/helpers';

// Strip diacritics so "Muller" matches "Müller", "Cezanne" matches "Cézanne".
// Applied both when building the index and when normalising search queries.
// Credit: approach from obsidian-citation-extended (MIT).
export const normalizeDiacritics = (s: string): string =>
  s.normalize('NFD').replace(/\p{Mn}/gu, '');

/**
 * Bump whenever rendering behaviour changes (parser fixes, CSL changes, …)
 * so a persisted rendered-citation cache from an older version is discarded
 * and every note re-renders with the new code. Without this, live preview
 * and reading mode keep serving stale citations from before a code fix.
 */
const RENDER_CACHE_VERSION = 2;

// Fuse getFn wrapper that strips diacritics from indexed string fields.
const fuseFn = (obj: any, path: string | string[]) => {
  const val = Fuse.config.getFn(obj, path);
  if (typeof val === 'string') return normalizeDiacritics(val);
  if (Array.isArray(val)) return val.map(v => typeof v === 'string' ? normalizeDiacritics(v) : v);
  return val;
};

// Citekey-biased: used for single-@ autocomplete.
const fuseSettings = {
  includeMatches: true,
  threshold: 0.35,
  minMatchCharLength: 2,
  getFn: fuseFn,
  keys: [
    { name: 'id', weight: 0.6 },
    { name: 'title', weight: 0.25 },
    { name: 'author.family', weight: 0.1 },
    { name: 'author.literal', weight: 0.05 },
  ],
};

// Title/author-biased: used for @@ full-text autocomplete.
const fuseTitleSettings = {
  includeMatches: true,
  threshold: 0.4,
  minMatchCharLength: 2,
  getFn: fuseFn,
  keys: [
    { name: 'title', weight: 0.6 },
    { name: 'author.family', weight: 0.2 },
    { name: 'author.literal', weight: 0.1 },
    { name: 'id', weight: 0.1 },
  ],
};

interface ScopedSettings {
  style?: string;
  lang?: string;
  bibliography?: string[];
  /** Resolved paths from the `bibliography` frontmatter key. The plugin loads
   *  these citekeys for blue/yellow/red colour comparison only — the global
   *  engine always handles rendering. */
  snapshotBib?: string[];
}

export interface FileCache {
  keys: Set<string>;
  resolvedKeys: Set<string>;
  unresolvedKeys: Set<string>;
  /** Keys that exist in the global library but are absent from the note's
   *  snapshot .bib (set via the `lc-snapshot` frontmatter key). Render with
   *  the `is-global-only` yellow style — resolvable but not yet snapshotted.
   *  Empty when no snapshot has been taken for this file. */
  globalOnlyKeys: Set<string>;
  bib: HTMLElement;
  citations: RenderedCitation[];
  citeBibMap: Map<string, string>;

  settings: ScopedSettings | null;

  source: {
    bibCache?: Map<string, PartialCSLEntry>;
    fuse?: Fuse<PartialCSLEntry>;
    engine?: any;
  };
}

/** Disk-serializable subset of FileCache for the persistent render cache. */
interface PersistedNoteCache {
  contentHash: string;
  /** File mtime at render time (ms). Used for a fast synchronous first-paint
   *  validity check (mtime unchanged → content unchanged → cached citations
   *  still apply) without reading the file. */
  mtime: number;
  /** Zotero library version at render time. If it's unchanged now, NO item
   *  could have changed — the whole note replays safely even when per-key
   *  `_version`s are unavailable (e.g. a cache written before _version was
   *  added). */
  libraryVersion: number;
  keys: string[];
  resolvedKeys: string[];
  unresolvedKeys: string[];
  globalOnlyKeys: string[];
  /** Per-citekey Zotero item version at render time. A key whose current
   *  `_version` differs needs its citation groups re-rendered; keys with a
   *  matching version keep their cached render. Absent for .bib-sourced keys
   *  (they have no per-entry version — the .bib file fingerprint covers them). */
  versions: Record<string, number | undefined>;
  /** Per-citekey fingerprint of the entry's author names + issued year.
   *  Disambiguation (initials, year-letters) depends only on these, so if a
   *  changed key's fingerprint is unchanged, EVERY citation in the note is
   *  provably identical and only the bibliography needs rebuilding. */
  authorYearFp: Record<string, string>;
  bibHtml: string | null;
  citations: RenderedCitation[];
  citeBibMap: Record<string, string>;
}

function getFrontmatterString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;

  const trimmed = value.trim();
  return trimmed || undefined;
}

function getFrontmatterStringList(value: unknown): string[] {
  const values = Array.isArray(value) ? value : [value];
  return values
    .map(getFrontmatterString)
    .filter((v): v is string => !!v);
}

// Resolve a frontmatter bibliography path relative to the containing note.
// Returns the path as-is if absolute; otherwise constructs a vault-relative path.
function resolveScopedPath(file: TFile, scopedPath: string): string {
  if (isAbsolutePath(scopedPath)) return scopedPath;
  const noteDir = file.path.split('/').slice(0, -1).join('/');
  return normalizePath(noteDir ? `${noteDir}/${scopedPath}` : scopedPath);
}

// Fast deterministic hash for cache keys (FNV-1a 32-bit → hex).
function fastHash(input: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16);
}

export function getScopedSettings(file: TFile): ScopedSettings | null {
  const metadata = app.metadataCache.getFileCache(file);
  const output: ScopedSettings = {};

  if (!metadata?.frontmatter) {
    return null;
  }

  const { frontmatter } = metadata;

  const bibliography = getFrontmatterStringList(frontmatter.bibliography).map(
    (bibPath) => resolveScopedPath(file, bibPath)
  );
  output.bibliography = bibliography.length ? bibliography : undefined;

  // bibliography frontmatter is used for colour comparison (blue/yellow/red)
  // and passed to other tools (Pandoc). the plugin always renders from the global
  // engine — bibliography never overrides the CSL source.
  output.snapshotBib = bibliography.length ? bibliography : undefined;

  output.style =
    getFrontmatterString(frontmatter.csl) ||
    getFrontmatterString(frontmatter['citation-style']) ||
    undefined;
  output.lang =
    getFrontmatterString(frontmatter.lang) ||
    getFrontmatterString(frontmatter['citation-language']) ||
    undefined;

  if (Object.values(output).every((v) => !v)) {
    return null;
  }

  return output;
}

function extractRawLocales(style: string, localeName?: string) {
  const locales = ['en-US'];
  if (localeName) {
    locales.push(localeName);
  }
  if (style) {
    const matches = style.match(/locale="[^"]+"/g);
    if (matches) {
      for (const match of matches) {
        const vals = match.slice(0, -1).slice(8).split(/\s+/);
        for (const val of vals) {
          locales.push(val);
        }
      }
    }
  }
  return normalizeLocales(locales);
}

function normalizeLocales(locales: string[]) {
  const obj: Record<string, boolean> = {};
  for (let locale of locales) {
    locale = locale.split('-').slice(0, 2).join('-');
    if (CSL.LANGS[locale]) {
      obj[locale] = true;
    } else {
      locale = locale.split('-')[0];
      if (CSL.LANG_BASES[locale]) {
        locale = CSL.LANG_BASES[locale].split('_').join('-');
        obj[locale] = true;
      }
    }
  }
  return Object.keys(obj);
}

export class BibManager {
  plugin: ReferenceList;
  fileCache: SimpleLRU<TFile, FileCache>;
  initPromise: PromiseCapability<void>;

  langCache: Map<string, string> = new Map();
  styleCache: Map<string, string> = new Map();

  bibCache: Map<string, PartialCSLEntry> = new Map();
  fuse: Fuse<PartialCSLEntry>;
  fuseTitle: Fuse<PartialCSLEntry>;
  engine: any;

  /** True as soon as the Fuse index is built — gates autocomplete independently
   *  of the CSL engine so `@` suggestions are available before citeproc compiles
   *  the citation style. */
  get fuseReady(): boolean {
    return this.fuse != null;
  }

  zCitekeyToLinks: Map<string, string> = new Map();
  zCitekeyToPDFLinks: Map<string, string[]> = new Map();

  /** Persistent citation index: file path → cited citekeys, covering only the
   *  vault's numbered content folders (`_1`, `_2`, …). Built once and updated
   *  incrementally on file changes so vault-wide queries (e.g. "create notes
   *  for cited works lacking notes") don't re-scan every markdown file. */
  citedKeysByFile: Map<string, Set<string>> = new Map();
  citedKeysIndexDirty = false;
  /** Number of indexable (_N) markdown files when the index was last built. */
  indexMdCount = 0;

  /** Persistent per-note rendered-citation cache, keyed by note path.
   *  Speeds up restarts: notes whose content + settings + bib source are
   *  unchanged reuse the rendered citations instead of re-running citeproc. */
  private renderedCache: Map<string, PersistedNoteCache> = new Map();
  renderedCacheLoaded = false;
  renderedCacheDirty = false;

  /** Paths whose persisted entry was validated and hydrated into fileCache at
   *  startup. For these, Obsidian's first render already used the cached
   *  citations, so dispatchResult's forced re-render can be skipped. */
  private hydratedPaths: Set<string> = new Set();

  /** Path → contentHash last dispatched to the renderer. Re-dispatching the
   *  same content forces a full markdown re-render for no gain — skip it. */
  private dispatchedHashes: Map<string, string> = new Map();

  /** Bumped whenever any .bib file content changes (watched-bib modify or
   *  reinit). Included in the note hash so a scoped .bib edit invalidates
   *  the persistent render cache even though per-key versions don't exist
   *  for .bib-sourced entries. */
  private bibEpoch = 0;

  // Keys loaded from the .bib file — used to detect cross-source conflicts.
  bibSourceKeys: Set<string> = new Set();
  // Keys present in both .bib and Zotero (Zotero wins; flagged in the UI).
  conflictKeys: Set<string> = new Set();

  /** Prevents concurrent refreshGlobalZBib() calls (both startup and the
   *  CiteSuggest-triggered path). Without this guard, two in-flight refresh
   *  calls can both call updateFuse() for the same newly-added items, pushing
   *  the same entry into fuse._docs twice and producing duplicate suggestions. */
  private _isRefreshingZBib = false;

  /** Maps the stable 8-char Zotero item key (_zoteroKey) to the citekey
   *  currently stored in bibCache for that item. The Zotero item key never
   *  changes; the citekey can be renamed by the user in Zotero/BBT.
   *  Used in mergeZoteroEntry to detect renames and evict stale citekeys. */
  private _zoteroKeyToCitekey: Map<string, string> = new Map();

  /**
   * Citekey renames detected during the most recent refreshGlobalZBib() call.
   * Maps old citekey → new citekey. Cleared at the start of each refresh.
   * main.ts reads this after the refresh completes and prompts the user if
   * non-empty. Public so main.ts can read it without going through a getter.
   */
  public _renamedThisRefresh: Map<string, string> = new Map();

  // Vault-relative paths of bib files to watch for changes.
  private watchedBibPaths: Set<string> = new Set();
  private globalWatchedBibPaths: Set<string> = new Set();
  private scopedWatchedBibPaths: Map<string, Set<string>> = new Map();

  constructor(plugin: ReferenceList) {
    this.plugin = plugin;
    this.initPromise = new PromiseCapability();
    this.fileCache = new SimpleLRU({ max: 10 });
    // Single vault-level listener replaces per-file FSWatchers.
    plugin.registerEvent(
      plugin.app.vault.on('modify', (file) => {
        const p = normalizePath(file.path);
        if (!this.watchedBibPaths.has(p)) return;
        // Reload all global sources (bib first, Zotero on top), rebuild engine.
        const { settings } = plugin;
        this.bibCache.clear();
        this.bibSourceKeys.clear();
        this.conflictKeys.clear();

        const reload = async () => {
          if (settings.bibliographyPaths?.length) await this.loadGlobalBibFiles();
          if (settings.pullFromZotero) await this.loadGlobalZBib(false);
          await this.buildGlobalEngine();
          this.bibEpoch++;
          this.fileCache.clear();
          plugin.processReferences();
        };
        reload().catch(console.error);
      })
    );
  }

  /** Drop the in-memory render state for a file so the next render (and the
   *  next dispatchResult) is forced to rebuild from the persisted cache or a
   *  fresh render. Called when the file's content changes. */
  invalidateFile(file: TFile) {
    this.fileCache.delete(file);
    this.dispatchedHashes.delete(file.path);
    this.hydratedPaths.delete(file.path);
  }

  destroy() {
    this.fileCache.clear();
    this.watchedBibPaths.clear();
    this.globalWatchedBibPaths.clear();
    this.scopedWatchedBibPaths.clear();
    this.bibSourceKeys.clear();
    this.conflictKeys.clear();
    this.langCache.clear();
    this.styleCache.clear();
    this.bibCache.clear();
    this.fuse = null;
    this.fuseTitle = null;
    this.engine = null;
    this.plugin = null;
  }

  async reinit(clearBibData: boolean) {
    this.initPromise = new PromiseCapability();
    this.fileCache.clear();

    if (clearBibData) {
      this._renamedThisRefresh.clear();
      this.bibCache.clear();
      this.bibSourceKeys.clear();
      this.conflictKeys.clear();
      this.bibEpoch++;

      const { settings } = this.plugin;
      if (settings.bibliographyPaths?.length) await this.loadGlobalBibFiles();
      if (settings.pullFromZotero) await this.loadGlobalZBib(false);
    }

    await this.buildGlobalEngine();
    this.initPromise.resolve();

    // loadGlobalZBib (above) calls mergeZoteroEntry for every item, which
    // populates _renamedThisRefresh just like refreshGlobalZBib does.
    // Check here so the "Refresh bibliography" button also triggers the
    // current-note auto-update when a rename is detected.
    if (this._renamedThisRefresh.size > 0) {
      const snapshot = new Map(this._renamedThisRefresh);
      setTimeout(() => this.plugin.autoUpdateCurrentNote(snapshot), 500);
    }
  }

  // Build the Fuse indexes from the current bibCache without touching the CSL
  // engine. Called after all data sources have loaded so that autocomplete
  // is available before the (slower) citeproc engine compilation finishes.
  buildFuseIndex() {
    this.setFuse(Array.from(this.bibCache.values()));
  }

  setFuse(data: PartialCSLEntry[] = []) {
    if (!this.fuse) {
      this.fuse = new Fuse(data, fuseSettings);
    } else {
      this.fuse.setCollection(data);
    }
    if (!this.fuseTitle) {
      this.fuseTitle = new Fuse(data, fuseTitleSettings);
    } else {
      this.fuseTitle.setCollection(data);
    }
  }

  updateFuse(_data?: Map<string, PartialCSLEntry>) {
    // Always rebuild from bibCache rather than doing incremental remove+add.
    // Incremental updates are susceptible to race conditions: if two concurrent
    // refreshGlobalZBib() calls both reach updateFuse() before either has
    // finished removing the old entries, fuse.add() can be called twice for the
    // same entry, inserting a duplicate into fuse._docs. searchCitekeyFirst()
    // iterates _docs directly (Tiers 1–2), so any duplicate surfaces immediately
    // as a doubled suggestion. A full rebuild from the Map-keyed bibCache is
    // always duplicate-free and only marginally slower for a typical library.
    if (!this.fuse) return;
    this.setFuse(Array.from(this.bibCache.values()));
  }

  async loadScopedEngine(settings: ScopedSettings) {
    if (!settings) return this;

    const pluginSettings = this.plugin.settings;
    let style =
      pluginSettings.cslStyleURL ??
      'https://raw.githubusercontent.com/citation-style-language/styles/master/apa.csl';
    let lang = pluginSettings.cslLang ?? 'en-US';
    let bibCache = this.bibCache;
    let fuse = this.fuse;
    let langs = [settings.lang];

    if (settings.style) {
      try {
        const isURL = /^http/.test(settings.style);
        const styleObj = isURL
          ? { id: settings.style }
          : { id: settings.style, explicitPath: settings.style };
        const styles = await this.loadStyles([styleObj]);
        for (const styleStr of styles) {
          langs = extractRawLocales(styleStr, settings.lang);
        }
        style = settings.style;
      } catch (e) {
        console.error(e);
        this.plugin.view?.setMessage((e as Error).message);
        return this;
      }
    }

    if (settings.lang) {
      try {
        await this.loadLangs(langs);
        lang = settings.lang;
      } catch (e) {
        console.error(e);
        this.plugin.view?.setMessage((e as Error).message);
        return this;
      }
    }

    if (settings.bibliography?.length) {
      try {
        const bib = await bibPathsToCSL(
          settings.bibliography,
          this.plugin.settings.pathToPandoc
        );
        bibCache = new Map();

        for (const entry of bib) {
          bibCache.set(entry.id, entry);
        }

        fuse = new Fuse(bib, fuseSettings);
      } catch (e) {
        console.error(e);
        throw e;
      }
    }

    try {
      const engine = this.buildEngine(
        lang,
        this.langCache,
        style,
        this.styleCache,
        bibCache
      );

      return {
        bibCache,
        fuse,
        engine,
      };
    } catch (e) {
      console.error(e);
      return this;
    }
  }

  // Load all configured .bib files into bibCache tagged as 'bib'.
  // Does not build the CSL engine — call buildGlobalEngine() after all sources load.
  //
  // Parse cache: results are stored in .pandoc/bib-parsed.json as an array of
  // per-file entries keyed by (path + mtime + size + pandocPath). On startup,
  // an unchanged file loads from JSON in ~5ms instead of running bibtex-parser.
  // Absolute paths outside the vault are always re-parsed (no stat available).
  async loadGlobalBibFiles() {
    const { settings } = this.plugin;
    const paths = settings.bibliographyPaths ?? [];
    if (!paths.length) return;

    const CACHE_DIR = normalizePath('.pandoc');
    const BIB_CACHE_PATH = normalizePath('.pandoc/bib-parsed.json');
    const pandoc = settings.pathToPandoc ?? '';

    // Load existing cache file once up-front.
    const cacheMap = new Map<string, { mtime: number; size: number; pandoc: string; entries: PartialCSLEntry[] }>();
    try {
      if (await app.vault.adapter.exists(BIB_CACHE_PATH)) {
        const raw = JSON.parse(await app.vault.adapter.read(BIB_CACHE_PATH));
        if (Array.isArray(raw)) {
          for (const entry of raw) cacheMap.set(entry.path, entry);
        }
      }
    } catch {
      // Corrupt or missing cache — start fresh.
    }

    let cacheModified = false;
    let settingsModified = false;

    for (let i = 0; i < paths.length; i++) {
      const rawPath = paths[i];
      if (!rawPath?.trim()) continue;

      let resolved: string;
      try {
        resolved = await getBibPath(rawPath);
      } catch (e) {
        console.error(`scholar-weave: cannot resolve .bib path "${rawPath}":`, e);
        continue;
      }

      // Persist normalised path back to settings if it changed.
      if (resolved !== rawPath) {
        console.info(`scholar-weave: normalised bib path "${rawPath}" → "${resolved}"`);
        settings.bibliographyPaths[i] = resolved;
        settingsModified = true;
      }

      let bib: PartialCSLEntry[] | null = null;

      if (!isAbsolutePath(resolved)) {
        try {
          const stat = await app.vault.adapter.stat(normalizePath(resolved));
          const cached = cacheMap.get(resolved);
          if (stat && cached &&
              cached.mtime === stat.mtime &&
              cached.size === stat.size &&
              cached.pandoc === pandoc) {
            bib = cached.entries;
            debugLog(`[lc:bib] parse cache hit for "${resolved}" — ${bib.length} entries`);
          }
        } catch {
          // Fall through to full parse.
        }
      }

      if (!bib) {
        try {
          bib = await bibToCSL(resolved, settings.pathToPandoc);
          debugLog(`[lc:bib] parsed "${resolved}" — ${bib?.length ?? 0} entries`);
        } catch (e) {
          console.error(`scholar-weave: failed to load "${resolved}":`, e);
          continue;
        }

        if (!isAbsolutePath(resolved)) {
          try {
            const stat = await app.vault.adapter.stat(normalizePath(resolved));
            if (stat) {
              cacheMap.set(resolved, { mtime: stat.mtime, size: stat.size, pandoc, entries: bib });
              cacheModified = true;
            }
          } catch {
            // Cache write failure is non-fatal.
          }
        }
      }

      // Register for change watching (vault-relative paths only).
      if (!isAbsolutePath(resolved)) {
        this.globalWatchedBibPaths.add(normalizePath(resolved));
      }

      for (const entry of bib) {
        this.bibCache.set(entry.id, { ...entry, _source: 'bib' });
        this.bibSourceKeys.add(entry.id);
      }
    }

    this.rebuildWatchedBibPaths();

    // Flush updated cache to disk.
    if (cacheModified) {
      try {
        if (!(await app.vault.adapter.exists(CACHE_DIR))) {
          await app.vault.adapter.mkdir(CACHE_DIR);
        }
        await app.vault.adapter.write(BIB_CACHE_PATH, JSON.stringify([...cacheMap.values()]));
      } catch {
        // Cache write failure is non-fatal.
      }
    }

    if (settingsModified) this.plugin.saveSettings();
    debugLog('[lc:bib] bibCache now has', this.bibCache.size, 'entries after .bib load');
  }

  getZoteroAdapter(): ZoteroAdapter {
    const { settings } = this.plugin;
    const port = settings.zoteroPort ?? DEFAULT_ZOTERO_PORT;
    return settings.useNativeZoteroAPI
      ? new NativeAdapter(port)
      : new BBTAdapter(port);
  }

  async isZoteroAvailable(): Promise<boolean> {
    return this.getZoteroAdapter().isRunning();
  }

  async loadAndRefreshGlobalZBib() {
    await this.loadGlobalZBib(true);
    // refreshGlobalZBib runs after engine is built by the caller
  }

  // Merge Zotero entries into bibCache (Zotero wins on conflicts with .bib).
  // Within Zotero, keeps the most recently modified entry when a citationKey
  // appears in multiple groups. Does not build the CSL engine.
  async loadGlobalZBib(fromCache?: boolean) {
    const { settings } = this.plugin;
    debugLog('[lc:bib] loadGlobalZBib, fromCache=', fromCache, 'zoteroGroups=', JSON.stringify(settings.zoteroGroups), 'pullFromZotero=', settings.pullFromZotero);
    if (!settings.zoteroGroups?.length) {
      debugLog('[lc:bib] no zoteroGroups configured — skipping Zotero load');
      return;
    }

    const adapter = this.getZoteroAdapter();
    debugLog('[lc:bib] using adapter:', (adapter as any).constructor?.name ?? typeof adapter);
    for (const group of settings.zoteroGroups) {
      try {
        debugLog('[lc:bib] fetching group', group.id, group.name);
        const res = await adapter.getBib('', group.id, fromCache);
        debugLog('[lc:bib] group', group.id, 'returned', res.list?.length ?? 'null', 'entries');
        if (!res.list?.length) continue;

        if (!fromCache) {
          group.lastUpdate = Date.now();
          group.libraryVersion = res.version;
        }

        for (const entry of res.list) {
          this.mergeZoteroEntry(entry);
        }
      } catch (e) {
        console.error('scholar-weave: Zotero load failed:', e);
      }
    }

    debugLog('[lc:bib] bibCache now has', this.bibCache.size, 'entries after Zotero load');
    this.plugin.saveSettings();
  }

  // Merge a single Zotero entry into bibCache with full priority + dedup logic.
  private mergeZoteroEntry(entry: PartialCSLEntry) {
    // ── Citekey-rename detection ──────────────────────────────────────────────
    // The Zotero item key (_zoteroKey, an 8-char stable ID) never changes even
    // when the user renames the citekey in Zotero or Better BibTeX. If we've
    // seen this Zotero item before under a DIFFERENT citekey, the old citekey
    // is now stale — delete it so the old key can no longer be cited or found.
    if (entry._zoteroKey) {
      const oldCitekey = this._zoteroKeyToCitekey.get(entry._zoteroKey);
      if (oldCitekey && oldCitekey !== entry.id) {
        this.bibCache.delete(oldCitekey);
        this.bibSourceKeys.delete(oldCitekey);
        this.conflictKeys.delete(oldCitekey);

        // ── Persist rename history ───────────────────────────────────────────
        // Record old → new in _renamedThisRefresh so the startup hook in
        // main.ts can prompt the user after this refresh completes.
        this._renamedThisRefresh.set(oldCitekey, entry.id);

        // Persist to settings with chain-following so that notes which still
        // contain a citekey from two or more renames ago are also updated.
        // Strategy: scan the existing history for any value === oldCitekey and
        // forward it to the new citekey. Then add oldCitekey → newCitekey.
        // Example: history had {A: B} and we see B → C.
        // After update: {A: C, B: C} — notes with @A get updated to @C in one
        // pass, without needing intermediate @B entries first.
        const history = this.plugin.settings.citekeyRenameHistory ?? {};
        for (const [ancestor, current] of Object.entries(history)) {
          if (current === oldCitekey) {
            history[ancestor] = entry.id;
          }
        }
        history[oldCitekey] = entry.id;
        this.plugin.settings.citekeyRenameHistory = history;
        // Persist immediately so the history survives plugin reloads.
        void this.plugin.saveSettings();
      }
      this._zoteroKeyToCitekey.set(entry._zoteroKey, entry.id);
    }

    const existing = this.bibCache.get(entry.id);
    const tagged = { ...entry, _source: 'zotero' as const };

    if (existing?._source === 'zotero') {
      // Cross-group duplicate — keep whichever was modified more recently.
      if ((tagged._dateModified ?? '') > (existing._dateModified ?? '')) {
        this.bibCache.set(entry.id, tagged);
      }
      // else keep existing; both from Zotero so no conflict with .bib
      return;
    }

    if (existing?._source === 'bib') {
      // Key exists in both sources — flag it, Zotero wins.
      this.conflictKeys.add(entry.id);
    }

    this.bibCache.set(entry.id, tagged);
  }

  async refreshGlobalZBib() {
    // Guard against concurrent executions. CiteSuggest calls this on every @
    // keystroke (rate-limited to 30 s) AND main.ts fires it unawaited at
    // startup — without the guard both can be in flight simultaneously,
    // causing duplicate fuse._docs entries and doubled suggestions.
    if (this._isRefreshingZBib) return;
    this._isRefreshingZBib = true;
    this._renamedThisRefresh.clear();

    try {
      const { settings } = this.plugin;
      if (!settings.zoteroGroups?.length) return;

      const adapter = this.getZoteroAdapter();
      const modifiedEntries: Map<string, PartialCSLEntry> = new Map();

      for (const group of settings.zoteroGroups) {
        try {
          const res = await adapter.refreshBib(
            '',
            group.id,
            group.libraryVersion ?? 0,
            group.lastUpdate
          );

          if (!res) continue;
          if (res.list?.length) group.lastUpdate = Date.now();

          for (const [k, v] of res.modified.entries()) {
            this.mergeZoteroEntry(v);
            modifiedEntries.set(k, this.bibCache.get(k)!);
          }
        } catch (e) {
          console.error('scholar-weave: Zotero refresh failed:', e);
        }
      }

      this.plugin.saveSettings();
      this.updateFuse(modifiedEntries);
      this.fileCache.clear();
      this.plugin.processReferences();

      // If any citekeys were renamed during this refresh, silently apply the
      // changes to the currently open note and show a brief Notice. A snapshot
      // is taken so the map can be cleared by a subsequent refresh without
      // affecting the pending setTimeout callback.
      if (this._renamedThisRefresh.size > 0) {
        const snapshot = new Map(this._renamedThisRefresh);
        setTimeout(() => this.plugin.autoUpdateCurrentNote(snapshot), 1500);
      }
    } finally {
      this._isRefreshingZBib = false;
    }
  }

  // ── Vault citekey-rename utilities ─────────────────────────────────────────

  /**
   * Apply renames to a single file in place, returning a list of which
   * old→new pairs were actually found and replaced.  Used by the auto-update
   * path so we don't scan the entire vault on every sync.
   */
  async applyRenamesInFile(
    file: TFile,
    renameMap: Map<string, string>
  ): Promise<Array<{ oldKey: string; newKey: string }>> {
    const { vault } = this.plugin.app;
    const NC = '(?![\\p{L}\\p{N}:.#$%&\\-+?<>~_\\/])';
    let content = await vault.read(file);
    const changed: Array<{ oldKey: string; newKey: string }> = [];

    for (const [oldKey, newKey] of renameMap) {
      const escaped = oldKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp(`@${escaped}${NC}`, 'gu');
      if (re.test(content)) {
        re.lastIndex = 0;
        content = content.replace(re, `@${newKey}`);
        changed.push({ oldKey, newKey });
      }
    }

    if (changed.length) await vault.modify(file, content);
    return changed;
  }

  /**
   * Scan all markdown files in the vault for occurrences of citekeys listed
   * in `renameMap` (old → new) and return a plan describing every file and
   * line that needs to change.
   *
   * The boundary regex `@key(?![…])` matches only when the character after the
   * key is NOT a citekey-valid character, preventing partial-key matches
   * (e.g. `@smithA` from matching `@smith`).
   */
  async findCitekeyUsagesInVault(
    renameMap: Record<string, string>
  ): Promise<Map<TFile, import('../modals/citekeyRenameModal').CitekeyChange[]>> {
    const { vault } = this.plugin.app;
    // Boundary: NOT a citekey-valid char.  Matches Pandoc's own citekey charset.
    const NC = '(?![\\p{L}\\p{N}:.#$%&\\-+?<>~_\\/])';

    // Pre-compile one regex per old key.
    const patterns: Array<{ oldKey: string; newKey: string; re: RegExp }> = [];
    for (const [oldKey, newKey] of Object.entries(renameMap)) {
      const escaped = oldKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      patterns.push({
        oldKey,
        newKey,
        re: new RegExp(`@${escaped}${NC}`, 'gu'),
      });
    }

    const plan: Map<TFile, import('../modals/citekeyRenameModal').CitekeyChange[]> = new Map();

    for (const file of vault.getMarkdownFiles()) {
      const content = await vault.read(file);
      const lines = content.split('\n');
      const changes: import('../modals/citekeyRenameModal').CitekeyChange[] = [];

      for (const { oldKey, newKey, re } of patterns) {
        const hitLines: number[] = [];
        for (let i = 0; i < lines.length; i++) {
          re.lastIndex = 0;
          if (re.test(lines[i])) hitLines.push(i + 1); // 1-based
        }
        if (hitLines.length) {
          changes.push({ oldKey, newKey, lines: hitLines });
        }
      }

      if (changes.length) plan.set(file, changes);
    }

    return plan;
  }

  /**
   * Scan a single file for stale citekeys in `renameMap`, returning a list of
   * `CitekeyChange` objects (one per old→new pair that actually appears in the
   * file).  Used by the unresolved-badge modal to show only the fixable keys
   * that exist in the current note rather than scanning the whole vault.
   */
  async findCitekeyUsagesInFile(
    file: TFile,
    renameMap: Record<string, string>
  ): Promise<import('../modals/citekeyRenameModal').CitekeyChange[]> {
    const { vault } = this.plugin.app;
    const NC = '(?![\\p{L}\\p{N}:.#$%&\\-+?<>~_\\/])';

    const patterns: Array<{ oldKey: string; newKey: string; re: RegExp }> = [];
    for (const [oldKey, newKey] of Object.entries(renameMap)) {
      const escaped = oldKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      patterns.push({ oldKey, newKey, re: new RegExp(`@${escaped}${NC}`, 'gu') });
    }

    const content = await vault.read(file);
    const lines = content.split('\n');
    const changes: import('../modals/citekeyRenameModal').CitekeyChange[] = [];

    for (const { oldKey, newKey, re } of patterns) {
      const hitLines: number[] = [];
      for (let i = 0; i < lines.length; i++) {
        re.lastIndex = 0;
        if (re.test(lines[i])) hitLines.push(i + 1);
      }
      if (hitLines.length) changes.push({ oldKey, newKey, lines: hitLines });
    }

    return changes;
  }

  /**
   * Apply a rename plan produced by `findCitekeyUsagesInVault` to the vault.
   * Each file is read, all matching `@oldKey` occurrences are replaced with
   * `@newKey` (respecting the same boundary), and the file is written back.
   */
  async applyRenames(
    plan: Map<TFile, import('../modals/citekeyRenameModal').CitekeyChange[]>
  ): Promise<void> {
    const { vault } = this.plugin.app;
    const NC = '(?![\\p{L}\\p{N}:.#$%&\\-+?<>~_\\/])';

    for (const [file, changes] of plan) {
      let content = await vault.read(file);
      for (const { oldKey, newKey } of changes) {
        const escaped = oldKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        // Lookahead doesn't consume, so replacing the whole match with
        // `@newKey` leaves the following character untouched.
        const re = new RegExp(`@${escaped}${NC}`, 'gu');
        content = content.replace(re, `@${newKey}`);
      }
      await vault.modify(file, content);
    }
  }

  // Build (or rebuild) the global CSL engine from the current bibCache.
  // Must be called after all sources have finished loading.
  // Also (re)builds the Fuse indexes so that reinit() and the vault-file watcher
  // don't need a separate buildFuseIndex() call.
  async buildGlobalEngine() {
    const { settings } = this.plugin;

    debugLog('[lc:bib] buildGlobalEngine, bibCache.size=', this.bibCache.size);
    this.setFuse(Array.from(this.bibCache.values()));

    const style =
      settings.cslStylePath ||
      settings.cslStyleURL ||
      'https://raw.githubusercontent.com/citation-style-language/styles/master/apa.csl';
    const lang = settings.cslLang || 'en-US';

    await this.getLangAndStyle(lang, {
      id: style,
      explicitPath: settings.cslStylePath,
    });
    if (!this.styleCache.has(style)) return;

    try {
      this.engine = this.buildEngine(
        lang,
        this.langCache,
        style,
        this.styleCache,
        this.bibCache
      );
    } catch (e) {
      console.error(e);
    }
  }

  buildEngine(
    lang: string,
    langCache: Map<string, string>,
    style: string,
    styleCache: Map<string, string>,
    bibCache: Map<string, PartialCSLEntry>
  ) {
    const styleXML = styleCache.get(style);
    if (!styleXML) {
      throw new Error(
        'attempting to build citproc engine with empty CSL style'
      );
    }
    if (!langCache.get(lang)) {
      throw new Error(
        'attempting to build citproc engine with empty CSL locale'
      );
    }
    const engine = new CSL.Engine(
      {
        retrieveLocale: (id: string) => {
          return langCache.get(id);
        },
        retrieveItem: (id: string) => {
          return bibCache.get(id);
        },
      },
      styleXML,
      lang
    );
    engine.opt.development_extensions.wrap_url_and_doi = true;
    return engine;
  }

  async getLangAndStyle(
    lang: string,
    style: { id: string; explicitPath?: string }
  ) {
    let styles: string[] = [];
    if (!this.styleCache.has(style.id)) {
      try {
        styles = await this.loadStyles([style]);
      } catch (e) {
        console.error('Error loading style', style, e);
        this.initPromise.resolve();
        return;
      }
    }

    let locales = [lang];
    for (const styleStr of styles) {
      locales = extractRawLocales(styleStr, lang);
    }

    try {
      await this.loadLangs(locales);
    } catch (e) {
      console.error('Error loading lang', lang, e);
      this.initPromise.resolve();
      return;
    }
  }

  async loadLangs(langs: string[]) {
    for (const lang of langs) {
      if (!lang) continue;
      if (!this.langCache.has(lang)) {
        await getCSLLocale(this.langCache, this.plugin.cacheDir, lang);
      }
    }
  }

  async loadStyles(styles: { id?: string; explicitPath?: string }[]) {
    const res: string[] = [];
    for (const style of styles) {
      if (!style.id && !style.explicitPath) continue;
      if (!this.styleCache.has(style.explicitPath ?? style.id)) {
        res.push(
          await getCSLStyle(
            this.styleCache,
            this.plugin.cacheDir,
            style.id,
            style.explicitPath
          )
        );
      }
    }
    return res;
  }

  getNoteForNoteIndex(file: TFile, index: string) {
    if (!this.fileCache.has(file)) {
      return null;
    }

    const cache = this.fileCache.get(file);
    const noteIndex = parseInt(index);

    const cite = cache.citations.find((c) => c.noteIndex === noteIndex);

    if (!cite.note) {
      return null;
    }

    const doc = new DOMParser().parseFromString(cite.note, 'text/html');
    return Array.from(doc.body.childNodes);
  }

  /** Ensure the Zotero select link for a citekey is known — derived from the
   *  entry's `_zoteroKey` (already in the library cache), zero HTTP. Called
   *  from the tooltip/bibliography render paths so links are available even
   *  on a cold start before getZLinksForKeys ran. */
  private ensureZLink(key: string) {
    if (this.zCitekeyToLinks.has(key)) return;
    const item = this.bibCache.get(key);
    const zKey = (item as any)?._zoteroKey;
    if (!zKey) return;
    const groupId = item?.groupID ?? 1;
    this.zCitekeyToLinks.set(
      key,
      groupId === 1
        ? `zotero://select/library/items/${zKey}`
        : `zotero://select/groups/${groupId}/items/${zKey}`
    );
  }

  getBibForCiteKey(file: TFile, key: string) {
    if (!this.fileCache.has(file)) {
      return null;
    }

    const cache = this.fileCache.get(file);
    if (!cache.keys.has(key)) {
      return null;
    }

    const html = cache.citeBibMap.get(key);
    if (!html) {
      return null;
    }

    this.ensureZLink(key);

    const doc = new DOMParser().parseFromString(html, 'text/html');
    const el = doc.body.firstElementChild as HTMLElement;
    if (el) {
      el.dataset.citekey = key;
      return this.prepBibHTML(el, file, true);
    }
    return el;
  }

  async getReferenceList(
    file: TFile,
    content: string,
    shouldContinue: () => boolean = () => true
  ) {
    await this.plugin.initPromise.promise;
    if (!shouldContinue()) return undefined;
    await this.initPromise.promise;
    if (!shouldContinue()) return undefined;

    const segs = getCitationSegments(
      content,
      !this.plugin.settings.renderLinkCitations,
      this.plugin.settings.formatLinkAliases
    );
    const processed = segs.map((s) => getCitations(s));

    // Load the persistent cache once so both the prune path (no citations)
    // and the fast path below see the on-disk state.
    await this.loadRenderedCache();

    if (!processed.length) {
      // Note no longer cites anything — drop any stale persisted entry.
      if (this.renderedCache.has(file.path)) {
        this.renderedCache.delete(file.path);
        this.renderedCacheDirty = true;
        this.scheduleRenderedCacheSave();
      }
      return null;
    }

    const citeKeys = new Set<string>();
    const unresolvedKeys = new Set<string>();
    const resolvedKeys = new Set<string>();
    const globalOnlyKeys = new Set<string>();
    const cachedDoc = this.fileCache.has(file)
      ? this.fileCache.get(file)
      : null;
    const citeBibMap = new Map<string, string>();
    const settings = getScopedSettings(file);

    processed.forEach((p) =>
      p.citations.forEach((c) => {
        if (c.id && !citeKeys.has(c.id)) {
          citeKeys.add(c.id);
        }
      })
    );

    const areSettingsEqual = equal(settings, cachedDoc?.settings);

    // Persistent render cache: if this note was rendered before and neither
    // its content nor the rendering inputs (style/lang/bib source) changed,
    // reuse the stored citations + bibliography instead of re-running
    // citeproc. No engine build, no cite() — just JSON → FileCache.
    const hash = this.noteCacheHash(file, content, settings);
    const persisted = this.renderedCache.get(file.path);
    const hashMatches = persisted && persisted.contentHash === hash;

    if (
      (!cachedDoc || !cachedDoc.source) &&
      hashMatches &&
      this.entryVersionsMatch(persisted!, this.bibCache)
    ) {
      // Whole-note replay: content AND every cited Zotero entry unchanged.
      const result = this.fileCacheFromPersisted(file, persisted!);
      if (!this.warmingSkipLRU) this.fileCache.set(file, result);
      this.dispatchResult(file, result, hash);
      return result.bib;
    }

    const source =
      cachedDoc?.source && areSettingsEqual
        ? cachedDoc.source
        : await this.loadScopedEngine(settings);
    if (!shouldContinue()) return undefined;

    this.updateScopedWatchedBibPaths(file, settings);

    const setNull = (): null => {
      const result: FileCache = {
        keys: citeKeys,
        resolvedKeys,
        unresolvedKeys,
        globalOnlyKeys,
        bib: null,
        citations: [],
        citeBibMap,
        settings: null,
        source,
      };

      if (!this.warmingSkipLRU) this.fileCache.set(file, result);
      this.dispatchResult(file, result);

      return null;
    };

    if (!source?.engine) {
      return setNull();
    }

    // Load snapshot citekeys (fast regex, no full parse) if the file has a
    // lc-snapshot frontmatter key. These are used only for colour comparison —
    // the global engine always handles rendering.
    const snapshotKeys: Set<string> = settings?.snapshotBib?.length
      ? await this.loadSnapshotKeys(settings.snapshotBib)
      : new Set();
    const hasSnapshot = snapshotKeys.size > 0;

    citeKeys.forEach((k) => {
      if (source.bibCache.has(k)) {
        resolvedKeys.add(k);
        if (hasSnapshot && !snapshotKeys.has(k)) {
          globalOnlyKeys.add(k); // in global library but not saved to snapshot → yellow
        }
      } else {
        unresolvedKeys.add(k);
      }
    });

    const filtered = processed.filter((s) =>
      s.citations.every((c) => {
        if (source.bibCache.has(c.id)) {
          resolvedKeys.add(c.id);
          if (hasSnapshot && !snapshotKeys.has(c.id)) {
            globalOnlyKeys.add(c.id);
          }
          return true;
        } else {
          unresolvedKeys.add(c.id);
          return false;
        }
      })
    );

    // Do we need this?
    // source.engine.updateItems(Array.from(resolvedKeys));

    // Partial re-render: content + settings unchanged (hash matched), but some
    // Zotero entries changed since the cached render. If every changed entry
    // kept its author+year, all inline citations are provably identical
    // (disambiguation only depends on author/year) — reuse the cached
    // citations and rebuild ONLY the bibliography. This skips the expensive
    // `cite()` cluster processing for notes that cite a just-edited entry.
    let citations: RenderedCitation[];
    const { changed, allAuthorYearStable } = hashMatches && persisted
      ? this.changedKeyStatus(persisted!, source.bibCache)
      : { changed: [], allAuthorYearStable: false };
    if (
      hashMatches &&
      persisted &&
      changed.length > 0 &&
      allAuthorYearStable
    ) {
      // Ensure the engine has all items so makeBibliography covers the full
      // reference list (cite() would normally register them during cluster
      // processing). updateItems alone keeps stale cached item data for
      // already-registered keys — force a re-fetch so the bibliography shows
      // the changed entries.
      source.engine.updateItems(Array.from(resolvedKeys));
      for (const key of changed) {
        source.engine.retrieveItem(key);
      }
      citations = persisted!.citations;
      if (!shouldContinue()) return undefined;
    } else {
      citations = cite(source.engine, filtered);
      if (!shouldContinue()) return undefined;
    }

    if (
      cachedDoc &&
      equal(cachedDoc.citations, citations) &&
      areSettingsEqual
    ) {
      return cachedDoc.bib;
    }

    const bib = source.engine.makeBibliography();

    if (!bib?.length) {
      return setNull();
    }

    const metadata = bib[0];
    const entries = bib[1];
    const htmlStr = [metadata.bibstart];

    metadata.entry_ids?.forEach((e: string, i: number) => {
      entries[i] = entries[i].replace(/>/, ` data-citekey="${e[0]}">`);
      citeBibMap.set(e[0], entries[i]);
    });

    for (const entry of entries) htmlStr.push(entry);

    htmlStr.push(metadata.bibend);
    let parsed = entries.length
      ? (new DOMParser().parseFromString(htmlStr.join(''), 'text/html').body
          .firstElementChild as HTMLElement)
      : null;

    if (parsed) {
      if (
        this.plugin.settings.pullFromZotero &&
        !settings?.bibliography?.length
      ) {
        await this.getZLinksForKeys(resolvedKeys);
        if (!shouldContinue()) return undefined;
      }
      parsed = this.prepBibHTML(parsed, file);
    }

    const result: FileCache = {
      keys: citeKeys,
      resolvedKeys,
      unresolvedKeys,
      globalOnlyKeys,
      bib: parsed,
      citations,
      citeBibMap,
      settings,
      source,
    };

    if (!this.warmingSkipLRU) this.fileCache.set(file, result);
    this.persistFileCache(file, result, hash);
    this.dispatchResult(file, result, hash);

    return result.bib;
  }

  /** Load citekeys from a snapshot .bib file using a fast regex scan —
   *  no full CSL parse needed since we only need the entry IDs. */
  private async loadSnapshotKeys(paths: string[]): Promise<Set<string>> {
    const keys = new Set<string>();
    for (const p of paths) {
      try {
        let text: string;
        if (isAbsolutePath(p)) {
          const buf = await FileSystemAdapter.readLocalFile(p);
          text = new TextDecoder().decode(buf);
        } else {
          text = await app.vault.adapter.read(normalizePath(p));
        }
        for (const m of text.matchAll(/@\w+\s*\{\s*([^,\s\n]+)\s*,/gm)) {
          keys.add(m[1].trim());
        }
      } catch {
        // File missing or unreadable — treat snapshot as empty.
      }
    }
    return keys;
  }

  /** Return all CSL entries for the citekeys currently used in `file`,
   *  drawing from the global library (so Zotero-only entries are included).
   *  Returns null if the file has no resolved or global-only keys yet. */
  snapshotEntries(file: TFile): PartialCSLEntry[] | null {
    const cache = this.fileCache.get(file);
    if (!cache) return null;
    const allKeys = new Set([...cache.resolvedKeys, ...cache.globalOnlyKeys]);
    if (!allKeys.size) return null;
    const entries: PartialCSLEntry[] = [];
    for (const key of allKeys) {
      const entry = this.bibCache.get(key);
      if (entry) entries.push(entry);
    }
    return entries.length ? entries : null;
  }

  /**
   * Populate the Zotero select-links + PDF-attachment maps for citekeys.
   * The `zotero://select` URL is built from the `_zoteroKey` already stored
   * in every CSL entry (item key from the library fetch) — ZERO HTTP calls.
   * PDF attachments still require a per-item fetch; only uncached keys are
   * fetched, and the maps are persisted (`.pandoc/zlinks.json`) so cold
   * starts skip the network entirely.
   */
  async getZLinksForKeys(citekeys: Set<string>) {
    // 1) Instant: derive select URLs from _zoteroKey, no network.
    citekeys.forEach((key) => {
      if (this.zCitekeyToLinks.has(key)) return;
      const item = this.bibCache.get(key);
      const zKey = (item as any)?._zoteroKey;
      if (!zKey) return;
      const groupId = item?.groupID ?? 1;
      const link =
        groupId === 1
          ? `zotero://select/library/items/${zKey}`
          : `zotero://select/groups/${groupId}/items/${zKey}`;
      this.zCitekeyToLinks.set(key, link);
    });

    // 2) Fetch PDF attachments only when the "show PDF links" setting is on,
    //    for keys that have a link but no cached attachment list yet.
    //    Batched per group, sequential per key (Zotero local API). Persisted
    //    so this only runs once per key ever. Skipped entirely during
    //    background warm-up.
    const queries: Record<number, string[]> = {};
    citekeys.forEach((key) => {
      if (this.zCitekeyToPDFLinks.has(key)) return;
      const item = this.bibCache.get(key);
      if (!item) return;
      const id = item.groupID;
      if (id === undefined) return;
      if (!queries[id]) queries[id] = [];
      queries[id].push(key);
    });
    if (
      this.warmingSkipPDFs ||
      this.plugin.settings.showPdfLinks === false ||
      !Object.keys(queries).length
    ) {
      return;
    }

    for (const id of Object.keys(queries)) {
      const groupId = Number(id);
      try {
        const items = await this.getZoteroAdapter().getItemsForCiteKeys(
          queries[groupId],
          groupId
        );
        if (items?.length) {
          for (const item of items) {
            const key = item.citekey || item.citationKey;
            if (item.attachments?.length) {
              const attLinks: string[] = [];
              for (const att of item.attachments) {
                if (/\.pdf$/.test(att.path)) {
                  attLinks.push(att.path);
                }
              }
              if (attLinks.length) {
                this.zCitekeyToPDFLinks.set(key, attLinks);
              }
            }
          }
        }
      } catch {
        //
      }
    }
    void this.plugin.persistZLinks?.();
  }

  /** Persist the Zotero link maps (debounced by caller). */
  async saveZLinks() {    try {
      const dir = normalizePath('.pandoc');
      if (!(await app.vault.adapter.exists(dir))) {
        await app.vault.adapter.mkdir(dir);
      }
      await app.vault.adapter.write(
        normalizePath('.pandoc/zlinks.json'),
        JSON.stringify({
          links: Object.fromEntries(this.zCitekeyToLinks),
          pdfs: Object.fromEntries(this.zCitekeyToPDFLinks),
        })
      );
    } catch (e) {
      console.warn('[lc] saveZLinks: error', e);
    }
  }

  /** Restore the persisted Zotero link maps (call once at startup). */
  async loadZLinks() {
    try {
      const raw = await app.vault.adapter.read(
        normalizePath('.pandoc/zlinks.json')
      );
      const data = JSON.parse(raw);
      if (data?.links) {
        for (const [k, v] of Object.entries(data.links)) {
          this.zCitekeyToLinks.set(k, v as string);
        }
      }
      if (data?.pdfs) {
        for (const [k, v] of Object.entries(data.pdfs)) {
          this.zCitekeyToPDFLinks.set(k, v as string[]);
        }
      }
    } catch {
      // no persisted links yet — first run populates them
    }
  }

  private warming = false;
  /** While true (background warm-up), skip the per-citekey PDF-attachment
   *  fetch — it only powers sidebar icons + the mobile link fallback, and
   *  those fetch on demand when the note is actually opened. */
  private warmingSkipPDFs = false;
  /** While true (background warm-up), don't write warmed notes into the
   *  10-slot in-memory LRU — that would churn the active note's entry. The
   *  persistent cache is still updated; opened notes hydrate from it. */
  private warmingSkipLRU = false;

  /**
   * Background warm-up: pre-render citations for files that are NOT currently
   * open (and not already cached), so opening them later is instant — no
   * 1–2 minute first-render wait. Runs at low priority with small yields so
   * the UI stays responsive; it is safe to run WHILE the user is working
   * (each render is fast now that the per-citekey HTTP fetch is gone).
   * Only notes with citations (from the cited-keys index) are considered.
   * Skips the PDF-attachment lookup while warming (near-dead weight — PDFs
   * only power sidebar icons / mobile link fallback; they fetch on demand
   * for notes actually opened).
   */
  async warmCitedFiles() {
    if (this.warming) return;
    this.warming = true;
    this.warmingSkipPDFs = true;
    this.warmingSkipLRU = true;
    try {
      await this.loadRenderedCache();
      const mdCount = app.vault
        .getMarkdownFiles()
        .filter((f) => this.isIndexablePath(f.path)).length;
      const stale =
        this.citedKeysByFile.size === 0 || this.indexMdCount !== mdCount;
      if (stale) await this.buildCitedKeysIndex();

      const openPaths = new Set<string>();
      app.workspace.getLeavesOfType('markdown').forEach((l) => {
        const v = l.view as MarkdownView;
        if (v?.file?.path) openPaths.add(v.file.path);
      });

      const candidates: TFile[] = [];
      for (const [path] of this.citedKeysByFile) {
        if (openPaths.has(path)) continue; // already open — rendered on demand
        if (this.renderedCache.has(path)) continue; // already cached
        const file = app.vault.getAbstractFileByPath(path);
        if (file instanceof TFile) candidates.push(file);
      }

      // Sort: most-cited first (biggest win), then render with yields.
      candidates.sort((a, b) => {
        const na = this.citedKeysByFile.get(a.path)?.size ?? 0;
        const nb = this.citedKeysByFile.get(b.path)?.size ?? 0;
        return nb - na;
      });

      for (const file of candidates) {
        try {
          const content = await app.vault.cachedRead(file);
          // getReferenceList persists the rendered entry itself when it
          // produces a bibliography (persistFileCache at the end of the
          // full path); LRU + PDF lookups are skipped while warming.
          await this.getReferenceList(file, content);
        } catch {
          // individual file failures shouldn't stop warming
        }
        await new Promise((r) => setTimeout(r, 15)); // yield to the UI
      }
      this.scheduleRenderedCacheSave();
    } finally {
      this.warming = false;
      this.warmingSkipPDFs = false;
      this.warmingSkipLRU = false;
    }
  }

  prepBibHTML(parsed: HTMLElement, file: TFile, inTooltip?: boolean) {
    if (this.plugin.settings.hideLinks) {
      parsed?.findAll('a').forEach((l) => {
        l.setAttribute('aria-label', l.innerText);
      });
    }

    if (parsed?.hasClass('csl-entry')) {
      const entry = parsed;
      parsed = createDiv();
      parsed.append(entry);
    }

    parsed?.findAll('.csl-entry').forEach((e) => {
      // Skip entries already wrapped (persisted HTML replayed from disk has
      // the full structure but no JS event handlers — bind those below).
      if (!e.parentElement?.hasClass('csl-entry-wrapper')) {
        const div = createDiv({ cls: 'csl-entry-wrapper' });
        e.parentElement.insertBefore(div, e);
        div.append(e);
      }

      if (e.dataset.citekey) {
        const citekey = e.dataset.citekey;
        if (!inTooltip) {
          e.setAttribute('aria-label', t('Click to jump to citation'));
          e.onClickEvent(() => {
            this.scrollToCitation(citekey, file).catch(console.error);
          });
          e.oncontextmenu = (evt) => {
            evt.preventDefault();
            new Menu()
              .addItem((item) =>
                item
                  .setTitle(t('Copy citekey'))
                  .setIcon('lucide-copy')
                  .onClick(() => copyTextToClipboard(`@${citekey}`))
              )
              .addItem((item) =>
                item
                  .setTitle(t('Copy reference'))
                  .setIcon('lucide-copy')
                  .onClick(() => copyElToClipboard(e))
              )
              .showAtMouseEvent(evt);
          };
        }

        // Derive the Zotero select link lazily (zero HTTP) so the button
        // shows even on a cold start before getZLinksForKeys ran — the
        // tooltip did this via getBibForCiteKey, the sidebar didn't.
        this.ensureZLink(citekey);
        const zLink = this.zCitekeyToLinks.get(citekey);
        const showPDFs = this.plugin.settings.showPdfLinks !== false;
        const zPDFLinks = showPDFs
          ? this.zCitekeyToPDFLinks.get(citekey)
          : undefined;
        const hasConflict = this.conflictKeys.has(citekey);

        // Use ZotLit's frontmatter-based note index when available; fall back
        // to filename-based detection for non-ZotLit setups.
        const litNote = getLitNoteForCitekey(citekey, file.path, app);
        // Show "Create literature note" whenever no note exists. Creation is
        // routed to ZotLit when the setting is on (see createLiteratureNote).
        const canCreateNote = !litNote;

        if (!litNote && !zLink && !zPDFLinks && !hasConflict && !canCreateNote) return;

        // Rebuild the button row every time: persisted HTML may carry stale
        // buttons (e.g. "Create literature note" when a note now exists, or
        // no Zotero link because the map was empty at render time).
        const wrapper = e.parentElement!;
        wrapper.findAll('.lc-entry-btns').forEach((b) => b.remove());

        wrapper.createDiv({ cls: 'lc-entry-btns' }, (div) => {
          if (hasConflict) {
            div.createDiv('clickable-icon lc-conflict-icon', (div) => {
              setIcon(div, 'lucide-alert-triangle');
              div.setAttr(
                'aria-label',
                t('This entry exists in both your .bib file and Zotero. Zotero data is shown.')
              );
            });
          }
          if (litNote) {
            div.createDiv('clickable-icon', (div) => {
              setIcon(div, 'sticky-note');
              div.setAttr('aria-label', t('Open literature note'));
              div.onClickEvent((evt) => {
                const newPane = Keymap.isModEvent(evt);
                app.workspace.openLinkText(litNote.linkText, file.path, newPane);
              });
            });
          } else if (canCreateNote) {
            div.createDiv('clickable-icon', (div) => {
              setIcon(div, 'lucide-file-plus');
              div.setAttr('aria-label', t('Create literature note'));
              div.onClickEvent(async () => {
                await this.createLiteratureNote(citekey, file);
              });
            });
          }
          if (zLink) {
            div.createDiv('clickable-icon', (div) => {
              setIcon(div, 'lucide-external-link');
              div.setAttr('aria-label', t('Open in Zotero'));
              div.onClickEvent(() => {
                activeWindow.open(zLink, '_blank');
              });
            });
          }
          if (zPDFLinks) {
            zPDFLinks.forEach((link) => {
              div.createDiv('clickable-icon', (div) => {
                setIcon(div, 'lucide-file-text');
                div.setAttr('aria-label', pathBasename(link));
                div.onClickEvent(() => {
                  activeWindow.open(`file://${encodeURI(link)}`, '_blank');
                });
              });
            });
          }
        });
      }
    });

    return parsed;
  }

  async scrollToCitation(citekey: string, sourceFile: TFile) {
    const escaped = citekey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const citekeyPattern = new RegExp(`@${escaped}\\b`);
    let targetView: MarkdownView | null = null;

    this.plugin.app.workspace.getLeavesOfType('markdown').forEach((leaf) => {
      const view = leaf.view as MarkdownView;
      if (view.file === sourceFile) {
        targetView = view;
      }
    });

    if (!targetView) {
      await this.plugin.app.workspace.openLinkText(sourceFile.path, '', false);
      targetView =
        this.plugin.app.workspace.getActiveViewOfType(MarkdownView) ?? null;
    }

    if (!targetView?.editor) return;

    const editor = targetView.editor;
    const offset = editor.getValue().search(citekeyPattern);
    if (offset < 0) return;

    const pos = editor.offsetToPos(offset);
    editor.setCursor(pos);
    editor.scrollIntoView({ from: pos, to: pos }, true);
    editor.focus();
  }

  async createLiteratureNote(citekey: string, sourceFile: TFile) {
    const entry = this.bibCache.get(citekey) as any;

    // Route to ZotLit when the user enabled it and ZotLit is available, so the
    // note is rendered with ZotLit's templates. ZotLit's note feature takes an
    // item identified by its indexedKey (the zotero-key field value).
    if (this.plugin.settings.createNotesWithZotLit !== false) {
      const zoteroItemKey: string | undefined = entry?._zoteroKey;
      const groupId: number | undefined = entry?.groupID;
      const indexedKey = zoteroItemKey
        ? groupId && groupId !== 1
          ? `${zoteroItemKey}g${groupId}`
          : zoteroItemKey
        : undefined;

      if (indexedKey) {
        const created = await createLitNoteViaZotLit(app, { indexedKey });
        if (created) {
          // The protocol handler (obsidian://zotlit/open) creates AND opens the
          // note itself — nothing more to do here.
          return;
        }
        // Fall through to the plugin template if ZotLit couldn't create it.
      }
    }

    const title = entry?.title ?? citekey;
    const year = entry?.issued?.['date-parts']?.[0]?.[0] ?? '';
    const authors: string[] = (entry?.author ?? [])
      .map((a: any) => [a.family, a.given].filter(Boolean).join(', ') || a.literal || '')
      .filter(Boolean);

    // Build the "zotero-key" value ZotLit needs to index this note.
    // Format: ITEMKEY for My Library (groupID 1), ITEMKEYgGROUPID for group libraries.
    // See ZotLit's getItemKeyGroupID and ZOTERO_KEY_FIELDNAME.
    const zoteroItemKey: string | undefined = entry?._zoteroKey;
    const groupId: number | undefined = entry?.groupID;
    let zoteroKeyField: string | null = null;
    if (zoteroItemKey) {
      zoteroKeyField =
        groupId && groupId !== 1
          ? `${zoteroItemKey}g${groupId}`
          : zoteroItemKey;
    }

    // Default to ZotLit's configured literature-note folder so notes land in
    // the same place whether created by ZotLit or by this fallback.
    const zotlitFolderRaw =
      (app as any).plugins?.plugins?.['zotlit']?.services?.settings?.current?.[
        'note.literature-folder'
      ] ?? (app as any).plugins?.plugins?.['zotlit']?.settings?.current?.[
        'note.literature-folder'
      ] ?? '';
    const zotlitFolder = zotlitFolderRaw.trim();
    const settingsFolder = (this.plugin.settings.literatureNoteFolder ?? '').trim();
    const folder = settingsFolder || zotlitFolder || '_2 Bibliographic notes';
    const filename = `@${citekey}.md`;
    const notePath = folder ? normalizePath(`${folder}/${filename}`) : filename;

    if (await app.vault.adapter.exists(notePath)) {
      await app.workspace.openLinkText(notePath, sourceFile.path, true);
      return;
    }

    const lines = [
      '---',
      `citekey: ${citekey}`,
      ...(zoteroKeyField ? [`zotero-key: ${zoteroKeyField}`] : []),
      `title: "${title.replace(/"/g, '\\"')}"`,
      `year: ${year}`,
      ...(authors.length
        ? [`authors:`, ...authors.map((a) => `  - "${a}"`)]
        : []),
      '---',
    ];

    const content = `${lines.join('\n')}\n\n# ${title}\n\n`;

    if (folder && !(await app.vault.adapter.exists(normalizePath(folder)))) {
      await app.vault.adapter.mkdir(normalizePath(folder));
    }

    await app.vault.create(notePath, content);
    await app.workspace.openLinkText(notePath, sourceFile.path, true);
  }

  /**
   * Scan literature notes (default: `_2 Bibliographic notes/`) and rename any
   * note whose `@filename` stem differs from its frontmatter `citekey:`. The
   * citekey in frontmatter is authoritative (it tracks citekey migrations);
   * the filename is what links use, so rename the file to `@<citekey>.md`.
   * Obsidian propagates `[[@old]]` → `[[@new]]` links on rename automatically.
   * Returns a list of {from, to} performed.
   */
  async syncLitNoteFilenames(folder = '_2 Bibliographic notes') {
    const results: { from: string; to: string }[] = [];
    const dir = app.vault.getAbstractFileByPath(folder);
    if (!(dir instanceof TFolder)) return results;
    const files = dir.children.filter(
      (f): f is TFile => f instanceof TFile && /^@.+\.md$/.test(f.name)
    );
    for (const file of files) {
      try {
        const content = await app.vault.read(file);
        const fm = app.metadataCache.getFileCache(file)?.frontmatter;
        const citekey = fm?.citekey;
        if (typeof citekey !== 'string' || !citekey) continue;
        const stem = file.basename.startsWith('@')
          ? file.basename.slice(1)
          : file.basename;
        if (stem === citekey) continue;
        const newPath = normalizePath(
          file.path.replace(/[^/]+$/, `@${citekey}.md`)
        );
        if (app.vault.getAbstractFileByPath(newPath)) continue; // target exists
        await app.vault.rename(file, newPath);
        results.push({ from: file.path, to: newPath });
      } catch (e) {
        console.warn('[lc] syncLitNoteFilenames: error on', file.path, e);
      }
    }
    return results;
  }

  /**
   * True when a file lives in a numbered content folder (`_1`, `_2`, …).
   * The citation index only tracks these folders — temporary/system dirs and
   * vault-root files aren't citation sources.
   */
  isIndexablePath(p: string): boolean {
    const first = p.split('/')[0];
    return /^_[0-9]/.test(first);
  }

  /** Parse ONE file and record its cited citekeys in the index. */
  private async indexFileCitekeys(file: TFile) {
    if (!this.isIndexablePath(file.path)) return;
    const keys = new Set<string>();
    try {
      const content = await app.vault.read(file);
      const segs = getCitationSegments(content, false, true);
      for (const g of segs) {
        for (const s of g) {
          if (s.type === 'key') keys.add(s.val);
        }
      }
    } catch {
      // unreadable — treat as no citations
    }
    this.citedKeysByFile.set(file.path, keys);
    this.citedKeysIndexDirty = true;
  }

  /** Build the citation index from scratch over the numbered content folders. */
  async buildCitedKeysIndex() {
    this.citedKeysByFile.clear();
    const files = app.vault.getMarkdownFiles().filter((f) =>
      this.isIndexablePath(f.path)
    );
    this.indexMdCount = files.length;
    for (const f of files) await this.indexFileCitekeys(f);
    this.citedKeysIndexDirty = true;
  }

  /** Update the index for a changed/created/renamed file. */
  async updateCitedKeysIndex(file: TFile) {
    if (!this.isIndexablePath(file.path)) return;
    await this.indexFileCitekeys(file);
  }

  /** Remove a deleted file from the index. */
  removeFromCitedKeysIndex(path: string) {
    if (this.citedKeysByFile.delete(path)) this.citedKeysIndexDirty = true;
  }

  /** All citekeys cited anywhere in the indexed folders. */
  getCitedKeys(): Set<string> {
    const out = new Set<string>();
    for (const keys of this.citedKeysByFile.values()) {
      for (const k of keys) out.add(k);
    }
    return out;
  }

  /** Serialize the index for persistence: {mdCount, files}. */
  serializeCitedKeysIndex(): { mdCount: number; files: Record<string, string[]> } {
    const files: Record<string, string[]> = {};
    for (const [p, keys] of this.citedKeysByFile) {
      if (keys.size) files[p] = [...keys];
    }
    return { mdCount: this.indexMdCount, files };
  }

  /** Restore a previously persisted index. */
  deserializeCitedKeysIndex(
    data: { mdCount?: number; files?: Record<string, string[]> } | null | undefined
  ) {
    this.citedKeysByFile.clear();
    if (data?.files) {
      for (const [p, keys] of Object.entries(data.files)) {
        this.citedKeysByFile.set(p, new Set(keys));
      }
    }
    this.indexMdCount = data?.mdCount ?? 0;
  }

  // ── persistent rendered-citation cache ────────────────────────────────────

  private renderedCachePath() {
    return normalizePath('.pandoc/rendered-citations.json');
  }

  /** Load the persistent rendered-citation cache from disk (once). */
  async loadRenderedCache() {
    if (this.renderedCacheLoaded) return;
    this.renderedCacheLoaded = true;
    try {
      const raw = await app.vault.adapter.read(this.renderedCachePath());
      const data = JSON.parse(raw);
      // Bump RENDER_CACHE_VERSION whenever rendering behaviour changes
      // (parser fixes, CSL changes, …): a mismatched persisted cache is
      // discarded so every note re-renders with the new code instead of
      // serving stale citations from before the fix.
      if (data?.version !== RENDER_CACHE_VERSION) return;
      if (data?.notes && typeof data.notes === 'object') {
        for (const [p, v] of Object.entries(data.notes)) {
          this.renderedCache.set(p, v as PersistedNoteCache);
        }
      }
    } catch {
      // no cache yet — first run populates it
    }
  }

  /** Persist the rendered-citation cache (debounced by caller). */
  async saveRenderedCache() {
    if (!this.renderedCacheDirty) return;
    const notes: Record<string, PersistedNoteCache> = {};
    for (const [p, v] of this.renderedCache) notes[p] = v;
    try {
      const dir = normalizePath('.pandoc');
      if (!(await app.vault.adapter.exists(dir))) {
        await app.vault.adapter.mkdir(dir);
      }
      await app.vault.adapter.write(
        this.renderedCachePath(),
        JSON.stringify({ version: RENDER_CACHE_VERSION, notes })
      );
      this.renderedCacheDirty = false;
    } catch (e) {
      console.warn('[lc] saveRenderedCache: error', e);
    }
  }

  /**
   * Compute a cache hash for a note: content + scoped settings (style/lang/
   * bibliography) + plugin CSL style/lang + a .bib source fingerprint.
   * Zotero entry changes are NOT included here — they are tracked per-key via
   * `_version` (see `entryVersionsMatch`), so editing one entry only
   * invalidates notes that cite it, not every note in the vault.
   */
  private noteCacheHash(
    file: TFile,
    content: string,
    settings: ScopedSettings | null
  ): string {
    const plugin = this.plugin.settings;
    const bibFp = this.bibSourceKeys.size
      ? `bib:${this.bibSourceKeys.size}`
      : '';
    return fastHash(
      [
        content,
        settings?.style ?? '',
        settings?.lang ?? '',
        settings?.bibliography?.join('|') ?? '',
        plugin.cslStyleURL ?? '',
        plugin.cslLang ?? '',
        bibFp,
        `e:${this.bibEpoch}`,
      ].join('\u0001')
    );
  }

  /** Current Zotero library version (0 when unknown / not configured). */
  private currentLibraryVersion(): number {
    const groups = this.plugin?.settings?.zoteroGroups;
    if (!groups?.length) return 0;
    return groups.reduce(
      (acc, g) => Math.max(acc, g.libraryVersion ?? 0),
      0
    );
  }

  /** True when every key in `persisted` still has the Zotero item version it
   *  had when the note was rendered. .bib-sourced keys (no `_version`) always
   *  match here — the .bib fingerprint in the content hash covers them.
   *
   *  If the whole-library version is unchanged since the render, no item can
   *  have changed → replay (covers caches written before per-key _version
   *  existed, where all per-key versions are missing). */
  private entryVersionsMatch(
    persisted: PersistedNoteCache,
    bibCache: Map<string, PartialCSLEntry>
  ): boolean {
    if (persisted.libraryVersion === this.currentLibraryVersion()) {
      return true;
    }
    for (const key of persisted.keys) {
      const prev = persisted.versions[key];
      const cur = bibCache.get(key)?._version;
      if (prev !== cur) return false;
    }
    return true;
  }

  /** Author names + issued year of an entry — the fields that drive citation
   *  disambiguation. If they're unchanged for a changed entry, no OTHER
   *  citation in the note can change either. */
  private authorYearFingerprint(entry?: PartialCSLEntry): string {
    if (!entry) return '';
    const names = (entry.author ?? [])
      .map((n) => `${n.family ?? ''} ${n.given ?? ''} ${n.literal ?? ''}`)
      .join('|');
    const year =
      ((entry as any).issued as any)?.['date-parts']?.[0]?.[0] ?? '';
    return fastHash(`${names}\u0001${year}`);
  }

  /** Keys whose Zotero version differs from the cached render, plus whether
   *  EVERY changed key kept its author+year. If author+year are all stable,
   *  the note's inline citations are provably identical (disambiguation
   *  depends only on author/year) and only the bibliography must re-render.
   *  When the whole-library version is unchanged, no keys changed at all. */
  private changedKeyStatus(
    persisted: PersistedNoteCache,
    bibCache: Map<string, PartialCSLEntry>
  ): { changed: string[]; allAuthorYearStable: boolean } {
    if (persisted.libraryVersion === this.currentLibraryVersion()) {
      return { changed: [], allAuthorYearStable: true };
    }
    const changed: string[] = [];
    let allStable = true;
    for (const key of persisted.keys) {
      const prev = persisted.versions[key];
      const cur = bibCache.get(key)?._version;
      if (prev !== cur) {
        changed.push(key);
        if (
          persisted.authorYearFp[key] !==
          this.authorYearFingerprint(bibCache.get(key))
        ) {
          allStable = false;
        }
      }
    }
    return { changed, allAuthorYearStable: allStable };
  }

  /** Build a FileCache from a persisted entry (no citeproc re-render).
   *  `source` may be omitted — it is only used to reuse a loaded CSL engine
   *  across renders; a falsy source simply forces a fresh engine load on the
   *  next real render. */
  private fileCacheFromPersisted(
    file: TFile,
    entry: PersistedNoteCache,
    source?: FileCache['source']
  ): FileCache {
    // Re-hydrate event handlers on the persisted bibliography: outerHTML
    // serializes structure but NOT JS handlers, so replayed sidebar/tooltip
    // entries were dead (click did nothing). prepBibHTML now skips re-wrapping
    // already-wrapped entries, re-attaches click/context handlers, and
    // rebuilds the button row from current state (lit note, Zotero link, PDFs).
    let bib: HTMLElement | null = null;
    if (entry.bibHtml) {
      bib = new DOMParser().parseFromString(entry.bibHtml, 'text/html').body
        .firstElementChild as HTMLElement;
      if (bib) {
        try {
          bib = this.prepBibHTML(bib, file);
        } catch {
          // if binding fails, keep the plain persisted HTML
        }
      }
    }
    return {
      keys: new Set(entry.keys),
      resolvedKeys: new Set(entry.resolvedKeys),
      unresolvedKeys: new Set(entry.unresolvedKeys),
      globalOnlyKeys: new Set(entry.globalOnlyKeys),
      bib,
      citations: entry.citations,
      citeBibMap: new Map(Object.entries(entry.citeBibMap ?? {})),
      settings: getScopedSettings(file),
      source,
    };
  }

  /** Store a FileCache into the persistent cache (marks dirty). */
  private persistFileCache(file: TFile, cache: FileCache, hash: string) {
    const versions: Record<string, number | undefined> = {};
    const authorYearFp: Record<string, string> = {};
    const srcBibCache = cache.source?.bibCache;
    for (const key of cache.keys) {
      const entry = srcBibCache?.get(key);
      versions[key] = entry?._version;
      authorYearFp[key] = this.authorYearFingerprint(entry);
    }
    this.renderedCache.set(file.path, {
      contentHash: hash,
      mtime: file.stat?.mtime ?? 0,
      libraryVersion: this.currentLibraryVersion(),
      keys: [...cache.keys],
      resolvedKeys: [...cache.resolvedKeys],
      unresolvedKeys: [...cache.unresolvedKeys],
      globalOnlyKeys: [...cache.globalOnlyKeys],
      versions,
      authorYearFp,
      bibHtml: cache.bib?.outerHTML ?? null,
      citations: cache.citations,
      citeBibMap: Object.fromEntries(cache.citeBibMap),
    });
    this.renderedCacheDirty = true;
    // Schedule a flush from here — NOT only from file events — so that merely
    // opening/rendering a note persists the cache even if no file is ever
    // modified. Without this, a restart right after reading notes finds an
    // empty on-disk cache and gets no speedup.
    this.scheduleRenderedCacheSave();
  }

  /** Debounced save of the rendered cache (2.5 s after the last write). */
  private scheduleRenderedCacheSave = debounce(() => {
    void this.saveRenderedCache();
  }, 2500);

  /**
   * Collect citekeys cited across the vault (or just one note) and create
   * literature notes for those without one. Uses ZotLit's BATCH import
   * (no tabs opened) when available; falls back to per-note protocol create.
   * Returns { created, missing }.
   * @param onProgress optional (done, total) callback for a progress display.
   */
  async createMissingLitNotes(
    opts: {
      file?: TFile | null;
      allVault?: boolean;
    } = {},
    onProgress?: (done: number, total: number) => void
  ) {
    const citekeys = new Set<string>();

    if (opts.file) {
      // Current note: index (or re-index) just this file.
      await this.indexFileCitekeys(opts.file);
      const keys = this.citedKeysByFile.get(opts.file.path);
      keys?.forEach((k) => citekeys.add(k));
    } else if (opts.allVault) {
      // Vault-wide: use the maintained index. Rebuild only when the index is
      // missing, or the number of _N markdown files changed since it was built
      // (files added/removed — the main way the index can drift outside the
      // in-session modify/create/delete events).
      const mdCount = app.vault
        .getMarkdownFiles()
        .filter((f) => this.isIndexablePath(f.path)).length;
      const stale =
        this.citedKeysByFile.size === 0 || this.indexMdCount !== mdCount;
      if (stale) await this.buildCitedKeysIndex();
      for (const k of this.getCitedKeys()) citekeys.add(k);
    } else {
      // default: active note
      const view = app.workspace.getActiveViewOfType(MarkdownView);
      if (view?.file) {
        await this.indexFileCitekeys(view.file);
        const keys = this.citedKeysByFile.get(view.file.path);
        keys?.forEach((k) => citekeys.add(k));
      }
    }

    const sourcePath = opts.file?.path ?? app.workspace.getActiveFile()?.path ?? '';
    const missing: string[] = [];
    for (const key of citekeys) {
      const exists = getLitNoteForCitekey(key, sourcePath, app);
      if (exists) continue;
      missing.push(key);
    }
    if (!missing.length) return { created: 0, missing, missingKeys: missing };

    // Build {indexedKey, itemID?, entry?} refs from the bib cache.
    const refs: { indexedKey: string; itemID?: number; entry?: any }[] = [];
    for (const key of missing) {
      const entry = this.bibCache.get(key) as any;
      const zoteroItemKey: string | undefined = entry?._zoteroKey;
      const groupId: number | undefined = entry?.groupID;
      if (!zoteroItemKey) continue;
      const indexedKey =
        groupId && groupId !== 1
          ? `${zoteroItemKey}g${groupId}`
          : zoteroItemKey;
      refs.push({ indexedKey, entry });
    }
    if (!refs.length) return { created: 0, missing: refs.map((r) => r.indexedKey), missingKeys: missing };

    // Prefer ZotLit's batch import (no tabs); fall back to per-note protocol.
    if (this.plugin.settings.createNotesWithZotLit !== false) {
      const accepted = await createLitNotesViaZotLitBulk(app, refs, onProgress);
      return { created: accepted, missing: refs.map((r) => r.indexedKey), missingKeys: missing };
    }

    // Fallback: create one at a time via the tooltip path (opens each note).
    let created = 0;
    const sourceFile =
      opts.file ??
      app.workspace.getActiveFile() ??
      app.vault.getMarkdownFiles()[0];
    const total = missing.length;
    for (const key of missing) {
      if (!sourceFile) break;
      await this.createLiteratureNote(key, sourceFile);
      created++;
      onProgress?.(created, total);
      await new Promise((r) => setTimeout(r, 250));
    }
    return { created, missing: refs.map((r) => r.indexedKey), missingKeys: missing };
  }

  dispatchResult(file: TFile, result: FileCache, hash?: string) {
    // If this exact content hash was already rendered, the current DOM shows
    // the cached citations already — re-dispatching would force Obsidian to
    // re-render the whole note for no change (very slow on long notes).
    if (hash && this.dispatchedHashes.get(file.path) === hash) {
      return;
    }
    if (hash) this.dispatchedHashes.set(file.path, hash);

    app.workspace.getLeavesOfType('markdown').forEach((l) => {
      const view = l.view as MarkdownView;
      if (view.file === file) {
        const previewMode = (view as any).previewMode;
        const renderer = previewMode?.renderer;
        if (renderer) {
          renderer.lastText = null;
          for (const section of renderer.sections) {
            if (
              !section.el.hasClass('mod-header') &&
              !section.el.hasClass('mod-footer')
            ) {
              section.rendered = false;
              section.el.empty();
            }
          }
          renderer.queueRender();
        } else if (typeof previewMode?.rerender === 'function') {
          previewMode.rerender(true);
        } else if (typeof (view as any).onMarkdownFold === 'function') {
          (view as any).onMarkdownFold();
        }

        const cm = (view.editor as any).cm as EditorView;
        if (cm.dispatch) {
          cm.dispatch({
            effects: [setCiteKeyCache.of(result)],
          });
          // Obsidian 1.13.x / CM: replace-widgets added when the citation
          // cache lands are not painted until a later transaction (cursor
          // move/scroll) forces a re-render — citations stay invisible.
          // This runs OUTSIDE the CM update cycle (async code, not inside a
          // plugin update()), so a follow-up dispatch is legal. Re-applying
          // the current selection forces CM to re-render the viewport and
          // draw the newly-added replace-widgets.
          const sel = cm.state.selection.main;
          cm.dispatch({
            selection: { anchor: sel.anchor, head: sel.head },
            effects: [],
          });
          cm.requestMeasure();
        }
      }
    });
  }

  private updateScopedWatchedBibPaths(file: TFile, settings: ScopedSettings | null) {
    const paths = new Set<string>();

    if (settings?.bibliography?.length) {
      for (const scopedBibPath of settings.bibliography) {
        if (!isAbsolutePath(scopedBibPath)) {
          paths.add(normalizePath(scopedBibPath));
        }
      }
    }

    if (paths.size) {
      this.scopedWatchedBibPaths.set(file.path, paths);
    } else {
      this.scopedWatchedBibPaths.delete(file.path);
    }

    this.rebuildWatchedBibPaths();
  }

  private rebuildWatchedBibPaths() {
    this.watchedBibPaths.clear();

    for (const path of this.globalWatchedBibPaths) {
      this.watchedBibPaths.add(path);
    }

    for (const paths of this.scopedWatchedBibPaths.values()) {
      for (const path of paths) {
        this.watchedBibPaths.add(path);
      }
    }
  }

  /** Synchronous first-paint validity check for a persisted entry:
   *  content unchanged (mtime within tolerance) + library unchanged. No file
   *  read, no engine. Old-format entries (no mtime/libraryVersion fields)
   *  fall back to the per-key version check — which matches when versions are
   *  absent on both sides (pre-_version caches). */
  private persistedEntryIsCurrent(
    file: TFile,
    entry: PersistedNoteCache
  ): boolean {
    // mtime changed → content edited since the cache was written. Allow a
    // small window: Obsidian's stat mtime can jitter by a millisecond or two
    // between reads (filesystem precision, watcher-touched writes), and a 1ms
    // difference must not invalidate an otherwise-valid cached render.
    if (entry.mtime && file.stat?.mtime) {
      const MTIME_TOLERANCE_MS = 5000;
      if (Math.abs(entry.mtime - file.stat.mtime) > MTIME_TOLERANCE_MS) {
        return false;
      }
    }
    // Library version changed → some item may have changed → re-render.
    if (entry.libraryVersion !== this.currentLibraryVersion()) {
      // New-format entries: strict. Old-format (no libraryVersion): fall
      // back to per-key versions, which all match when both sides lack
      // _version (pre-migration caches).
      if (entry.libraryVersion !== undefined) return false;
      for (const key of entry.keys) {
        const prev = entry.versions[key];
        const cur = this.bibCache.get(key)?._version;
        if (prev !== cur) return false;
      }
    }
    return true;
  }

  /** The render cache for a path, hydrating from the persisted cache on
   *  first access. The postprocessor + CM field call this synchronously on
   *  every render, so a note opens ALREADY formatted when its persisted
   *  entry is still current (mtime + library version unchanged) — no raw
   *  [@key] flash, no forced re-render. */
  getCacheForPath(filePath: string) {
    const file = app.vault.getAbstractFileByPath(filePath);
    if (file && file instanceof TFile && this.fileCache.has(file)) {
      const cache = this.fileCache.get(file);
      return cache;
    }

    if (file instanceof TFile) {
      const entry = this.renderedCache.get(file.path);
      if (entry && this.persistedEntryIsCurrent(file, entry)) {
        const result = this.fileCacheFromPersisted(file, entry);
        this.fileCache.set(file, result);
        this.hydratedPaths.add(file.path);
        this.dispatchedHashes.set(file.path, entry.contentHash);
        return result;
      }
    }

    return null;
  }

  getResolution(filePath: string, key: string) {
    const file = app.vault.getAbstractFileByPath(filePath);
    if (file && file instanceof TFile && this.fileCache.has(file)) {
      const cache = this.fileCache.get(file);
      return {
        isResolved: cache.resolvedKeys.has(key),
        isUnresolved: cache.unresolvedKeys.has(key),
      };
    }

    return {
      isResolved: false,
      isUnresolved: false,
    };
  }

  getCitationsForSection(filePath: string, lineStart: number, lineEnd: number) {
    const file = app.vault.getAbstractFileByPath(filePath);
    if (file && file instanceof TFile && this.fileCache.has(file)) {
      const cache = this.fileCache.get(file);
      const mCache = app.metadataCache.getCache(filePath);

      // Prefer an EXACT metadataCache section match (fast path). Obsidian's
      // getSectionInfo(el) and metadataCache.sections can disagree on the
      // section's end line (esp. inside callouts, lists, or adjacent
      // paragraphs), so fall back to the section whose line range CONTAINS
      // the requested one, then to any section overlapping lineStart.
      const exact = mCache.sections?.find(
        (s) =>
          s.position.start.line === lineStart && s.position.end.line === lineEnd
      );
      const containing = !exact
        ? mCache.sections?.find(
            (s) =>
              s.position.start.line <= lineStart && s.position.end.line >= lineEnd
          )
        : undefined;
      const overlapping = !exact && !containing
        ? mCache.sections?.find(
            (s) =>
              (s.position.start.line <= lineStart && s.position.end.line >= lineStart) ||
              (s.position.start.line <= lineEnd && s.position.end.line >= lineEnd)
          )
        : undefined;

      const section = exact ?? containing ?? overlapping;
      if (!section) return [];

      const startOffset = section.position.start.offset;
      const endOffset = section.position.end.offset;

      const cites = cache.citations.filter(
        (c) => c.from >= startOffset && c.to <= endOffset
      );
      return cites;
    }

    return [];
  }
}
