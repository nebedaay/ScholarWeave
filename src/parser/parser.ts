import { locatorToTerm, locators } from './locators';

export enum SegmentType {
  at = 'at',
  key = 'key',
  curlyBracket = 'curlyBracket',

  // In brackets
  suppressor = 'suppressor',
  bracket = 'bracket',
  prefix = 'prefix',
  suffix = 'suffix',
  locatorSuffix = 'locatorSuffix',
  locator = 'locator',
  locatorLabel = 'locatorLabel',
  separator = 'separator',
}

export interface Segment {
  type: SegmentType;
  from: number;
  to: number;
  val: string;
}

interface State {
  inBrackets: boolean;
  inExplicitKey: boolean;
  inExplicitLocator: boolean;
  inKey: boolean;
  inLink: boolean;
  // True if we are inside a wikilink ([[...]]) that contains an alias pipe
  // e.g. [[@key|Alias]]. When this is true, we should ignore this bracketed
  // segment entirely so Obsidian can render the alias natively.
  inLinkHasAlias: boolean;
  // True when inside a wikilink whose target is "@key (space and other text)"
  // — e.g. [[@key - transcription]], the vault convention for derived files.
  // These are native wikilinks, NOT citations: a space after the key means
  // the filename is a derivative, not the literature note.
  inLinkDerived: boolean;
  inSuffix: boolean;
  seekingSuffix: boolean;
  seekingLocator: boolean;
  encounteredKey: boolean;
  shouldCancelSeek: boolean;
  // Index of the last ';' that was appended to a suffix (not emitted as a
  // separator). Used to suppress the "prev===';' starts a prefix" heuristic
  // when the semicolon was not actually a citation separator.
  semicolonAppendedAt: number;
  segment: Segment[];
  currentSegment: Segment;
  bracketDepth: number;
}

function newState(): State {
  return {
    bracketDepth: 0,
    inBrackets: false,
    inKey: false,
    inExplicitKey: false,
    inExplicitLocator: false,
    inSuffix: false,
    inLink: false,
    inLinkHasAlias: false,
    inLinkDerived: false,
    seekingSuffix: false,
    seekingLocator: false,
    encounteredKey: false,
    shouldCancelSeek: false,
    semicolonAppendedAt: -1,
    segment: [] as Segment[],
    currentSegment: null as Segment,
  };
}

