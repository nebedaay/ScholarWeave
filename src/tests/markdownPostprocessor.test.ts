/**
 * Regression tests for the reading-mode postprocessor DOM handling.
 *
 * Obsidian reading mode renders [[@key|alias]] as
 *   <a class="internal-link" data-href="@key">alias</a>
 * and the postprocessor must REPLACE that whole anchor with the rendered
 * citation span (span > a.internal-link), not nest the span inside the
 * leftover anchor. Nesting forced Obsidian's blue link color onto the
 * citation via the inner anchor's `color: inherit` (cases 1–7 showed
 * bluegreen while container spans 8–9 showed black).
 */
jest.mock('obsidian', () => ({ parseYaml: (s: string) => JSON.parse(s) }), {
  virtual: true,
});

// Obsidian extends HTMLElement with helpers that jsdom lacks.
beforeAll(() => {
  (HTMLElement.prototype as any).hasClass = function (cls: string) {
    return this.classList.contains(cls);
  };
  (HTMLElement.prototype as any).addClass = function (cls: string) {
    this.classList.add(cls);
  };
  (globalThis as any).createFragment = () => {
    const frag = document.createDocumentFragment() as any;
    frag.appendText = (text: string) => {
      frag.appendChild(document.createTextNode(text));
    };
    frag.createSpan = (opts: any) => {
      const span = document.createElement('span');
      if (opts?.cls) span.className = opts.cls;
      if (opts?.text != null) span.textContent = opts.text;
      if (opts?.attr) {
        for (const [k, v] of Object.entries(opts.attr)) span.setAttribute(k, v as string);
      }
      frag.appendChild(span);
      return span;
    };
    return frag;
  };
});

jest.mock('../zotlit', () => ({
  getLitNoteForCitekey: jest.fn((): undefined => undefined),
}));

import { processCiteKeys } from '../markdownPostprocessor';

function makePlugin(overrides: Record<string, unknown> = {}): any {
  const settings = {
    renderLinkCitations: true,
    renderCitationsAsLinks: true,
    renderCitationsReadingMode: true,
    formatLinkAliases: true,
    ...overrides,
  };
  return {
    settings,
    app: {},
    tooltipManager: { bindPreviewTooltipHandler: jest.fn() },
    bibManager: {
      getCacheForPath: jest.fn((): any => ({})),
      getCitationsForSection: jest.fn((): any[] => []),
      getResolution: jest.fn((): undefined => undefined),
    },
  } as any;
}

/**
 * sectionCites entry that matches the alias citation [[@key|alias]].
 * getCitationSegments('[alias]', false, true, 'key') produces:
 *   [bracket, prefix/at/key/suffix, bracket] — the rendered citation must
 * carry the same val/type sequence so the walker finds it.
 */
function renderedFor(
  content: string,
  citekey: string,
  segments: any[]
): any {
  return {
    data: segments,
    citations: [{ id: citekey }],
    from: 0,
    to: content.length,
    val: '(Author 2021)',
  };
}

