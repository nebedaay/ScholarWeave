import { Notice, normalizePath } from 'obsidian';
import type ReferenceList from './main';
import { TemplaterRuleModal } from './modals/templaterRuleModal';
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
  /** Whether Templater's on-creation trigger is now on (a LOCAL setting). */
  triggerEnabled: boolean;
  /** What happened to the root ("/") folder-template rule. */
  ruleAction: 'none' | 'added' | 'kept' | 'replaced';
  /** Set when a different "/" rule exists: its template path (await the user). */
  pendingDecision?: string;
  existingRootTemplate?: string;
  error?: string;
}

/** How to treat an existing "/" rule when installing our root rule. */
export type TemplaterRuleMode = 'default' | 'keep' | 'replace';

const normFolder = (f: unknown): string =>
  typeof f === 'string' ? f.replace(/^\/+|\/+$/g, '') : '';
const isRootRule = (r: unknown): boolean =>
  !!r && typeof r === 'object' && normFolder((r as any).folder) === '';
const isOurRule = (r: unknown): boolean =>
  !!r &&
  typeof r === 'object' &&
  (r as Record<string, unknown>).template === SW_BASIC_NOTE_PATH;

/**
 * Templater keeps `trigger_on_file_creation` NOT in data.json but in a
 * per-device local setting (Obsidian's `loadLocalStorage`/`saveLocalStorage`),
 * alongside the "accept the risks" confirmation. So writing data.json alone
 * never makes new notes trigger. We set the local key too — the user opted in
 * by clicking ScholarWeft's button — and merge, so we don't disturb their other
 * Templater local settings.
 */
const TEMPLATER_LOCAL_KEY = 'templater-local-settings';

function enableTemplaterTrigger(app: unknown): boolean {
  try {
    const a = app as {
      loadLocalStorage?: (k: string) => unknown;
      saveLocalStorage?: (k: string, v: unknown) => void;
    };
    const current =
      (a.loadLocalStorage?.(TEMPLATER_LOCAL_KEY) as
        | Record<string, unknown>
        | null) ?? {};
    if (current['trigger_on_file_creation'] !== true) {
      a.saveLocalStorage?.(TEMPLATER_LOCAL_KEY, {
        ...current,
        trigger_on_file_creation: true,
      });
    }
    return true;
  } catch {
    return false;
  }
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
  plugin: ReferenceList,
  ruleMode: TemplaterRuleMode = 'default'
): Promise<TemplaterInstallResult> {
  const { app } = plugin;
  const adapter = app.vault.adapter;
  const result: TemplaterInstallResult = {
    written: [],
    folder: SW_MARKDOWN_FOLDER,
    templaterDetected: false,
    folderConfigured: false,
    reloadedTemplater: false,
    triggerEnabled: false,
    ruleAction: 'none',
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
      // A different rule already applying to the vault root would be lost if we
      // just appended ours (or would shadow it). Ask (or honour the caller's choice).
      const otherRoot = rules.find((r) => isRootRule(r) && !isOurRule(r));
      if (otherRoot) {
        const existingTemplate = String((otherRoot as any).template ?? '');
        result.existingRootTemplate = existingTemplate;
        if (ruleMode === 'default') {
          // Stop and let the caller ask. `finally` re-enables Templater; nothing
          // has been written to data.json yet.
          result.pendingDecision = existingTemplate;
          return result;
        }
        if (ruleMode === 'keep') {
          result.ruleAction = 'kept';
        } else {
          for (let i = rules.length - 1; i >= 0; i--) {
            if (isRootRule(rules[i]) && !isOurRule(rules[i])) rules.splice(i, 1);
          }
          if (!rules.some(isOurRule)) {
            rules.push({ folder: '/', template: SW_BASIC_NOTE_PATH });
          }
          result.ruleAction = 'replaced';
        }
      } else if (!rules.some(isOurRule)) {
        rules.push({ folder: '/', template: SW_BASIC_NOTE_PATH });
        result.ruleAction = 'added';
      }
      data['folder_templates'] = rules;
      await adapter.write(dataPath, JSON.stringify(data, null, 2));
      result.folderConfigured = true;
      // The trigger itself lives in Templater's local settings, not data.json.
      result.triggerEnabled = enableTemplaterTrigger(app);
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

/** Run the install and show a Notice describing what happened. If a different
 *  "/" rule already exists, ask whether to keep it or replace it, then finish. */
export async function installTemplaterTemplatesWithNotice(
  plugin: ReferenceList
): Promise<void> {
  let r = await installTemplaterTemplates(plugin);
  if (r.pendingDecision !== undefined) {
    const existing = r.existingRootTemplate ?? '';
    const replace = await new Promise<boolean>((resolve) => {
      new TemplaterRuleModal(plugin.app, existing, SW_BASIC_NOTE_PATH, resolve).open();
    });
    if (!replace) {
      new Notice(
        `ScholarWeft: kept your existing "/" rule ("${existing}"). The Basic note template is installed, but not applied at the root.`,
        9000
      );
      return;
    }
    r = await installTemplaterTemplates(plugin, 'replace');
  }
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
      if (!r.triggerEnabled) {
        lines.push(
          'One more step (Templater blocks this until you accept): open Templater\'s settings, turn on "Trigger Templater on new file creation", and confirm its warning.'
        );
      }
    }
    if (r.error) lines.push(r.error);
  } else {
    lines.push(
      `Templater not detected — install and enable it, then click again.`
    );
  }
  new Notice(`ScholarWeft: ${lines.join('\n')}`, 9000);
}
