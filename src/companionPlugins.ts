import { Notice, normalizePath, requestUrl } from 'obsidian';
import type ReferenceList from './main';

/**
 * Optional companion plugins ScholarWeft can install for the user.
 *
 * We fetch each plugin's STABLE release straight from GitHub (not the in-app
 * community browser, which has no public API) and write it into
 * `<configDir>/plugins/<id>/`. Obsidian identifies plugins by manifest `id`, so
 * a plugin placed this way is a normal community plugin: it appears in Settings
 * and — because ZotLit and Templater are in Obsidian's community registry —
 * Obsidian's own updater keeps it current from then on. (ScholarWeft itself is
 * NOT in the registry and is updated by BRAT.)
 */
export interface Companion {
  /** Plugin folder + manifest id. */
  id: string;
  /** GitHub `owner/repo`. */
  repo: string;
  /** Human-readable name for messages. */
  name: string;
}

export const COMPANIONS: Record<string, Companion> = {
  zotlit: { id: 'zotlit', repo: 'PKM-er/obsidian-zotlit', name: 'ZotLit' },
  templater: {
    id: 'templater-obsidian',
    repo: 'SilentVoid13/Templater',
    name: 'Templater',
  },
};

interface ReleaseAsset {
  name: string;
  browser_download_url: string;
}

const GH_HEADERS = { Accept: 'application/vnd.github+json' };

/** Newest STABLE release (skips pre-releases); falls back to the list if the
 *  repository has no non-pre-release `latest`. */
async function latestStable(
  repo: string
): Promise<{ tag: string; assets: ReleaseAsset[] } | null> {
  try {
    const res = await requestUrl({
      url: `https://api.github.com/repos/${repo}/releases/latest`,
      headers: GH_HEADERS,
      throw: false,
    });
    if (res.status < 400 && res.json?.assets) {
      return { tag: res.json.tag_name, assets: res.json.assets };
    }
  } catch {
    /* fall through to the list endpoint */
  }
  try {
    const res = await requestUrl({
      url: `https://api.github.com/repos/${repo}/releases?per_page=20`,
      headers: GH_HEADERS,
      throw: false,
    });
    const list: any[] = Array.isArray(res.json) ? res.json : [];
    const stable = list.find((r) => !r.prerelease && !r.draft) ?? list[0];
    if (stable?.assets) return { tag: stable.tag_name, assets: stable.assets };
  } catch {
    /* unreachable / rate limited */
  }
  return null;
}

export function isPluginInstalled(app: any, id: string): boolean {
  return !!app?.plugins?.manifests?.[id];
}

export function isPluginEnabled(app: any, id: string): boolean {
  return !!app?.plugins?.plugins?.[id];
}

/** Download a companion plugin's stable release and enable it. */
export async function installCompanionPlugin(
  plugin: ReferenceList,
  key: string
): Promise<{ ok: boolean; error?: string; version?: string }> {
  const companion = COMPANIONS[key];
  if (!companion) return { ok: false, error: `Unknown companion "${key}".` };
  const { app } = plugin;
  const adapter = app.vault.adapter;

  const rel = await latestStable(companion.repo);
  if (!rel) {
    return {
      ok: false,
      error: `Could not read ${companion.name}'s releases (no network, or GitHub rate limit). Install it from Settings → Community plugins instead.`,
    };
  }

  const dir = normalizePath(`${app.vault.configDir}/plugins/${companion.id}`);
  try {
    await adapter.mkdir(dir);
  } catch {
    /* already exists */
  }

  for (const name of ['main.js', 'manifest.json', 'styles.css']) {
    const asset = rel.assets.find((a) => a.name === name);
    if (!asset) {
      if (name === 'styles.css') continue; // optional
      return {
        ok: false,
        error: `${companion.name}'s release is missing ${name}. Install it from Settings → Community plugins instead.`,
      };
    }
    try {
      const res = await requestUrl({ url: asset.browser_download_url, throw: false });
      if (res.status >= 400) throw new Error(`HTTP ${res.status}`);
      await adapter.writeBinary(normalizePath(`${dir}/${name}`), res.arrayBuffer);
    } catch (e) {
      return {
        ok: false,
        error: `Downloading ${companion.name} failed: ${(e as Error).message}`,
      };
    }
  }

  const anyApp = app as any;
  try {
    if (typeof anyApp.plugins?.loadManifests === 'function') {
      await anyApp.plugins.loadManifests();
    }
  } catch {
    /* ignore — enabling below will still load from disk after a restart */
  }
  try {
    await anyApp.plugins.enablePlugin(companion.id);
  } catch {
    return {
      ok: false,
      version: rel.tag,
      error: `${companion.name} was downloaded, but Obsidian couldn't enable it automatically. Enable it in Settings → Community plugins — and if you see "Restricted mode" (or "Turn on community plugins"), turn that on first.`,
    };
  }
  return { ok: true, version: rel.tag };
}

export async function enableCompanionPlugin(
  plugin: ReferenceList,
  key: string
): Promise<boolean> {
  const companion = COMPANIONS[key];
  if (!companion) return false;
  try {
    await (plugin.app as any).plugins.enablePlugin(companion.id);
    return true;
  } catch {
    return false;
  }
}

/** Install and show a Notice; returns true when the plugin is now enabled. */
export async function installCompanionPluginWithNotice(
  plugin: ReferenceList,
  key: string
): Promise<boolean> {
  const companion = COMPANIONS[key];
  const r = await installCompanionPlugin(plugin, key);
  if (r.ok) {
    new Notice(
      `ScholarWeft: installed and enabled ${companion.name}${
        r.version ? ` (${r.version})` : ''
      }. Click the button again to finish setting it up.`,
      9000
    );
    return true;
  }
  new Notice(`ScholarWeft: ${r.error}`, 12000);
  return false;
}