describe('reading-mode postprocessor anchor handling', () => {
  it('replaces the whole Obsidian anchor with the citation span (no nesting)', () => {
    // Obsidian reading-mode DOM for: text [[@key|see also @@, 10]] more text
    const p = document.createElement('p');
    p.innerHTML =
      'text <a class="internal-link" data-href="@key" href="@key">see also @@, 10</a> more';
    // Replace the inner anchor text with a single text node the way Obsidian
    // produces it: <a>see also @@, 10</a>
    const a = p.querySelector('a')!;
    a.textContent = 'see also @@, 10';

    const citekey = 'key';
    const plugin = makePlugin();
    // [[@key|see also @@, 10]] with linkCiteKey 'key' expands @@ to @key:
    // segments = [bracket, prefix 'see also ', at '@', key 'key', suffix ', 10', bracket]
    const segments = [
      { type: 'bracket', val: '[' },
      { type: 'prefix', val: 'see also ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'key' },
      { type: 'locatorSuffix', val: ', ' },
      { type: 'locator', val: '10' },
      { type: 'locatorLabel', val: 'page' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      renderedFor('[see also @@, 10]', citekey, segments),
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    // The citation span must be a DIRECT child of the paragraph, and there
    // must be no leftover Obsidian <a> wrapping it.
    const span = p.querySelector('span.pandoc-citation');
    expect(span).not.toBeNull();
    expect(span!.parentElement).toBe(p);
    expect(span!.parentElement!.closest('a')).toBeNull();
    expect(span!.className).toContain('is-link');

    // And the span must contain its own <a class="internal-link"> for nav.
    const inner = span!.querySelector('a.internal-link');
    expect(inner).not.toBeNull();
  });

  it('keeps non-anchor citations untouched in structure (plain bracket cites)', () => {
    const p = document.createElement('p');
    p.innerHTML = 'text [@key] more';
    const citekey = 'key';
    const plugin = makePlugin();
    const segments = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'key' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      renderedFor('[@key]', citekey, segments),
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    const span = p.querySelector('span.pandoc-citation');
    expect(span).not.toBeNull();
    expect(span!.parentElement).toBe(p);
  });

  it('leaves the anchor in place when renderCitationsAsLinks is off', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '<a class="internal-link" data-href="@key" href="@key">see also @@, 10</a>';
    const a = p.querySelector('a')!;
    a.textContent = 'see also @@, 10';

    const citekey = 'key';
    const plugin = makePlugin({ renderCitationsAsLinks: false });
    const segments = [
      { type: 'bracket', val: '[' },
      { type: 'prefix', val: 'see also ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'key' },
      { type: 'locatorSuffix', val: ', ' },
      { type: 'locator', val: '10' },
      { type: 'locatorLabel', val: 'page' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      renderedFor('[see also @@, 10]', citekey, segments),
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    const span = p.querySelector('span.pandoc-citation');
    expect(span).not.toBeNull();
    // No is-link, and the outer Obsidian anchor survives (kept as the link).
    expect(span!.className).not.toContain('is-link');
    expect(p.querySelector('a.internal-link')).not.toBeNull();
  });

  it('merges a ⟦…⟧ container into ONE citation span (no leftover ⟦⟧, no corruption)', () => {
    // Reading-mode DOM for: ⟦[[@a]]; [[@b]]⟧ (plain wikilinks, no aliases).
    // Regression: plain [[@key]] previously fell through to the base parser
    // with segments from index 1, so the container pre-pass could not match
    // the merged container in sectionCites — the container stayed as raw
    // ⟦…⟧ text and the second link corrupted to `[@@key4]`.
    const p = document.createElement('p');
    p.innerHTML =
      'text ⟦<a class="internal-link" data-href="@a" href="@a">@a</a>; ' +
      '<a class="internal-link" data-href="@b" href="@b">@b</a>⟧ more';
    // The pre-pass skips text nodes where !isConnected — in real Obsidian the
    // element is attached to the preview, so attach it here too.
    document.body.appendChild(p);

    const plugin = makePlugin();
    // sectionCites entry matching the MERGED container [@a; @b]
    const containerSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'a' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'b' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      {
        data: containerSegs,
        citations: [{ id: 'a' }, { id: 'b' }],
        from: 0,
        to: 0,
        val: '(A; B)',
      },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    // The container must be a SINGLE citation span, directly in the paragraph.
    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].parentElement).toBe(p);
    // No leftover ⟦ ⟧ or raw anchors.
    expect(p.textContent).not.toContain('⟦');
    expect(p.textContent).not.toContain('⟧');
    expect(p.querySelectorAll('a.internal-link').length).toBeLessThanOrEqual(1);
    // The span carries both citekeys.
    expect(spans[0].getAttribute('data-citekey')).toBe('a|b');
    // The walker must not have corrupted anything into @@… patterns.
    expect(p.textContent).not.toContain('@@');
  });

  it('merges an outer-bracket container [ [[@a]]; [[@b]] ] into ONE span (no leftover brackets)', () => {
    // Reading-mode DOM for: [ [[@a]]; [[@b]] ] (plain wikilinks, no aliases).
    // Obsidian renders: text "[ ", <a>@a</a>, text "; ", <a>@b</a>, text " ]".
    const p = document.createElement('p');
    p.innerHTML =
      'text [ <a class="internal-link" data-href="@a" href="@a">@a</a>; ' +
      '<a class="internal-link" data-href="@b" href="@b">@b</a> ] more';
    document.body.appendChild(p);

    const plugin = makePlugin();
    // sectionCites entry matching the MERGED container [@a; @b]
    const containerSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'a' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'b' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      {
        data: containerSegs,
        citations: [{ id: 'a' }, { id: 'b' }],
        from: 0,
        to: 0,
        val: '(A; B)',
      },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    // ONE citation span, directly in the paragraph; no leftover [ ] brackets.
    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].parentElement).toBe(p);
    expect(p.textContent).not.toContain('[');
    expect(p.textContent).not.toContain(']');
    expect(p.textContent).not.toContain('@@');
    // Trailing text after the closing ']' is preserved.
    expect(p.textContent.trim()).toContain('more');
    expect(spans[0].getAttribute('data-citekey')).toBe('a|b');
  });

  it('leaves bracket prose without 2+ citation links alone', () => {
    // A plain bracket like "[note]" must NOT be treated as a container.
    const p = document.createElement('p');
    p.innerHTML = 'text [note] more';
    document.body.appendChild(p);

    const plugin = makePlugin();
    plugin.bibManager.getCitationsForSection.mockReturnValue([]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    expect(p.textContent).toBe('text [note] more');
  });
});

describe('reading-mode postprocessor inside callouts', () => {
  // Obsidian renders callouts as:
  //   <div class="callout" data-callout="definition">
  //     <div class="callout-content"><blockquote><p>… <a>@@, p. 1</a></p></blockquote></div>
  //   </div>
  // The postprocessor is invoked once for the block. This test verifies the
  // anchor inside a callout gets replaced by the citation span exactly like
  // an anchor in a plain paragraph.
  it('replaces a linked citation inside a callout block', () => {
    const root = document.createElement('div');
    root.className = 'callout';
    root.setAttribute('data-callout', 'definition');
    root.innerHTML = `
      <div class="callout-title"><div class="callout-icon"></div>
        <div class="callout-title-inner">Sufism</div></div>
      <div class="callout-content"><blockquote>
        <p>“the ascetic-mystical stream in Islam” <a class="internal-link" data-href="@knyshSufismNew2017" href="@knyshSufismNew2017">@@, p. 1</a></p>
      </blockquote></div>`;
    document.body.appendChild(root);

    const citekey = 'knyshSufismNew2017';
    const plugin = makePlugin();
    const segments = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: citekey },
      { type: 'locatorSuffix', val: ', ' },
      { type: 'locatorLabel', val: 'p.' },
      { type: 'locatorSuffix', val: ' ' },
      { type: 'locator', val: '1' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      {
        data: segments,
        citations: [{ id: citekey }],
        from: 0,
        to: 0,
        val: '(Knysh 2017, 1)',
      },
    ]);

    processCiteKeys(plugin)(root, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 0, lineEnd: 1 }),
    } as any);

    const span = root.querySelector('span.pandoc-citation');
    expect(span).not.toBeNull();
    expect(span!.textContent).toContain('Knysh');
    expect(span!.className).toContain('is-link');
    // No leftover Obsidian anchor nesting.
    expect(root.querySelector('a.internal-link')).not.toBeNull(); // the span's own inner link
  });
});

