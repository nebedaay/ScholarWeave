import { TFile } from 'obsidian';
import type ReferenceList from './main';
import type { ExportFormat } from './exportModal';
import type { StyleMapping } from './settings';
import { findPandoc } from './bib/pandoc';
import { convertCitationsInText } from './convertCitations';
import { findPython3, findNode } from './tools';

// esbuild outputs this file in CJS format where `require` is available at
// runtime, but TypeScript's project-level `module: ESNext` doesn't declare it.
// We use require() instead of dynamic import() for the same reason as
// src/bib/pandoc.ts — esbuild 0.13.x leaves import() of external modules
// verbatim in the CJS bundle, which fails in Electron's renderer.
declare const require: (id: string) => any;

function execFileAsync(
  file: string,
  args: string[],
  options?: { env?: Record<string, string | undefined> }
): Promise<{ stdout: string; stderr: string }> {
  const { execFile } = require('child_process') as typeof import('child_process');
  const { promisify } = require('util') as typeof import('util');
  return promisify(execFile)(file, args, {
    maxBuffer: 50 * 1024 * 1024,
    ...options,
  });
}

/** Absolute path to the plugin's bundled scripts/ directory (desktop). */
function pluginScriptsDir(plugin: ReferenceList): string | null {
  const adapter = plugin.app.vault.adapter as any;
  if (typeof adapter?.getBasePath !== 'function') return null; // mobile
  const base = adapter.getBasePath() as string;
  const dir = plugin.manifest.dir;
  if (!dir) return null;
  return `${base}/${dir}/scripts`;
}

/** Expand a leading ~ or ~/ in a user-supplied path to the home directory. */
function expandTilde(p: string): string {
  if (p === '~') return require('os').homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return require('os').homedir() + p.slice(1);
  }
  return p;
}

// findPython3 / findNode now live in src/tools.ts (shared with probeTools()).

export interface CompileResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  /** Absolute path of the produced file (last stdout line on success). */
  outputPath?: string;
}

export interface CompilerOptions {
  /** Output format: 'md' = compile only (no pandoc); 'docx', 'odt', 'latex', or 'pdf' = export. */
  format: ExportFormat;
  /** Template name chosen in the modal (no extension). Overrides frontmatter. */
  template?: string;
  /** Explicit TOC choice (checkbox). Overrides template-aware default. */
  toc: boolean;
  /** Explicit table-of-figures choice (checkbox). Only rendered when the doc has figures. */
  tof: boolean;
  /** true = restart footnote numbering at each top-level heading; false = continuous. */
  restartFootnotes: boolean;
  /** true = top-level headings start on a new page. */
  newPageHeadings: boolean;
  /** true = insert today's date on the cover when the note has no `date:` property. */
  generatedDate: boolean;
  /** true = roman frontmatter page numbers, switching to arabic at the reset heading. */
  romanFrontmatter: boolean;
  /** Heading text that begins arabic 'page 1'. '' = auto. */
  romanStart: string;
  /** Output folder (vault-relative or absolute, ~ expanded); '' = same folder as source. */
  outputDir?: string;
  /** Desired output filename (basename.ext). When set and different from the
   *  compiler's default name, the output file is renamed after compilation. */
  outputFilename?: string;
  /** PDF only: keep the intermediate docx/odt/tex after PDF conversion. The
   *  intermediate format itself is NOT user-selectable — it's always the
   *  format of the chosen template (auto-detected by DocumentCompiler.py). */
  keepIntermediate?: boolean;
  /** Non-md formats only: keep the compiled markdown DocumentCompiler
   *  produces from an outline, instead of deleting it once export is done. */
  keepIntermediateMd?: boolean;
  /** IDs of StyleMappings to apply on this export (subset of settings.styleMappings). */
  enabledMappingIds?: string[];
  /** Export dialog: apply an explicit citation style, overriding the
   *  template's own. undefined = dialog not used (CLI defaults apply);
   *  false = use the template's style / global default; true = use `cslStyle`. */
  overrideCslStyle?: boolean;
  /** The style to apply when overrideCslStyle is true: a Zotero style name,
   *  a .csl path, or a URL. */
  cslStyle?: string;
  /** When true, leave citations as literal text (no Zotero fields, no
   *  --citeproc). Set by the export modal when the user chooses to proceed
   *  without Zotero. Applies to DOCX/ODT only. */
  rawCitations?: boolean;
  /** Path to a CSL-JSON bibliography to render citations statically from
   *  (instead of live Zotero fields / fetching Zotero). Set by the export
   *  modal when Zotero is unavailable. DOCX/ODT only. */
  staticBibliography?: string;
}

/** Convert a user-supplied folder (vault-relative, absolute, or ~) to an
 *  absolute path. Returns '' when empty. */
