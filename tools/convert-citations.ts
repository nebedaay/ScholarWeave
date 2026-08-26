/**
 * Vault-wide citation converter: rewrite citations to the new link-based
 * system.
 *
 *   [@key]                ->  [[@key]]
 *   [@key, 6]             ->  [[@key|@@, 6]]          (suffix -> alias)
 *   [see @key]            ->  [[@key|see @@]]         (prefix -> alias)
 *   [see also @key, 10]   ->  [[@key|see also @@, 10]]
 *   [@a; @b]              ->  [ [[@a]]; [[@b]] ]
 *   [see @a, 10; @b]      ->  [ [[@a|see @@, 10]]; [[@b]] ]
 *   @key  (bare in prose) ->  [[@key]]
 *   [@key -] (narrative)  ->  [[@key|@@ -]]
 *
 * Only citations whose citekey resolves in Zotero are converted. Unresolved /
 * malformed citations are skipped and reported.
 *
 * Before modifying a file, a backup copy is written to "<path>.md.bk".
 *
 * Usage: node tools/convert-citations.js [--dry-run] [--sample N] [--dir FOLDER]
 */
import * as fs from 'fs';
import * as path from 'path';
import { getCitationSegments } from '../src/parser/parser';

// ── config ────────────────────────────────────────────────────────────────────

const VAULT = '/Users/josephhill/Documents/Obsidian Vault';
// citekeys.json lives next to the SOURCE file (tools/), which may be up a few
// dirs from the compiled output depending on outDir.
const CITEKEYS: Set<string> = new Set(
  Object.keys(
    JSON.parse(
      fs.readFileSync(
        path.join(__dirname, 'citekeys.json'),
        'utf8'
      )
    )
  )
);

// Folders/files to skip entirely.
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
const EXCLUDED_FILES = new Set(['README.md']);

interface Report {
  converted: number;
  skipped: { text: string; reason: string; file: string }[];
  filesChanged: number;
  filesScanned: number;
}

// ── helpers ────────────────────────────────────────────────────────────────────

function shouldSkipFile(rel: string): boolean {
  if (EXCLUDED_FILES.has(rel)) return true;
  if (EXCLUDED_SUFFIXES.some((s) => rel.endsWith(s))) return true;
  return EXCLUDED_PREFIXES.some((p) => rel.startsWith(p));
}

function stripFrontmatter(content: string): { body: string; frontmatter: string | null; rest: string } {
  if (!content.startsWith('---\n')) return { body: content, frontmatter: null, rest: '' };
  const m = /^---\n[\s\S]*?\n---\n?/.exec(content);
  if (!m) return { body: content, frontmatter: null, rest: '' };
  return {
    frontmatter: m[0],
    body: content.slice(m[0].length),
    rest: content.slice(m[0].length),
  };
}

// Escape regex chars
function escRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Build the alias text for ONE citation key from its parsed prefix/suffix.
// prefix + "@@" + suffix, trimmed; trailing-dash narrative kept in suffix.
function aliasFor(a: { prefix: string; suffix: string; narrative: boolean; suppressAuthor: boolean }): string {
  let alias = (a.prefix || '') + '@@' + (a.suffix || '');
  alias = alias.trim();
  if (!alias) alias = '@@';
  // suppress-author: "-@key" -> alias "-@@"
  if (a.suppressAuthor) alias = '-' + alias;
  return alias;
}

// Emit a single member: [[@key]] when the alias is just "@@", else [[@key|alias]].
function memberFor(key: string, a: { prefix: string; suffix: string; narrative: boolean; suppressAuthor: boolean }): string {
  const alias = aliasFor(a);
  return alias === '@@' ? `[[@${key}]]` : `[[@${key}|${alias}]]`;
}

// Parse a single bracketed citation "[...]" into {key, prefix, suffix, narrative}
// using the plugin parser segments.
interface ParsedCite {
  keys: string[];
  // per-key alias info
  aliases: { key: string; prefix: string; suffix: string; narrative: boolean; suppressAuthor: boolean }[];
  raw: string;
}

