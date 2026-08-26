import { syntaxTree } from '@codemirror/language';
import { tokenClassNodeProp } from '@codemirror/language';
import { RangeSetBuilder, StateEffect, StateField } from '@codemirror/state';
import {
  Decoration,
  DecorationSet,
  EditorView,
  ViewPlugin,
  ViewUpdate,
  WidgetType,
} from '@codemirror/view';
import { Tree } from '@lezer/common';
import {
  Keymap,
  editorInfoField,
  editorLivePreviewField,
} from 'obsidian';

import {
  RenderedCitation,
  Segment,
  SegmentType,
  getCitationSegments,
} from './parser/parser';
import { BibManager, FileCache } from './bib/bibManager';
import equal from 'fast-deep-equal';
import { TooltipManager } from './tooltip';
import { getLitNoteForCitekey } from './zotlit';

const ignoreListRegEx = /code|math|templater|hashtag/;

const citeMark = (
  citekey: string,
  sourceFile: string | undefined,
  isResolved: boolean,
  isUnresolved: boolean,
  isGlobalOnly: boolean,
  noteIndex?: string
) => {
  const cls = ['cm-pandoc-citation', 'pandoc-citation'];

  if (isGlobalOnly) cls.push('is-global-only');
  else if (isResolved) cls.push('is-resolved');
  else if (isUnresolved) cls.push('is-unresolved');

  const attr: Record<string, string> = {
    'data-citekey': citekey,
    'data-source': sourceFile || '',
  };

  if (noteIndex) attr.noteIndex = noteIndex;

  return Decoration.mark({
    class: cls.join(' '),
    attributes: attr,
  });
};

const citeMarkFormatting = (type: string) => {
  return Decoration.mark({
    class: `cm-pandoc-citation-formatting ${type}`,
  });
};

const citeMarkExtra = (type: string) => {
  return Decoration.mark({
    class: `cm-pandoc-citation-extra ${type}`,
  });
};

export function editorTooltipHandler(manager: TooltipManager) {
  return EditorView.domEventHandlers(manager.getEditorTooltipHandler());
}

class CiteWidget extends WidgetType {
  cite: RenderedCitation;
  sourcePath?: string;
  linkText?: string;
  hasLitNote: boolean;
  isWikilink: boolean;
  memberStates: { key: string; isWikilink: boolean; hasLitNote: boolean }[];

  constructor(
    cite: RenderedCitation,
    sourcePath?: string,
    linkText?: string,
    hasLitNote = false,
    isWikilink = false,
    memberStates: { key: string; isWikilink: boolean; hasLitNote: boolean }[] = []
  ) {
    super();
    this.cite = cite;
    this.sourcePath = sourcePath;
    this.linkText = linkText;
    this.hasLitNote = hasLitNote;
    this.isWikilink = isWikilink;
    this.memberStates = memberStates;
  }

  // Obsidian 1.13.x / CM: when eq() compares equal, RangeSet.compare treats
  // the point decoration as unchanged and CM skips rebuilding its DOM — so a
  // citation widget added when the cache lands stays invisible until a later
  // transaction (cursor move/scroll) forces a re-render. Returning false
  // forces CM to always treat the widget as changed and (re)create its DOM on
  // every recompute. The mkDeco memo guards against needless recomputes.
  eq(): boolean {
    return false;
  }

