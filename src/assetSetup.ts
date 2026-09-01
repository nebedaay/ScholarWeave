import { normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { BUNDLED_ASSETS } from 'bundled:assets';

/**
 * Extract bundled scripts and templates into the plugin's own directory on
 * first load, and again whenever the plugin version changes.
 *
 * Files live at  <vault>/<plugin.manifest.dir>/scripts/  and  .../templates/
 * — the same relative locations they occupy in the source repo — so all
 * existing paths in the Python scripts and plugin settings continue to work
 * without modification.
 *
 * A tiny version-stamp file (.asset-version) is written alongside the assets
 * after a successful extraction. On subsequent loads we compare it to
 * manifest.version and skip the write if they match, keeping startup fast.
 */
export async function setupAssets(plugin: ReferenceList): Promise<void> {
  const { app, manifest } = plugin;
  const pluginDir = manifest.dir; // e.g. ".obsidian/plugins/scholar-weave"
  const versionStampPath = normalizePath(`${pluginDir}/.asset-version`);

  // Skip extraction if assets are already at the current version.
  try {
    const stored = await app.vault.adapter.read(versionStampPath);
    if (stored.trim() === manifest.version) return;
  } catch {
    // First run or version stamp missing — fall through to extraction.
  }

  // Collect the unique subdirectories we need to create.
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

  // Write each bundled asset.
  for (const [relativePath, { content, binary }] of Object.entries(BUNDLED_ASSETS)) {
    const fullPath = normalizePath(`${pluginDir}/${relativePath}`);
    try {
      if (binary) {
        // Decode base64 → ArrayBuffer and write as binary.
        const raw = atob(content);
        const buf = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
        await app.vault.adapter.writeBinary(fullPath, buf.buffer);
      } else {
        await app.vault.adapter.write(fullPath, content);
      }
    } catch (e) {
      console.warn(`ScholarWeave: failed to write bundled asset "${relativePath}":`, e);
    }
  }

  // Record the version so the next load skips extraction.
  try {
    await app.vault.adapter.write(versionStampPath, manifest.version);
  } catch (e) {
    console.warn('ScholarWeave: failed to write asset version stamp:', e);
  }
}