function parseBracket(raw: string): ParsedCite | null {
  const segs = getCitationSegments(raw, false, false);
  if (!segs.length) return null;
  // take the first group
  const group = segs[0];
  let current: { key: string; prefix: string; suffix: string; narrative: boolean; suppressAuthor: boolean } | null = null;
  const aliases: ParsedCite['aliases'] = [];
  let pendingPrefix = '';
  let pendingSuppress = false;

  for (const seg of group) {
    switch (seg.type) {
      case 'bracket':
        break;
      case 'suppressor':
        // "-@key" — suppress author. Applies to the NEXT key.
        pendingSuppress = true;
        break;
      case 'prefix':
        pendingPrefix += seg.val;
        break;
      case 'at':
        break;
      case 'key':
        {
          // Trim trailing sentence punctuation that may have been absorbed into
          // the key (e.g. "[@sukayrijAlkawkabAlwahhajnd.]" — the period is not
          // part of the citekey). Citekeys are alphanumeric.
          const cleaned = seg.val.replace(/[.,;:]+$/, '');
          current = {
            key: cleaned,
            prefix: pendingPrefix,
            suffix: '',
            narrative: false,
            suppressAuthor: pendingSuppress,
          };
          pendingPrefix = '';
          pendingSuppress = false;
          aliases.push(current);
        }
        break;
      case 'suffix':
        if (current) current.suffix += seg.val;
        else pendingPrefix += seg.val;
        break;
      case 'separator':
        // next cite
        pendingPrefix = '';
        pendingSuppress = false;
        break;
      default:
        break;
    }
  }

  if (!aliases.length) return null;
  return { keys: aliases.map((a) => a.key), aliases, raw };
}

// ── main rewrite ───────────────────────────────────────────────────────────────

