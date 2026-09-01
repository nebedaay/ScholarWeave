import { TFile } from 'obsidian';
import type ReferenceList from './main';
import type { ExportFormat } from './exportModal';
import type { StyleMapping } from './settings';
import { findPandoc } from './bib/pandoc';

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

/**
 * Resolve the Python 3 interpreter, VERIFYING it can import the modules the
 * pipeline needs (lxml, python-docx). Electron's renderer doesn't inherit the
 * shell PATH — `python3` often resolves to macOS's CLT build (3.9, no lxml)
 * while a Homebrew/python.org build with the required packages sits at a
 * known location. Explicit setting wins; otherwise we test PATH candidates
 * and known locations in order, returning the first that imports cleanly.
 */
async function findPython3(configured: string): Promise<string | null> {
  if (configured.trim()) return configured.trim();
  const { execFile } = require('child_process') as typeof import('child_process');
  const { promisify } = require('util') as typeof import('util');
  const execAsync = promisify(execFile);
  const platform = globalThis.process?.platform;

  const probe = async (p: string): Promise<boolean> => {
    try {
      await execAsync(p, [
        '-c',
        'import lxml, docx; import sys; sys.exit(0)',
      ]);
      return true;
    } catch {
      return false;
    }
  };

  // PATH candidates first (respects a user-visible install on Windows too).
  const candidates: string[] = [];
  if (platform === 'win32') {
    candidates.push('py', 'python', 'python3');
  } else {
    candidates.push('python3');
  }
  candidates.push(
    ...(platform === 'win32'
      ? [
          'C:\\Python313\\python.exe',
          'C:\\Python312\\python.exe',
          'C:\\Python311\\python.exe',
        ]
      : [
          '/opt/homebrew/bin/python3', // Apple Silicon Homebrew
          '/usr/local/bin/python3',    // Intel Homebrew / python.org
          '/usr/bin/python3',          // macOS CLT build (often no lxml)
        ])
  );

  for (const p of candidates) {
    if (await probe(p)) return p;
  }
  return null;
}

/** Resolve the node binary (used by convert-citations.mjs). Same PATH
 *  problem as python/pandoc: Electron doesn't inherit the shell PATH, so we
 *  try `which node` then common install locations. */
async function findNode(): Promise<string | null> {
  const { execFile } = require('child_process') as typeof import('child_process');
  const { promisify } = require('util') as typeof import('util');
  const execAsync = promisify(execFile);
  const platform = globalThis.process?.platform;

  const probe = async (p: string): Promise<boolean> => {
    try {
      await execAsync(p, ['--version']);
      return true;
    } catch {
      return false;
    }
  };

  const candidates =
    platform === 'win32'
      ? [
          'node',
          'C:\\Program Files\\nodejs\\node.exe',
          `${process.env.APPDATA ?? ''}\\nvm\\node.exe`,
        ]
      : [
          'node',
          '/opt/homebrew/bin/node', // Apple Silicon Homebrew
          '/usr/local/bin/node',    // Intel Homebrew / nodejs.org
          '/usr/bin/node',
        ];

  for (const p of candidates) {
    if (await probe(p)) return p;
  }
  return null;
}

export interface CompileResult {
  ok: boolean;
  stdout: string;
  stderr: string;
  /** Absolute path of the produced file (last stdout line on success). */
  outputPath?: string;
}

export interface CompilerOptions {
  /** Output format: 'md' = compile only (no pandoc), 'docx', 'odt', or 'pdf' = export. */
  format: ExportFormat;
  /** Template name chosen in the modal (no extension). Overrides frontmatter. */
  template?: string;
  /** Explicit TOC choice (checkbox). Overrides template-aware default. */
  toc: boolean;
  /** true = restart footnote numbering at each top-level heading; false = continuous. */
  restartFootnotes: boolean;
  /** true = top-level headings start on a new page. */
  newPageHeadings: boolean;
  /** Output folder (vault-relative or absolute, ~ expanded); '' = same folder as source. */
  outputDir?: string;
  /** Desired output filename (basename.ext). When set and different from the
   *  compiler's default name, the output file is renamed after compilation. */
  outputFilename?: string;
  /** PDF only: keep the intermediate docx/odt after PDF conversion. */
  keepIntermediate?: boolean;
  /** PDF only: intermediate format ('odt' default, 'docx' alternative). */
  pdfIntermediate?: 'docx' | 'odt';
  /** IDs of StyleMappings to apply on this export (subset of settings.styleMappings). */
  enabledMappingIds?: string[];
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
 * `export: true` → docx / odt / pdf) on the given note. Desktop only.
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
      typeof rawTpl === 'string' ? rawTpl.replace(/\.(docx|odt)$/i, '') : '';
  }

  const args = [absMaster];
  if (isExport) {
    args.push('--export');
    args.push('--format', opts.format); // 'docx', 'odt', or 'pdf'
    if (opts.format === 'pdf') {
      args.push('--pdf-intermediate', opts.pdfIntermediate ?? 'odt');
      if (opts.keepIntermediate) args.push('--keep-intermediate');
    }
  }
  args.push(opts.toc ? '--toc' : '--no-toc');
  args.push(opts.restartFootnotes ? '--no-global-footnotes' : '--global-footnotes');
  args.push(opts.newPageHeadings ? '--new-page-headings' : '--no-new-page-headings');

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
  const outputDir = resolveFolder(opts.outputDir, vaultBase);
  if (outputDir) args.push('--output-dir', outputDir);

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

  const script = `${scriptsDir}/DocumentCompiler.py`;
  // Pass resolved tool paths through so the script doesn't depend on PATH.
  const baseEnv = (globalThis.process?.env ?? {}) as Record<string, string>;
  const env: Record<string, string | undefined> = { ...baseEnv, SW_PYTHON: py };
  if (isExport) {
    const node = await findNode();
    if (!node) {
      return {
        ok: false,
        stdout: '',
        stderr: 'Node.js not found. Install it (nodejs.org or Homebrew).',
      };
    }
    env.SW_NODE = node;
    const pandoc = plugin.settings.pathToPandoc?.trim() || (await findPandoc());
    if (!pandoc) {
      return {
        ok: false,
        stdout: '',
        stderr: 'Pandoc not found. Set its path in the plugin settings.',
      };
    }
    env.SW_PANDOC = pandoc;
  }

  try {
    const res = await execFileAsync(py, [script, ...args], { env });
    const rawOutputPath = res.stdout.trim().split('\n').pop() ?? '';

    // Optionally rename to the user-supplied filename.
    let finalOutputPath = rawOutputPath;
    if (
      opts.outputFilename &&
      rawOutputPath &&
      require('path').basename(rawOutputPath) !== opts.outputFilename
    ) {
      try {
        const path = require('path') as typeof import('path');
        const fs = require('fs') as typeof import('fs');
        const renamed = path.join(path.dirname(rawOutputPath), opts.outputFilename);
        fs.renameSync(rawOutputPath, renamed);
        finalOutputPath = renamed;
      } catch (renameErr) {
        console.warn('[scholar-weave] Could not rename output file:', renameErr);
        // Non-fatal — still report the original path.
      }
    }

    return {
      ok: true,
      stdout: res.stdout,
      stderr: res.stderr,
      outputPath: finalOutputPath || undefined,
    };
  } catch (e) {
    const err = e as any;
    return {
      ok: false,
      stdout: err?.stdout || '',
      stderr: (err?.stderr || String(err?.message ?? err)).trim(),
    };
  }
}
