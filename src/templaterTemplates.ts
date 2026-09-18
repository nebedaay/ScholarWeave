import { Notice, normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { BUNDLED_ASSETS } from 'bundled:assets';

/**
 * Folder (vault-relative) where ScholarWeft's Basic note template is installed.
 * Deliberately NOT "Templates" so a one-click install can't clobber a user's
 * own templates — this folder is ScholarWeft-managed.
 */
export const SW_MARKDOWN_FOLDER = 'sw-markdown-templates';

/** The template applied to new notes created at the vault root. */
export const SW_BASIC_NOTE = 'sw-basic-note-template.md';

export const SW_BASIC_NOTE_PATH = `${SW_MARKDOWN_FOLDER}/${SW_BASIC_NOTE}`;

const TEMPLATER_PLUGIN_ID = 'templater-obsidian';

export interface TemplaterInstallResult {
  written: string[];
  folder: string;
  templaterDetected: boolean;
  folderConfigured: boolean;
  reloadedTemplater: boolean;
  error?: string;
}

/**
 * Copy the bundled Basic note template into `SW_MARKDOWN_FOLDER` and, when
 * Templater is installed, configure it to apply that template to every new note
 * created at the vault root (reloading Templater so it takes effect at once).
 *
 * Same safety pattern as the ZotLit button: never touch a settings file for a
 * plugin that isn't there, quiesce the plugin BEFORE writing (so its own save
 * can't interleave), back the file up, and refuse to overwrite one that won't
 * parse.
 */
export async function installTemplaterTemplates(
  plugin: ReferenceList
): Promise<TemplaterInstallResult> {
  const { app } = plugin;
  const adapter = app.vault.adapter;
  const result: TemplaterInstallResult = {
    written: [],
    folder: SW_MARKDOWN_FOLDER,
    templaterDetected: false,
    folderConfigured: false,
    reloadedTemplater: false,
  };

  const entries = Object.entries(BUNDLED_ASSETS).filter(([p]) =>
    p.startsWith('sw-markdown-templates/')
  );
  if (entries.length === 0) {
    result.error = 'No bundled note templates found in this build.';
    return result;
  }

  try {
    if (!(await adapter.exists(SW_MARKDOWN_FOLDER))) {
      await adapter.mkdir(SW_MARKDOWN_FOLDER);
    }
    for (const [relativePath, asset] of entries) {
      const name = relativePath.slice('sw-markdown-templates/'.length);
      await adapter.write(
        normalizePath(`${SW_MARKDOWN_FOLDER}/${name}`),
        asset.content
      );
      result.written.push(name);
    }
  } catch (e) {
    result.error = `Could not write templates: ${(e as Error).message}`;
    return result;
  }

  const anyApp = app as any;
  const templater = anyApp.plugins?.plugins?.[TEMPLATER_PLUGIN_ID];
  result.templaterDetected = !!templater;
  if (templater) {
    const dataPath = normalizePath(
      `${app.vault.configDir}/plugins/${TEMPLATER_PLUGIN_ID}/data.json`
    );
    // Quiesce Templater FIRST so it flushes its own settings, then ours lands
    // last and cannot race its save.
    let disabled = false;
    try {
      await anyApp.plugins.disablePlugin(TEMPLATER_PLUGIN_ID);
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
          result.error =
            "Templater's settings file couldn't be parsed, so it was left " +
            `untouched — in Templater's settings, turn on "Trigger Templater ` +
            `on new file creation", set the matching mode to "Folder ` +
            `templates", and add ${SW_BASIC_NOTE_PATH} for "/".`;
          return result;
        }
      } else {
        data = {};
      }
      if (existing !== null) {
        await adapter.write(`${dataPath}.scholarweft.bak`, existing);
      }
      data['trigger_on_file_creation'] = true;
      data['trigger_on_file_creation_mode'] = 'folder';
      const existingRules = data['folder_templates'];
      const rules: unknown[] = Array.isArray(existingRules)
        ? existingRules.slice()
        : [];
      const already = rules.some(
        (r) =>
          r &&
          typeof r === 'object' &&
          (r as Record<string, unknown>).template === SW_BASIC_NOTE_PATH
      );
      if (!already) rules.push({ folder: '/', template: SW_BASIC_NOTE_PATH });
      data['folder_templates'] = rules;
      await adapter.write(dataPath, JSON.stringify(data, null, 2));
      result.folderConfigured = true;
    } catch (e) {
      result.error = `Template installed, but could not update Templater's setting: ${(e as Error).message}`;
    } finally {
      if (disabled) {
        try {
          await anyApp.plugins.enablePlugin(TEMPLATER_PLUGIN_ID);
          result.reloadedTemplater = true;
        } catch {
          /* the written setting still applies after a restart */
        }
      }
    }
  }

  return result;
}

/** Run the install and show a Notice describing what happened. */
export async function installTemplaterTemplatesWithNotice(
  plugin: ReferenceList
): Promise<void> {
  const r = await installTemplaterTemplates(plugin);
  if (r.error && r.written.length === 0) {
    new Notice(`ScholarWeft: ${r.error}`, 8000);
    return;
  }
  const lines = [
    `Installed ${r.written.length} note template(s) to ${r.folder}/`,
  ];
  if (r.templaterDetected) {
    if (r.folderConfigured) {
      lines.push(
        r.reloadedTemplater
          ? `Templater set to apply ${SW_BASIC_NOTE_PATH} to new notes in "/" (Templater reloaded).`
          : `Templater set to apply ${SW_BASIC_NOTE_PATH} to new notes in "/" — restart Obsidian to apply.`
      );
    }
    if (r.error) lines.push(r.error);
  } else {
    lines.push(
      `Templater not detected — install and enable it, then click again.`
    );
  }
  new Notice(`ScholarWeft: ${lines.join('\n')}`, 9000);
}
