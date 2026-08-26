/* eslint-disable @typescript-eslint/ban-ts-comment */

jest.mock(
  'obsidian',
  () => ({
    FileSystemAdapter: { readLocalFile: jest.fn() },
    Keymap: { isModEvent: jest.fn(() => false) },
    MarkdownView: class MarkdownView {},
    Menu: class Menu {
      addItem(callback: any) {
        callback({
          setTitle() {
            return this;
          },
          setIcon() {
            return this;
          },
          onClick() {
            return this;
          },
        });
        return this;
      }
      showAtMouseEvent() {
        return this;
      }
    },
    Platform: { isDesktop: false },
    TFile: class TFile {},
    debounce: (fn: any) => fn,
    htmlToMarkdown: (html: string) => html.replace(/<[^>]+>/g, ''),
    normalizePath: (path: string) => path.replace(/\\/g, '/').replace(/\/+/g, '/'),
    requestUrl: jest.fn(),
    setIcon: jest.fn(),
  }),
  { virtual: true }
);

jest.mock('../bibtex', () => ({
  parseBibFile: jest.fn(),
}));

import { BibManager } from '../bibManager';
import { bibPathsToCSL } from '../helpers';
import { parseBibFile } from '../bibtex';
import { ZOTERO_TYPE_TO_CSL, zoteroItemToCSL } from '../zotero-csl';
import { SimpleLRU } from '../lru';
import { locales, styles } from 'src/parser/tests/styles';
import { PromiseCapability } from 'src/helpers';
import { PartialCSLEntry } from '../types';

const DEFAULT_STYLE = 'apa';

function makePlugin(overrides: Record<string, any> = {}) {
  const initPromise = new PromiseCapability<void>();
  initPromise.resolve();

  return {
    app: global.app,
    cacheDir: '.pandoc',
    initPromise,
    settings: {
      cslStyleURL: DEFAULT_STYLE,
      cslLang: 'en-US',
      renderLinkCitations: true,
      pullFromZotero: false,
      ...overrides,
    },
    registerEvent: jest.fn(),
    saveSettings: jest.fn(),
    processReferences: jest.fn(),
    view: { setMessage: jest.fn() },
  } as any;
}

function makeManager(entries: PartialCSLEntry[], settings = {}) {
  const plugin = makePlugin(settings);
  const manager = new BibManager(plugin);
  manager.initPromise.resolve();

  for (const entry of entries) {
    manager.bibCache.set(entry.id, entry);
  }
  manager.styleCache.set(DEFAULT_STYLE, styles[DEFAULT_STYLE]);
  manager.langCache.set('en-US', locales['en-US']);

  return { manager, plugin };
}

function makeFile(path = 'notes/test.md') {
  return { path, extension: 'md', basename: 'test', name: 'test.md' } as any;
}

beforeAll(() => {
  HTMLElement.prototype.findAll = function (selector: string) {
    return Array.from(this.querySelectorAll(selector)) as HTMLElement[];
  };
  HTMLElement.prototype.hasClass = function (className: string) {
    return this.classList.contains(className);
  };
  HTMLElement.prototype.onClickEvent = function (
    callback: (this: HTMLElement, ev: MouseEvent) => any,
    options?: boolean | AddEventListenerOptions
  ): void {
    this.addEventListener('click', callback, options);
  };
  HTMLElement.prototype.setAttr = function (name: string, value: string) {
    this.setAttribute(name, value);
  };
  HTMLElement.prototype.createDiv = function (options?: any, callback?: (el: HTMLDivElement) => void) {
    const el = (global as any).createDiv(options, callback);
    this.append(el);
    return el;
  };
});

beforeEach(() => {
  document.body.innerHTML = '';
  (parseBibFile as jest.Mock).mockReset();

  (global as any).app = {
    metadataCache: {
      getFileCache: jest.fn((): null => null),
      getFirstLinkpathDest: jest.fn((): null => null),
    },
    workspace: {
      getLeavesOfType: jest.fn((): any[] => []),
      openLinkText: jest.fn(),
    },
    vault: {
      on: jest.fn(() => ({ detach: jest.fn() })),
      adapter: {
        exists: jest.fn(async (): Promise<boolean> => false),
        mkdir: jest.fn(async (): Promise<void> => undefined),
        read: jest.fn(),
        write: jest.fn(),
      },
      create: jest.fn(),
    },
  };

  (global as any).createDiv = (options?: any, callback?: (el: HTMLDivElement) => void) => {
    const el = document.createElement('div');
    if (typeof options === 'string') {
      el.className = options;
    } else if (options) {
      if (options.cls) el.className = options.cls;
      if (options.text) el.textContent = options.text;
      if (options.attr) {
        for (const [key, value] of Object.entries(options.attr)) {
          el.setAttribute(key, String(value));
        }
      }
    }
    callback?.(el);
    return el;
  };
});

