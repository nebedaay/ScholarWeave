/**
 * In-process citation converter (the TypeScript twin of
 * scripts/convert-citations.mjs).
 *
 * The plugin converts linked-citation wikilinks to pandoc syntax BEFORE
 * pandoc runs. Historically this was spawned as `node convert-citations.mjs`;
 * doing it here instead means the plugin needs no external Node.js runtime
 * (the CLI still runs the .mjs — see `--citations-input` in
 * DocumentCompiler.py). Both use the SAME parser (`expandAlias`), so they
 * must stay behaviourally identical.
 *
 *   [[@key]]                    -> [@key]
 *   [[@key|alias]]              -> [<alias with @-tokens expanded>]
 *   [[@key|Smith's work]]       -> Smith's work [@key]   (pure text label)
 *   [ [[@a]]; [[@b]] ]          -> [@a; @b]              (container)
 *   [[@key|@key -]]             -> [@key -]              (narrative)
 *   [[@key|-@key]]              -> [-@key]               (suppress author)
 *
 * Non-citation wikilinks ([[note name]]) are left untouched.
 */
import { expandAlias } from './parser/parser';

// Matches [[@key|alias]] / [[@key]] / ⟦ (from transformLinkAliases specialRe).
const SPECIAL_RE = new RegExp(
  '\\[\\[@([^|\\]\\s]+)\\|([\\s\\S]*?)\\]\\]|' +
    '\\[\\[@([^|\\]\\s]+)\\]\\]|' +
    '\u27e6',
  'g'
);

// Container member: [[@key|alias]] OR plain [@key, suffix] (plugin linkRe).
const LINK_RE =
  /\[\[@([^|\]\s]+)(?:\|([\s\S]*?))?\]\]|\[@([^\]\s,;]+)([^\]]*)\]/g;

/**
 * Rewrite outer-bracket containers "[ ... [[@k1]] ... [[@k2]] ... ]" into a
 * single merged pandoc citation, using the plugin's exact scanning logic and
 * linkRe. Any text between the outer brackets and the wikilinks (e.g. "see
 * also") is dropped per the plugin (it emits only the merged parts).
 */
function rewriteContainers(str: string): string {
  const containers: { open: number; close: number; merged: string }[] = [];
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
        depth++;
        i++;
      } else if (str[i] === '[' && str[i + 1] !== '[') {
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
    const links: { key: string; alias: string | undefined }[] = [];
    let lm: RegExpExecArray | null;
    LINK_RE.lastIndex = 0;
    while ((lm = LINK_RE.exec(inside))) {
      if (lm[1] !== undefined) {
        links.push({ key: lm[1], alias: lm[2] });
      } else {
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
      containers.push({
        open,
        close,
        merged: '[' + mergedParts.join('; ') + ']',
      });
      scan = close + 1;
      continue;
    }
    scan = open + 1;
  }

  // Emit: replace each container's source range with its merged form, skipping
  // any [[@…]] wikilinks that fall inside an emitted container.
  let out = '';
  let last = 0;
  let emittedUntil = -1;
  const isInside = (pos: number) => pos <= emittedUntil;
  let ci = 0;
  let m: RegExpExecArray | null;
  SPECIAL_RE.lastIndex = 0;
  while ((m = SPECIAL_RE.exec(str))) {
    while (ci < containers.length && containers[ci].open < m.index) {
      const c = containers[ci];
      if (c.open > emittedUntil) {
        out += str.slice(last, c.open);
        out += c.merged;
        last = c.close + 1;
        emittedUntil = c.close;
      }
      ci++;
    }
    if (isInside(m.index)) continue;
    // Standalone wikilink — emit the alias-expanded citation.
    out += str.slice(last, m.index);
    const full = m[0];
    const key = m[1] ?? m[3];
    const alias = m[2];
    const aliasText = alias ?? '@' + key;
    if (alias !== undefined && !/@/.test(alias)) {
      // Pure text label (no citation material): in Obsidian this stays a simple
      // link to the literature note; an exported document can't follow the
      // wikilink, so the citation is attached: "Smith's work [@key]".
      out += alias + ' [@' + key + ']';
    } else {
      out += '[' + expandAlias(aliasText, key) + ']';
    }
    last = m.index + full.length;
  }
  while (ci < containers.length) {
    const c = containers[ci];
    if (c.open > emittedUntil) {
      out += str.slice(last, c.open);
      out += c.merged;
      last = c.close + 1;
      emittedUntil = c.close;
    }
    ci++;
  }
  out += str.slice(last);
  return out;
}

/** Convert linked-citation wikilinks in `text` to pandoc citation syntax. */
export function convertCitationsInText(text: string): string {
  const lines = text.split('\n');
  const outLines = lines.map((line) =>
    /\[\[@/.test(line) ? rewriteContainers(line) : line
  );
  return outLines.join('\n');
}
