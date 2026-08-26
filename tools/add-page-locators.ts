/**
 * Vault-wide bare-locator labeler: add "p. " to [[@key|alias]] wikilinks
 * whose citation alias ends with a BARE numeric locator.
 *
 *   [[@key|@@, 27]]        ->  [[@key|@@, p. 27]]
 *   [[@key|-@@, 269]]      ->  [[@key|-@@, p. 269]]
 *   [[@key|qtd. in @@, 61]] -> [[@key|qtd. in @@, p. 61]]
 *   [[@key|@@, 155–56]]    ->  [[@key|@@, p. 155–56]]   (ranges keep "p.",
 *                                matching the vault's existing convention)
 *   [[@key|@@, 1:159]]     ->  [[@key|@@, p. 1:159]]   (volume:page)
 *
 * Only aliases that are real citation aliases (contain @@ / -@@) are touched;
 * plain display labels like "(Knysh 2017, 1)" are left alone, as are aliases
 * that already carry a locator label (p., pp., chap., vol., §, …) or whose
 * trailing locator is letter-prefixed (S27, I19) or roman (xii, viii).
 *
 * Before modifying a file, a backup copy is written to "<path>.md.bk" (never
 * overwritten — idempotent across runs; .bk files are never re-processed).
 *
 * Usage: node tools/add-page-locators.js [--dry-run] [--dir FOLDER]
 */
import * as fs from 'fs';
import * as path from 'path';

const VAULT = '/Users/josephhill/Documents/Obsidian Vault';

const EXCLUDED_PREFIXES = [
  'tmp/',
  '.obsidian/',
  '.opencode/',
  '.trash/',
  'node_modules/',
  'Attachments/',
  'Templates/',
  'AI OS/',
];
const EXCLUDED_SUFFIXES = ['.bk', '.bak'];

// Trailing ", <numeric locator>": digits, digit ranges (–/-), volume:page.
const LOC_RE = /,\s+(\d+(?:\d|–|-)*\d?|\d+:\d+|\d+[–-]\d+)\s*$/;
// Alias must be a citation alias: contains @@ (possibly as -@@) or @key.
const ALIAS_HAS_AT = /@/;
// Already labeled: a locator label word immediately before the number.
const HAS_LABEL =
  /,\s*(?:p{1,2}\.?|chap\.?|ch\.?|sec\.?|s\.|vol\.?|no\.?|n\.?|§|para\.?|par\.?|page|pages|line|lines|f\.|ff\.|fol\.?|art\.?|pt\.?|book|bk\.?|at)\s+\d/iu;

interface Report {
  locatorsLabeled: number;
  filesChanged: number;
  filesScanned: number;
  skipped: { text: string; reason: string; file: string }[];
}

function shouldSkipFile(rel: string): boolean {
  return (
    EXCLUDED_SUFFIXES.some((s) => rel.endsWith(s)) ||
    EXCLUDED_PREFIXES.some((p) => rel.startsWith(p))
  );
}

function rewriteBody(body: string, report: Report, file: string): string {
  let changed = false;

  // Protect fenced + inline code so no wikilink inside is touched.
  const codeBlocks: string[] = [];
  let out = body.replace(/```[\s\S]*?```/g, (b) => {
    codeBlocks.push(b);
    return `\u0000CODE${codeBlocks.length - 1}\u0000`;
  });
  out = out.replace(/`[^`\n]+`/g, (b) => {
    codeBlocks.push(b);
    return `\u0000CODE${codeBlocks.length - 1}\u0000`;
  });

  const linkRe = /\[\[@([^|\]\s]+)\|([\s\S]*?)\]\]/g;
  out = out.replace(linkRe, (raw, key: string, aliasRaw: string) => {
    const alias = aliasRaw.trim();
    if (!ALIAS_HAS_AT.test(alias)) return raw;
    if (HAS_LABEL.test(alias)) return raw;
    // Match the trailing ", <locator>" directly on the raw alias text.
    const m = aliasRaw.match(
      /(,\s+)(\d+(?:\d|–|-)*\d?|\d+:\d+|\d+[–-]\d+)\s*$/
    );
    if (!m) return raw;
    const newAlias =
      aliasRaw.slice(0, m.index) + m[1] + 'p. ' + m[2];
    changed = true;
    report.locatorsLabeled += 1;
    return `[[@${key}|${newAlias}]]`;
  });

  // Restore protected code.
  out = out.replace(/\u0000CODE(\d+)\u0000/g, (_, i: string) => codeBlocks[+i]);

  return out;
}

function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const dirIdx = args.findIndex((a) => a === '--dir');
  const onlyDir = dirIdx !== -1 ? args[dirIdx + 1] : null;

  const report: Report = { locatorsLabeled: 0, filesChanged: 0, filesScanned: 0, skipped: [] };
  const changedFiles: string[] = [];

  function walk(dir: string): string[] {
    const results: string[] = [];
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      const rel = path.relative(VAULT, full).replace(/\\/g, '/');
      if (entry.isDirectory()) {
        if (EXCLUDED_PREFIXES.some((p) => rel.startsWith(p))) continue;
        results.push(...walk(full));
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        results.push(full);
      }
    }
    return results;
  }

  const targetDir = onlyDir ? path.join(VAULT, onlyDir) : VAULT;
  const files = walk(targetDir);
  report.filesScanned = files.length;

  for (const file of files) {
    const rel = path.relative(VAULT, file).replace(/\\/g, '/');
    if (shouldSkipFile(rel)) continue;

    let content: string;
    try {
      content = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }

    // Never touch frontmatter (YAML props like up/related could contain
    // [[@key|alias]] links too, but frontmatter edits are risky — leave it).
    const fm = /^---\n[\s\S]*?\n---\n?/.exec(content);
    const fmText = fm ? fm[0] : '';
    const body = fm ? content.slice(fm[0].length) : content;

    const newBody = rewriteBody(body, report, rel);
    if (newBody !== body) {
      const newContent = fmText + newBody;
      if (!dryRun) {
        const bkPath = file + '.bk';
        if (!fs.existsSync(bkPath)) {
          fs.writeFileSync(bkPath, content, 'utf8');
        }
        fs.writeFileSync(file, newContent, 'utf8');
      }
      report.filesChanged += 1;
      changedFiles.push(rel);
    }
  }

  console.log('=== PAGE-LOCATOR LABEL REPORT ===');
  console.log(`files scanned: ${report.filesScanned}`);
  console.log(`files changed: ${report.filesChanged}${dryRun ? ' (dry run)' : ''}`);
  console.log(`aliases labeled: ${report.locatorsLabeled}`);
  console.log(`skipped: ${report.skipped.length}`);

  if (changedFiles.length && !dryRun) {
    console.log('\n--- changed files ---');
    changedFiles.forEach((f) => console.log('  ' + f));
  }
}

main();