function rewriteBody(body: string, report: Report, file: string): string {
  let out = body;
  let changed = false;

  // Protect fenced code blocks: mask them so no citation inside is touched,
  // then restore after rewriting.
  const codeBlocks: string[] = [];
  out = out.replace(/```[\s\S]*?```/g, (block) => {
    codeBlocks.push(block);
    return `\u0000CODE${codeBlocks.length - 1}\u0000`;
  });
  out = out.replace(/`[^`\n]+`/g, (inline) => {
    // inline code: also protect (e.g. `@scope/pkg`)
    codeBlocks.push(inline);
    return `\u0000CODE${codeBlocks.length - 1}\u0000`;
  });

  // 1. Bracketed citations [...]: match any single-level [...] (NOT starting
  //    with '[[', which is a wikilink) and let the plugin parser decide if
  //    it's a citation. Run BEFORE the bare-@key pass so keys inside brackets
  //    are not touched by it. The negative lookbehind prevents matching the
  //    inner "[@key|alias]" of an existing [[@key|alias]] wikilink. Markdown
  //    links "[text](url)" fail to parse as citations and are left alone.
  const bracketRe = /(?<!\[)\[(?!\[)[^\]\n]*\]/g;
  out = out.replace(bracketRe, (raw) => {
    // Must contain an '@' to be a candidate citation.
    if (!raw.includes('@')) return raw;
    // Skip emails like [name@example.com].
    if (/@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}/.test(raw)) return raw;
    // Skip already-converted new-syntax containers "[ [[@a]]; [[@b]] ]" and
    // any bracket that already contains a wikilink citation.
    if (/\[\[@/.test(raw)) return raw;
    const parsed = parseBracket(raw);
    if (!parsed) {
      report.skipped.push({ text: raw, reason: 'unparseable', file });
      return raw;
    }
    // All keys must resolve
    const unresolved = parsed.keys.filter((k) => !CITEKEYS.has(k));
    if (unresolved.length) {
      report.skipped.push({
        text: raw,
        reason: `unresolved: ${unresolved.join(', ')}`,
        file,
      });
      return raw;
    }
    changed = true;

    if (parsed.keys.length === 1) {
      const a = parsed.aliases[0];
      return memberFor(a.key, a);
    }

    // multi-work container: each member gets its own alias (prefix @@ suffix)
    const members = parsed.aliases.map((a) => memberFor(a.key, a));
    return `[ ${members.join('; ')} ]`;
  });

  // 2. Bare @citekey in prose (not inside [[...]] or [@...] already)
  //    Match @key where key is alphanumeric (no brackets/punct). Exclude
  //    emails (name@example.com), hashtags, URL handles (/@nikalie), and
  //    anything preceded by '|' (inside a wikilink alias) or '[' / ']'.
  //    A bare @key in prose is an in-text (narrative) citation, so its linked
  //    form is [[@key|@@ -]] — target @key, alias "@@ -" where @@ expands to
  //    the link's own citekey and the trailing dash = narrative flag. (NOT
  //    [[@key -]], which would link to a note literally named "@key -".)
  const bareRe = /(?<![\w@[\]/|])@([a-zA-Z][a-zA-Z0-9]{2,})(?![\w@.])/g;
  out = out.replace(bareRe, (m, key: string) => {
    if (!CITEKEYS.has(key)) {
      report.skipped.push({ text: m, reason: 'unresolved bare citekey', file });
      return m;
    }
    changed = true;
    return `[[@${key}|@@ -]]`;
  });

  // Restore protected code blocks / inline code.
  out = out.replace(/\u0000CODE(\d+)\u0000/g, (_, i: string) => codeBlocks[+i]);

  if (changed) report.converted += 1;
  return out;
}

// ── main ───────────────────────────────────────────────────────────────────────

function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const sampleIdx = args.findIndex((a) => a === '--sample');
  const sample = sampleIdx !== -1 ? parseInt(args[sampleIdx + 1], 10) : 0;
  const dirIdx = args.findIndex((a) => a === '--dir');
  const onlyDir = dirIdx !== -1 ? args[dirIdx + 1] : null;

  const report: Report = { converted: 0, skipped: [], filesChanged: 0, filesScanned: 0 };
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

  const toProcess = sample > 0 ? files.slice(0, sample) : files;

  for (const file of toProcess) {
    const rel = path.relative(VAULT, file).replace(/\\/g, '/');
    if (shouldSkipFile(rel)) continue;

    let content: string;
    try {
      content = fs.readFileSync(file, 'utf8');
    } catch {
      continue;
    }

    const { frontmatter, body } = stripFrontmatter(content);
    const newBody = rewriteBody(body, report, rel);
    if (newBody !== body) {
      const newContent = frontmatter ? frontmatter + newBody : newBody;
      if (!dryRun) {
        // backup
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

  console.log('=== CONVERSION REPORT ===');
  console.log(`files scanned: ${report.filesScanned}`);
  console.log(`files changed: ${report.filesChanged}${dryRun ? ' (dry run)' : ''}`);
  console.log(`citations converted: ${report.converted}`);
  console.log(`skipped: ${report.skipped.length}`);
  if (report.skipped.length) {
    console.log('\n--- skipped (need review) ---');
    const unique = new Map<string, { reason: string; files: Set<string> }>();
    for (const s of report.skipped) {
      const k = `${s.text} :: ${s.reason}`;
      if (!unique.has(k)) unique.set(k, { reason: s.reason, files: new Set() });
      unique.get(k)!.files.add(s.file);
    }
    for (const [k, v] of unique) {
      const [text, reason] = [k.split(' :: ')[0], k.split(' :: ')[1]];
      console.log(`  [${v.files.size} files] "${text}" — ${reason}`);
    }
  }
  if (!dryRun && changedFiles.length) {
    console.log('\n--- changed files ---');
    changedFiles.forEach((f) => console.log('  ' + f));
  }
}

main();
