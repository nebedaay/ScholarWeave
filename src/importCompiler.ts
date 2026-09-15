import type ReferenceList from './main';
import { findPandoc } from './bib/pandoc';
import { findPython3 } from './tools';

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
  }) as any;
}

/** Absolute path to the plugin's bundled scripts/ directory (desktop only). */
function pluginScriptsDir(plugin: ReferenceList): string | null {
  const adapter = plugin.app.vault.adapter as any;
  if (typeof adapter?.getBasePath !== 'function') return null;
  const base = adapter.getBasePath() as string;
  const dir = plugin.manifest.dir;
  if (!dir) return null;
  return `${base}/${dir}/scripts`;
}

// findPython3 now lives in src/tools.ts (shared with probeTools()).

export interface ImportResult {
  ok: boolean;
  stdout: string;
  stderr: string;
}

/**
 * Run zotero-to-md.py on inputPath, writing Markdown output to outputPath.
 * Requires Python 3 with lxml and requests, plus Zotero running locally.
 */
export async function runImportScript(
  plugin: ReferenceList,
  inputPath: string,
  outputPath: string
): Promise<ImportResult> {
  const scriptsDir = pluginScriptsDir(plugin);
  if (!scriptsDir) {
    return { ok: false, stdout: '', stderr: 'Document import is only available on desktop.' };
  }

  const py = await findPython3(plugin.settings.pathToPython ?? '', ['lxml', 'requests']);
  if (!py) {
    return {
      ok: false,
      stdout: '',
      stderr:
        'Python 3 with lxml and requests is required for document import.\n' +
        'Install them with:  pip install lxml requests\n' +
        'Or set a Python path under ScholarWeave settings → Document Compiler.',
    };
  }

  const script = `${scriptsDir}/zotero-to-md.py`;
  const baseEnv = (globalThis.process?.env ?? {}) as Record<string, string>;

  // zotero-to-md.py shells out to pandoc; resolve it now so SW_PANDOC is set
  // and the script doesn't rely on PATH (Electron doesn't inherit the shell PATH).
  const pandoc = plugin.settings.pathToPandoc?.trim() || (await findPandoc()) || 'pandoc';
  const env: Record<string, string | undefined> = { ...baseEnv, SW_PANDOC: pandoc };

  try {
    const { stdout, stderr } = await execFileAsync(py, [script, inputPath, outputPath], {
      env,
    });
    return { ok: true, stdout, stderr };
  } catch (err: any) {
    return {
      ok: false,
      stdout: err.stdout ?? '',
      stderr: err.stderr ?? String(err),
    };
  }
}