const alphaNumeric = /[\p{L}\p{N}]/u;
const punct = /[:.#$%&\-+?<>~_/]/;
const nonKeyPunct = /\p{P}/u;
const space = /[ \t\v]/;
const preKey = /[ \t\v[\-\r\n;]/;
const locatorRe =
  /^((?:[[(]?[a-z\p{N}]+[\])]?[–—:-][[(]?[a-z\p{N}]+[\])]?|[a-z\p{N}()[\]]*\p{N}+[a-z\p{N}()[\]]*|[mdclxvi]+)(?:[ \t]*,[ \t]*(?:[[(]?[a-z\p{N}]+[\])]?[–—:-][[(]?[a-z\p{N}]+[\])]?|[a-z\p{N}()[\]]*\p{N}+[a-z\p{N}()[\]]*|[mdclxvi]+))*)/iu;

function isTerminus(s?: string) {
  return !s || s === '\r' || s === '\n';
}

function isValidPreKey(s?: string) {
  return !s || preKey.test(s);
}

export function getSegmentData(segments: Segment[]) {
  let key: string;
  let locator: string;
  let locatorLabel: string;
  let prefix: string;
  let suffix: string;

  for (const seg of segments) {
    if (seg.type === SegmentType.prefix) {
      prefix = seg.val;
      continue;
    }

    if (seg.type === SegmentType.locator) {
      locator = seg.val;
      suffix = '';
      continue;
    }

    if (seg.type === SegmentType.locatorLabel) {
      locatorLabel = seg.val;
      continue;
    }

    if (seg.type === SegmentType.key) {
      key = seg.val;
      continue;
    }

    if (seg.type === SegmentType.suffix) {
      suffix = seg.val;
      continue;
    }
  }

  return {
    key,
    locator,
    locatorLabel,
    prefix,
    suffix,
  };
}

const parsePossibleLocator = (state: State) => {
  const segments: Segment[] = [];

  // The suffix segment always includes the separator that introduced it:
  //   ' p. 27'   (space before the label)
  //   ', p. 27'  (comma + space)
  //   ', 27'     (bare number — pandoc treats this as a PAGE locator)
  // Strip the leading separator first so the anchored locators/locatorRe
  // regexes can match the rest. The separator itself is kept as
  // locatorSuffix so the rendered citation still shows ", 27".
  const val = state.currentSegment.val;
  const sepMatch = val.match(/^([ \t]*[,;]?[ \t]*)/);
  const sep = sepMatch ? sepMatch[0] : '';
  const rest = val.slice(sep.length);
  let index = state.currentSegment.from + sep.length;

  if (sep) {
    segments.push({
      from: state.currentSegment.from,
      to: index,
      val: sep,
      type: SegmentType.locatorSuffix,
    });
  }

  const match = rest.match(locators);
  if (match) {
    const sp0 = match[1];
    const label = match[2];
    const sp1 = match[3];

    if (sp0) {
      segments.push({
        from: index,
        to: index + sp0.length,
        val: sp0,
        type: SegmentType.locatorSuffix,
      });
      index = index + sp0.length;
    }

    segments.push({
      from: index,
      to: index + label.length,
      val: label,
      type: SegmentType.locatorLabel,
    });
    index = index + label.length;

    if (sp1) {
      segments.push({
        from: index,
        to: index + sp1.length,
        val: sp1,
        type: SegmentType.locatorSuffix,
      });
      index = index + sp1.length;
    }

    const sliced = rest.slice(match.index + match[0].length);
    const locMatch = sliced.match(locatorRe);
    if (locMatch) {
      const loc = locMatch[1];
      segments.push({
        from: index,
        to: index + loc.length,
        val: loc,
        type: SegmentType.locator,
      });
      index = index + loc.length;

      const suffix = sliced.slice(locMatch.index + locMatch[0].length);
      if (suffix) {
        segments.push({
          from: index,
          to: index + suffix.length,
          val: suffix,
          type: SegmentType.suffix,
        });
      }
    } else {
      return [];
    }
  } else {
    // No explicit label (e.g. "p." / "chap."). Pandoc treats a bare number /
    // range / roman numeral after the comma as a PAGE locator, so mirror that:
    //   [@key, 27]   -> locator "27"   with implicit label "page"
    //   [@key, 155–56] -> locator "155–56" (page)
    //   [@key, xvii] -> locator "xvii" (roman page)
    // This makes citeproc render it via the CSL style (normalized) instead of
    // as raw suffix text, matching the Word-plugin / pandoc behaviour.
    //
    // IMPORTANT: a bare SPACE separator (no comma) means PROSE, not a
    // locator — e.g. "[[@key|@ discusses this]]" must keep "discusses this"
    // as a plain suffix ("(Key 2020 discusses this)"), NOT a locator. Without
    // this guard, the roman-numeral alternative in locatorRe matches "di" of
    // "discusses" (d/i are roman numerals) and mangles the suffix.
    const bare = rest.match(locatorRe);
    if (!bare) return [];
    const loc = bare[1];
    // No comma in the separator ⇒ the "locator" must start with a digit (a
    // real number/range); letters-only (incl. roman) after a bare space is
    // prose. With a comma, letters/roman are allowed (", xvii").
    if (!/,/.test(sep) && !/\p{N}/u.test(loc)) return [];

    segments.push({
      from: index,
      to: index + loc.length,
      val: loc,
      type: SegmentType.locator,
    });
    index = index + loc.length;

    // Explicit page label so citeproc knows the locator type even though the
    // source omitted it (pandoc's default). Zero-width: no text of its own.
    segments.push({
      from: index,
      to: index,
      val: 'page',
      type: SegmentType.locatorLabel,
    });

    const suffix = rest.slice(bare.index + bare[0].length);
    if (suffix) {
      segments.push({
        from: index,
        to: index + suffix.length,
        val: suffix,
        type: SegmentType.suffix,
      });
    }
  }
  return segments;
};

const parseExplicitLocator = (state: State) => {
  const match = state.currentSegment.val.match(locators);
  const segments: Segment[] = [];
  if (match) {
    const sp0 = match[1];
    const label = match[2];
    const sp1 = match[3];
    let index = state.currentSegment.from;

    if (sp0) {
      segments.push({
        from: index,
        to: index + sp0.length,
        val: sp0,
        type: SegmentType.locatorSuffix,
      });
      index = index + sp0.length;
    }

    segments.push({
      from: index,
      to: index + label.length,
      val: label,
      type: SegmentType.locatorLabel,
    });
    index = index + label.length;

    if (sp1) {
      segments.push({
        from: index,
        to: index + sp1.length,
        val: sp1,
        type: SegmentType.locatorSuffix,
      });
      index = index + sp1.length;
    }

    const sliced = state.currentSegment.val.slice(
      match.index + match[0].length
    );
    if (sliced) {
      segments.push({
        from: index,
        to: index + sliced.length,
        val: sliced,
        type: SegmentType.locator,
      });
    } else {
      return [];
    }
  } else {
    state.currentSegment.type = SegmentType.locator;
  }
  return segments;
};

export interface Citation {
  prefix?: string;
  suffix?: string;
  infix?: string;
  locator?: string;
  label?: string;
  'suppress-author'?: boolean;
  'author-only'?: boolean;
  composite?: boolean;
  id: string;
}

export interface CitationGroup {
  data: Segment[];
  citations: Citation[];
  from: number;
  to: number;
}

export interface RenderedCitation extends CitationGroup {
  val: string;
  noteIndex?: number;
  note?: string;
}

export function getCitations(
  segments: Segment[],
  locale: string = 'en-US'
): CitationGroup {
  const cites: Citation[] = [];

  let key: string;
  let prefix: string;
  let suffix: string;
  let infix: string;
  let locator: string;
  let label: string;

  let suppressAuthor = false;
  let onlyAuthor = false;
  let composite = false;

  const push = () => {
    // A trailing whitespace-separated '-' is the author-in-text flag (the
    // pandoc `@key -` form). On the first citation it makes the whole group
    // narrative (composite); elsewhere it is dropped (this engine cannot
    // render mid-group narrative). The marker itself is never shown.
    if (suffix?.trim() === '-') {
      if (cites.length === 0) composite = true;
      suffix = undefined;
    }

    // Combine a multi-part locator "vol. X, p. Y" into the single Chicago
    // locator "X:Y". Zotero allows only ONE locator per citation item, and
    // Chicago renders volume:page as "1:113" — so `vol. I, p. 113` becomes
    // locator "1:113" (roman volume converted to arabic), label "page".
    if (
      (label === 'volume' || label === 'vol.' || label === 'vols.') &&
      locator &&
      suffix &&
      /^,\s*p{1,2}\.?\s*(\S+)/i.test(suffix)
    ) {
      const page = suffix.replace(/^,\s*p{1,2}\.?\s*/i, '');
      locator = `${romanToArabic(locator)}:${page}`;
      label = 'page';
      suffix = undefined;
    }

    const cite: Citation = {
      id: key,
    };

    if (prefix?.trim()) cite.prefix = prefix.trim();
    if (suffix?.trim()) cite.suffix = suffix.trim();
    if (infix?.trim()) cite.infix = infix.trim();
    if (locator) cite.locator = locator;
    if (label && locatorToTerm[locale] && locatorToTerm[locale][label]) {
      cite.label = locatorToTerm[locale][label];
    }
    if (composite) cite.composite = composite;
    else if (suppressAuthor) cite['suppress-author'] = suppressAuthor;
    else if (onlyAuthor) cite['author-only'] = onlyAuthor;

    composite = false;
    onlyAuthor = false;
    suppressAuthor = false;

    cites.push(cite);
  };

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    switch (seg.type) {
      case SegmentType.at:
        if (i === 0) {
          composite = true;
        }
        continue;
      case SegmentType.suppressor:
        if (composite) {
          suffix = undefined;
          locator = undefined;
          label = undefined;
          composite = false;
          onlyAuthor = true;
          push();
        }
        suppressAuthor = true;
        continue;
      case SegmentType.separator:
        push();
        prefix = undefined;
        suffix = undefined;
        locator = undefined;
        label = undefined;
        infix = undefined;
        onlyAuthor = false;
        suppressAuthor = false;
        composite = false;
        continue;
      case SegmentType.key:
        key = seg.val;
        continue;
      case SegmentType.prefix:
        prefix = seg.val;
        continue;
      case SegmentType.suffix:
        suffix = seg.val;
        continue;
      case SegmentType.locator:
        locator = seg.val;
        continue;
      case SegmentType.locatorLabel:
        label = seg.val;
        continue;
    }
  }

  push();

  return {
    data: segments,
    citations: cites,
    from: segments[0].from,
    to: segments[segments.length - 1].to,
  };
}

/** Convert a roman numeral string (I, IV, X, …) to an arabic number, or
 *  return the input unchanged when it isn't a roman numeral. */
function romanToArabic(s: string): string {
  const map: Record<string, number> = {
    I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000,
  };
  const up = s.toUpperCase();
  if (!/^[IVXLCDM]+$/.test(up)) return s;
  let total = 0;
  let prev = 0;
  for (let i = up.length - 1; i >= 0; i--) {
    const v = map[up[i]];
    if (v < prev) total -= v;
    else { total += v; prev = v; }
  }
  return String(total);
}

/**
 * Expand an alias string the same way the pandoc export filter does: every
 * '@'-token is replaced by the link's own citekey. A bare '@' (followed by a
 * delimiter such as space, ',', ';', or the end of the alias) is the shorthand
 * proxy and also expands — unambiguous because citekeys always have letters
 * after the '@'. A trailing whitespace-separated '-' (the author-in-text flag)
 * is deliberately kept in the text so the state machine can see it and mark
 * the citation as narrative (composite).
 *
 * Multi-part locators of the form `vol. X, p. Y` (or `pp. Y-Z`) are combined
 * into the single Chicago-style locator `X:Y` — Zotero allows only ONE locator
 * per citation item, and Chicago renders volume:page as "1:113". The volume
 * numeral is converted to arabic ("I" → "1"). A standalone `vol. X` keeps its
 * volume label.
 *
 *   'see also @, 6'   ->  'see also @key, 6'   (bare @ is the proxy)
 *   'see also @@, 6'  ->  'see also @key, 6'   (legacy @@ shorthand)
 *   '@other, p. 5'    ->  '@key, p. 5'   (link key wins over alias keys)
 *   '@, vol. I, p. 113' -> '@key, 1:113'  (combined Chicago locator)
 *   '@key -'          ->  '@key -'       (flag survives for getCitations)
 *   '-@key'           ->  '-@key'        (suppressed author survives)
 */
export function expandAlias(alias: string, linkKey: string): string {
  const expanded = alias.replace(/@[^\s,;]*/g, '@' + linkKey);
  // Combine "vol. X, p. Y" / "vol. X, pp. Y-Z" into "X:Y" / "X:Y-Z".
  // Handles both arabic and roman volume numerals (roman → arabic).
  return expanded.replace(
    /(^|[\s,(])vol\.\s*([IVXLCDM]+|\d+),\s*p{1,2}\.?\s*(\S+)/gi,
    (_m, pre: string, vol: string, page: string) =>
      `${pre}${romanToArabic(vol)}:${page}`
  );
}

const containerOpen = '\u27E6'; // ⟦
const containerClose = '\u27E7'; // ⟧

/**
 * Merge a multi-work citation container of the form
 *
 *   ⟦[[@a|see also @@, 3]]; [[@b]]; [[@c]]⟧
 *
 * into a single bracketed citation expression "[see also @a, 3; @b; @c]".
 * Members are separated by whitespace and/or ';'. Returns null when the
 * container is not a valid multi-citation group (fewer than two links,
 * plain-label members, or stray text between members).
 */
export function mergeContainerExpression(containerText: string): string | null {
  if (
    !containerText.startsWith(containerOpen) ||
    !containerText.endsWith(containerClose)
  ) {
    return null;
  }
  const content = containerText.slice(
    containerOpen.length,
    containerText.length - containerClose.length
  );
  const anyLinkRe = /\[\[@([^|\]\s]+)(?:\|([\s\S]*?))?\]\]/g;
  let expr = '';
  let members = 0;
  let lastEnd = 0;
  let m: RegExpExecArray;
  while ((m = anyLinkRe.exec(content))) {
    const [full, key, alias] = m;
    const between = content.slice(lastEnd, m.index);
    if (!/^[\s;]*$/.test(between)) return null;
    if (members > 0 && !/;/.test(between)) return null;
    if (members === 0 && /;/.test(between)) return null;
    if (alias !== undefined && !alias.includes('@')) return null;
    const aliasText = alias ?? '@' + key;
    if (members > 0) expr += '; ';
    expr += expandAlias(aliasText, key);
    members++;
    lastEnd = m.index + full.length;
  }
  if (members < 2) return null;
  if (!/^[\s;]*$/.test(content.slice(lastEnd))) return null;
  return '[' + expr + ']';
}

/**
 * Pre-scan for aliased citation wikilinks of the form `[[@key|alias]]` and
 * rewrite them into plain bracket citations `[alias]` so the existing state
 * machine parses the alias text as a citation expression:
 *
 *   [[@smith1992|see also @@, 6]]  ->  [see also @smith1992, 6]
 *
 * Inside the alias, EVERY '@'-token (including the `@@` shorthand) expands to
 * the link's own citekey, so key conflicts resolve in favour of the link (the
 * autocompleted target is trusted; an alias key is assumed to be a typo).
 * A trailing whitespace-separated '-' is the author-in-text flag, kept in the
 * text for getCitations to interpret:
 *
 *   [[@smith1992|@smith1992 -]]    ->  [@smith1992 -]  (narrative citation)
 *
 * Multi-work containers are merged into a single citation:
 *
 *   ⟦[[@a]]; [[@b|see also @@, 3]]⟧ -> [see also @a, 3; @b]
 *
 * When `linkCiteKey` is provided (reading mode, where Obsidian renders only
 * the alias text inside an <a> element and the raw `[[@key|…]]` markup is
 * unavailable), the same token and trailing-dash rules apply to the bare text.
 *
 * Returns the rewritten text plus an index map from rewritten position back to
 * original position, so segment offsets can be restored after parsing.
 */
function transformLinkAliases(
  str: string,
  linkCiteKey?: string
): { text: string; map: number[] } {
  const out: string[] = [];
  const map: number[] = [];
  let last = 0;

  const push = (ch: string, src: number) => {
    out.push(ch);
    map.push(src);
  };

  const copyRange = (from: number, to: number) => {
    for (let i = from; i < to; i++) push(str[i], i);
  };

  // Copy an alias with every '@'-token (including a bare '@' proxy) replaced
  // by '@' + linkKey, mapping each emitted character back to the alias
  // position it came from.
  const emitExpanded = (alias: string, key: string, aliasStart: number) => {
    const tokenRe = /@[^\s,;]*/g;
    let cursor = 0;
    let tm: RegExpExecArray;
    while ((tm = tokenRe.exec(alias))) {
      for (let k = cursor; k < tm.index; k++) push(alias[k], aliasStart + k);
      push('@', aliasStart + tm.index);
      for (let n = 0; n < key.length; n++) push(key[n], aliasStart + tm.index + n);
      cursor = tm.index + tm[0].length;
    }
    for (let k = cursor; k < alias.length; k++) push(alias[k], aliasStart + k);
  };

  const specialRe = new RegExp(
    '\\[\\[@([^|\\]\\s]+)\\|([\\s\\S]*?)\\]\\]|' +
      '\\[\\[@([^|\\]\\s]+)\\]\\]|' +
      containerOpen,
    'g'
  );

  // Outer-bracket multi-citation containers: `[ ... [[@k1]] ... [[@k2]] ... ]`
  // (2+ wikilinks inside a plain bracket pair). The base parser would treat
  // the inner `[[`/`]]` as literal prefix/suffix text, so rewrite the whole
  // group into a clean pandoc multi-cite `[@k1; @k2]` — ignoring any text or
  // whitespace between the outer brackets and the inner wikilinks. Single
  // wikilinks (`[ [[@k]] ]`) collapse to `[@k]`. NOT triggered for normal
  // bracket citations (`[@a; @b]`, `[@a, p. 5]`, `[text](url)`) — those
  // contain no `[[@…]]` pattern.
  const bracketContainers: Array<{ open: number; close: number; merged: string }> = [];
  {
    let scan = 0;
    while (scan < str.length) {
      const open = str.indexOf('[', scan);
      if (open === -1) break;
      if (str[open + 1] === '[') {
        scan = open + 2;
        continue;
      }
      let depth = 0;
      let close = -1;
      for (let i = open + 1; i < str.length; i++) {
        if (str[i] === '[' && str[i + 1] === '[') {
          // Wikilink open [[ — nested inside the outer bracket pair.
          depth++;
          i++;
        } else if (str[i] === '[' && str[i + 1] !== '[') {
          // Plain citation open [@key — also nested (so `[ [@a]; [@b] ]`
          // doesn't close at the inner [@a]'s ']').
          depth++;
        } else if (str[i] === ']' && str[i + 1] === ']') {
          if (depth > 0) {
            depth--;
            i++;
          } else {
            close = i;
            break;
          }
        } else if (str[i] === ']' && str[i + 1] !== ']') {
          if (depth > 0) {
            depth--;
          } else {
            close = i;
            break;
          }
        }
      }
      if (close === -1) break;

      const inside = str.slice(open + 1, close);
      const links: { key: string; alias?: string }[] = [];
      let lm: RegExpExecArray;
      // Accept BOTH wikilink members ([[@key|alias]]) and plain pandoc
      // citations ([@key, p. 5]) inside the outer brackets, in order. This
      // makes mixed containers ("[ [[@a]]; [@b] ]") and all-plain containers
      // ("[ [@a]; [@b] ]") merge correctly instead of dropping the plain
      // members or leaking stray inner brackets.
      const linkRe =
        /\[\[@([^|\]\s]+)(?:\|([\s\S]*?))?\]\]|\[@([^\]\s,;]+)([^\]]*)\]/g;
      while ((lm = linkRe.exec(inside))) {
        if (lm[1] !== undefined) {
          links.push({ key: lm[1], alias: lm[2] });
        } else {
          // Plain [@key, suffix] member: key is lm[3], trailing text lm[4]
          // (e.g. ", p. 5") becomes an alias-style suffix after @@.
          const key = lm[3];
          const tail = (lm[4] ?? '').trim();
          links.push({ key, alias: tail ? `@@${tail}` : undefined });
        }
      }

      if (links.length >= 1) {
        const mergedParts: string[] = [];
        for (const link of links) {
          const aliasText = link.alias ?? '@' + link.key;
          mergedParts.push(expandAlias(aliasText, link.key));
        }
        bracketContainers.push({
          open,
          close,
          merged: '[' + mergedParts.join('; ') + ']',
        });
        scan = close + 1;
        continue;
      }

      scan = open + 1;
    }
  }

  let m: RegExpExecArray;

  // Emit the outer-bracket containers (in document order) interleaved with the
  // specialRe matches. The specialRe loop skips any wikilink that falls inside
  // an already-emitted container — otherwise the inner [[@key]] get rewritten a
  // second time (producing stray duplicate segments after the merged group).
  let containerIdx = 0;
  let emittedUntil = -1; // positions <= this were consumed by a container
  const isInsideEmittedContainer = (pos: number): boolean =>
    pos <= emittedUntil;

  while ((m = specialRe.exec(str))) {
    // Emit any container that starts before this specialRe match.
    while (
      containerIdx < bracketContainers.length &&
      bracketContainers[containerIdx].open < m.index
    ) {
      const c = bracketContainers[containerIdx];
      if (c.open > emittedUntil) {
        copyRange(last, c.open);
        for (let k = 0; k < c.merged.length; k++) {
          // First char maps to the outer '[', last to the outer ']' so the
          // widget covers the whole bracket group.
          push(c.merged[k], k === c.merged.length - 1 ? c.close : c.open);
        }
        last = c.close + 1;
        emittedUntil = c.close;
      }
      containerIdx++;
    }

    if (isInsideEmittedContainer(m.index)) {
      // This wikilink is part of an already-emitted outer-bracket container.
      continue;
    }

    if (m[0] === containerOpen) {
      // Multi-work container: ⟦ … ⟧ -> single merged citation
      const close = str.indexOf(containerClose, m.index + 1);
      if (close === -1) continue;
      const merged = mergeContainerExpression(str.slice(m.index, close + 1));
      if (merged === null) continue;
      copyRange(last, m.index);
      for (let k = 0; k < merged.length; k++) {
        // First char maps to '⟦', last to '⟧' so the widget/span covers the
        // whole container; interior chars map to the open delimiter.
        push(merged[k], k === merged.length - 1 ? close : m.index);
      }
      specialRe.lastIndex = close + 1;
      last = close + 1;
      continue;
    }

    // Plain [[@key]] (no alias) — groups 3/4; aliased [[@key|alias]] —
    // groups 1/2. Normalise both to a single bracket citation so the widget
    // range covers the FULL wikilink (starting at the first '['). Without
    // this, plain [[@key]] fell through to the base parser which produced a
    // bracket segment at from:1 — leaving the leading '[' outside the widget
    // range, which made live-preview render Obsidian's link decoration for
    // that dangling '[' instead of our citation widget.
    const full = m[0];
    const key = m[1] ?? m[3];
    const alias = m[2];
    const start = m.index;
    const end = m.index + full.length;

    // Copy everything up to and including the first '[' of '[['
    copyRange(last, start + 1);

    if (alias !== undefined) {
      const aliasStart = start + 4 + key.length; // after '[[@key|'

      // Copy the alias, expanding every '@'-token to the link's key. A
      // trailing ' -' flag is kept so getCitations can mark the group as
      // narrative.
      emitExpanded(alias, key, aliasStart);

      // Closing ']' maps to the first ']' of ']]'
      push(']', end - 2);
    } else {
      // [[@key]] -> [@key]: copy '@key', then the closing ']' maps to the
      // first ']' of ']]'.
      const keyStart = start + 2; // after '[['
      for (let k = 0; k < key.length + 1; k++) {
        push(str[keyStart + k], keyStart + k); // '@' + key
      }
      push(']', end - 2);
    }
    last = end;
  }

  // Emit any remaining outer-bracket containers after the last specialRe match.
  while (containerIdx < bracketContainers.length) {
    const c = bracketContainers[containerIdx];
    if (c.open > emittedUntil) {
      copyRange(last, c.open);
      for (let k = 0; k < c.merged.length; k++) {
        push(c.merged[k], k === c.merged.length - 1 ? c.close : c.open);
      }
      last = c.close + 1;
      emittedUntil = c.close;
    }
    containerIdx++;
  }

  // Bare-text expansion (reading mode: content is just the anchor text)
  if (linkCiteKey) {
    let i = last;
    while (i < str.length) {
      if (str[i] === '@') {
        // Stop the token at ']' too: reading mode wraps the anchor text in
        // brackets ("[@key]"), so a token like /@[^\s,;]*/ would swallow the
        // closing bracket and break the citation.
        const token = /^@[^\s,;\]\[]*/.exec(str.slice(i));
        if (token) {
          push('@', i);
          // Map the key characters to the original positions AFTER the '@'
          // (the token body). Mapping them at 'i + n' (over the '@') made the
          // key segment's original from/to overlap the at segment, and the
          // walker's content-slicing then produced doubled '@' + trailing
          // garbage (the "[@@key4]" corruption in containers, cases 8/9).
          for (let n = 0; n < linkCiteKey.length; n++) push(linkCiteKey[n], i + 1 + n);
          i += token[0].length;
          continue;
        }
      }
      push(str[i], i);
      i++;
    }
    return { text: out.join(''), map };
  }

  copyRange(last, str.length);
  return { text: out.join(''), map };
}

