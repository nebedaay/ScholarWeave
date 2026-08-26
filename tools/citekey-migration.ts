/**
 * Citekey migration mapper (Task 3).
 *
 * Finds old/obsolete citekeys in the vault and maps them to the new
 * convention used in My Library (Better BibTeX):
 *   auth(15).lower.alphanum.nopunct + shorttitle(2,2).nopunct.alphanum + year
 *
 * Strategy per unresolved citekey:
 *   1. Apply the correction rules directly: strip punctuation, author <= 15,
 *      keep 2 title words, keep year. If the result exists in My Library,
 *      it's an auto-match.
 *   2. Otherwise flag for manual review (with context: which file/line).
 *
 * The mapping is REVIEW-ONLY: it never modifies files. It writes a
 * markdown report the user reviews before applying.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as readline from 'readline';

const VAULT = '/Users/josephhill/Documents/Obsidian Vault';
const CITEKEYS: Set<string> = new Set(
  Object.keys(JSON.parse(fs.readFileSync(path.join(__dirname, 'citekeys.json'), 'utf8')))
);

const EXCLUDED_PREFIXES = ['tmp/', '.obsidian/', '.opencode/', 'Attachments/', 'Templates/', 'AI OS/'];

function splitCitekey(k: string): { author: string; words: string[]; year: string } {
  // Author part may contain a hyphen ("al-saih") which is punctuation to
  // strip; normalize it to alnum BEFORE finding the title-word boundary.
  let head = k;
  // year = trailing digits+letters
  const m = /(\d+[a-z0-9]*)$/.exec(head);
  const year = m ? m[1] : '';
  if (m) head = head.slice(0, m.index);
  // author = leading lowercase run, allowing '-' (stripped after)
  const authorMatch = /^([a-z0-9-]+)/.exec(head);
  const author = authorMatch ? authorMatch[1].replace(/-/g, '') : '';
  const title = head.slice(authorMatch ? authorMatch[0].length : 0);
  const words = /[A-Z][a-z0-9]*/g;
  let w: RegExpExecArray | null;
  const found: string[] = [];
  while ((w = words.exec(title)) && found.length < 3) found.push(w[0]);
  return { author, words: found, year };
}

function applyRules(k: string): string {
  // Remove bracket-parentheticals in the author name but KEEP their content
  // ("inyas[niasse]" -> "inyasniasse" — the bracketed word is part of the
  // author's name, e.g. Niyās ibn Niasse; "kanun[ganun]" -> "kanunganun").
  const noBrackets = k.replace(/\[([^\]]*)\]/g, '$1');
  const { author, words, year } = splitCitekey(noBrackets);
  const author15 = author.replace(/[^a-z0-9]/g, '').slice(0, 15);
  const w2 = words.slice(0, 2).join('');
  return author15 + w2 + year;
}

function walkFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const rel = path.relative(VAULT, full).replace(/\\/g, '/');
    if (entry.isDirectory()) {
      if (EXCLUDED_PREFIXES.some((p) => rel.startsWith(p))) continue;
      out.push(...walkFiles(full));
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      out.push(full);
    }
  }
  return out;
}

// Find every citekey used in the vault (all forms), with file+line context.
function collectVaultCitekeys(): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>();
  const files = walkFiles(VAULT);
  for (const file of files) {
    if (file.endsWith('.bk')) continue;
    let content: string;
    try {
      content = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    const rel = path.relative(VAULT, file).replace(/\\/g, '/');
    const lines = content.split('\n');
    // Bracket-citekey family: @inyas[niasse]TanbihAlbintAlmuslimah1968
    const bracketRe = /(?:\[\[@|\[@|(?<![\w@[\]/|])@)([a-zA-Z][a-zA-Z0-9-]*\[[a-zA-Z]+\][a-zA-Z0-9-]*)/g;
    // Standard citekeys
    const re = /(?:\[\[@|\[@|(?<![\w@[\]/|])@)([a-zA-Z][a-zA-Z0-9_-]*)/g;
    lines.forEach((line, i) => {
      const add = (key: string) => {
        if (!map.has(key)) map.set(key, new Set());
        map.get(key)!.add(`${rel}:${i + 1}`);
      };
      let m: RegExpExecArray | null;
      bracketRe.lastIndex = 0;
      while ((m = bracketRe.exec(line))) {
        add(m[1]);
        // avoid double-adding via the generic regex: advance past
        re.lastIndex = Math.max(re.lastIndex, m.index + m[1].length);
      }
      while ((m = re.exec(line))) add(m[1]);
    });
  }
  return map;
}

function main() {
  const vaultKeys = collectVaultCitekeys();
  const unresolved = [...vaultKeys.keys()].filter((k) => !CITEKEYS.has(k)).sort();

  const autoMatched: { old: string; newKey: string; files: string[] }[] = [];
  const manual: { old: string; candidate: string | null; files: string[] }[] = [];

  for (const k of unresolved) {
    // skip obvious placeholders
    if (k === 'citekey' || k === 'linkingyourthinking' || k === 'OSMANLIDERGAH') {
      manual.push({ old: k, candidate: null, files: [...(vaultKeys.get(k) || [])] });
      continue;
    }
    const cand = applyRules(k);
    if (CITEKEYS.has(cand)) {
      autoMatched.push({ old: k, newKey: cand, files: [...(vaultKeys.get(k) || [])] });
    } else {
      manual.push({ old: k, candidate: cand, files: [...(vaultKeys.get(k) || [])] });
    }
  }

  // ── report ──
  let report = `# Citekey migration — review report\n\n`;
  report += `Generated ${new Date().toISOString()}. Old citekeys not found in My Library.\n\n`;
  report += `## Auto-matched (rule applied → new citekey exists in My Library)\n\n`;
  report += `| Old citekey | New citekey |\n|---|---|\n`;
  for (const a of autoMatched) report += `| \`${a.old}\` | \`${a.newKey}\` |\n`;

  report += `\n## Manual review needed\n\n`;
  report += `| Old citekey | Rule candidate (if any) |\n|---|---|\n`;
  for (const m of manual) {
    report += `| \`${m.old}\` | ${m.candidate ? '`' + m.candidate + '`' : '—'} |\n`;
  }

  report += `\n## File locations\n\n`;
  report += `<details><summary>Show files</summary>\n\n`;
  for (const a of [...autoMatched, ...manual]) {
    report += `**${a.old}** → ${'newKey' in a ? a.newKey : a.candidate ?? '—'}\n`;
    for (const f of a.files.slice(0, 8)) report += `  - ${f}\n`;
    if (a.files.length > 8) report += `  - … +${a.files.length - 8} more\n`;
    report += `\n`;
  }
  report += `</details>\n`;

  const outPath = path.join(VAULT, 'citekey-migration-report.md');
  fs.writeFileSync(outPath, report, 'utf8');
  console.log(`Auto-matched: ${autoMatched.length}`);
  console.log(`Manual review: ${manual.length}`);
  console.log(`Report written to ${path.relative(VAULT, outPath)}`);
}

main();
