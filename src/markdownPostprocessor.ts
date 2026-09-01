import { MarkdownPostProcessorContext } from 'obsidian';

import ReferenceList from './main';
import {
  Segment,
  SegmentType,
  RenderedCitation,
  expandAlias,
  getCitationSegments,
  mergeContainerExpression,
} from './parser/parser';
import equal from 'fast-deep-equal';
import { getLitNoteForCitekey } from './zotlit';

function getCiteClass(isResolved: boolean, isUnresolved: boolean) {
  const cls = ['pandoc-citation'];
  if (isResolved) cls.push('is-resolved');
  if (isUnresolved) cls.push('is-unresolved');

  return cls.join(' ');
}

function onlyValType(segs: Segment[]) {
  return segs.map((s) => ({ type: s.type, val: s.val }));
}

/**
 * Extract the citekey from a reading-mode wikilink anchor. Obsidian renders
 * [[@key|alias]] as <a data-href="@key" …>alias</a>; the href may include a
 * folder path and/or a .md extension (e.g. "Literature/@smith1992.md").
 * Returns the bare citekey ("smith1992") or undefined when the target is not
 * an @citekey note.
 *
 * A link whose target is "@key (space and other text)" — e.g. the vault's
 * convention for derived files like [[@key - transcription]] or
 * [[@key - English translation]] — is NOT a citation link: the space after
 * the key means the filename is a derivative, not the literature note. Those
 * links must stay native wikilinks, so this returns undefined for them.
 */
function getLinkCiteKey(a: HTMLElement): string | undefined {
  const href =
    a.getAttribute('data-href') || a.getAttribute('href') || '';
  const base = href.replace(/\\/g, '/').split('/').pop() ?? '';
  const stem = base.replace(/\.md$/i, '');
  if (!stem.startsWith('@')) return undefined;
  const citekey = stem.slice(1);
  // A space (or other non-citekey char) after the key means the target is a
  // derived filename (@key - transcription), not the literature note.
  if (!/^[\w:.-]+$/.test(citekey)) return undefined;
  return citekey;
}

// Obsidian renders footnotes in a <section data-footnotes> element.
// The post-processor is called for that element but getSectionInfo returns
// null because footnotes have no direct line correspondence in the source.
// Detect this case so we can still process citations inside footnotes.
function isFootnoteSection(el: HTMLElement): boolean {
  return !!(
    el.dataset?.footnotes !== undefined ||
    el.closest('[data-footnotes]') ||
    el.hasClass('footnotes')
  );
}

// Obsidian renders callouts (> [!type] …) in Live Preview via a NESTED
// markdown render: the callout body is a separate render context, so the
// post-processor IS called for it but getSectionInfo() returns null (no line
// correspondence — confirmed upstream bug report, obsidian#104289). In
// reading mode getSectionInfo returns the whole-callout range, which is fine.
// Detect callout content so we fall back to the full-file cache instead of
// bailing out (same pattern as footnotes).
function isCalloutSection(el: HTMLElement): boolean {
  return (
    el.hasClass('callout') ||
    !!el.closest('.callout') ||
    !!el.closest('blockquote.callout') ||
    (el.parentElement?.hasClass('callout-content') ?? false)
  );
}

/**
 * Build the reading-mode DOM for a rendered citation. THE single builder used
 * by both the text walker (single citations) and the container pre-pass
 * (⟦…⟧ multi-work runs), so both produce identical markup:
 *
 *   <span class="pandoc-citation is-resolved [is-link]">
 *     rendered text
 *     [<a class="internal-link">…</a>]   when renderCitationsAsLinks
 *
 * Callers must NOT wrap the result in their own <a> — the span is the unit.
 */
