import { App, FuzzySuggestModal, Notice } from 'obsidian';
import type ReferenceList from '../main';

declare const require: (id: string) => any;

export interface ZoteroStyle {
  title: string;
  path: string;
}

/**
 * Return candidate directories where Zotero stores installed CSL styles.
 * A user-configured data folder (settings.zoteroDataDir) is tried first;
 * then the default Zotero data dir location for macOS, Windows, and Linux.
 */
export function zoteroStyleDirs(customDataDir?: string): string[] {
  if (typeof require !== 'function') return [];
  const os = require('os') as typeof import('os');
  const path = require('path') as typeof import('path');
  const home = os.homedir();
  const platform = globalThis.process?.platform;

  const dataDirs: string[] = [];
  const custom = (customDataDir ?? '').trim();
  if (custom) {
    // Accept either the data dir or the styles dir itself.
    dataDirs.push(
      path.basename(custom) === 'styles' ? custom : path.join(custom, 'styles'),
      path.join(custom, 'styles')
    );
  }
  if (platform === 'win32') {
    dataDirs.push(
      path.join(home, 'Zotero', 'styles'),
      path.join(process.env.APPDATA ?? '', 'Zotero', 'Zotero', 'styles')
    );
  } else if (platform === 'darwin') {
    dataDirs.push(
      path.join(home, 'Zotero', 'styles')
    );
  } else {
    // Linux
    dataDirs.push(
      path.join(home, 'Zotero', 'styles'),
      path.join(home, '.zotero', 'zotero', 'styles')
    );
  }
  return dataDirs;
}

/**
 * Read all *.csl files from the first Zotero styles directory that exists.
 * Parses the <title> element from each file to produce a human-readable name.
 * Falls back to the filename stem when the title can't be extracted.
 */
export function listZoteroInstalledStyles(customDataDir?: string): ZoteroStyle[] {
  const fs = require('fs') as typeof import('fs');
  const path = require('path') as typeof import('path');

  let stylesDir: string | null = null;
  for (const dir of zoteroStyleDirs(customDataDir)) {
    try {
      if (fs.existsSync(dir)) {
        stylesDir = dir;
        break;
      }
    } catch { /* ignore */ }
  }

  if (!stylesDir) return [];

  let files: string[];
  try {
    files = fs.readdirSync(stylesDir).filter((f: string) => f.endsWith('.csl'));
  } catch {
    return [];
  }

  const styles: ZoteroStyle[] = [];
  for (const file of files) {
    const filePath = path.join(stylesDir, file);
    let title = path.basename(file, '.csl');
    try {
      // Read just the first 2 KB — enough to find <title>
      const buf = Buffer.alloc(2048);
      const fd = fs.openSync(filePath, 'r');
      const bytesRead = fs.readSync(fd, buf, 0, 2048, 0);
      fs.closeSync(fd);
      const head = buf.slice(0, bytesRead).toString('utf-8');
      const m = head.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
      if (m) title = m[1].trim();
    } catch { /* use filename stem */ }
    styles.push({ title, path: filePath });
  }

  return styles.sort((a, b) => a.title.localeCompare(b.title));
}

/**
 * Resolve a `csl:` frontmatter value or a stored style choice to a usable
 * reference. A URL or an existing file path is returned unchanged; a bare
 * style name (e.g. "chicago-note-bibliography") is looked up as
 * `<zotero styles dir>/<name>.csl`. Returns null when a bare name can't be
 * found in any styles folder.
 */
export function resolveZoteroStylePath(
  value: string,
  customDataDir?: string
): string | null {
  const v = (value ?? '').trim();
  if (!v) return null;
  if (/^https?:\/\//i.test(v)) return v;
  // Node fs/path are desktop-only; on mobile a bare name can't be resolved.
  if (typeof require !== 'function') return null;

  const fs = require('fs') as typeof import('fs');
  const path = require('path') as typeof import('path');
  if (v.includes('/') || v.includes('\\') || v.toLowerCase().endsWith('.csl')) {
    try {
      if (fs.existsSync(v)) return v;
    } catch { /* fall through to name lookup */ }
  }

  const name = v.replace(/\.csl$/i, '');
  for (const dir of zoteroStyleDirs(customDataDir)) {
    const candidate = path.join(dir, `${name}.csl`);
    try {
      if (fs.existsSync(candidate)) return candidate;
    } catch { /* ignore */ }
  }
  return null;
}

/**
 * A fuzzy-search modal that lists all CSL styles installed in Zotero's data
 * directory and lets the user pick one. On selection it sets cslStylePath to
 * the chosen file's absolute path and reinitialises the citation engine.
 */
export class ZoteroStylePicker extends FuzzySuggestModal<ZoteroStyle> {
  private plugin: ReferenceList;
  private styles: ZoteroStyle[];
  private onPick: (style: ZoteroStyle) => void;

  constructor(
    app: App,
    plugin: ReferenceList,
    onPick: (style: ZoteroStyle) => void
  ) {
    super(app);
    this.plugin = plugin;
    this.onPick = onPick;
    this.setPlaceholder('Search your installed Zotero CSL styles…');

    const loaded = listZoteroInstalledStyles(plugin.settings.zoteroDataDir);
    this.styles = loaded;
    if (loaded.length === 0) {
      new Notice(
        'No Zotero styles found. Make sure Zotero is installed and has styles in ~/Zotero/styles/.',
        6000
      );
    }
  }

  getItems(): ZoteroStyle[] {
    return this.styles;
  }

  getItemText(item: ZoteroStyle): string {
    return item.title;
  }

  onChooseItem(item: ZoteroStyle): void {
    this.onPick(item);
  }
}
