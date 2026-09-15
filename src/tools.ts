/**
 * Shared external-tool resolution + probing, used by the export/import
 * compilers (for the actual paths they pass to the scripts) and by the export
 * modal / settings (to grey out options whose dependency is missing).
 *
 * Electron's renderer doesn't inherit the shell PATH, so every finder probes
 * PATH candidates and known install locations. `probeTools()` memoises the
 * whole set briefly so opening a modal doesn't re-spawn Python each time.
 */
import type ReferenceList from './main';
import { findPandoc } from './bib/pandoc';
import { isZoteroRunning, isZoteroRunningNative } from './bib/helpers';

declare const require: (id: string) => any;

async function execProbe(file: string, args: string[]): Promise<boolean> {
  const { execFile } = require('child_process') as typeof import('child_process');
  const { promisify } = require('util') as typeof import('util');
  try {
    await promisify(execFile)(file, args);
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve a Python 3 interpreter that can import `modules` (default: the
 * export pipeline's `lxml` + `python-docx`). An explicit configured path wins
 * without probing. Returns the first candidate that imports cleanly.
 */
export async function findPython3(
  configured: string,
  modules: string[] = ['lxml', 'docx']
): Promise<string | null> {
  if (configured.trim()) return configured.trim();
  const platform = globalThis.process?.platform;
  const probe = (p: string) =>
    execProbe(p, ['-c', `import ${modules.join(', ')}; import sys; sys.exit(0)`]);

  const candidates: string[] = platform === 'win32' ? ['py', 'python', 'python3'] : ['python3'];
  candidates.push(
    ...(platform === 'win32'
      ? ['C:\\Python313\\python.exe', 'C:\\Python312\\python.exe', 'C:\\Python311\\python.exe']
      : ['/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3'])
  );
  for (const p of candidates) {
    if (await probe(p)) return p;
  }
  return null;
}

/** Resolve the node binary (only needed by the CLI converter fallback). */
export async function findNode(): Promise<string | null> {
  const platform = globalThis.process?.platform;
  const candidates =
    platform === 'win32'
      ? ['node', 'C:\\Program Files\\nodejs\\node.exe', `${process.env.APPDATA ?? ''}\\nvm\\node.exe`]
      : ['node', '/opt/homebrew/bin/node', '/usr/local/bin/node', '/usr/bin/node'];
  for (const p of candidates) {
    if (await execProbe(p, ['--version'])) return p;
  }
  return null;
}

/** Resolve LibreOffice's soffice binary (mirrors find_soffice() in Python). */
export async function findSoffice(): Promise<string | null> {
  const candidates = [
    process.env.SW_SOFFICE ?? '',
    'soffice',
    '/Applications/LibreOffice.app/Contents/MacOS/soffice',
    '/usr/bin/soffice',
    '/usr/local/bin/soffice',
    'C:\\Program Files\\LibreOffice\\program\\soffice.exe',
  ];
  for (const c of candidates) {
    if (c && (await execProbe(c, ['--version']))) return c;
  }
  return null;
}

/** Resolve the lualatex engine (mirrors find_latex_engine() in Python). */
export async function findLatexEngine(): Promise<string | null> {
  const candidates = [
    process.env.SW_LUALATEX ?? '',
    'lualatex',
    '/Library/TeX/texbin/lualatex',
    '/usr/bin/lualatex',
    '/usr/local/bin/lualatex',
    'C:\\Program Files\\MiKTeX\\miktex\\bin\\x64\\lualatex.exe',
    'C:\\texlive\\2026\\bin\\windows\\lualatex.exe',
  ];
  for (const c of candidates) {
    if (c && (await execProbe(c, ['--version']))) return c;
  }
  return null;
}

export interface ToolProbe {
  pandoc: string | null;
  /** Python with lxml + python-docx (compile/export). */
  python: string | null;
  /** Python with lxml + requests (import). */
  pythonImport: string | null;
  node: string | null;
  soffice: string | null;
  latex: string | null;
  zotero: boolean;
}

let cache: { at: number; value: Promise<ToolProbe> } | null = null;
const TTL_MS = 30_000;

/** Drop the memoised probe (e.g. after the user changes a path setting). */
export function invalidateToolProbe(): void {
  cache = null;
}

/**
 * Probe every external tool the plugin can use. Memoised for {@link TTL_MS};
 * pass `force` to re-check (e.g. a "re-check" action).
 */
export function probeTools(plugin: ReferenceList, force = false): Promise<ToolProbe> {
  const now = Date.now();
  if (!force && cache && now - cache.at < TTL_MS) return cache.value;

  const configuredPy = plugin.settings.pathToPython ?? '';
  const port = plugin.settings.zoteroPort;
  const value: Promise<ToolProbe> = (async (): Promise<ToolProbe> => {
    let pandoc: string | null = plugin.settings.pathToPandoc?.trim() || null;
    if (!pandoc) {
      try { pandoc = await findPandoc(); } catch { pandoc = null; }
    }
    let zotero = false;
    try {
      zotero = (await isZoteroRunning(port)) || (await isZoteroRunningNative(port));
    } catch { zotero = false; }
    const [python, pythonImport, node, soffice, latex] = await Promise.all([
      findPython3(configuredPy),
      findPython3(configuredPy, ['lxml', 'requests']),
      findNode(),
      findSoffice(),
      findLatexEngine(),
    ]);
    return { pandoc, python, pythonImport, node, soffice, latex, zotero };
  })();
  cache = { at: now, value };
  return value;
}
