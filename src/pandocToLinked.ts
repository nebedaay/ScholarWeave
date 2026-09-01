import { Notice, TFile } from 'obsidian';
import type ReferenceList from './main';
import { getCitationSegments } from 'src/parser/parser';

// ── config ────────────────────────────────────────────────────────────────────

interface CiteAlias {
  key: string;
  prefix: string;
  suffix: string;
  narrative: boolean;
  suppressAuthor: boolean;
}

interface ParsedCite {
  keys: string[];
  aliases: CiteAlias[];
  raw: string;
}

export interface ConversionReport {
  converted: number;
  skipped: { text: string; reason: string }[];
}

// Escape regex chars
function escRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Build the alias text for ONE citation key from its parsed prefix/suffix.
// prefix + "@" + suffix, trimmed; trailing-dash narrative kept in suffix.
// The lone "@" is the plugin's bare-@ proxy: it expands to the link's own
// citekey, so "[[@key|@, p. 6]]" renders as "(Key 2020, 6)". Matches the
// vault-wide convention (aliases were converted from "@@" to "@" Aug 2026).
function aliasFor(a: CiteAlias): string {
  let alias = (a.prefix || '') + '@' + (a.suffix || '');
  alias = alias.trim();
  if (!alias) alias = '@';
  // suppress-author: "-@key" -> alias "-@"
  if (a.suppressAuthor) alias = '-' + alias;
  return alias;
}

// Emit a single member: [[@key]] when the alias is just "@", else [[@key|alias]].
function memberFor(key: string, a: CiteAlias): string {
  const alias = aliasFor(a);
  return alias === '@' ? `[[@${key}]]` : `[[@${key}|${alias}]]`;
}

