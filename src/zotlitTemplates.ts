import { Notice, normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { BUNDLED_ASSETS } from 'bundled:assets';

/**
 * Folder (vault-relative) where ScholarWeft's ZotLit import templates are
 * installed. Deliberately NOT "Templates" so a one-click install can't clobber
 * a user's own ZotLit templates — this folder is ScholarWeft-managed.
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

  // Point ZotLit's "Template folder" at our folder, but only when ZotLit is
  // actually loaded. We never create or clobber ZotLit's settings file when
  // ZotLit isn't there to read it — writing a settings file for a plugin that
  // doesn't exist (or that has its own file) is how you brick it.
  const anyApp = app as any;
  const zotlit = anyApp.plugins?.plugins?.[ZOTLIT_PLUGIN_ID];
  result.zotlitDetected = !!zotlit;
  if (zotlit) {
    const dataPath = normalizePath(
      `${app.vault.configDir}/plugins/${ZOTLIT_PLUGIN_ID}/data.json`
    );
    // Quiesce ZotLit FIRST: disabling it makes it flush its own settings to
    // data.json, so our write happens last and cannot interleave with its save.
    // (Writing while it is live — or right before a disable — races two writers
    // and can corrupt the file, after which ZotLit fails to load.)
    let disabled = false;
    try {
      await anyApp.plugins.disablePlugin(ZOTLIT_PLUGIN_ID);
      disabled = true;
    } catch {
      /* couldn't disable — we'll still write, but won't reload at the end */
    }
    try {
      const existing = (await adapter.exists(dataPath))
        ? await adapter.read(dataPath)
        : null;
      let data: Record<string, unknown>;
      if (existing && existing.trim()) {
        try {
          data = JSON.parse(existing);
        } catch {
          // Never overwrite an unreadable settings file — that turns a bad file
          // into a bricked plugin. Leave it alone and tell the user.
          result.error =
            "ZotLit's settings file couldn't be parsed, so it was left " +
            `untouched — set ZotLit's "Template folder" to ${SW_ZOTLIT_FOLDER} manually.`;
          return result;
        }
      } else {
        data = {};
      }
      if (existing !== null) {
        await adapter.write(`${dataPath}.scholarweft.bak`, existing);
      }
      // ZotLit stores settings as flat dot-keys.
      data['template.folder'] = SW_ZOTLIT_FOLDER;
      await adapter.write(dataPath, JSON.stringify(data, null, 2));
      result.folderConfigured = true;
    } catch (e) {
      result.error = `Templates installed, but could not update ZotLit's setting: ${(e as Error).message}`;
    } finally {
      if (disabled) {
        try {
          // Re-enable so ZotLit re-reads data.json now rather than on next launch.
          await anyApp.plugins.enablePlugin(ZOTLIT_PLUGIN_ID);
          result.reloadedZotlit = true;
        } catch {
          /* the written setting still applies after a restart */
        }
      }
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
    new Notice(`ScholarWeft: ${r.error}`, 8000);
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
  new Notice(`ScholarWeft: ${lines.join('\n')}`, 9000);
}