  toDOM() {
    const attr: Record<string, string> = {
      'data-citekey': this.cite.citations.map((c) => c.id).join('|'),
      'data-source': this.sourcePath,
    };

    if (this.cite.note) {
      attr['data-note-index'] = this.cite.noteIndex.toString();
    }

    return createSpan(
      {
        cls: 'pandoc-citation is-resolved',
        attr,
      },
      (span) => {
        if (this.linkText) {
          span.addClass('is-link');
          // Use mousedown instead of click: in live preview CM synchronously
          // removes the widget from the DOM on mousedown (cursor moves inside),
          // so the click event never reaches our handler. Mousedown fires first.
          span.addEventListener('mousedown', (evt) => {
            if (evt.button !== 0) return;
            const newPane = Keymap.isModEvent(evt);
            activeWindow.setTimeout(() => {
              app.workspace.openLinkText(
                this.linkText,
                this.sourcePath,
                newPane
              );
            }, 0);
          });
        }
        // "is-wikilink": the citation is a [[@key]] wikilink (vs a plain
        // [@citekey] bracket). "has-lit-note": a real literature note exists
        // for the citekey (same lookup the tooltip uses for view/create).
        // Together they drive the three-state styling:
        //   linked     = is-wikilink && has-lit-note
        //   unlinked   = !is-wikilink
        //   unimported = is-wikilink && !has-lit-note
        if (this.isWikilink) span.addClass('is-wikilink');
        if (this.hasLitNote) span.addClass('has-lit-note');

        if (/</.test(this.cite.val)) {
          const parsed = new DOMParser().parseFromString(
            this.cite.val,
            'text/html'
          );
          span.append(...Array.from(parsed.body.childNodes));
        } else if (
          this.memberStates.length > 1 &&
          this.memberStates.length === this.cite.citations.length
        ) {
          // Multi-work container: split the rendered string per member so each
          // work carries its own is-wikilink / has-lit-note classes (and thus
          // its own underline color). Citeproc joins members with "; " inside
          // the surrounding parentheses; split on "; " only when the part count
          // matches the member count (fall back to a single span otherwise).
          const parts = this.cite.val.split(/;\s+/);
          if (parts.length === this.memberStates.length) {
            parts.forEach((part, i) => {
              const m = this.memberStates[i];
              const cls = ['pandoc-citation-member'];
              if (m.isWikilink) cls.push('is-wikilink');
              if (m.hasLitNote) cls.push('has-lit-note');
              const ms = document.createElement('span');
              ms.className = cls.join(' ');
              ms.textContent = part;
              span.appendChild(ms);
              // Re-insert the "; " separator after every member but the last.
              if (i < parts.length - 1) {
                span.appendChild(document.createTextNode('; '));
              }
            });
          } else {
            span.setText(this.cite.val);
          }
        } else {
          span.setText(this.cite.val);
        }
      }
    );
  }

  ignoreEvent(): boolean {
    return false;
  }
}

const citeDeco = (
  cite: RenderedCitation,
  sourcePath?: string,
  linkText?: string,
  hasLitNote = false,
  isWikilink = false,
  memberStates: { key: string; isWikilink: boolean; hasLitNote: boolean }[] = []
) =>
  Decoration.replace({
    widget: new CiteWidget(
      cite,
      sourcePath,
      linkText,
      hasLitNote,
      isWikilink,
      memberStates
    ),
  });

function onlyValType(segs: Segment[]) {
  return segs.map((s) => ({ type: s.type, val: s.val }));
}

function getCitationSignature(view: EditorView) {
  const {
    plugin: { settings },
  } = view.state.field(bibManagerField);

  const segments = getCitationSegments(
    view.state.doc.toString(),
    !settings.renderLinkCitations,
    settings.formatLinkAliases
  );

  return JSON.stringify(segments.map(onlyValType));
}