// ─── SimpleLRU ────────────────────────────────────────────────────────────────

describe('SimpleLRU', () => {
  it('stores and retrieves values', () => {
    const lru = new SimpleLRU<string, number>({ max: 3 });
    lru.set('a', 1);
    lru.set('b', 2);
    expect(lru.has('a')).toBe(true);
    expect(lru.get('a')).toBe(1);
    expect(lru.has('z')).toBe(false);
    expect(lru.get('z')).toBeUndefined();
  });

  it('evicts the least-recently-used entry when over max', () => {
    const evicted: string[] = [];
    const lru = new SimpleLRU<string, string>({
      max: 2,
      dispose: (v) => evicted.push(v),
    });

    lru.set('a', 'A');
    lru.set('b', 'B');
    lru.get('a'); // access 'a' to make 'b' the oldest
    lru.set('c', 'C'); // 'b' is oldest — should be evicted
    expect(lru.has('b')).toBe(false);
    expect(lru.has('a')).toBe(true);
    expect(lru.has('c')).toBe(true);
    expect(evicted).toEqual(['B']);
  });

  it('does not call dispose when overwriting an existing key', () => {
    const evicted: string[] = [];
    const lru = new SimpleLRU<string, string>({
      max: 2,
      dispose: (v) => evicted.push(v),
    });

    lru.set('a', 'A');
    lru.set('a', 'A2');
    expect(lru.get('a')).toBe('A2');
    expect(evicted).toEqual([]);
  });

  it('delete removes the entry', () => {
    const lru = new SimpleLRU<string, number>({ max: 5 });
    lru.set('x', 99);
    lru.delete('x');
    expect(lru.has('x')).toBe(false);
  });

  it('clear empties the cache', () => {
    const lru = new SimpleLRU<string, number>({ max: 5 });
    lru.set('a', 1);
    lru.set('b', 2);
    lru.clear();
    expect(lru.has('a')).toBe(false);
    expect(lru.has('b')).toBe(false);
  });
});

// ─── BibTeX multi-file loading ──────────────────────────────────────────────

describe('bibPathsToCSL()', () => {
  it('concatenates multiple BibTeX files before parsing so @string macros can cross file boundaries', async () => {
    (global.app.vault.adapter.exists as jest.Mock).mockResolvedValue(true);
    (global.app.vault.adapter.read as jest.Mock).mockImplementation(
      async (path: string) =>
        path === 'strings.bib'
          ? '@string{IEEE = "IEEE Transactions on Testing"}'
          : '@article{smith2020, journal = IEEE}'
    );
    (parseBibFile as jest.Mock).mockReturnValue([{ id: 'smith2020' }]);

    const result = await bibPathsToCSL(['strings.bib', 'refs.bib']);

    expect(result).toEqual([{ id: 'smith2020' }]);
    expect(parseBibFile).toHaveBeenCalledWith(
      '@string{IEEE = "IEEE Transactions on Testing"}\n\n@article{smith2020, journal = IEEE}',
      'strings.bib'
    );
  });
});

// ─── zoteroItemToCSL ─────────────────────────────────────────────────────────

