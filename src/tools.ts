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
 * without probing. Each candidate is probed by actually importing the modules,
 * so a Python that exists but lacks them is skipped rather than chosen.
 *
 * Candidates include the private virtual environment our installer script
 * creates (`~/ScholarWeft/venv`), conda/anaconda, pyenv, Homebrew and the
 * distro interpreters — so users normally don't have to paste a path at all.
 */
export async function findPython3(
  configured: string,
  modules: string[] = ['lxml', 'docx']
): Promise<string | null> {
  if (configured.trim()) return configured.trim();
  const platform = globalThis.process?.platform;
  const win = platform === 'win32';
  let home = '';
  try {
    home = (require('os').homedir() as string) ?? '';
  } catch {
    home = '';
  }
  const probe = (p: string) =>
    execProbe(p, ['-c', `import ${modules.join(', ')}; import sys; sys.exit(0)`]);

  const candidates: string[] = [];
  // 1. Whatever is on PATH — the user's own working interpreter if they have one.
  candidates.push(...(win ? ['py', 'python', 'python3'] : ['python3', 'python']));
  // 2. The virtual environment created by install/install-*.sh — this is what
  //    lets the installer finish without the user copying a path around.
  if (home) {
    candidates.push(
      win
        ? `${home}\\ScholarWeft\\venv\\Scripts\\python.exe`
        : `${home}/ScholarWeft/venv/bin/python3`
    );
  }
  // 3. Well-known per-user and system locations.
  if (win) {
    const local = process.env.LOCALAPPDATA ?? '';
    candidates.push(
      'C:\\Python313\\python.exe', 'C:\\Python312\\python.exe', 'C:\\Python311\\python.exe',
      `${home}\\miniconda3\\python.exe`, `${home}\\anaconda3\\python.exe`,
      `${local}\\Programs\\Python\\Python313\\python.exe`,
      `${local}\\Programs\\Python\\Python312\\python.exe`
    );
  } else {
    candidates.push(
      'python3.13', 'python3.12', 'python3.11',
      '/opt/homebrew/bin/python3', '/usr/local/bin/python3', '/usr/bin/python3',
      `${home}/miniconda3/bin/python3`, `${home}/anaconda3/bin/python3`,
      '/opt/miniconda3/bin/python3', '/opt/anaconda3/bin/python3',
      `${home}/opt/anaconda3/bin/python3`, `${home}/.pyenv/shims/python3`,
      '/opt/homebrew/opt/python/libexec/bin/python'
    );
  }
  for (const p of candidates) {
    if (p && (await probe(p))) return p;
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