export const citeKeyPlugin = ViewPlugin.fromClass(
  class {
    view: EditorView;
    citationSignature: string;
    constructor(view: EditorView) {
      this.view = view;
      this.citationSignature = getCitationSignature(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged) {
        const nextSignature = getCitationSignature(update.view);
        if (nextSignature !== this.citationSignature) {
          this.citationSignature = nextSignature;
          update.view.state.field(bibManagerField).plugin.processReferences();
        }
      }
    }
    mkDeco(): DecorationSet {
      const view = this.view;
      const {
        plugin: { settings },
      } = view.state.field(bibManagerField);

      const obsView = view.state.field(editorInfoField);
      const citekeyCache = view.state.field(citeKeyCacheField);
      const isLivePreview =
        settings.renderCitations && view.state.field(editorLivePreviewField);

      const b = new RangeSetBuilder<Decoration>();

      // Don't get the syntax tree until we have to
      let tree: Tree;

      const matched = new Set<RenderedCitation>();

      for (const { from, to } of view.visibleRanges) {
        const range = view.state.sliceDoc(from, to);
        const segments = getCitationSegments(
          range,
          !settings.renderLinkCitations,
          settings.formatLinkAliases
        );

        for (const match of segments) {
          if (!tree) tree = syntaxTree(view.state);
          const rendered = citekeyCache?.citations.find(
            (c) =>
              !matched.has(c) &&
              equal(onlyValType(c?.data || []), onlyValType(match))
          );

          if (rendered) {
            matched.add(rendered);
          }

          if (isLivePreview) {
            if (rendered) {
              const start = from + match[0].from;
              const end = from + match[match.length - 1].to;

              let linkText: string;
              let hasLitNote = false;
              let isWikilink = false;

              // Per-member state (one entry per key segment, in citation
              // order): whether that member is a [[@key]] wikilink and whether
              // a literature note exists for it. Lets the widget render a
              // multi-work container with per-member styling, so a glance at
              // "[ (A); (B); (C) ]" shows which works are linked / unlinked /
              // unimported.
              const memberStates: {
                key: string;
                isWikilink: boolean;
                hasLitNote: boolean;
              }[] = [];

              // A citation is a "wikilink" when its syntax is [[@key]] rather
              // than a plain [@citekey] bracket. The syntax tree marks wikilink
              // ranges with the hmd-internal-link token. For single citations
              // the CENTER of the range lands inside the wikilink; for
              // multi-work containers ([ [[@a]]; [[@b]] ]) the center falls
              // between the links, so probe every key segment instead.
              let firstWikilinkTarget: string | undefined;

              // Determine which members are [[@key]] wikilinks by scanning the
              // actual document text of the citation range. (We cannot probe
              // the syntax tree at each key segment: the alias-expansion
              // position map collapses ALL container member positions onto the
              // container's opening '[' — verified — so resolveInner lands on a
              // bare bracket, never on hmd-internal-link.)
              const rangeText = view.state.sliceDoc(start, end);
              const wikilinkKeys = new Set<string>();
              // A citation wikilink is [[@key]] or [[@key|alias]]. A target
              // like "@key - transcription" (space after the key — the vault's
              // convention for derived files) is NOT a citation link, so the
              // key must be immediately followed by | or ].
              const wlRe = /\[\[@([^|\]\s]+)(?=[|\]])/g;
              let wm: RegExpExecArray | null;
              while ((wm = wlRe.exec(rangeText))) {
                wikilinkKeys.add(wm[1]);
                if (!firstWikilinkTarget) {
                  const full = wm[0];
                  const target = full.match(/^\[\[([^|\]]+)/)?.[1];
                  firstWikilinkTarget = target ?? full;
                }
              }

              for (const seg of match) {
                if (seg.type !== SegmentType.key) continue;
                const segIsLink = wikilinkKeys.has(seg.val);
                const resolved = getLitNoteForCitekey(
                  seg.val,
                  obsView?.file?.path ?? '',
                  app
                );
                memberStates.push({
                  key: seg.val,
                  isWikilink: segIsLink,
                  hasLitNote: !!resolved,
                });
                if (segIsLink) isWikilink = true;
                if (resolved) hasLitNote = true;
              }
              if (firstWikilinkTarget) {
                linkText = firstWikilinkTarget;
              } else if (settings.renderCitationsAsLinks) {
                // Plain bracket citation: navigate to the first key's note
                // when it resolves, else the @citekey fallback.
                const first = memberStates[0];
                if (first) {
                  linkText = first.hasLitNote ? first.key : '@' + first.key;
                }
              }

              if (
                view.state.selection.ranges.every((r) => {
                  return (
                    !(start >= r.from && end <= r.to) &&
                    !(
                      (r.from >= start && r.from <= end) ||
                      (r.to >= start && r.to <= end)
                    )
                  );
                })
              ) {
                b.add(
                  start,
                  end,
                  citeDeco(
                    rendered,
                    obsView?.file.path,
                    linkText,
                    hasLitNote,
                    isWikilink,
                    memberStates
                  )
                );
                continue;
              }
            }
          }

          for (let i = 0, len = match.length; i < len; i++) {
            const part = match[i];
            const next = match[i + 1];
            const start = from + part.from;
            const end = from + part.to;

            const nodeProps = tree
              .resolveInner(start, 1)
              .type.prop(tokenClassNodeProp);

            if (nodeProps && ignoreListRegEx.test(nodeProps)) {
              break;
            }

            switch (part.type) {
              case SegmentType.key: {
                const isUnresolved =
                  !nodeProps?.includes('link') &&
                  citekeyCache?.unresolvedKeys.has(part.val);
                const isResolved = citekeyCache?.resolvedKeys.has(part.val);
                const isGlobalOnly = citekeyCache?.globalOnlyKeys?.has(part.val) ?? false;

                b.add(
                  start,
                  end,
                  citeMark(
                    part.val,
                    obsView?.file.path,
                    isResolved,
                    isUnresolved,
                    isGlobalOnly,
                    rendered?.note ? rendered.noteIndex.toString() : undefined
                  )
                );
                continue;
              }
              case SegmentType.at: {
                const isUnresolved =
                  !!next &&
                  !nodeProps?.includes('link') &&
                  citekeyCache?.unresolvedKeys.has(next.val);
                const isResolved =
                  !!next && citekeyCache?.resolvedKeys.has(next.val);
                const isGlobalOnly =
                  !!next && (citekeyCache?.globalOnlyKeys?.has(next.val) ?? false);

                const classes: string[] = [part.type];

                if (isGlobalOnly) classes.push('is-global-only');
                else if (isUnresolved) classes.push('is-unresolved');
                else if (isResolved) classes.push('is-resolved');

                b.add(start, end, citeMarkFormatting(classes.join(' ')));
                continue;
              }
              case SegmentType.curlyBracket:
              case SegmentType.bracket:
                b.add(start, end, citeMarkFormatting(part.type));
                continue;
              case SegmentType.separator:
              case SegmentType.suppressor:
              case SegmentType.prefix:
              case SegmentType.suffix:
              case SegmentType.locator:
              case SegmentType.locatorLabel:
              case SegmentType.locatorSuffix:
                b.add(start, end, citeMarkExtra(part.type));
                continue;
            }
          }
        }
      }

      return b.finish();
    }
  },
  {
    // Compute decorations on demand in the facet itself, rather than caching
    // them in the plugin and returning the cached field.
    //
    // Obsidian 1.13.x / CM: the view pipeline reads the decorations facet
    // (ViewState.update → updateDeco → d(this.view)) BEFORE it runs plugin
    // update() methods (updatePlugins). With the old approach (setting
    // this.decorations inside update()), the facet read the PREVIOUS set on
    // the transaction that landed the citation cache — so the replace-widgets
    // were added one render late and CM skipped the DOM update entirely
    // (changedRanges was empty). Widgets only appeared after a subsequent
    // transaction (e.g. a cursor move) forced a re-render.
    //
    // Computing here means the facet always reflects the CURRENT state
    // (doc, cache field, selection) at the moment CM renders.
    decorations: (v) => v.mkDeco(),
  }
);

export const setCiteKeyCache = StateEffect.define<FileCache>();
export const citeKeyCacheField = StateField.define<FileCache>({
  create(state) {
    const obsView = state.field(editorInfoField);
    const bibManager = state.field(bibManagerField);

    // Lazy-hydrate from the persisted render cache so live preview paints
    // with formatted citations on the FIRST frame (no raw [@key] flash).
    // getCacheForPath is synchronous (mtime + library version check) and
    // reuses the fast replay when valid.
    if (obsView?.file && bibManager) {
      return bibManager.getCacheForPath(obsView.file.path);
    }

    return null;
  },
  update(state, tr) {
    for (const e of tr.effects) {
      if (e.is(setCiteKeyCache)) {
        state = e.value;
      }
    }

    return state;
  },
});

export const bibManagerField = StateField.define<BibManager>({
  create() {
    return null;
  },
  update(state) {
    return state;
  },
});
