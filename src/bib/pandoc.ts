import { Platform, requestUrl } from 'obsidian';
import { PartialCSLEntry } from './types';

/**
 * Attempt to locate the Pandoc executable.
 * Returns the path as a string, or null if Pandoc is not found or the platform
 * is not desktop.
 */
export async function findPandoc(): Promise<string | null> {
  if (!Platform.isDesktop) return null;

  // Try well-known installation paths first so we avoid spawning a shell when
  // possible (shell start-up can be slow on some systems).
  const candidates = [
    '/usr/local/bin/pandoc',
    '/usr/bin/pandoc',
    '/opt/homebrew/bin/pandoc',
    'C:\\Program Files\\Pandoc\\pandoc.exe',
    'C:\\Program Files (x86)\\Pandoc\\pandoc.exe',
  ];

  try {
    const { exec } = require('child_process') as typeof import('child_process');
    const { promisify } = require('util') as typeof import('util');
    const execAsync = promisify(exec);
    const fs = require('fs') as typeof import('fs');

    // Check hard-coded candidates synchronously first.
    for (const p of candidates) {
      if (fs.existsSync(p)) return p;
    }

    // Fall back to asking the shell.
    const cmd = process.platform === 'win32'
      ? 'where pandoc'
      : 'which pandoc';

    try {
      const { stdout } = await execAsync(cmd, { timeout: 5000 });
      const path = stdout.trim().split('\n')[0].trim();
      if (path) return path;
    } catch {
      // `which`/`where` not found or pandoc not on PATH — fall through.
    }
  } catch {
    // require() not available (shouldn't happen on Electron desktop).
  }

  return null;
}

/**
 * Convert a bibliography file to a CSL-JSON array using Pandoc.
 * Pandoc must be installed and `pandocPath` must point to its executable.
 */
export async function bibToCSLViaPandoc(
  bibPath: string,
  pandocPath: string
): Promise<PartialCSLEntry[]> {
  const { execFile } = require('child_process') as typeof import('child_process');
  const { promisify } = require('util') as typeof import('util');
  const execFileAsync = promisify(execFile);

  const { stdout } = await execFileAsync(
    pandocPath,
    ['--standalone', '-f', 'biblatex', '-t', 'csljson', bibPath],
    { timeout: 30_000, maxBuffer: 50 * 1024 * 1024 }
  );

  try {
    const parsed = JSON.parse(stdout.trim());
    // Pandoc wraps top-level CSL-JSON in an array directly.
    if (Array.isArray(parsed)) return parsed as PartialCSLEntry[];
    if (parsed && Array.isArray(parsed.references)) return parsed.references as PartialCSLEntry[];
    return [];
  } catch {
    return [];
  }
}