describe('reading-mode postprocessor with callout sectionInfo', () => {
  // Obsidian's getSectionInfo "may return null in many circumstances".
  // Callouts in LIVE PREVIEW render via a nested markdown render, so the
  // post-processor IS called for their content but getSectionInfo() returns
  // null (upstream bug report obsidian#104289). The postprocessor must NOT
  // bail: it falls back to the whole-file cache, exactly like footnotes.
  it('renders citations inside callouts even when getSectionInfo returns null', () => {
    const root = document.createElement('div');
    root.className = 'callout';
    root.setAttribute('data-callout', 'definition');
    root.innerHTML = `
      <div class="callout-title"><div class="callout-icon"></div>
        <div class="callout-title-inner">Sufism</div></div>
      <div class="callout-content"><blockquote>
        <p>“the ascetic-mystical stream in Islam” <a class="internal-link" data-href="@knyshSufismNew2017" href="@knyshSufismNew2017">@@, p. 1</a></p>
      </blockquote></div>`;
    document.body.appendChild(root);

    const citekey = 'knyshSufismNew2017';
    const plugin = makePlugin();
    const segments = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: citekey },
      { type: 'locatorSuffix', val: ', ' },
      { type: 'locatorLabel', val: 'p.' },
      { type: 'locatorSuffix', val: ' ' },
      { type: 'locator', val: '1' },
      { type: 'bracket', val: ']' },
    ];
    // Callout fallback uses the whole-file cache, NOT section-scoped cites.
    plugin.bibManager.getCacheForPath.mockReturnValue({
      citations: [
        {
          data: segments,
          citations: [{ id: citekey }],
          from: 0,
          to: 0,
          val: '(Knysh 2017, 1)',
        },
      ],
    });

    // getSectionInfo returns null — as Obsidian does for callouts in live
    // preview. The citation must still be rendered.
    processCiteKeys(plugin)(root, {
      sourcePath: 'test.md',
      getSectionInfo: () => null as never,
    } as any);

    const span = root.querySelector('span.pandoc-citation');
    expect(span).not.toBeNull();
    expect(span!.textContent).toContain('Knysh');
  });
});

