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
 * `scripts/` and `templates/` are BOTH rewritten on every load, to always
 * match this exact main.js — the previous "skip when .asset-version matches
 * manifest.version" optimisation silently left stale scripts (and, later,
 * stale templates) on disk after a BRAT update whose extraction didn't fire.
 * `templates/` used to be "only written when missing", on the theory that
 * users hand-edit the installed copies directly — but that meant a plugin
 * update could never ship a template fix to anyone who'd ever had that file
 * on disk (which is everyone, since it's written on first install). The
 * supported customization path is instead to copy a template out of this
 * folder into the user's own Export Templates folder (a different directory
 * entirely — see `exportTemplatesDir` in settings) and edit the copy there;
 * this folder itself is treated as plugin-managed content, exactly like
 * scripts/, and any local edit made directly here will be overwritten on the
 * next reload.
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
    try {
      if (relativePath.startsWith('zotlit-templates/')) {
        continue; // opt-in only — written into the vault by the settings button
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