function resolveFolder(
  folder: string | undefined,
  vaultBase: string
): string {
  const f = (folder ?? '').trim();
  if (!f) return '';
  const expanded = expandTilde(f);
  if (expanded.startsWith('/')) return expanded;
  return `${vaultBase}/${expanded}`;
}

/**
 * Run the bundled DocumentCompiler.py (compile outline → markdown, and with
 * `export: true` → docx / odt / latex / pdf) on the given note. Desktop only.
 *
 * The interpreter is resolved here (with lxml/docx verification) and the
 * resolved python/node/pandoc paths are handed to the script via SW_* env
 * vars, because Electron's renderer doesn't inherit the shell PATH.
 */
export async function runDocumentCompiler(
  plugin: ReferenceList,
  file: TFile,
  opts: CompilerOptions
): Promise<CompileResult> {
  const scriptsDir = pluginScriptsDir(plugin);
  if (!scriptsDir) {
    return {
      ok: false,
      stdout: '',
      stderr: 'Book export requires the desktop app (filesystem access).',
    };
  }

  const py = await findPython3(plugin.settings.pathToPython ?? '');
  if (!py) {
    return {
      ok: false,
      stdout: '',
      stderr:
        'Python 3 (with lxml and python-docx) not found. Install it ' +
        '(python.org or Homebrew: `pip install lxml python-docx`), then set ' +
        'its path in the plugin settings.',
    };
  }

  const adapter = plugin.app.vault.adapter as any;
  const vaultBase = adapter.getBasePath() as string;
  const absMaster = `${vaultBase}/${file.path}`;
  const isExport = opts.format !== 'md';

  // Template name: modal selection takes priority over frontmatter.
  // Fall back to frontmatter for callers that don't supply opts.template.
  let templateName = opts.template ?? '';
  if (!templateName) {
    const cache = plugin.app.metadataCache.getFileCache(file);
    const rawTpl = (cache?.frontmatter as Record<string, unknown> | undefined)
      ?.template;
    templateName =
      typeof rawTpl === 'string' ? rawTpl.replace(/\.(docx|odt|tex)$/i, '') : '';
  }

  const outputDir = resolveFolder(opts.outputDir, vaultBase);
  const buildArgs = (input: string, citationsInput?: string): string[] => {
  const args = [input];
  if (isExport) {
    args.push('--export');
    args.push('--format', opts.format); // 'docx', 'odt', or 'pdf'
    if (opts.format === 'pdf' && opts.keepIntermediate) {
      args.push('--keep-intermediate');
    }
    if (opts.keepIntermediateMd) args.push('--keep-compiled-md');
  }
  args.push(opts.toc ? '--toc' : '--no-toc');
  args.push(opts.tof ? '--list-of-figures' : '--no-list-of-figures');
  args.push(opts.restartFootnotes ? '--no-global-footnotes' : '--global-footnotes');
  args.push(opts.newPageHeadings ? '--new-page-headings' : '--no-new-page-headings');
  if (!opts.generatedDate) args.push('--no-generated-date');
  if (opts.romanFrontmatter) {
    args.push('--roman-frontmatter');
    if (opts.romanStart) args.push('--page1-starts-with', opts.romanStart);
  }
  if (isExport && opts.overrideCslStyle === true && opts.cslStyle) {
    args.push('--csl-style', opts.cslStyle);
  } else if (isExport && opts.overrideCslStyle === false) {
    args.push('--csl-style-from-template');
  }
  // Chosen when the user proceeds without Zotero: leave citations literal
  // rather than emitting (and failing to populate) live Zotero fields.
  if (isExport && opts.rawCitations &&
      (opts.format === 'docx' || opts.format === 'odt')) {
    args.push('--raw-citations');
  }
  if (isExport && opts.staticBibliography &&
      (opts.format === 'docx' || opts.format === 'odt')) {
    args.push('--static-bibliography', opts.staticBibliography);
  }

  // Pass the Obsidian account display name as a fallback author so the
  // merge script can set dc:creator even when `author:` is absent from YAML.
  const accountName: string | undefined =
    (plugin.app as any).account?.name ?? undefined;
  if (accountName) args.push('--default-author', accountName);

  // Always pass --templates-dir; fall back to <vault>/Export Templates/ when
  // the setting is empty so Python doesn't have to guess the vault root.
  const templateDir = resolveFolder(
    plugin.settings.exportTemplatesDir || 'Export Templates',
    vaultBase
  );
  args.push('--templates-dir', templateDir);
  if (templateName) args.push('--template', templateName);
  if (outputDir) args.push('--output-dir', outputDir);
  // Let the script write the final filename directly — generating the
  // default-named file first and renaming it clobbers an existing export the
  // user keeps under the note's own name.
  if (opts.outputFilename) {
    args.push('--output-name', opts.outputFilename);
  }

  // Resolve enabled style mappings and pass as JSON.
  if (isExport && opts.enabledMappingIds && opts.enabledMappingIds.length > 0) {
    const allMappings: StyleMapping[] = plugin.settings.styleMappings ?? [];
    const enabledSet = new Set(opts.enabledMappingIds);
    const activeMappings = allMappings
      .filter(m => enabledSet.has(m.id) && m.source && m.styleName)
      .map(m => ({ source: m.source, styleName: m.styleName }));
    if (activeMappings.length > 0) {
      args.push('--mappings', JSON.stringify(activeMappings));
    }
  }

  if (citationsInput) args.push('--citations-input', citationsInput);
  return args;
  };

  const script = `${scriptsDir}/DocumentCompiler.py`;
  // Pass resolved tool paths through so the script doesn't depend on PATH,
  // and the vault root so the script never has to guess it.
  const baseEnv = (globalThis.process?.env ?? {}) as Record<string, string>;
  const env: Record<string, string | undefined> = { ...baseEnv, SW_PYTHON: py };
  {
    const a = plugin.app.vault.adapter as any;
    if (typeof a?.getBasePath === 'function') env.SW_VAULT = a.getBasePath();
  }
  // Zotero styles folder + the plugin's configured live-render style, so the
  // Python side can resolve bare style names and use the same fallback the
  // editor uses when a template carries no style.
  if (plugin.settings.zoteroDataDir) {
    env.SW_ZOTERO_DIR = plugin.settings.zoteroDataDir;
  }
  {
    const configured =
      plugin.settings.cslStylePath || plugin.settings.cslStyleURL || '';
    if (configured) env.SW_DEFAULT_CSL = configured;
  }
  const execCompiler = (args: string[]) =>
    execFileAsync(py, [script, ...args], { env });

  const toResult = (res: { stdout: string; stderr: string }): CompileResult => {
    const rawOutputPath = res.stdout.trim().split('\n').pop() ?? '';
    return {
      ok: true,
      stdout: res.stdout,
      stderr: res.stderr,
      outputPath: rawOutputPath || undefined,
    };
  };
  const toError = (e: any): CompileResult => ({
    ok: false,
    stdout: e?.stdout || '',
    stderr: (e?.stderr || String(e?.message ?? e)).trim(),
  });

  if (!isExport) {
    try {
      return toResult(await execCompiler(buildArgs(absMaster)));
    } catch (e) {
      return toError(e);
    }
  }

  const pandoc = plugin.settings.pathToPandoc?.trim() || (await findPandoc());
  if (!pandoc) {
    return {
      ok: false,
      stdout: '',
      stderr: 'Pandoc not found. Set its path in the plugin settings.',
    };
  }
  env.SW_PANDOC = pandoc;

  // Preferred path: convert citations IN-PROCESS, so no external Node.js
  // runtime is needed:
  //   1. Python compiles (if the input is an outline) and prints the markdown
  //      path (--prepare-convert).
  //   2. We convert the citation wikilinks here with the plugin's own parser.
  //   3. Python exports from that converted markdown (--citations-input).
  // If any step fails, fall back to the external Node.js converter.
  try {
    const fs = require('fs') as typeof import('fs');
    const prepArgs = [
      '--prepare-convert',
      opts.restartFootnotes ? '--no-global-footnotes' : '--global-footnotes',
    ];
    if (outputDir) prepArgs.push('--output-dir', outputDir);
    if (templateName) prepArgs.push('--template', templateName);
    const prep = await execCompiler([absMaster, ...prepArgs]);
    const mdPath = (prep.stdout.trim().split('\n').pop() || '').trim();
    if (!mdPath || !fs.existsSync(mdPath)) {
      throw new Error('prepare-convert did not return a usable markdown path');
    }
    const converted = convertCitationsInText(fs.readFileSync(mdPath, 'utf-8'));
    const convPath = `${mdPath}.swcitations.md`;
    fs.writeFileSync(convPath, converted, 'utf-8');
    try {
      return toResult(await execCompiler(buildArgs(mdPath, convPath)));
    } finally {
      try { fs.unlinkSync(convPath); } catch { /* ignore */ }
      if (mdPath !== absMaster && !opts.keepIntermediateMd) {
        try { fs.unlinkSync(mdPath); } catch { /* ignore */ }
      }
    }
  } catch (e) {
    // Fallback: the external Node.js converter (historical path).
    const node = await findNode();
    if (!node) return toError(e);
    env.SW_NODE = node;
    try {
      return toResult(await execCompiler(buildArgs(absMaster)));
    } catch (e2) {
      return toError(e2);
    }
  }
}