describe('multi-work container inside a callout', () => {
  it('merges a ⟦…⟧ container inside a callout when getSectionInfo is null', () => {
    const root = document.createElement('div');
    root.className = 'callout';
    root.setAttribute('data-callout', 'note');
    root.innerHTML = `
      <div class="callout-content"><blockquote>
        <p>⟦<a class="internal-link" data-href="@a" href="@a">@a</a>; ` +
      `<a class="internal-link" data-href="@b" href="@b">@b</a>⟧</p>
      </blockquote></div>`;
    document.body.appendChild(root);

    const plugin = makePlugin();
    const containerSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'a' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'b' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCacheForPath.mockReturnValue({
      citations: [
        {
          data: containerSegs,
          citations: [{ id: 'a' }, { id: 'b' }],
          from: 0,
          to: 0,
          val: '(A; B)',
        },
      ],
    });

    processCiteKeys(plugin)(root, {
      sourcePath: 'test.md',
      getSectionInfo: () => null as never,
    } as any);

    const spans = root.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].getAttribute('data-citekey')).toBe('a|b');
    expect(root.textContent).not.toContain('⟦');
  });

  it('merges an outer-bracket container inside a callout with no stray ]', () => {
    // Obsidian renders [ [[@a]]; [[@b]] ] in a callout body as:
    // text "[ ", <a>@a</a>, text "; ", <a>@b</a>, text " ]"
    const root = document.createElement('div');
    root.className = 'callout';
    root.setAttribute('data-callout', 'note');
    root.innerHTML = `
      <div class="callout-content"><blockquote>
        <p>[ <a class="internal-link" data-href="@a" href="@a">@a</a>; ` +
      `<a class="internal-link" data-href="@b" href="@b">@b</a> ]</p>
      </blockquote></div>`;
    document.body.appendChild(root);

    const plugin = makePlugin();
    const containerSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'a' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'b' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCacheForPath.mockReturnValue({
      citations: [
        {
          data: containerSegs,
          citations: [{ id: 'a' }, { id: 'b' }],
          from: 0,
          to: 0,
          val: '(A; B)',
        },
      ],
    });

    processCiteKeys(plugin)(root, {
      sourcePath: 'test.md',
      getSectionInfo: () => null as never,
    } as any);

    const spans = root.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].getAttribute('data-citekey')).toBe('a|b');
    // No leftover outer brackets — especially no stray ']' at the end.
    expect(root.textContent).not.toContain('[');
    expect(root.textContent).not.toContain(']');
  });
});