// Parse a single bracketed citation "[...]" into {key, prefix, suffix, narrative}
// using the plugin parser segments.
function parseBracket(raw: string): ParsedCite | null {
  const segs = getCitationSegments(raw, false, false);
  if (!segs.length) return null;
  // take the first group
  const group = segs[0];
  let current: CiteAlias | null = null;
  const aliases: CiteAlias[] = [];
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
      case 'locatorSuffix':
        // ", " / " " before a locator or its label — append verbatim so
        // simple locators survive: [@key, 6] -> [[@key|@@, 6]]
        if (current) current.suffix += seg.val;
        else pendingPrefix += seg.val;
        break;
      case 'locator':
        if (current) current.suffix += seg.val;
        else pendingPrefix += seg.val;
        break;
      case 'locatorLabel':
        // Only append the label when it literally appears in the source.
        // The parser INFERS a default label for bare-number locators
        // ("page" for "[@key, 6]") — emitting that would rewrite ", 6"
        // to ", 6page". Check the raw bracket text before appending.
        if (seg.val && raw.includes(seg.val)) {
          if (current) current.suffix += seg.val;
          else pendingPrefix += seg.val;
        }
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

/**
 * Rewrite pandoc citations in `body` to linked citations, resolving keys
 * against `resolvable` (a Set of citekeys known to the plugin's Zotero/bib
 * index). Returns the rewritten body plus a report of what was converted and
 * what was skipped (and why).
 *
 * When `allowUnresolved` is true the resolution check is skipped and all
 * syntactically valid citations are converted regardless of whether their
 * keys appear in the bib index.  Use this for the import pipeline where the
 * source is trusted (the file just came from Zotero).
 */
export function rewritePandocToLinked(
  body: string,
  resolvable: Set<string>,
  allowUnresolved = false
): { out: string; report: ConversionReport } {
  const report: ConversionReport = { converted: 0, skipped: [] };
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
      report.skipped.push({ text: raw, reason: 'unparseable' });
      return raw;
    }
    // All keys must resolve (unless allowUnresolved — e.g. import from Zotero).
    if (!allowUnresolved) {
      const unresolved = parsed.keys.filter((k) => !resolvable.has(k));
      if (unresolved.length) {
        report.skipped.push({
          text: raw,
          reason: `unresolved: ${unresolved.join(', ')}`,
        });
        return raw;
      }
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
  //    form is [[@key|@ -]] — target @key, alias "@ -" where @ expands to
  //    the link's own citekey and the trailing dash = narrative flag. (NOT
  //    [[@key -]], which would link to a note literally named "@key -".)
  const bareRe = /(?<![\w@[\]/|])@([a-zA-Z][a-zA-Z0-9]{2,})(?![\w@.])/g;
  out = out.replace(bareRe, (m, key: string) => {
    if (!resolvable.has(key)) {
      report.skipped.push({ text: m, reason: 'unresolved bare citekey' });
      return m;
    }
    changed = true;
    return `[[@${key}|@ -]]`;
  });

  // Restore protected code blocks / inline code.
  out = out.replace(/\u0000CODE(\d+)\u0000/g, (_, i: string) => codeBlocks[+i]);

  if (changed) report.converted += 1;
  return { out, report };
}

/**
 * Run the conversion on every markdown file in the vault.  Resolves citekeys
 * against the plugin's live Zotero/bib index, writes a "<file>.bk" backup for
 * each file changed (only when no backup exists yet), and shows a summary
 * Notice when done.
 */
export async function convertVault(
  plugin: ReferenceList
): Promise<void> {
  const resolvable = new Set(plugin.bibManager.bibCache.keys());
  if (!resolvable.size) {
    new Notice('No bibliography loaded — cannot resolve citekeys.', 6000);
    return;
  }

  const files = plugin.app.vault.getMarkdownFiles().filter(
    (f) => !f.path.endsWith('.bk') && !f.path.endsWith('.bk.md')
  );

  const progress = new Notice(`Converting citations across ${files.length} files…`, 0);
  let convertedFiles = 0;
  let totalSkipped = 0;

  try {
    for (const file of files) {
      const content = await plugin.app.vault.read(file);

      let body = content;
      let frontmatter = '';
      const fm = /^---\n[\s\S]*?\n---\n?/.exec(content);
      if (fm) {
        frontmatter = fm[0];
        body = content.slice(fm[0].length);
      }

      const { out, report } = rewritePandocToLinked(body, resolvable);
      totalSkipped += report.skipped.length;
      if (out === body) continue;

      const bkPath = `${file.path}.bk`;
      if (!(await plugin.app.vault.adapter.exists(bkPath))) {
        await plugin.app.vault.adapter.write(bkPath, content);
      }
      await plugin.app.vault.modify(file, frontmatter + out);
      convertedFiles++;

      if (report.skipped.length) {
        console.warn(
          `[scholar-weave] skipped in ${file.path}:`,
          report.skipped.map((s) => `${s.text} (${s.reason})`)
        );
      }
    }
  } finally {
    progress.hide();
  }

  const skippedNote = totalSkipped > 0
    ? `\nSkipped ${totalSkipped} citations (unresolved/unparseable — see console)`
    : '';
  new Notice(
    convertedFiles > 0
      ? `Converted citations in ${convertedFiles} file${convertedFiles !== 1 ? 's' : ''}.${skippedNote}`
      : `No pandoc citations found in vault.`,
    6000
  );
}

/**
 * Run the conversion on the active note. Resolves citekeys against the
 * plugin's live Zotero/bib index (bibCache), writes a "<file>.bk" backup on
 * first conversion, and reports counts + skipped items in a Notice.
 *
 * Pass `allowUnresolved: true` to skip the resolution check (used by the
 * import pipeline where citations are trusted to be valid Zotero keys).
 */
export async function convertActiveNote(
  plugin: ReferenceList,
  file: TFile,
  { allowUnresolved = false }: { allowUnresolved?: boolean } = {}
): Promise<ConversionReport> {
  const content = await plugin.app.vault.read(file);

  const resolvable = new Set(plugin.bibManager.bibCache.keys());
  if (!allowUnresolved && !resolvable.size) {
    new Notice('No bibliography loaded — cannot resolve citekeys.', 6000);
    return { converted: 0, skipped: [] };
  }

  // Keep frontmatter untouched; convert only the body.
  let body = content;
  let frontmatter = '';
  const fm = /^---\n[\s\S]*?\n---\n?/.exec(content);
  if (fm) {
    frontmatter = fm[0];
    body = content.slice(fm[0].length);
  }

  const { out, report } = rewritePandocToLinked(body, resolvable, allowUnresolved);
  if (out === body) {
    new Notice(`No pandoc citations found in ${file.basename}.`, 4000);
    return report;
  }

  // Backup first (only if no .bk exists yet).
  const bkPath = `${file.path}.bk`;
  if (!(await plugin.app.vault.adapter.exists(bkPath))) {
    await plugin.app.vault.adapter.write(bkPath, content);
  }

  await plugin.app.vault.modify(file, frontmatter + out);

  const skippedNote =
    report.skipped.length > 0
      ? `\nSkipped ${report.skipped.length} (unresolved/unparseable — see console)`
      : '';
  new Notice(
    `Converted citations in ${file.basename}.${skippedNote}`,
    6000
  );
  if (report.skipped.length) {
    console.warn(
      '[scholar-weave] skipped pandoc citations:',
      report.skipped.map((s) => `${s.text} (${s.reason})`)
    );
  }
  return report;
}
