import { App, FuzzySuggestModal, Notice } from 'obsidian';
import type ReferenceList from '../main';

declare const require: (id: string) => any;

export interface ZoteroStyle {
  title: string;
  path: string;
}

/**
 * Return candidate directories where Zotero stores installed CSL styles.
 * Checks the default Zotero data dir location for macOS, Windows, and Linux.
 */
function zoteroStyleDirs(): string[] {
  const os = require('os') as typeof import('os');
  const path = require('path') as typeof import('path');
  const home = os.homedir();
  const platform = globalThis.process?.platform;

  const dataDirs: string[] = [];
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
export function listZoteroInstalledStyles(): ZoteroStyle[] {
  const fs = require('fs') as typeof import('fs');
  const path = require('path') as typeof import('path');

  let stylesDir: string | null = null;
  for (const dir of zoteroStyleDirs()) {
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

    const loaded = listZoteroInstalledStyles();
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