describe('reading-mode section lookup fallback', () => {
  // Obsidian's getSectionInfo(el) and metadataCache.sections can disagree on
  // the section's line range (especially inside callouts, lists, or adjacent
  // paragraphs). If getCitationsForSection returns [] for that reason, the
  // postprocessor must fall back to the whole-file cache instead of
  // degrading to raw per-part formatting.
  it('falls back to the whole-file cache when the section lookup is empty', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '<a class="internal-link" data-href="@abouelfadlReasoningGod2014" href="@abouelfadlReasoningGod2014">@abouelfadlReasoningGod2014</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    // Section lookup fails (line mismatch) → getCitationsForSection returns [].
    plugin.bibManager.getCitationsForSection.mockReturnValue([]);
    // Whole-file cache has the merged container.
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCacheForPath.mockReturnValue({
      citations: [
        {
          data: mergedSegs,
          citations: [{ id: 'a' }, { id: 'b' }],
          from: 0,
          to: 0,
          val: '(Abd al-Wahid 2021; Abou El Fadl 2014)',
        },
      ],
    });

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toContain('Abd al-Wahid 2021');
    // No leftover raw brackets.
    expect(p.textContent).not.toContain('[');
    expect(p.textContent).not.toContain(']');
  });
});

describe('container pre-pass with incomplete section lookup', () => {
  // Regression: getCitationsForSection can return a NON-empty subset that is
  // missing the merged container group (e.g. standalone [[@a]] entries from
  // neighbouring lines present, but the merged [@a; @b] for THIS line
  // absent, because getSectionInfo/metadataCache line ranges disagree).
  // The pre-pass must fall back to the whole-file cache and still merge.
  it('merges container when section lookup is missing the merged group', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '<a class="internal-link" data-href="@abouelfadlReasoningGod2014" href="@abouelfadlReasoningGod2014">@abouelfadlReasoningGod2014</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    // Section lookup returns ONLY the standalone first-link entry.
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      {
        data: [
          { type: 'bracket', val: '[' },
          { type: 'at', val: '@' },
          { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
          { type: 'bracket', val: ']' },
        ],
        citations: [{ id: 'abdalwahidDecolonisingDunkirk2021' }],
        from: 0,
        to: 0,
        val: '(Abd al-Wahid 2021)',
      },
    ]);
    // Whole-file cache has the MERGED container.
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCacheForPath.mockReturnValue({
      citations: [
        {
          data: mergedSegs,
          citations: [{ id: 'a' }, { id: 'b' }],
          from: 0,
          to: 0,
          val: '(Abd al-Wahid 2021; Abou El Fadl 2014)',
        },
      ],
    });

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    // ONE merged span; no raw brackets; no standalone first-link span.
    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toContain('Abou El Fadl 2014');
    expect(p.textContent).not.toContain('[');
    expect(p.textContent).not.toContain(']');
  });
});

describe('container pre-pass with whitespace-laden anchors (list items)', () => {
  // Obsidian's reading-mode list-item DOM wraps wikilink anchors with stray
  // newlines/whitespace in textContent (e.g. "<a>\n\n@key</a>"). The pre-pass
  // must trim anchor text so plain [[@key]] links are detected and containers
  // still merge (regression: strict regex found 0 links → merge skipped).
  it('merges container when anchor text has leading/trailing whitespace', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">\n\n@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '<a class="internal-link" data-href="@abouelfadlReasoningGod2014" href="@abouelfadlReasoningGod2014">\n\n@abouelfadlReasoningGod2014</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      { data: mergedSegs, citations: [{ id: 'a' }, { id: 'b' }], from: 0, to: 0, val: '(A; B)' },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toBe('(A; B)');
    expect(p.textContent).not.toContain('[');
    expect(p.textContent).not.toContain(']');
  });
});

