import { Notice, normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { BUNDLED_ASSETS } from 'bundled:assets';

/**
 * Folder (vault-relative) where ScholarWeave's ZotLit import templates are
 * installed. Deliberately NOT "Templates" so a one-click install can't clobber
 * a user's own ZotLit templates — this folder is ScholarWeave-managed.
 */
export const SW_ZOTLIT_FOLDER = 'sw-zotlit-templates';

const ZOTLIT_PLUGIN_ID = 'zotlit';

export interface ZotlitInstallResult {
  written: string[];
  folder: string;
  zotlitDetected: boolean;
  folderConfigured: boolean;
  reloadedZotlit: boolean;
  error?: string;
}

/**
 * Copy the bundled ZotLit templates into `SW_ZOTLIT_FOLDER` and, when ZotLit
 * is installed, point its "Template folder" setting at that folder (reloading
 * ZotLit so it takes effect immediately).
 */
export async function installZotlitTemplates(
  plugin: ReferenceList
): Promise<ZotlitInstallResult> {
  const { app } = plugin;
  const adapter = app.vault.adapter;
  const result: ZotlitInstallResult = {
    written: [],
    folder: SW_ZOTLIT_FOLDER,
    zotlitDetected: false,
    folderConfigured: false,
    reloadedZotlit: false,
  };

  const entries = Object.entries(BUNDLED_ASSETS).filter(([p]) =>
    p.startsWith('zotlit-templates/')
  );
  if (entries.length === 0) {
    result.error = 'No bundled ZotLit templates found in this build.';
    return result;
  }

  try {
    if (!(await adapter.exists(SW_ZOTLIT_FOLDER))) {
      await adapter.mkdir(SW_ZOTLIT_FOLDER);
    }
    for (const [relativePath, asset] of entries) {
      const name = relativePath.slice('zotlit-templates/'.length);
      await adapter.write(
        normalizePath(`${SW_ZOTLIT_FOLDER}/${name}`),
        asset.content
      );
      result.written.push(name);
    }
  } catch (e) {
    result.error = `Could not write templates: ${(e as Error).message}`;
    return result;
  }

  // Point ZotLit's "Template folder" at our folder, if ZotLit is present.
  const anyApp = app as any;
  const zotlit = anyApp.plugins?.plugins?.[ZOTLIT_PLUGIN_ID];
  result.zotlitDetected = !!zotlit;
  if (zotlit) {
    try {
      const dataPath = normalizePath(
        `${app.vault.configDir}/plugins/${ZOTLIT_PLUGIN_ID}/data.json`
      );
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(await adapter.read(dataPath));
      } catch {
        /* no existing data.json — start fresh */
      }
      // ZotLit stores settings as flat dot-keys.
      data['template.folder'] = SW_ZOTLIT_FOLDER;
      await adapter.write(dataPath, JSON.stringify(data, null, 2));
      result.folderConfigured = true;

      // Reload ZotLit so it re-reads data.json now rather than on next launch.
      try {
        await anyApp.plugins.disablePlugin(ZOTLIT_PLUGIN_ID);
        await anyApp.plugins.enablePlugin(ZOTLIT_PLUGIN_ID);
        result.reloadedZotlit = true;
      } catch {
        /* reload failed — the written setting still applies after a restart */
      }
    } catch (e) {
      result.error = `Templates installed, but could not update ZotLit's setting: ${(e as Error).message}`;
    }
  }

  return result;
}

/** Run the install and show a Notice describing what happened. */
export async function installZotlitTemplatesWithNotice(
  plugin: ReferenceList
): Promise<void> {
  const r = await installZotlitTemplates(plugin);
  if (r.error && r.written.length === 0) {
    new Notice(`ScholarWeave: ${r.error}`, 8000);
    return;
  }
  const lines = [`Installed ${r.written.length} ZotLit template(s) to ${r.folder}/`];
  if (r.zotlitDetected) {
    if (r.folderConfigured) {
      lines.push(
        r.reloadedZotlit
          ? `ZotLit's Template folder set to ${r.folder} (ZotLit reloaded).`
          : `ZotLit's Template folder set to ${r.folder} — restart Obsidian to apply.`
      );
    }
    if (r.error) lines.push(r.error);
  } else {
    lines.push(
      `ZotLit not detected — set its "Template folder" to ${r.folder} manually.`
    );
  }
  new Notice(`ScholarWeave: ${lines.join('\n')}`, 9000);
}