describe('zoteroItemToCSL()', () => {
  const baseItem = (overrides: Record<string, any> = {}) => ({
    data: {
      citationKey: 'smith2020',
      itemType: 'journalArticle',
      title: 'A Test Article',
      creators: [{ creatorType: 'author', firstName: 'Jane', lastName: 'Smith' }],
      date: '2020-06-15',
      publicationTitle: 'Journal of Testing',
      volume: '12',
      issue: '3',
      pages: '100-110',
      DOI: '10.1234/test',
      ...overrides,
    },
  });

  it('returns null when citationKey is missing', () => {
    expect(zoteroItemToCSL({ data: { itemType: 'book' } }, 1)).toBeNull();
  });

  it('maps a journal article correctly', () => {
    const result = zoteroItemToCSL(baseItem(), 1);
    expect(result).not.toBeNull();
    expect(result!.id).toBe('smith2020');
    expect((result as any).type).toBe('article-journal');
    expect((result as any).title).toBe('A Test Article');
    expect((result as any)['container-title']).toBe('Journal of Testing');
    expect((result as any).DOI).toBe('10.1234/test');
    expect((result as any).author).toEqual([{ family: 'Smith', given: 'Jane' }]);
  });

  it('sets groupID on every entry', () => {
    const result = zoteroItemToCSL(baseItem(), 42);
    expect((result as any).groupID).toBe(42);
  });

  it('parses full date (YYYY-MM-DD)', () => {
    const result = zoteroItemToCSL(baseItem(), 1);
    expect((result as any).issued).toEqual({ 'date-parts': [[2020, 6, 15]] });
  });

  it('falls back to document type for unknown itemType', () => {
    const result = zoteroItemToCSL(baseItem({ itemType: 'unknownType' }), 1);
    expect((result as any).type).toBe('document');
  });

  it('maps every configured Zotero item type to its CSL type', () => {
    expect(Object.keys(ZOTERO_TYPE_TO_CSL)).toHaveLength(38);

    for (const [itemType, cslType] of Object.entries(ZOTERO_TYPE_TO_CSL)) {
      const result = zoteroItemToCSL(baseItem({ itemType }), 1);
      expect((result as any)?.type).toBe(cslType);
    }
  });

  it('maps editor creator type', () => {
    const item = baseItem({
      creators: [{ creatorType: 'editor', firstName: 'Bob', lastName: 'Jones' }],
    });
    const result = zoteroItemToCSL(item, 1);
    expect((result as any).editor).toEqual([{ family: 'Jones', given: 'Bob' }]);
    expect((result as any).author).toBeUndefined();
  });

  it('handles institutional authors (literal name)', () => {
    const item = baseItem({
      creators: [{ creatorType: 'author', name: 'ACME Corp' }],
    });
    const result = zoteroItemToCSL(item, 1);
    expect((result as any).author).toEqual([{ literal: 'ACME Corp' }]);
  });
});

// ─── CSL rendering pipeline ─────────────────────────────────────────────────