describe('container pre-pass with label brackets ([…])', () => {
  // The label "([…])" contains a bare '[' that the bracket-finder picks up as
  // a false container candidate. The advance-to-next-'[' logic must skip it
  // without leaking a stray '[' into the kept preText (regression: the
  // merged citation appeared as "): [(A; B)" with an extra '[').
  it('does not leak the container opening bracket when label has ([…])', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '8. Multi-work container ([…]): [ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '<a class="internal-link" data-href="@abouelfadlReasoningGod2014" href="@abouelfadlReasoningGod2014">@abouelfadlReasoningGod2014</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      { data: mergedSegs, citations: [{ id: 'a' }, { id: 'b' }], from: 0, to: 0, val: '(A; B)' },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    // The label's own "([…])" brackets are legitimate; the ONLY stray
    // container '[' before the citation must be gone.
    expect(p.textContent).toBe('8. Multi-work container ([…]): (A; B)');
  });
});

describe('reading-mode mixed container (wikilink + plain citation)', () => {
  // Reading-mode DOM for "[ [[@a]]; [@b] ]": text "[ ", <a>@a</a>, text "; [@b] ]".
  // The plain [@b] is a TEXT NODE (Obsidian renders [@citekey] literally).
  // The pre-pass must merge both members into ONE [@a; @b] citation.
  it('merges a mixed container with a plain [@key] member', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '[@abouelfadlReasoningGod2014] ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      { data: mergedSegs, citations: [{ id: 'a' }, { id: 'b' }], from: 0, to: 0, val: '(A; B)' },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toBe('(A; B)');
    expect(p.textContent).not.toContain('[');
    expect(p.textContent).not.toContain(']');
  });
});







describe('container pre-pass tolerant walk', () => {
  // Obsidian may insert unrelated elements (spans, breaks) between container
  // members or give an anchor a non-@citekey href. The pre-pass must SKIP
  // these instead of bailing, so the container still merges.
  it('skips unexpected elements between members and still merges', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>' +
      '<br>; ' +
      '<a class="internal-link" data-href="@abouelfadlReasoningGod2014" href="@abouelfadlReasoningGod2014">@abouelfadlReasoningGod2014</a>' +
      '<span class="x">;</span> ' +
      '<a class="internal-link" data-href="@hooksWeReal2004" href="@hooksWeReal2004">@hooksWeReal2004</a>; ' +
      '<a class="internal-link" data-href="@haenniMondainesSpiritualites2002" href="@haenniMondainesSpiritualites2002">@haenniMondainesSpiritualites2002</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abouelfadlReasoningGod2014' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'hooksWeReal2004' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'haenniMondainesSpiritualites2002' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      { data: mergedSegs, citations: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }], from: 0, to: 0, val: '(A; B; C; D)' },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toBe('(A; B; C; D)');
  });

  it('skips an anchor without a citekey href without killing the merge', () => {
    const p = document.createElement('p');
    p.innerHTML =
      '[ <a class="internal-link" data-href="@abdalwahidDecolonisingDunkirk2021" href="@abdalwahidDecolonisingDunkirk2021">@abdalwahidDecolonisingDunkirk2021</a>; ' +
      '<a class="internal-link" data-href="Some Other Note" href="Some Other Note">@abouelfadlReasoningGod2014</a>; ' +
      '<a class="internal-link" data-href="@hooksWeReal2004" href="@hooksWeReal2004">@hooksWeReal2004</a> ]';
    document.body.appendChild(p);

    const plugin = makePlugin();
    const mergedSegs = [
      { type: 'bracket', val: '[' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'abdalwahidDecolonisingDunkirk2021' },
      { type: 'separator', val: ';' },
      { type: 'prefix', val: ' ' },
      { type: 'at', val: '@' },
      { type: 'key', val: 'hooksWeReal2004' },
      { type: 'bracket', val: ']' },
    ];
    plugin.bibManager.getCitationsForSection.mockReturnValue([
      { data: mergedSegs, citations: [{ id: 'a' }, { id: 'c' }], from: 0, to: 0, val: '(A; C)' },
    ]);

    processCiteKeys(plugin)(p, {
      sourcePath: 'test.md',
      getSectionInfo: () => ({ lineStart: 26, lineEnd: 26 }),
    } as any);

    const spans = p.querySelectorAll('span.pandoc-citation');
    expect(spans.length).toBe(1);
    expect(spans[0].textContent).toBe('(A; C)');
  });
});
