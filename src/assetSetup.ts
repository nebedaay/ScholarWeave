import { normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { BUNDLED_ASSETS } from 'bundled:assets';

/**
 * Extract bundled scripts and templates into the plugin's own directory.
 *
 * Files live at  <vault>/<plugin.manifest.dir>/scripts/  and  .../templates/
 * — the same relative locations they occupy in the source repo — so all
 * existing paths in the Python scripts and plugin settings continue to work
 * without modification.
 *
 * `scripts/` is rewritten on EVERY load: it is code that must match this exact
 * main.js, it is tiny, and the previous "skip when .asset-version matches
 * manifest.version" optimisation silently left stale scripts on disk after a
 * BRAT update whose extraction didn't fire. `templates/` is only written when
 * the file is MISSING — users hand-edit the installed template copies, so a
 * blanket overwrite would clobber their work.
 */
export async function setupAssets(plugin: ReferenceList): Promise<void> {
  const { app, manifest } = plugin;
  const pluginDir = manifest.dir; // e.g. ".obsidian/plugins/scholar-weave"

  // Create the subdirectories we need.
  const dirs = new Set<string>();
  for (const relativePath of Object.keys(BUNDLED_ASSETS)) {
    const slash = relativePath.lastIndexOf('/');
    if (slash > 0) {
      dirs.add(normalizePath(`${pluginDir}/${relativePath.slice(0, slash)}`));
    }
  }
  for (const dir of dirs) {
    try {
      await app.vault.adapter.mkdir(dir);
    } catch {
      // Directory already exists — that's fine.
    }
  }

  let written = 0;
  let failed = 0;
  for (const [relativePath, { content, binary }] of Object.entries(BUNDLED_ASSETS)) {
    const fullPath = normalizePath(`${pluginDir}/${relativePath}`);
    const isTemplate = relativePath.startsWith('templates/');
    try {
      if (relativePath.startsWith('zotlit-templates/')) {
        continue; // opt-in only — written into the vault by the settings button
      }
      if (isTemplate && (await app.vault.adapter.exists(fullPath))) {
        continue; // never overwrite a template the user may have edited
      }
      if (binary) {
        const raw = atob(content);
        const buf = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
        await app.vault.adapter.writeBinary(fullPath, buf.buffer);
      } else {
        await app.vault.adapter.write(fullPath, content);
      }
      written++;
    } catch (e) {
      failed++;
      console.warn(`ScholarWeave: failed to write bundled asset "${relativePath}":`, e);
    }
  }
  console.log(
    `ScholarWeave ${manifest.version}: extracted ${written} bundled asset(s)`
      + (failed ? `, ${failed} failed` : ''),
  );

  // Legacy stamp file from older versions — remove it so nothing keys off it.
  try {
    await app.vault.adapter.remove(normalizePath(`${pluginDir}/.asset-version`));
  } catch {
    // not present — fine
  }
}