export function getCitationSegments(
  str: string,
  ignoreLinks: boolean = false,
  expandLinkAliases: boolean = false,
  linkCiteKey?: string
): Segment[][] {
  // Aliased-link citations only apply when link citations are processed at
  // all (ignoreLinks === false means renderLinkCitations is on).
  if (expandLinkAliases && !ignoreLinks) {
    const { text, map } = transformLinkAliases(str, linkCiteKey);
    const groups = getCitationSegments(text, ignoreLinks);
    if (!groups.length) return groups;
    return groups.map((group) =>
      group.map((seg) => ({
        ...seg,
        from: map[seg.from],
        to: map[seg.to - 1] + 1,
      }))
    );
  }

  const segments: Segment[][] = [];

  let state: State = null;
  let seekState: State = null;

  const endSegment = () => {
    if (state.encounteredKey) {
      segments.push(state.segment);
    }
    state = null;
  };

  const newCurrent = (i: number, c: string, type: SegmentType): Segment => {
    return {
      from: i,
      to: i + 1,
      val: c,
      type: type,
    };
  };

  const endCurrent = (i: number) => {
    if (state.seekingLocator || seekState?.seekingLocator) {
      if (state.currentSegment.type === SegmentType.suffix) {
        const segments = parsePossibleLocator(state);
        if (segments.length) {
          state.segment.push(...segments);
          state.seekingLocator = false;
          return;
        }
      } else if (state.currentSegment.type === SegmentType.locatorSuffix) {
        const segments = parseExplicitLocator(state);
        if (segments.length) {
          state.segment.push(...segments);
          state.seekingLocator = false;
          return;
        }
      }
    }

    state.currentSegment.to = i;
    state.segment.push(state.currentSegment);
  };

  for (let i = 0, len = str.length + 1; i < len; i++) {
    const prev = str[i - 1];
    const c = str[i];
    const next = str[i + 1];

    if (c === '[') {
      if (next === '[' && !state) continue;
      if (state) state.bracketDepth++;
      if (!state || state.bracketDepth === 1) {
        if (state?.seekingSuffix) {
          seekState = state;
        }
        state = newState();
        state.bracketDepth = 1;
        state.currentSegment = newCurrent(i, c, SegmentType.bracket);
        state.inBrackets = true;
        if (prev === '[') state.inLink = true;
        continue;
      }
    }

    if (c === '@' && isValidPreKey(prev)) {
      if (seekState && state.shouldCancelSeek) {
        segments.push(seekState.segment);
        seekState = null;
      }

      if (state?.inBrackets) {
        endCurrent(i);
      } else {
        state = newState();
      }

      state.currentSegment = newCurrent(i, c, SegmentType.at);
      state.inKey = true;
      state.encounteredKey = true;
      continue;
    }

    if (state?.seekingSuffix && !space.test(c)) {
      endSegment();
      continue;
    }

    if (state?.inKey) {
      if (isTerminus(c)) {
        if (!state.inBrackets) {
          endCurrent(i);
          endSegment();
        }
        state = null;
        continue;
      }

      if (prev === '@') {
        if (alphaNumeric.test(c) || c === '_') {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.key);
          continue;
        }

        if (c === '{') {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.curlyBracket);
          state.inExplicitKey = true;
          continue;
        }

        state = null;
        continue;
      }

      if (state.inExplicitKey && c !== '}') {
        if (state.currentSegment.type !== SegmentType.key) {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.key);
          continue;
        }
        state.currentSegment.val += c;
        continue;
      }

      if (c === '}') {
        endCurrent(i);
        state.inKey = false;
        state.inExplicitKey = true;
        state.seekingLocator = true;
        if (!state.inBrackets) {
          state.segment.push(newCurrent(i, c, SegmentType.curlyBracket));
          state.seekingSuffix = true;
          state.shouldCancelSeek = true;
        } else {
          state.currentSegment = newCurrent(i, c, SegmentType.curlyBracket);
          state.inSuffix = true;
        }
        continue;
      }

      if (c === '{') {
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.curlyBracket);
        state.inKey = false;
        state.inSuffix = true;
        state.seekingLocator = true;
        state.inExplicitLocator = true;
        continue;
      }

      if (alphaNumeric.test(c)) {
        state.currentSegment.val += c;
        continue;
      }

      if (space.test(c)) {
        // Inside a wikilink ([[...]]), a space after the key means the target
        // is "@key (space and other text)" — a derived filename like
        // [[@key - transcription]], NOT a citation. Mark the link so the
        // closing ']' skips it entirely (native wikilink stays intact).
        if (state.inLink) state.inLinkDerived = true;

        endCurrent(i);
        state.inKey = false;
        state.seekingLocator = true;

        if (!state.inBrackets) {
          state.seekingSuffix = true;
          state.shouldCancelSeek = true;
        } else {
          state.currentSegment = newCurrent(i, c, SegmentType.suffix);
          state.inSuffix = true;
        }
        continue;
      }

      if (punct.test(c)) {
        if (isTerminus(next)) {
          if (!state.inBrackets) {
            endCurrent(i);
            endSegment();
          }
          state = null;
          continue;
        }

        if (next && punct.test(next)) {
          // Double punct
          endCurrent(i);
          state.inKey = false;
          if (!state.inBrackets) {
            endSegment();
          } else {
            state.currentSegment = newCurrent(i, c, SegmentType.suffix);
            state.inSuffix = true;
            state.seekingLocator = true;
          }
          continue;
        }

        if (space.test(next)) {
          if (!state.inBrackets) {
            endSegment();
          } else {
            endCurrent(i);
            state.inKey = false;
            state.currentSegment = newCurrent(i, c, SegmentType.suffix);
            state.inSuffix = true;
            state.seekingLocator = true;
          }
          continue;
        }

        state.currentSegment.val += c;
        continue;
      }

      if (!state.inBrackets) {
        if (nonKeyPunct.test(c)) {
          endCurrent(i);
          endSegment();
        }
        state = null;
        continue;
      }
    }

    if (state?.inBrackets) {
      if (isTerminus(c)) {
        state = null;
        continue;
      }

      // Detect alias pipe inside a wikilink. If we see a '|' while inside
      // a double-bracket link, mark this link as aliased so we can skip it
      // when the link closes. This preserves Obsidian's native alias display
      // in Live Preview and Reading view.
      if (c === '|' && state.inLink) {
        state.inLinkHasAlias = true;
      }

      if (c === ']') {
        state.bracketDepth--;
        if (state.bracketDepth === 0) {
          // Skip citation parsing for links when ignoreLinks is enabled,
          // OR when we are in a wikilink that contains an alias pipe,
          // OR when the target is a derived filename (@key - transcription).
          if (
            ignoreLinks ||
            (state.inLink && state.inLinkHasAlias) ||
            (state.inLink && state.inLinkDerived)
          ) {
            if (state.inLink || next === '(') {
              state = null;
              seekState = null;
              continue;
            }
          }

          endCurrent(i);
          state.segment.push(newCurrent(i, c, SegmentType.bracket));

          if (!seekState) {
            endSegment();
          } else {
            seekState.segment.push(...state.segment);
            segments.push(seekState.segment);
            seekState = null;
            state = null;
          }
          continue;
        }
      }

      if (c === ';') {
        // Only treat as a citation separator when an '@' (or '-@') follows
        // before the bracket closes. A semicolon with no subsequent citation
        // key is suffix/note text, not a separator — matching pandoc's behaviour
        // and fixing the case where users write prose like "[@key, see Smith; cf. Jones]".
        let j = i + 1;
        let hasFollowingKey = false;
        let depth = state.bracketDepth;
        for (; j < str.length; j++) {
          if (str[j] === '[') depth++;
          else if (str[j] === ']') {
            if (--depth === 0) break;
          } else if (str[j] === '@') {
            hasFollowingKey = true;
            break;
          }
        }
        if (hasFollowingKey) {
          state.shouldCancelSeek = false;
          endCurrent(i);
          state.inKey = false;
          state.currentSegment = newCurrent(i, c, SegmentType.separator);
        } else if (state.inKey) {
          // ';' immediately after the key with no following citation: start a suffix
          endCurrent(i);
          state.inKey = false;
          state.inSuffix = true;
          state.seekingLocator = false;
          state.currentSegment = newCurrent(i, c, SegmentType.suffix);
          state.semicolonAppendedAt = i;
        } else {
          state.currentSegment.val += c;
          state.semicolonAppendedAt = i;
        }
        continue;
      }

      if (c === '-' && next === '@') {
        state.shouldCancelSeek = false;
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.suppressor);
        continue;
      }

      if (c === '{') {
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.curlyBracket);
        if (seekState?.seekingLocator) {
          state.inExplicitLocator = true;
        }
        continue;
      }

      if (c === '}') {
        if (
          state.inExplicitLocator &&
          state.currentSegment.type === SegmentType.suffix
        ) {
          state.currentSegment.type = SegmentType.locatorSuffix;
          state.seekingLocator = false;
        }
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.curlyBracket);
        continue;
      }

      if (prev === '{') {
        endCurrent(i);
        if (state.seekingLocator && state.encounteredKey) {
          state.currentSegment = newCurrent(i, c, SegmentType.locatorSuffix);
        } else {
          state.currentSegment = newCurrent(i, c, SegmentType.suffix);
        }
        state.inSuffix = true;
        continue;
      }

      if (prev === '}' || prev === '{') {
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.suffix);
        state.inSuffix = true;
        continue;
      }

      if (seekState) {
        if (prev === ';' && state.semicolonAppendedAt !== i - 1) {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.prefix);
          state.inSuffix = false;
          continue;
        } else if (prev === '[' && state.bracketDepth === 1) {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.suffix);
          state.inSuffix = true;
          continue;
        }
      } else {
        if (prev === '[' || (prev === ';' && state.semicolonAppendedAt !== i - 1)) {
          endCurrent(i);
          state.currentSegment = newCurrent(i, c, SegmentType.prefix);
          continue;
        }
      }

      if (state.inKey) {
        endCurrent(i);
        state.currentSegment = newCurrent(i, c, SegmentType.suffix);
        state.inSuffix = true;
        state.inKey = false;
        state.seekingLocator = true;
        continue;
      }

      state.currentSegment.val += c;
      continue;
    }

    if (!state?.seekingSuffix) {
      state = null;
    }
  }

  if (state?.seekingSuffix) {
    segments.push(state.segment);
  }

  return segments;
}