describe('BibManager CSL rendering pipeline', () => {
  const entries: PartialCSLEntry[] = [
    {
      id: 'smith2020',
      type: 'article-journal',
      title: 'A Test Article',
      author: [{ family: 'Smith', given: 'Jane' }],
      issued: { 'date-parts': [[2020]] },
      'container-title': 'Journal of Testing',
      volume: '12',
      issue: '3',
      page: '100-110',
    } as any,
    {
      id: 'doe2021',
      type: 'book',
      title: 'A Test Book',
      author: [{ family: 'Doe', given: 'John' }],
      issued: { 'date-parts': [[2021]] },
      publisher: 'Testing Press',
    } as any,
  ];

  it('builds a CSL engine from cached style, locale, and bibliography entries', async () => {
    const { manager } = makeManager(entries);

    await manager.buildGlobalEngine();

    expect(manager.engine).toBeTruthy();
    expect(manager.fuse).toBeTruthy();
  });

  it('renders a bibliography for resolved citekeys and tracks cache metadata', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile();

    await manager.buildGlobalEngine();
    const bib = await manager.getReferenceList(
      file,
      'Smith citation [@smith2020] and Doe citation [@doe2021].'
    );

    expect(bib).toBeInstanceOf(HTMLElement);
    expect(bib.querySelectorAll('.csl-entry')).toHaveLength(2);
    expect(bib.textContent).toContain('A Test Article');
    expect(bib.textContent).toContain('A Test Book');

    const cache = manager.fileCache.get(file)!;
    expect(cache.keys).toEqual(new Set(['smith2020', 'doe2021']));
    expect(cache.resolvedKeys).toEqual(new Set(['smith2020', 'doe2021']));
    expect(cache.unresolvedKeys.size).toBe(0);
    expect(cache.citations).toHaveLength(2);
    expect(cache.citeBibMap.get('smith2020')).toContain('A Test Article');
  });

  it('does not render unresolved citekeys but records them in the file cache', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile();

    await manager.buildGlobalEngine();
    const bib = await manager.getReferenceList(
      file,
      'Known [@smith2020] and unknown [@missing2024].'
    );

    expect(bib.querySelectorAll('.csl-entry')).toHaveLength(1);
    expect(bib.textContent).toContain('A Test Article');
    expect(bib.textContent).not.toContain('missing2024');

    const cache = manager.fileCache.get(file)!;
    expect(cache.resolvedKeys).toEqual(new Set(['smith2020']));
    expect(cache.unresolvedKeys).toEqual(new Set(['missing2024']));
  });

  it('persists a rendered note and replays it from disk without re-rendering', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/persist.md');
    const content = 'Smith citation [@smith2020] and Doe citation [@doe2021].';

    await manager.buildGlobalEngine();
    const bib = await manager.getReferenceList(file, content);
    expect(bib).toBeInstanceOf(HTMLElement);
    expect(bib.querySelectorAll('.csl-entry')).toHaveLength(2);

    // The note was written to the in-memory persistent cache.
    const persisted = (manager as any).renderedCache.get(file.path);
    expect(persisted).toBeTruthy();
    expect(persisted.contentHash).toMatch(/^[0-9a-f]{1,8}$/);
    expect(persisted.citations).toHaveLength(2);
    expect(persisted.bibHtml).toContain('A Test Article');

    // Simulate a restart: new manager, cache loaded from disk.
    const { manager: manager2 } = makeManager(entries);
    (manager2 as any).renderedCacheLoaded = true;
    (manager2 as any).renderedCache.set(file.path, persisted);
    await manager2.buildGlobalEngine();

    const bib2 = await manager2.getReferenceList(file, content);

    // Replayed from the persisted entry: same HTML, no engine source attached
    // (proves cite()/makeBibliography were skipped).
    expect(bib2).toBeInstanceOf(HTMLElement);
    expect(bib2.textContent).toContain('A Test Article');
    expect(bib2.textContent).toContain('A Test Book');
    expect((manager2.fileCache.get(file) as any).source).toBeUndefined();
    expect(manager2.fileCache.get(file)!.citations).toHaveLength(2);
    expect(manager2.fileCache.get(file)!.citeBibMap.get('smith2020')).toContain(
      'A Test Article'
    );
  });

  it('invalidates the persistent cache when note content changes', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/persist2.md');
    const content = 'Smith citation [@smith2020].';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);

    const persistedBefore = (manager as any).renderedCache.get(file.path);
    expect(persistedBefore).toBeTruthy();

    // New content → full re-render with a real engine.
    const bib = await manager.getReferenceList(file, 'Smith [@smith2020] again.');
    expect(bib).toBeInstanceOf(HTMLElement);
    expect((manager.fileCache.get(file) as any).source).toBeTruthy();
  });

  it('partially re-renders: reuses cached citations when a changed entry keeps author+year', async () => {
    const { manager } = makeManager(entries, {
      zoteroGroups: [{ id: 1, name: 'My Library', libraryVersion: 100 }],
    });
    const file = makeFile('notes/partial.md');
    const content = 'Smith [@smith2020] and Doe [@doe2021].';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);

    const persisted = (manager as any).renderedCache.get(file.path);
    expect(persisted).toBeTruthy();
    expect(persisted.libraryVersion).toBe(100);
    expect(persisted.authorYearFp['smith2020']).toMatch(/^[0-9a-f]{1,8}$/);

    // Simulate a Zotero edit that keeps author+year (e.g. title changed):
    // bump the library version + the entry's _version but leave author/year
    // identical.
    (manager.plugin.settings.zoteroGroups[0].libraryVersion = 101);
    const smith = {
      ...entries[0],
      _version: 99,
      title: 'A Retitled Article',
    } as any;
    manager.bibCache.set('smith2020', smith);
    manager.fileCache.delete(file);

    const bib2 = await manager.getReferenceList(file, content);
    expect(bib2).toBeInstanceOf(HTMLElement);
    const cache2 = manager.fileCache.get(file)!;
    // Citations reused from cache (engine attached, but cite() was skipped —
    // the citations array is byte-identical to the persisted one).
    expect(cache2.citations).toEqual(persisted.citations);
    expect(cache2.citations).toHaveLength(2);
    // Bibliography rebuilt with the new title.
    expect(cache2.citeBibMap.get('smith2020')).toContain('A Retitled Article');
    expect(cache2.citeBibMap.get('doe2021')).toContain('A Test Book');
    // Persisted cache updated with the new version.
    const persisted2 = (manager as any).renderedCache.get(file.path);
    expect(persisted2.versions['smith2020']).toBe(99);
  });

  it('fully re-renders when a changed entry has a different author', async () => {
    const { manager } = makeManager(entries, {
      zoteroGroups: [{ id: 1, name: 'My Library', libraryVersion: 100 }],
    });
    const file = makeFile('notes/partial-author.md');
    const content = 'Smith [@smith2020] and Doe [@doe2021].';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);

    // Change the AUTHOR of smith2020 (disambiguation could shift → full render).
    (manager.plugin.settings.zoteroGroups[0].libraryVersion = 101);
    const smith = {
      ...entries[0],
      _version: 99,
      author: [{ family: 'Jones', given: 'Jane' }],
    } as any;
    manager.bibCache.set('smith2020', smith);
    manager.fileCache.delete(file);

    await manager.getReferenceList(file, content);
    const cache = manager.fileCache.get(file)!;
    // Full re-render: fresh citations (different author name).
    expect(cache.citations[0].val).toContain('Jones');
    expect(cache.citations[0].val).not.toContain('Smith');
  });

  it('lazily hydrates getCacheForPath from the persisted cache (first paint)', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/hydrate.md');
    const content = 'Smith [@smith2020].';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);
    const persisted = (manager as any).renderedCache.get(file.path);
    expect(persisted).toBeTruthy();

    // Fresh manager simulating startup: persisted cache loaded, engine NOT
    // built yet (bibCache empty — library-version shortcut must carry it).
    const { manager: manager2 } = makeManager(entries);
    (manager2 as any).renderedCacheLoaded = true;
    (manager2 as any).renderedCache.set(file.path, persisted);
    (manager2 as any).fileCache.clear();

    // getAbstractFileByPath must return a TFile instance for the instanceof
    // check in getCacheForPath. stat.mtime must match the persisted entry.
    const tfile = Object.assign(new (require('obsidian').TFile)(), {
      path: file.path,
      extension: 'md',
      basename: 'hydrate',
      name: 'hydrate.md',
      stat: { mtime: persisted.mtime },
    });
    (global as any).app.vault.getAbstractFileByPath = jest.fn(() => tfile);

    // getCacheForPath hydrates synchronously — no async call needed.
    const hydrated = manager2.getCacheForPath(file.path)!;
    expect(hydrated).toBeTruthy();
    expect(hydrated.citations).toHaveLength(1);
    // Marked as already-dispatched so the later getReferenceList fast path
    // skips the forced re-render.
    expect((manager2 as any).dispatchedHashes.get(file.path)).toBe(
      persisted.contentHash
    );
  });

  it('rebuilt sidebar bibliography includes a Zotero button for every entry', async () => {
    const { manager } = makeManager(entries, {
      zoteroGroups: [{ id: 1, name: 'My Library', libraryVersion: 100 }],
    });
    const file = makeFile('notes/zotbtn.md');
    const content = 'Smith [@smith2020] and Doe [@doe2021].';

    // Both entries carry _zoteroKey like the real library cache.
    (manager.bibCache.get('smith2020') as any)._zoteroKey = 'ABC12345';
    (manager.bibCache.get('doe2021') as any)._zoteroKey = 'XYZ99999';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);
    const persisted = (manager as any).renderedCache.get(file.path);
    expect(persisted).toBeTruthy();

    // Simulate cold start: persisted cache loaded, engine NOT built yet
    // (bibCache empty → the first hydration pass builds buttons without
    // Zotero links), then the engine finishes and the fast path re-renders.
    const { manager: manager2 } = makeManager(entries, {
      zoteroGroups: [{ id: 1, name: 'My Library', libraryVersion: 100 }],
    });
    (manager2 as any).renderedCacheLoaded = true;
    (manager2 as any).renderedCache.set(file.path, persisted);
    (manager2 as any).fileCache.clear();
    (manager2 as any).zCitekeyToLinks.clear(); // empty maps on cold start
    // Cold start: Zotero library not loaded yet → bibCache empty. (makeManager
    // shares the entries array by reference with `manager`, so clear it.)
    (manager2 as any).bibCache.clear();

    // First pass (Zotero engine not loaded): hydrate → no Zotero buttons.
    const tfile = Object.assign(new (require('obsidian').TFile)(), {
      path: file.path,
      extension: 'md',
      basename: 'zotbtn',
      name: 'zotbtn.md',
      stat: { mtime: persisted.mtime },
    });
    (global as any).app.vault.getAbstractFileByPath = jest.fn(() => tfile);
    const zotBtnIn = (el: HTMLElement) =>
      Array.from(el.querySelectorAll('.clickable-icon')).filter((b) =>
        b.getAttribute('aria-label')?.includes('Open in Zotero')
      );
    const hydrated = manager2.getCacheForPath(file.path)!;
    expect(zotBtnIn(hydrated.bib!)).toHaveLength(0);

    // Second pass: engine loads (bibCache populated) → fast path re-renders
    // → Zotero button for EVERY entry.
    (manager2 as any).bibCache.set('smith2020', {
      ...entries[0],
      _zoteroKey: 'ABC12345',
    });
    (manager2 as any).bibCache.set('doe2021', {
      ...entries[1],
      _zoteroKey: 'XYZ99999',
    });
    await manager2.buildGlobalEngine();
    const bib2 = await manager2.getReferenceList(file, content);
    expect(bib2).toBeInstanceOf(HTMLElement);
    expect(zotBtnIn(bib2!)).toHaveLength(2);
  });

  it('does not hydrate getCacheForPath when the file mtime changed', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/hydrate-stale.md');
    const content = 'Smith [@smith2020].';

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, content);
    const persisted = (manager as any).renderedCache.get(file.path);
    // Simulate a persisted entry with a real mtime, far outside the 5s
    // tolerance (Obsidian's stat mtime jitters by ms; a real edit is seconds+).
    persisted.mtime = 60000;

    const tfile = Object.assign(new (require('obsidian').TFile)(), {
      path: file.path,
      extension: 'md',
      basename: 'hydrate-stale',
      name: 'hydrate-stale.md',
      stat: { mtime: 2000 }, // edited long after render
    });
    (global as any).app.vault.getAbstractFileByPath = jest.fn(() => tfile);

    expect(manager.getCacheForPath(file.path)).toBeNull();
  });

  it('derives zotero://select URLs from _zoteroKey without HTTP', async () => {
    const { manager } = makeManager(entries);
    // Attach _zoteroKey to entries as the library fetch would have.
    (manager.bibCache.get('smith2020') as any)._zoteroKey = 'ABC12345';
    (manager.bibCache.get('doe2021') as any)._zoteroKey = 'XYZ99999';

    const getItems = jest.fn(async (): Promise<any[] | null> => null);
    (manager as any).getZoteroAdapter = () => ({ getItemsForCiteKeys: getItems });

    await manager.getZLinksForKeys(new Set(['smith2020', 'doe2021']));

    expect(manager.zCitekeyToLinks.get('smith2020')).toBe(
      'zotero://select/library/items/ABC12345'
    );
    expect(manager.zCitekeyToLinks.get('doe2021')).toBe(
      'zotero://select/library/items/XYZ99999'
    );
    // No network needed for the select URLs (group 1 → library path).
    expect(getItems).not.toHaveBeenCalled();
  });

  it('persists and restores the Zotero link maps', async () => {
    const { manager } = makeManager(entries);
    manager.zCitekeyToLinks.set('smith2020', 'zotero://select/library/items/ABC12345');
    manager.zCitekeyToPDFLinks.set('smith2020', ['pdf://x.pdf']);

    const writes: string[] = [];
    (global as any).app.vault.adapter.write.mockImplementation(
      async (_p: string, data: string) => {
        writes.push(data);
      }
    );
    await manager.saveZLinks();
    expect(writes.length).toBeGreaterThanOrEqual(1);
    const parsed = JSON.parse(writes[writes.length - 1]);
    expect(parsed.links['smith2020']).toBe('zotero://select/library/items/ABC12345');
    expect(parsed.pdfs['smith2020']).toEqual(['pdf://x.pdf']);

    (global as any).app.vault.adapter.read.mockResolvedValue(writes[0]);
    const { manager: manager2 } = makeManager(entries);
    await manager2.loadZLinks();
    expect(manager2.zCitekeyToLinks.get('smith2020')).toBe(
      'zotero://select/library/items/ABC12345'
    );
    expect(manager2.zCitekeyToPDFLinks.get('smith2020')).toEqual(['pdf://x.pdf']);
  });

  it('skips dispatchResult when the same content hash was already rendered', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/skipdispatch.md');
    const content = 'Smith [@smith2020].';

    const queueRender = jest.fn();
    const renderer = {
      lastText: 'x',
      sections: [] as any[],
      queueRender,
    };
    (global as any).app.workspace.getLeavesOfType = jest.fn(() => [
      { view: { file, previewMode: { renderer }, editor: { cm: { dispatch: jest.fn(), state: { selection: { main: { anchor: 0, head: 0 } } }, requestMeasure: jest.fn() } } } },
    ]);

    await manager.buildGlobalEngine();
    // First render: full path → dispatchResult → forced re-render.
    await manager.getReferenceList(file, content);
    expect(queueRender).toHaveBeenCalledTimes(1);

    // Simulate the hydration case: same content already dispatched.
    const persisted = (manager as any).renderedCache.get(file.path);
    (manager as any).dispatchedHashes.set(file.path, persisted.contentHash);
    (manager as any).fileCache.delete(file);

    // Second call: fast path → dispatchResult early-returns → no re-render.
    await manager.getReferenceList(file, content);
    expect(queueRender).toHaveBeenCalledTimes(1);
  });

  it('loads and saves the rendered cache through the vault adapter', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile('notes/io.md');

    await manager.buildGlobalEngine();
    await manager.getReferenceList(file, 'Smith [@smith2020].');

    // Rendering schedules an automatic flush (debounce mocked as identity),
    // so the cache is on disk without any explicit save call.
    const writes: string[] = [];
    (global as any).app.vault.adapter.write.mockImplementation(
      async (_path: string, data: string) => {
        writes.push(data);
      }
    );
    (manager as any).renderedCacheDirty = true;
    await (manager as any).saveRenderedCache();

    expect(writes.length).toBeGreaterThanOrEqual(1);
    const parsed = JSON.parse(writes[writes.length - 1]);
    expect(parsed.version).toBe(2);
    expect(parsed.notes['notes/io.md']).toBeTruthy();

    // Round-trip through loadRenderedCache into a fresh map.
    (global as any).app.vault.adapter.read.mockResolvedValue(writes[0]);
    const { manager: manager2 } = makeManager(entries);
    await (manager2 as any).loadRenderedCache();
    expect((manager2 as any).renderedCache.has('notes/io.md')).toBe(true);
  });

  it('returns null and caches citekeys when no CSL engine is available', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile();

    const bib = await manager.getReferenceList(file, 'Known [@smith2020].');

    expect(bib).toBeNull();

    const cache = manager.fileCache.get(file)!;
    expect(cache.keys).toEqual(new Set(['smith2020']));
    expect(cache.bib).toBeNull();
    expect(cache.citations).toEqual([]);
  });

  it('can abort before caching stale reference results', async () => {
    const { manager } = makeManager(entries);
    const file = makeFile();

    const bib = await manager.getReferenceList(
      file,
      'Known [@smith2020].',
      () => false
    );

    expect(bib).toBeUndefined();
    expect(manager.fileCache.has(file)).toBe(false);
  });

  it('scrolls from a sidebar entry to the first exact citekey occurrence', async () => {
    const { manager, plugin } = makeManager(entries);
    const file = makeFile();
    const pos = { line: 0, ch: 14 };
    const editor = {
      getValue: jest.fn(() => 'Other [@smith2020a]\nTarget [@smith2020].'),
      offsetToPos: jest.fn(() => pos),
      setCursor: jest.fn(),
      scrollIntoView: jest.fn(),
      focus: jest.fn(),
    };
    const view = { file, editor };
    plugin.app.workspace.getLeavesOfType = jest.fn(() => [{ view }]);

    await manager.scrollToCitation('smith2020', file);

    expect(editor.offsetToPos).toHaveBeenCalledWith(28);
    expect(editor.setCursor).toHaveBeenCalledWith(pos);
    expect(editor.scrollIntoView).toHaveBeenCalledWith(
      { from: pos, to: pos },
      true
    );
    expect(editor.focus).toHaveBeenCalled();
  });

  it('replaces stale scoped bibliography watch paths when frontmatter changes', () => {
    const { manager } = makeManager(entries);
    const file = makeFile();

    (manager as any).globalWatchedBibPaths.add('global.bib');
    (manager as any).updateScopedWatchedBibPaths(file, {
      bibliography: ['refs/old.bib'],
    });

    expect((manager as any).watchedBibPaths).toEqual(
      new Set(['global.bib', 'refs/old.bib'])
    );

    (manager as any).updateScopedWatchedBibPaths(file, {
      bibliography: ['refs/new.bib'],
    });

    expect((manager as any).watchedBibPaths).toEqual(
      new Set(['global.bib', 'refs/new.bib'])
    );
  });
});