function buildCitationSpan(
  plugin: ReferenceList,
  rendered: RenderedCitation,
  ctx: MarkdownPostProcessorContext,
  sourceText?: Node
): HTMLSpanElement {
  const attr: Record<string, string> = {
    'data-citekey': rendered.citations.map((c) => c.id).join('|'),
    'data-source': ctx.sourcePath,
  };
  if (rendered.note) {
    attr['data-note-index'] = rendered.noteIndex.toString();
  }

  const span = document.createElement('span');
  span.className = getCiteClass(true, false);
  for (const [k, v] of Object.entries(attr)) span.setAttribute(k, v);

  // When "render citations in reading mode" is off, fall back to the raw
  // source text the citation came from (walker passes the text node).
  if (plugin.settings.renderCitationsReadingMode || !sourceText) {
    if (/</.test(rendered.val)) {
      const parsed = new DOMParser().parseFromString(
        rendered.val,
        'text/html'
      );
      span.append(...Array.from(parsed.body.childNodes));
    } else {
      span.textContent = rendered.val;
    }
  } else {
    span.appendChild(sourceText.cloneNode());
  }

  // If "link citations to literature notes" is on, wrap the content in an
  // <a class="internal-link"> so Obsidian's own reading-mode click handler
  // navigates to the note (same mechanism as [[wikilinks]]).
  if (plugin.settings.renderCitationsAsLinks && rendered.citations.length) {
    const citekey = rendered.citations[0].id;
    const resolved = getLitNoteForCitekey(citekey, ctx.sourcePath, plugin.app);
    const linkTarget = resolved?.linkText ?? '@' + citekey;
    span.classList.add('is-link');
    const a = document.createElement('a');
    a.className = 'internal-link';
    a.setAttribute('data-href', linkTarget);
    a.setAttribute('href', linkTarget);
    while (span.firstChild) a.insertBefore(span.firstChild, a.firstChild);
    span.appendChild(a);
  }

  plugin.tooltipManager.bindPreviewTooltipHandler(span);
  return span;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function processCiteKeys(plugin: ReferenceList) {
  return (el: HTMLElement, ctx: MarkdownPostProcessorContext) => {
    const toRemove: Node[] = [];
    const doc = (el as any).doc || el.ownerDocument || document;
    const sectionInfo = ctx.getSectionInfo(el);
    const isFootnote = isFootnoteSection(el);
    const isCallout = isCalloutSection(el);

    // Callouts (live preview) and footnotes have no usable sectionInfo; the
    // whole-file cache is used for them instead. Bailing here would leave
    // every citation inside a callout unrendered.
    if (
      !sectionInfo &&
      !isCallout &&
      !el.hasClass('markdown-preview-view') &&
      !isFootnote
    )
      return;

    // We wont get a sectionInfo in print mode or for footnote sections
    const cache = plugin.bibManager.getCacheForPath(ctx.sourcePath);
    const sectionCites0 =
      sectionInfo && !isFootnote
        ? plugin.bibManager.getCitationsForSection(
            ctx.sourcePath,
            sectionInfo.lineStart,
            sectionInfo.lineEnd
          )
        : cache?.citations;

    // If the section lookup came up empty (Obsidian's getSectionInfo and
    // metadataCache.sections can disagree on ranges), fall back to the
    // whole-file cache so citations still render — matching the footnote /
    // callout path. Prevents the base walker from degrading to raw
    // per-part formatting for every citation in the section.
    const sectionCites =
      !sectionCites0?.length && cache?.citations?.length
        ? cache.citations
        : sectionCites0;

    if (!sectionCites?.length) {
      return;
    }

    // Find a rendered citation by val/type equality. Prefer the section-scoped
    // list, but fall back to the whole-file cache: Obsidian's
    // getSectionInfo(el) and metadataCache.sections can disagree on a section's
    // line range, so getCitationsForSection may return a subset that is missing
    // the citation we need (e.g. the MERGED container group while standalone
    // single-link entries from neighbouring lines ARE present). Without the
    // fallback the container pre-pass silently does nothing and the walker
    // degrades to raw per-part formatting.
    const findRendered = (segs: Segment[]): RenderedCitation | undefined => {
      const want = onlyValType(segs);
      return (
        sectionCites.find((c) => equal(onlyValType(c.data), want)) ??
        cache?.citations?.find((c) => equal(onlyValType(c.data), want))
      );
    };

    // Multi-work containers (⟦…⟧): Obsidian renders each [[…]] inside the run
    // as a separate <a>, so group the whole run into ONE rendered citation
    // before the text walker sees it.
    if (
      plugin.settings.renderLinkCitations &&
      plugin.settings.formatLinkAliases
    ) {
      const containerOpen = '\u27E6';
      const containerClose = '\u27E7';

      const buildSpan = (rendered: RenderedCitation): HTMLSpanElement =>
        buildCitationSpan(plugin, rendered, ctx);

      const finder = doc.createNodeIterator(el, NodeFilter.SHOW_TEXT);
      const containerStarts: Text[] = [];
      let fn: Node | null;
      while ((fn = finder.nextNode())) {
        if (fn.nodeValue && fn.nodeValue.includes(containerOpen)) {
          containerStarts.push(fn as Text);
        }
      }

      for (const start of containerStarts) {
        if (!start.isConnected || start.parentElement === null) continue;
        const parent = start.parentElement;
        const value = start.nodeValue ?? '';

        // A text node can hold several '⟦' (e.g. "(⟦…⟧): ⟦[[@a]]…"): try each
        // occurrence — an early one may just be the parenthetical ⟦…⟧ with no
        // anchors, while a later one starts the real container.
        let openIdx = value.indexOf(containerOpen);
        while (openIdx !== -1) {
          const preText = value.slice(0, openIdx);
          let nodeText = value.slice(openIdx);
          let cursor: Node = start;
          let textNode: Text = start;
          let first = true;
          const runNodes: Node[] = [];
          const pieces: string[] = [];
          let anchors = 0;
          let valid = true;
          let closed = false;
          let tailText = '';

          while (true) {
            const closeIdx = nodeText.indexOf(containerClose);
            if (closeIdx !== -1) {
              pieces.push(nodeText.slice(0, closeIdx + 1));
              tailText = nodeText.slice(closeIdx + 1);
              closed = true;
              break;
            }
            pieces.push(nodeText);
            if (!first) runNodes.push(cursor);
            first = false;

            const next = cursor.nextSibling;
            if (!next) {
              valid = false;
              break;
            }
            if (next.nodeType === Node.TEXT_NODE) {
              cursor = next;
              textNode = next as Text;
              nodeText = (next as Text).nodeValue ?? '';
              continue;
            }
            if (next.nodeType === Node.ELEMENT_NODE && next.nodeName === 'A') {
              const a = next as HTMLAnchorElement;
              const key = getLinkCiteKey(a);
              if (!key) {
                valid = false;
                break;
              }
              anchors++;
              const aText = (a.textContent ?? '').trim();
              pieces.push(
                aText === '@' + key
                  ? `[[@${key}]]`
                  : `[[@${key}|${aText}]]`
              );
              runNodes.push(next);
              // Advance past any run of adjacent anchors rendered without a
              // text node between them (e.g. ⟦[[@a]][[@b]]⟧ with no spaces).
              let after: ChildNode | null = next.nextSibling;
              while (
                after &&
                after.nodeType === Node.ELEMENT_NODE &&
                (after as Element).nodeName === 'A'
              ) {
                const nextA = after as HTMLAnchorElement;
                const nextKey = getLinkCiteKey(nextA);
                if (!nextKey) { valid = false; break; }
                pieces.push(''); // implicit empty separator between adjacent anchors
                anchors++;
                const nextAText = (nextA.textContent ?? '').trim();
                pieces.push(
                  nextAText === '@' + nextKey
                    ? `[[@${nextKey}]]`
                    : `[[@${nextKey}|${nextAText}]]`
                );
                runNodes.push(after);
                after = after.nextSibling;
              }
              if (!valid) break;
              if (!after || after.nodeType !== Node.TEXT_NODE) {
                valid = false;
                break;
              }
              cursor = after;
              textNode = after as Text;
              nodeText = (after as Text).nodeValue ?? '';
              continue;
            }
            valid = false;
            break;
          }

          if (closed && valid && anchors >= 2) {
            const merged = mergeContainerExpression(pieces.join(''));
            if (merged !== null) {
              const segs = getCitationSegments(merged, false, false);
              if (segs.length) {
                const rendered = findRendered(segs[0]);
                if (rendered) {
                  const span = buildSpan(rendered);
                  if (preText) {
                    start.nodeValue = preText;
                    parent.insertBefore(span, start.nextSibling);
                  } else {
                    parent.insertBefore(span, start);
                    parent.removeChild(start);
                  }
                  for (const n of runNodes) parent.removeChild(n);
                  if (tailText) {
                    textNode.nodeValue = tailText;
                  } else {
                    parent.removeChild(textNode);
                  }
                  break; // consumed this container — stop scanning this node
                }
              }
            }
          }

          openIdx = value.indexOf(containerOpen, openIdx + 1);
        }
      }

      // Outer-bracket multi-citation containers: `[ [[@a]]; [[@b]] ]` (2+
      // wikilinks inside a plain bracket pair). Obsidian renders this as
      // text "[ ", <a>@a</a>, text "; ", <a>@b</a>, text " ]". The base walker
      // would render each anchor separately and leave the outer brackets as
      // literal text, so merge the run into ONE rendered citation first.
      // A bare `[` not part of `[[` starts a candidate; a bare `]` (not part of
      // `]]`) closes it. Requires 2+ citation anchors inside to be treated as a
      // container — single-wikilink or anchor-free brackets are left alone.
      {
        const innerLinkRe = /\[\[@([^|\]\s]+)(?:\|([\s\S]*?))?\]\]/g;


        const finder2 = doc.createNodeIterator(el, NodeFilter.SHOW_TEXT);
        const bracketStarts: Text[] = [];
        let fn2: Node | null;
        while ((fn2 = finder2.nextNode())) {
          if (fn2.nodeValue && /(^|[^[])\[/.test(fn2.nodeValue)) {
            bracketStarts.push(fn2 as Text);
          }
        }

        for (const start of bracketStarts) {
          if (!start.isConnected || start.parentElement === null) continue;
          const parent = start.parentElement;
          const value = start.nodeValue ?? '';

          // Try each bare '[' in this text node (skip '[' that starts '[[').
          let openIdx = value.search(/(^|[^[])\[/);
          while (openIdx !== -1) {
            // openIdx from search is the index of the char BEFORE '[', so
            // adjust. (search returns index of the group start; the '[' is at
            // openIdx + 1 when preceded by a char, or 0 when at start.)
            const bracketAt = openIdx === 0 ? 0 : openIdx + 1;
            const preText = value.slice(0, bracketAt);
            let nodeText = value.slice(bracketAt);
            let cursor: Node = start;
            let textNode: Text = start;
            let first = true;
            const runNodes: Node[] = [];
            let anchors = 0;
            let valid = true;
            let closed = false;
            let tailText = '';
            let innerText = '';

            while (true) {
              // Closing bare ']' (not part of ']]'): scan for a ']' at
              // bracket depth 0. Nested plain citations ([@b]) have their own
              // ']' which must NOT close the outer container — so count '['
              // and ']' (skipping '[[…]]' wikilink pairs) over the text
              // accumulated so far plus the current node's text.
              const accumulated = innerText + nodeText;
              let closeAt = -1;
              let depth = 0;
              for (let ci = 0; ci < accumulated.length; ci++) {
                const ch = accumulated[ci];
                if (ch === '[' && accumulated[ci + 1] === '[') {
                  depth++;
                  ci++;
                } else if (ch === '[' && accumulated[ci + 1] !== '[') {
                  depth++;
                } else if (ch === ']' && accumulated[ci + 1] === ']') {
                  if (depth > 1) {
                    depth--;
                    ci++;
                  } else {
                    // depth 1: this ']]' closes the outer container.
                    closeAt = ci;
                    break;
                  }
                } else if (ch === ']' && accumulated[ci + 1] !== ']') {
                  if (depth > 1) {
                    depth--;
                  } else {
                    // depth 0 or 1: the outer container's closing ']'.
                    closeAt = ci;
                    break;
                  }
                }
              }
              if (closeAt !== -1) {
                const rel = closeAt - innerText.length;
                innerText += nodeText.slice(0, rel);
                tailText = nodeText.slice(rel + 1);
                closed = true;
                break;
              }
              innerText += nodeText;
              if (!first) runNodes.push(cursor);
              first = false;

              // Walk forward through siblings, tolerating ANY structure:
              // text nodes, citation anchors, and unrelated elements (Obsidian
              // may insert spans/breaks around list items). We only give up
              // when we run off the end of the DOM without finding the closing
              // ']'. Anchors without a citekey href are skipped, not fatal.
              const next = cursor.nextSibling;
              if (!next) {
                valid = false;
                break;
              }
              if (next.nodeType === Node.TEXT_NODE) {
                cursor = next;
                textNode = next as Text;
                nodeText = (next as Text).nodeValue ?? '';
                continue;
              }
              if (next.nodeType === Node.ELEMENT_NODE && next.nodeName === 'A') {
                const a = next as HTMLAnchorElement;
                const key = getLinkCiteKey(a);
                if (key) {
                  anchors++;
                  // Obsidian's reading-mode anchor text can carry
                  // leading/trailing whitespace or newlines (esp. inside list
                  // items); trim before comparing so plain [[@key]] links are
                  // detected as such and aliased links don't smuggle stray
                  // whitespace into the merged citation expression.
                  const aText = (a.textContent ?? '').trim();
                  innerText +=
                    aText === '@' + key
                      ? `[[@${key}]]`
                      : `[[@${key}|${aText}]]`;
                }
                runNodes.push(next);
                const after = next.nextSibling;
                if (!after) {
                  valid = false;
                  break;
                }
                // Advance past the anchor to whatever follows.
                cursor = after;
                textNode = after as Text;
                nodeText = (after as Text).nodeValue ?? '';
                continue;
              }
              // Unrelated element: skip it and keep scanning.
              cursor = next;
              textNode = next as Text;
              nodeText = '';
              continue;
            }

            if (closed && valid) {
              // Collect the members inside and merge into [@k1; @k2; …],
              // ignoring any text between the outer brackets and the links.
              // Tolerant of whitespace around [[ ]]/@ (Obsidian's reading-mode
              // anchor text can carry stray newlines inside list items).
              // Accept BOTH wikilinks ([[@key|alias]]) and plain pandoc
              // citations ([@key, p. 5]) so mixed containers merge correctly
              // instead of dropping the plain members.
              const links: { key: string; alias?: string }[] = [];
              let lm: RegExpExecArray;
              const linkRe =
                /\[\[\s*@([^|\]\s]+)(?:\|\s*([\s\S]*?))?\s*\]\]|\[@([^\]\s,;]+)([^\]]*)\]/g;
              while ((lm = linkRe.exec(innerText))) {
                if (lm[1] !== undefined) {
                  links.push({ key: lm[1], alias: lm[2]?.trim() });
                } else {
                  const key = lm[3];
                  const tail = (lm[4] ?? '').trim();
                  links.push({ key, alias: tail ? `@@${tail}` : undefined });
                }
              }
              if (links.length >= 2) {
                const mergedParts: string[] = [];
                for (const link of links) {
                  const aliasText = link.alias ?? '@' + link.key;
                  mergedParts.push(expandAlias(aliasText, link.key));
                }
                const merged = '[' + mergedParts.join('; ') + ']';
                const segs = getCitationSegments(merged, false, false);
                if (segs.length) {
                  const rendered = findRendered(segs[0]);
                  if (rendered) {
                    const span = buildSpan(rendered);
                    if (preText) {
                      start.nodeValue = preText;
                      parent.insertBefore(span, start.nextSibling);
                    } else {
                      parent.insertBefore(span, start);
                      parent.removeChild(start);
                    }
                    for (const n of runNodes) parent.removeChild(n);
                    if (tailText) {
                      textNode.nodeValue = tailText;
                    } else {
                      parent.removeChild(textNode);
                    }
                    break; // consumed this container
                  }
                }
              }
            }

            // Move to the next bare '[' in this text node.
            const rest = value.slice(bracketAt + 1);
            const nextM = rest.match(/(^|[^[])\[/);
            if (!nextM) {
              openIdx = -1;
            } else {
              // nextM.index = index (in rest) of the char BEFORE the '['
              // (0 when the '[' sits at rest[0] via the '^' alternative).
              // The '[' itself is at nextM.index + nextM[0].length - 1.
              const bracketInRest = nextM.index + nextM[0].length - 1;
              const bracketAbs = bracketAt + 1 + bracketInRest;
              // openIdx must point at the char BEFORE the '[' (0 when at start).
              openIdx = bracketAbs === 0 ? 0 : bracketAbs - 1;
            }
          }
        }
      }
    }

    const walker = doc.createNodeIterator(el, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.parentElement && node.parentElement.tagName === 'CODE') {
        continue;
      }

      let content = node.nodeValue;
      let linkCiteKey: string | undefined;
      if (node.parentElement.tagName === 'A') {
        if (!plugin.settings.renderLinkCitations) continue;
        content = `[${content}]`;
        if (plugin.settings.formatLinkAliases) {
          linkCiteKey = getLinkCiteKey(node.parentElement);
        }
      }

      let frag = createFragment();
      let pos = 0;
      let didMatch = false;

      const segments = getCitationSegments(
        content,
        !plugin.settings.renderLinkCitations,
        plugin.settings.formatLinkAliases,
        linkCiteKey
      );
      for (const match of segments) {
        if (!didMatch) didMatch = true;

        const rendered = findRendered(match);

        if (rendered) {
          const preCite = content.substring(pos, match[0].from);
          pos = match[match.length - 1].to;

          frag.appendText(preCite);
          frag.appendChild(buildCitationSpan(plugin, rendered, ctx, node));
          continue;
        }

        for (let i = 0, len = match.length; i < len; i++) {
          const part = match[i];
          const next = match[i + 1];
          frag.appendText(content.substring(pos, part.from));
          pos = part.to;

          switch (part.type) {
            case SegmentType.key: {
              const { isResolved, isUnresolved } =
                plugin.bibManager.getResolution(ctx.sourcePath, part.val) || {
                  isResolved: false,
                  isUnresolved: false,
                };

              frag.createSpan({
                cls: getCiteClass(isResolved, isUnresolved),
                text: part.val,
                attr: {
                  'data-citekey': part.val,
                  'data-source': ctx.sourcePath,
                },
              });
              continue;
            }
            case SegmentType.at: {
              const { isResolved, isUnresolved } =
                plugin.bibManager.getResolution(ctx.sourcePath, next?.val) || {
                  isResolved: false,
                  isUnresolved: false,
                };

              const classes: string[] = [part.type];

              if (isUnresolved) classes.push('is-unresolved');
              if (isResolved) classes.push('is-resolved');

              frag.createSpan({
                cls: `pandoc-citation-formatting ${classes.join(' ')}`,
                text: part.val,
              });
              continue;
            }
            case SegmentType.curlyBracket:
            case SegmentType.bracket:
            case SegmentType.separator:
            case SegmentType.suppressor:
            case SegmentType.prefix:
            case SegmentType.suffix:
            case SegmentType.locator:
            case SegmentType.locatorLabel:
            case SegmentType.locatorSuffix:
              frag.createSpan({
                cls: `pandoc-citation-formatting ${part.type}`,
                text: part.val,
              });
              continue;
          }
        }
      }

      if (didMatch) {
        // Add trailing text
        frag.appendText(content.substring(pos));
        const parent = node.parentElement;
        if (
          parent &&
          parent.tagName === 'A' &&
          plugin.settings.renderCitationsAsLinks &&
          parent.childNodes.length === 1 &&
          parent.firstChild === node
        ) {
          // The citation came from a reading-mode wikilink
          // (<a class="internal-link">…</a>). The rendered citation span
          // already carries its own <a class="internal-link"> for navigation,
          // so replace the WHOLE original anchor. Leaving it in place would
          // nest the span inside Obsidian's link and force its blue link
          // styling onto the citation (via the inner anchor's color: inherit).
          parent.parentNode.insertBefore(frag, parent);
          toRemove.push(parent); // detaches the text node along with it
        } else {
          node.parentNode.insertBefore(frag, node);
          toRemove.push(node);
        }
        frag = null;
      }
    }

    toRemove.forEach((n) => n.parentNode.removeChild(n));
  };
}
