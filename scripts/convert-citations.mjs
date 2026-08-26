#!/usr/bin/env node
/*
 * convert-citations.mjs — convert Obsidian citation wikilinks in a compiled
 * book markdown into standard pandoc citation syntax, using the EXACT same
 * parsing logic as the Linked Citations plugin (parser-bundle.mjs is the
 * plugin's parser.ts + locators.ts compiled by esbuild).
 *
 * What works in Obsidian (reading mode / live preview) therefore produces
 * the same citations when exported to docx: alias expansion, multi-work
 * containers, locator combining, suppress-author — all handled by the
 * plugin's own parser, not re-derived here.
 *
 * Conventions (mirroring src/parser/parser.ts):
 *   [[@key]]                    -> [@key]
 *   [[@key|alias]]              -> [<alias with @-tokens expanded>]
 *   [[@key|Smith's work]]       -> Smith's work [@key]  (pure text label:
 *                                 in Obsidian it stays a native link to the
 *                                 literature note; in the exported doc the
 *                                 wikilink would be lost, so the citation is
 *                                 attached — the ONE deliberate
 *                                 Obsidian-vs-export difference)
 *   [ [[@a]]; [[@b]] ]          -> [@a; @b]          (container, 1+ wiki)
 *   [ see also [[@a]]; [[@b]] ] -> [see also @a; @b]
 *   [ [[@a]]; [@b, p. 5] ]      -> [@a; @b, p. 5]    (mixed members)
 *   [[@key|@key -]]             -> [@key -]          (narrative)
 *   [[@key|-@key]]              -> [-@key]           (suppress author)
 *
 * Non-citation wikilinks [[note name]] are left untouched.
 *
 * Usage:
 *   node scripts/convert-citations.mjs <input.md> <output.md>
 */

import { expandAlias } from './parser-bundle.mjs';
import { readFileSync, writeFileSync } from 'fs';

// ── exact plugin regexes ─────────────────────────────────────────────────────

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

// ── container merge (mirrors parser.ts bracketContainers scan) ───────────────

/**
 * Rewrite outer-bracket containers "[ ... [[@k1]] ... [[@k2]] ... ]" into a
 * single merged pandoc citation, using the plugin's exact scanning logic and
 * linkRe. Any text between the outer brackets and the wikilinks (e.g. "see
 * also") is dropped per the plugin (it emits only the merged parts). Returns
 * the rewritten text.
 */
function rewriteContainers(str) {
  const containers = []; // { open, close, merged }
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
    const links = [];
    let lm;
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
      const mergedParts = [];
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

  // Emit: replace each container's source range with its merged form,
  // skipping any [[@…]] wikilinks that fall inside an emitted container.
  let out = '';
  let last = 0;
  let emittedUntil = -1;
  const isInside = (pos) => pos <= emittedUntil;
  let ci = 0;
  let m;
  SPECIAL_RE.lastIndex = 0;
  while ((m = SPECIAL_RE.exec(str))) {
    while (
      ci < containers.length &&
      containers[ci].open < m.index
    ) {
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
      // Pure text label (no citation material): in Obsidian this stays a
      // simple link to the literature note ("Smith’s work"); in an exported
      // document the reader cannot follow the wikilink, so the citation must
      // be attached: "Smith’s work [@key]" → renders "Smith’s work (2021)".
      out += alias + ' [@' + key + ']';
    } else {
      // Citation material (or plain [[@key]]): emit the expanded citation.
      out += '[' + expandAlias(aliasText, key) + ']';
    }
    last = m.index + full.length;
  }
  // Remaining containers after the last wikilink match.
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

// ── main ────────────────────────────────────────────────────────────────────

function convert(input, output) {
  const text = readFileSync(input, 'utf-8');
  const lines = text.split('\n');
  const outLines = lines.map((line) => {
    if (/\[\[@/.test(line) || /\[\[@/.test(line.replace(/\s/g, ''))) {
      return rewriteContainers(line);
    }
    return line;
  });
  writeFileSync(output, outLines.join('\n'), 'utf-8');
  console.log(`Converted citations: ${input} → ${output}`);
}

const [, , input, output] = process.argv;
if (!input || !output) {
  console.error('Usage: node scripts/convert-citations.mjs <input.md> <output.md>');
  process.exit(1);
}
convert(input, output);
