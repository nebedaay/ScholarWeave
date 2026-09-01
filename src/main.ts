import { Prec } from '@codemirror/state';
import {
  Editor,
  Events,
  MarkdownView,
  Menu,
  Modal,
  Notice,
  Platform,
  Plugin,
  TAbstractFile,
  TFile,
  WorkspaceLeaf,
  debounce,
  htmlToMarkdown,
  normalizePath,
  setIcon,
} from 'obsidian';

import {
  citeKeyCacheField,
  citeKeyPlugin,
  bibManagerField,
  editorTooltipHandler,
} from './editorExtension';
import { API_VERSION, LinkedCitationsApi } from './api';
import { cslToBibTeX } from './bib/bibtexSerializer';
import { t } from './lang/helpers';
import { processCiteKeys } from './markdownPostprocessor';
import {
  DEFAULT_SETTINGS,
  ReferenceListSettings,
  ReferenceListSettingsTab,
} from './settings';
import { TooltipManager } from './tooltip';
import { ReferenceListView, viewType } from './view';
import { PromiseCapability, debugLog } from './helpers';
import { isAbsolutePath } from './bib/helpers';
import { findPandoc } from './bib/pandoc';
import { BibManager, getScopedSettings } from './bib/bibManager';
import { CiteSuggest } from './citeSuggest/citeSuggest';
import { ExportModal } from './exportModal';
import { ImportModal } from './importModal';
import { CitekeyRenameModal } from './modals/citekeyRenameModal';
import { convertActiveNote, convertVault } from './pandocToLinked';
import { convertNoteToPandoc, convertVaultToPandoc } from './linkedToPandoc';
import { setupAssets } from './assetSetup';

const bibliographyExtensions = new Set(['bib', 'json', 'yaml', 'yml']);

function isBibliographyFile(file: TAbstractFile): file is TFile {
  return file instanceof TFile && bibliographyExtensions.has(file.extension);
}

// Minimal posix-style path helpers for vault paths (always forward-slash).
function posixDirname(p: string): string {
  const idx = p.lastIndexOf('/');
  return idx <= 0 ? '' : p.slice(0, idx);
}

function posixBasename(p: string): string {
  return p.split('/').pop() ?? p;
}

function posixRelative(from: string, to: string): string {
  const a = from.split('/').filter(Boolean);
  const b = to.split('/').filter(Boolean);
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return [...a.slice(i).map(() => '..'), ...b.slice(i)].join('/') || '.';
}

function getFileRelativePath(sourceFile: TFile, targetPath: string) {
  const sourceDir = posixDirname(sourceFile.path);
  const rel = posixRelative(sourceDir, targetPath);
  return rel || posixBasename(targetPath);
}

function bibliographyMatchesPath(
  sourceFile: TFile,
  bibliography: string,
  targetPath: string
) {
  const sourceDir = posixDirname(sourceFile.path);
  const normalizedBibliography = normalizePath(bibliography);
  const noteRelativePath = normalizePath(`${sourceDir}/${normalizedBibliography}`);
  const vaultRelativePath = normalizePath(normalizedBibliography);

  if (noteRelativePath === targetPath || vaultRelativePath === targetPath) {
    return true;
  }

  if (isAbsolutePath(bibliography)) {
    // Absolute path: compare normalised strings directly.
    const vaultRoot = (app.vault.adapter as any).getBasePath?.() ?? '';
    const targetAbs = vaultRoot ? `${vaultRoot}/${targetPath}` : targetPath;
    return bibliography.replace(/\\/g, '/') === targetAbs.replace(/\\/g, '/');
  }

  return false;
}

function updateBibliographyPath(
  sourceFile: TFile,
  bibliography: unknown,
  oldPath: string,
  newPath: string
) {
  const getUpdatedPath = (value: unknown) => {
    if (
      typeof value === 'string' &&
      bibliographyMatchesPath(sourceFile, value, oldPath)
    ) {
      return getFileRelativePath(sourceFile, newPath);
    }

    return value;
  };

  if (Array.isArray(bibliography)) {
    let changed = false;
    const updated = bibliography.map((value) => {
      const next = getUpdatedPath(value);
      changed ||= next !== value;
      return next;
    });

    return changed ? updated : bibliography;
  }

  return getUpdatedPath(bibliography);
}

export default class ReferenceList extends Plugin {
  api: LinkedCitationsApi;
  settings: ReferenceListSettings;
  emitter: Events;
  tooltipManager: TooltipManager;
  bibManager: BibManager;
  private citeSuggest: CiteSuggest;
  private _pendingCitedKeysIndex?: { mdCount?: number; files?: Record<string, string[]> };
  cacheDir = '.pandoc';
  _initPromise: PromiseCapability<void>;
  private processReferencesRun = 0;

  get initPromise() {
    if (!this._initPromise) {
      return (this._initPromise = new PromiseCapability());
    }
    return this._initPromise;
  }

  async onload() {
    const { app } = this;

    await this.loadSettings();

    // Extract bundled scripts and templates into the plugin directory so
    // users who installed via BRAT get everything they need automatically.
    await setupAssets(this);

    this.registerView(
      viewType,
      (leaf: WorkspaceLeaf) => new ReferenceListView(leaf, this)
    );

    this.emitter = new Events();
    this.bibManager = new BibManager(this);
    // Restore the persisted citation index now that bibManager exists.
    if (this._pendingCitedKeysIndex) {
      this.bibManager.deserializeCitedKeysIndex(this._pendingCitedKeysIndex);
      this._pendingCitedKeysIndex = undefined;
    }

    // Load the persistent rendered-citation cache from disk IMMEDIATELY —
    // before the workspace layout is ready and any note paints. Rendering
    // (postprocessor + live-preview CM field) lazy-hydrates from this via
    // bibManager.getCacheForPath(), so the FIRST frame of a cached note
    // already shows formatted citations. No raw [@key] flash, no forced
    // re-render. This does not depend on the Zotero engine (still loading);
    // validity uses the file mtime + persisted library version.
    await this.bibManager.loadRenderedCache();
    // Restore the persisted Zotero select-link / PDF maps so cold starts
    // skip the per-citekey HTTP fetch entirely.
    await this.bibManager.loadZLinks();
    this.api = {
      version: API_VERSION,
      focusReferenceListView: () => this.initLeaf(),
      getCitekeysForFile: (file?: TFile) => this.getCitekeysForFile(file),
    };

    debugLog('[lc:main] loaded settings:', JSON.stringify({
      bibliographyPaths: this.settings.bibliographyPaths,
      pullFromZotero: this.settings.pullFromZotero,
      zoteroGroups: this.settings.zoteroGroups,
      useNativeZoteroAPI: this.settings.useNativeZoteroAPI,
      zoteroPort: this.settings.zoteroPort,
      enableCiteKeyCompletion: this.settings.enableCiteKeyCompletion,
    }));

    this.initPromise.promise
      .then(async () => {
        const { settings, bibManager } = this;
        debugLog('[lc:main] initPromise.then fired — starting bib load');
        // Load sources in priority order: .bib first (lower priority),
        // Zotero on top (higher priority, wins on conflicts).
        if (settings.bibliographyPaths?.length) {
          await bibManager.loadGlobalBibFiles();
        } else {
          debugLog('[lc:main] no bibliographyPaths set, skipping .bib load');
        }
        if (settings.pullFromZotero) {
          await bibManager.loadAndRefreshGlobalZBib();
        } else {
          debugLog('[lc:main] pullFromZotero not set, skipping Zotero load');
        }
        // Build the Fuse index now so @ autocomplete is available immediately,
        // before the (slower) CSL engine compilation below.
        bibManager.buildFuseIndex();
        // CSL engine compilation can take 30–60 s on a large library — show a
        // persistent Notice so the user knows why autocomplete and citation
        // formatting aren't available yet.
        const hasSources =
          (settings.bibliographyPaths?.length ?? 0) > 0 || settings.pullFromZotero;
        const engineNotice = hasSources
          ? new Notice(
              'ScholarWeave: building citation engine… autocomplete and citation formatting will be ready shortly.',
              0
            )
          : null;
        // Build the CSL engine once, after all sources are merged.
        await bibManager.buildGlobalEngine();
        engineNotice?.hide();
        // Force all open reading-mode views to re-render now that the citation
        // engine is ready. The markdown post-processor runs synchronously when
        // Obsidian first paints a reading view — if the engine wasn't done yet
        // the file cache was empty and the citations stayed as raw text.
        // Calling rerender(true) triggers a full re-parse so formatted citations
        // appear without the user having to switch away and back.
        this.app.workspace.getLeavesOfType('markdown').forEach((leaf) => {
          const mv = leaf.view as any;
          if (mv?.getMode?.() === 'preview') {
            mv.previewMode?.rerender?.(true);
          }
        });
        debugLog('[lc:main] bib load complete, bibManager.initPromise resolving');
        // Incremental Zotero refresh runs async after the engine is ready.
        // If renames are detected, refreshGlobalZBib() schedules the
        // confirmation modal itself (works for startup and mid-session refreshes).
        if (settings.pullFromZotero) {
          bibManager.refreshGlobalZBib().catch(console.error);
        }
      })
      .finally(() => this.bibManager.initPromise.resolve());

    this.addSettingTab(new ReferenceListSettingsTab(this));
    this.citeSuggest = new CiteSuggest(app, this);
    this.registerEditorSuggest(this.citeSuggest);
    this.positionSuggest();
    // Re-position CiteSuggest in Obsidian's EditorSuggest queue as the user
    // types: front while typing a citation ([[@ / [@), back for plain "[["
    // wikilinks so Obsidian's native link suggest stays fast.
    this.registerEvent(
      app.workspace.on('editor-change', () => this.positionSuggest())
    );
    this.tooltipManager = new TooltipManager(this);
    this.registerMarkdownPostProcessor(processCiteKeys(this));
    this.registerEditorExtension([
      bibManagerField.init(() => this.bibManager),
      citeKeyCacheField,
      // Prec.highest: our replace-widgets must WIN over Obsidian's built-in
      // live-preview link widget. Both decorate the same [[@key]] range; CM
      // renders only the higher-precedence one. Without this, Obsidian's link
      // widget takes precedence and our rendered citations stay invisible in
      // live preview (plain brackets and ⟦…⟧ containers have no competing
      // Obsidian decoration, so they always worked).
      Prec.highest(citeKeyPlugin),
      editorTooltipHandler(this.tooltipManager),
    ]);

    // Attempt to auto-detect Pandoc on desktop if not already configured.
    findPandoc().then((found) => {
      if (found && !this.settings.pathToPandoc) {
        this.settings.pathToPandoc = found;
        this.saveSettings();
      }
    });

    this.initPromise.resolve();
    this.app.workspace.trigger('parse-style-settings');

    // Auto-open the reference panel on first launch or on mobile (where workspace
    // state isn't reliably persisted between sessions). On desktop after the first
    // open we let the workspace manage the panel's lifecycle — if the user closed
    // it we don't force it back open on every restart.
    //
    // We also guard against duplicate leaves: check getLeavesOfType() directly
    // rather than this.view, because the view's instanceof check returns null while
    // the workspace is still initializing a restored leaf (which would cause a
    // second leaf to be created alongside the restored one).
    // Open the reference list whenever it isn't already in the workspace
    // (e.g. on every app start / plugin enable). The user wants the panel
    // available without running "Show reference list" after each restart.
    this.app.workspace.onLayoutReady(() => {
      const hasLeaf = this.app.workspace.getLeavesOfType(viewType).length > 0;
      if (!hasLeaf) {
        this.initLeaf();
      }
    });

    this.addCommand({
      id: 'focus-reference-list-view',
      name: t('Show reference list'),
      callback: async () => {
        this.initLeaf();
      },
    });

    this.addCommand({
      id: 'insert-bibliography',
      name: t('Insert bibliography at cursor'),
      editorCallback: (editor: Editor, view: MarkdownView) => {
        if (!view.file) return;
        const cache = this.bibManager.fileCache.get(view.file);
        if (!cache?.bib) return;

        const entries = cache.bib.findAll('.csl-entry');
        if (!entries.length) return;

        const text = entries
          .map((e) => htmlToMarkdown(e.innerHTML).trim())
          .join('\n\n');

        editor.replaceSelection(text);
      },
    });

    this.addCommand({
      id: 'snapshot-bibliography',
      name: t('Save bibliography snapshot for this note'),
      checkCallback: (checking) => {
        const view = this.app.workspace.getActiveViewOfType(MarkdownView);
        if (!view?.file) return false;
        const entries = this.bibManager.snapshotEntries(view.file);
        if (!entries?.length) return false;
        if (!checking) this.openSnapshot(view.file, entries);
        return true;
      },
    });

    // "Sync literature note filenames" is now merged into the combined
    // "Update stale citekeys and literature note filenames" command below.

    this.addCommand({
      id: 'create-missing-lit-notes-note',
      name: t('Create literature notes for citations lacking notes (current note)'),
      callback: async () => {
        const view = this.app.workspace.getActiveViewOfType(MarkdownView);
        if (!view?.file) return;
        const progress = new Notice('Creating literature notes…', 0);
        // Force the progress bar visible immediately (indeterminate until the
        // first item completes).
        (progress as any).setProgress?.(0, 0);
        const { created, missingKeys } = await this.bibManager.createMissingLitNotes(
          { file: view.file },
          (done, total) => (progress as any).setProgress?.(done, total)
        );
        progress.hide();
        // Re-render the sidebar reference list so per-entry buttons flip from
        // "Create literature note" to "Open literature note".
        this.processReferences();
        new Notice(
          missingKeys.length
            ? `Created literature notes for ${created}/${missingKeys.length} missing citations.`
            : 'All citations in this note already have literature notes.',
          6000
        );
      },
    });

    this.addCommand({
      id: 'create-missing-lit-notes-vault',
      name: t('Create literature notes for citations lacking notes (vault)'),
      callback: async () => {
        const progress = new Notice('Creating literature notes…', 0);
        // Force the progress bar visible immediately (indeterminate until the
        // first item completes).
        (progress as any).setProgress?.(0, 0);
        const { created, missingKeys } = await this.bibManager.createMissingLitNotes(
          { allVault: true },
          (done, total) => (progress as any).setProgress?.(done, total)
        );
        progress.hide();
        // Re-render the sidebar so entry buttons reflect the new notes.
        this.processReferences();
        new Notice(
          missingKeys.length
            ? `Created literature notes for ${created}/${missingKeys.length} missing citations vault-wide.`
            : 'All cited works in the vault already have literature notes.',
          6000
        );
      },
    });

    // Document Compiler — outline → markdown, and outline/markdown → docx.
    // Desktop only: runs the bundled scripts/DocumentCompiler.py with Python 3.
    // A single command opens a modal with the TOC / footnotes / output-folder
    // options (template-aware defaults) and Compile / Export buttons.
    if (Platform.isDesktop) {
      this.addCommand({
        id: 'compile-export-book',
        name: t('Compile and export a book, article, or other document (outline or markdown)'),
        checkCallback: (checking) => {
          const file = app.workspace.getActiveViewOfType(MarkdownView)?.file;
          if (!file) return false;
          if (!checking) {
            new ExportModal(app, this, file).open();
          }
          return true;
        },
      });
    }

    // Import a DOCX or ODT file with Zotero citation fields into the vault
    // as a Markdown note, then optionally link citations and create lit notes.
    if (Platform.isDesktop) {
      this.addCommand({
        id: 'import-document',
        name: t('Import a Word or ODT document with Zotero citations'),
        callback: () => {
          new ImportModal(app, this).open();
        },
      });
    }

    // Convert pandoc citations ([@key]) in the current note to linked
    // citations ([[@key]]), resolving keys against the plugin's Zotero index.
    this.addCommand({
      id: 'convert-pandoc-to-linked',
      name: t('Convert pandoc citations to linked citations (current note)'),
      checkCallback: (checking) => {
        const file = app.workspace.getActiveViewOfType(MarkdownView)?.file;
        if (!file) return false;
        if (!checking) {
          void convertActiveNote(this, file);
        }
        return true;
      },
    });

    // Convert pandoc citations in every vault note to linked citations.
    this.addCommand({
      id: 'convert-pandoc-to-linked-vault',
      name: t('Convert pandoc citations to linked citations (vault)'),
      callback: () => { void convertVault(this); },
    });

    // Revert linked citations back to pandoc-style in the current note.
    this.addCommand({
      id: 'revert-to-pandoc-current-note',
      name: t('Revert linked citations to pandoc-style citations (current note)'),
      checkCallback: (checking) => {
        const file = app.workspace.getActiveViewOfType(MarkdownView)?.file;
        if (!file) return false;
        if (!checking) void convertNoteToPandoc(this, file);
        return true;
      },
    });

    // Revert linked citations back to pandoc-style across the entire vault.
    this.addCommand({
      id: 'revert-to-pandoc-vault',
      name: t('Revert linked citations to pandoc-style citations (vault)'),
      callback: () => { void convertVaultToPandoc(this); },
    });

    // Scan the vault for stale citekeys (from rename history) and offer to
    // update them — and optionally sync literature note filenames — after the
    // user confirms a summary of affected files.
    this.addCommand({
      id: 'update-stale-citekeys',
      name: t('Update stale citekeys and literature note filenames (vault)'),
      callback: () => { void this.showCitekeyRenameDialog(); },
    });

    // Clear the accumulated citekey rename history. Use when all vault notes
    // are known to be up-to-date and the history is no longer needed.
    this.addCommand({
      id: 'purge-citekey-rename-history',
      name: t('Purge citekey rename history'),
      callback: () => {
        const count = Object.keys(this.settings.citekeyRenameHistory ?? {}).length;
        if (!count) {
          new Notice('Citekey rename history is already empty.');
          return;
        }
        this.settings.citekeyRenameHistory = {};
        this.saveSettings();
        new Notice(`Cleared ${count} citekey rename record${count !== 1 ? 's' : ''}.`);
      },
    });

    document.body.toggleClass(
      'lc-tooltips',
      this.settings.showCitekeyTooltips !== false
    );
    document.body.toggleClass(
      'lc-decorations',
      this.settings.showCitationDecorations ?? true
    );
    this.applyCitationColors();

    this.registerEvent(
      app.metadataCache.on(
        'changed',
        debounce(
          async (file) => {
            await this.initPromise.promise;
            await this.bibManager.initPromise.promise;

            const activeView = app.workspace.getActiveViewOfType(MarkdownView);
            if (activeView && file === activeView.file) {
              this.processReferences();
            }
          },
          100,
          true
        )
      )
    );

    this.registerEvent(
      app.workspace.on(
        'active-leaf-change',
        debounce(
          async (leaf) => {
            await this.initPromise.promise;
            await this.bibManager.initPromise.promise;

            app.workspace.iterateRootLeaves((rootLeaf) => {
              if (rootLeaf === leaf) {
                if (leaf.view instanceof MarkdownView) {
                  this.processReferences();
                } else {
                  this.view?.setNoContentMessage();
                }
              }
            });
          },
          100,
          true
        )
      )
    );

    this.registerEvent(
      app.vault.on(
        'rename',
        debounce(
          async (file, oldPath) => {
            await this.initPromise.promise;
            await this.bibManager.initPromise.promise;

            if (isBibliographyFile(file)) {
              await this.updateBibliographyFrontmatter(oldPath, file.path);
            }

            // Keep the citation index in sync across renames.
            this.bibManager.removeFromCitedKeysIndex(oldPath);
            if (file instanceof TFile) {
              await this.bibManager.updateCitedKeysIndex(file);
              this.persistCitedKeysIndex();
            }
            this.persistRenderedCache();

            const activeView = app.workspace.getActiveViewOfType(MarkdownView);
            if (activeView?.file instanceof TFile) {
              this.bibManager.fileCache.delete(activeView.file);
              this.processReferences();
            }
          },
          100,
          true
        )
      )
    );

    // Keep the citation index current as files are created / modified.
    this.registerEvent(
      app.vault.on(
        'modify',
        debounce(
          async (file) => {
            if (!(file instanceof TFile)) return;
            await this.bibManager.updateCitedKeysIndex(file);
            this.persistCitedKeysIndex();
            this.persistRenderedCache();
            // Re-render the modified note: the in-memory cache + dispatched
            // hash are stale after an edit, so without this the citations
            // keep showing the OLD render (or stay as regular links) until
            // the next file switch — and a stale widget can corrupt the
            // live-preview viewport (vanishing paragraphs).
            if (this.app.workspace.getActiveFile()?.path === file.path) {
              this.bibManager.invalidateFile(file);
              this.processReferences();
            }
          },
          150,
          true
        )
      )
    );
    this.registerEvent(
      app.vault.on(
        'create',
        debounce(
          async (file) => {
            if (!(file instanceof TFile)) return;
            await this.bibManager.updateCitedKeysIndex(file);
            this.persistCitedKeysIndex();
            this.persistRenderedCache();
          },
          150,
          true
        )
      )
    );
    this.registerEvent(
      app.vault.on(
        'delete',
        (file) => {
          this.bibManager.removeFromCitedKeysIndex(file.path);
          this.persistCitedKeysIndex();
        }
      )
    );

    (async () => {
      this.initStatusBar();
      this.setStatusBarLoading();

      await this.initPromise.promise;
      await this.bibManager.initPromise.promise;

      this.setStatusBarIdle();
      // The first reading-mode render runs before the bib engine is ready, so
      // the post-processor has no cache and leaves container citations as raw
      // text.  getCacheForPath then hydrates from the persisted cache and
      // stamps dispatchedHashes — which causes dispatchResult to skip the
      // re-render that would fix the formatting.  Clear the hash guard for the
      // active file so dispatchResult triggers a fresh DOM re-render once
      // processReferences() finishes.
      const activeView = this.app.workspace.getActiveViewOfType(MarkdownView);
      if (activeView?.file) {
        this.bibManager.invalidateFile(activeView.file);
      }
      this.processReferences();
    })();
  }

  onunload() {
    document.body.removeClass('lc-tooltips');
    this.app.workspace
      .getLeavesOfType(viewType)
      .forEach((leaf) => leaf.detach());
    void this.bibManager.saveRenderedCache();
    void this.bibManager.saveZLinks();
    this.bibManager.destroy();
  }

  async updateBibliographyFrontmatter(oldPath: string, newPath: string) {
    oldPath = normalizePath(oldPath);
    newPath = normalizePath(newPath);

    for (const file of this.app.vault.getMarkdownFiles()) {
      const metadata = this.app.metadataCache.getFileCache(file);
      if (!metadata?.frontmatter?.bibliography) continue;

      let changed = false;
      try {
        await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
          const nextBibliography = updateBibliographyPath(
            file,
            frontmatter.bibliography,
            oldPath,
            newPath
          );

          if (nextBibliography !== frontmatter.bibliography) {
            frontmatter.bibliography = nextBibliography;
            changed = true;
          }
        });

        if (changed) {
          this.bibManager.fileCache.delete(file);
        }
      } catch (e) {
        console.error(e);
      }
    }
  }

  statusBarIcon: HTMLElement;
  initStatusBar() {
    const ico = (this.statusBarIcon = this.addStatusBarItem());
    ico.addClass('lc-status-icon', 'clickable-icon');
    ico.setAttr('aria-label', t('Linked Citations settings'));
    ico.setAttr('data-tooltip-position', 'top');
    this.setStatusBarIdle();
    let isOpen = false;
    ico.addEventListener('click', () => {
      if (isOpen) return;
      const { settings } = this;
      const menu = (new Menu() as any)
        .addSections(['settings', 'actions'])
        .addItem((item: any) =>
          item
            .setSection('settings')
            .setIcon('lucide-message-square')
            .setTitle(t('Show citekey tooltips'))
            .setChecked(!!settings.showCitekeyTooltips)
            .onClick(() => {
              this.settings.showCitekeyTooltips = !settings.showCitekeyTooltips;
              this.saveSettings();
            })
        )
        .addItem((item: any) =>
          item
            .setSection('settings')
            .setIcon('lucide-at-sign')
            .setTitle(t('Show citekey suggestions'))
            .setChecked(!!settings.enableCiteKeyCompletion)
            .onClick(() => {
              this.settings.enableCiteKeyCompletion =
                !settings.enableCiteKeyCompletion;
              this.saveSettings();
            })
        )
        .addItem((item: any) =>
          item
            .setSection('actions')
            .setIcon('lucide-rotate-cw')
            .setTitle(t('Refresh bibliography'))
            .onClick(async () => {
              const activeView =
                this.app.workspace.getActiveViewOfType(MarkdownView);
              if (activeView) {
                const file = activeView.file;

                if (this.bibManager.fileCache.has(file)) {
                  const cache = this.bibManager.fileCache.get(file);
                  if (cache.source !== this.bibManager) {
                    this.bibManager.fileCache.delete(file);
                    this.processReferences();
                    return;
                  }
                }
              }

              this.bibManager.reinit(true);
              await this.bibManager.initPromise.promise;
              this.processReferences();
            })
        );

      const rect = ico.getBoundingClientRect();
      menu.onHide(() => {
        isOpen = false;
      });
      menu.setParentElement(ico).showAtPosition({
        x: rect.x,
        y: rect.top - 5,
        width: rect.width,
        overlap: true,
        left: false,
      });
      isOpen = true;
    });
  }

  setStatusBarLoading() {
    this.statusBarIcon.addClass('is-loading');
    setIcon(this.statusBarIcon, 'lucide-loader');
  }

  setStatusBarIdle() {
    this.statusBarIcon.removeClass('is-loading');
    setIcon(this.statusBarIcon, 'lucide-at-sign');
  }

  get view() {
    const leaves = this.app.workspace.getLeavesOfType(viewType);
    if (!leaves?.length) return null;
    const v = leaves[0].view;
    return v instanceof ReferenceListView ? v : null;
  }

  async initLeaf() {
    // Guard against duplicates using getLeavesOfType rather than this.view:
    // this.view's instanceof check returns null while the workspace is still
    // initialising a restored leaf, which would otherwise create a second leaf.
    if (this.app.workspace.getLeavesOfType(viewType).length) {
      return this.revealLeaf();
    }

    // getRightLeaf(false) can return null on mobile or when the workspace
    // isn't fully ready yet — guard before chaining .setViewState().
    const leaf = this.app.workspace.getRightLeaf(false);
    if (!leaf) return;

    await leaf.setViewState({ type: viewType });

    this.revealLeaf();

    // Remember that we've opened the panel at least once so that the
    // onLayoutReady auto-open doesn't fire on every subsequent desktop restart.
    if (!this.settings.panelAutoOpened) {
      this.settings.panelAutoOpened = true;
      this.saveSettings();
    }

    await this.initPromise.promise;
    await this.bibManager.initPromise.promise;

    // Rebuild the citation index on startup if the persisted one is missing,
    // empty, or stale — otherwise a failed load would silently shrink it.
    void this.ensureCitedKeysIndex();

    const activeView = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (activeView) {
      this.processReferences();
    }

    // Background warm-up: pre-render citations for files not currently open
    // so the first open of any cited note is instant. Safe to run while the
    // user works (each render is fast; small yields keep the UI responsive;
    // PDF lookups are skipped while warming).
    setTimeout(() => {
      void this.bibManager.warmCitedFiles();
    }, 2500);
  }

  revealLeaf() {
    const leaves = this.app.workspace.getLeavesOfType(viewType);
    if (!leaves?.length) return;
    this.app.workspace.revealLeaf(leaves[0]);
  }

  async getCitekeysForFile(file?: TFile) {
    const target = file ?? this.app.workspace.getActiveFile();
    if (!target) return [];

    const cached = this.bibManager.fileCache.get(target);
    if (cached?.keys) return Array.from(cached.keys);

    try {
      const content = await this.app.vault.cachedRead(target);
      await this.bibManager.getReferenceList(target, content);
      return Array.from(this.bibManager.fileCache.get(target)?.keys ?? []);
    } catch (error) {
      console.error('ScholarWeave: failed to read citekeys for API consumer', error);
      return [];
    }
  }

  async loadSettings() {
    const saved = (await this.loadData()) ?? {};

    // Restore the persisted citation index from its own file (.pandoc/
    // cited-keys.json) — NOT from data.json, which saveSettings() rewrites and
    // would otherwise clobber the index. Loaded lazily after bibManager exists.
    try {
      const cached = await this.app.vault.adapter.read(
        normalizePath(`${this.cacheDir}/cited-keys.json`)
      );
      this._pendingCitedKeysIndex = JSON.parse(cached);
    } catch {
      // no persisted index yet — first build will populate it
    }

    // Migration: these settings defaulted to false in older builds due to a bug
    // (undefined was treated as false in the UI, so the toggle appeared off and
    // may have been saved as false). Since the feature was never reliably on,
    // we reset any saved false so the new default (true) takes effect.
    // Users who intentionally disable these can still do so via the settings tab.
    if (saved.enableCiteKeyCompletion === false) delete saved.enableCiteKeyCompletion;
    if (saved.showCitekeyTooltips === false) delete saved.showCitekeyTooltips;
    // formatLinkAliases is now on by default (merged into the single
    // "Process linked citations" toggle). Reset any saved false so the new
    // default (true) takes effect for existing users.
    if (saved.formatLinkAliases === false) delete saved.formatLinkAliases;

    // Migrate single pathToBibliography → bibliographyPaths array.
    if (saved.pathToBibliography && !saved.bibliographyPaths?.length) {
      saved.bibliographyPaths = [saved.pathToBibliography];
      delete saved.pathToBibliography;
    }

    this.settings = Object.assign({}, DEFAULT_SETTINGS, saved);
  }

  // Keep CiteSuggest in the right place in Obsidian's shared EditorSuggest
  // queue, based on what the user is actually typing:
  //   - "[[@..." — an unambiguous citation. MUST be at the front, or
  //     Obsidian's native link suggester (which fires on "[[") wins and the
  //     plugin never sees the trigger (native search can't show unimported
  //     references).
  //   - "[@..." — a pandoc citation. Front when prioritizeCiteKeyCompletion
  //     is on, so we win over any other plugin's "@" suggester (e.g. ZotLit).
  //   - anything else (plain "[[note", prose) — pushed to the BACK so
  //     Obsidian's native link suggest is consulted first. Keeping us at the
  //     front unconditionally made plain "[[" wikilinks slow: the native
  //     suggest only ran after our onTrigger (which returns null for non-@
  //     text), delaying its popup and resetting its query window.
  // Called on load, on setting change, and on every editor-change.
  private suggestPosition: 'front' | 'back' | null = null;

  private positionSuggest() {
    const suggests = (this.app.workspace as any).editorSuggest?.suggests as unknown[] | undefined;
    if (!Array.isArray(suggests) || !this.citeSuggest) return;
    const idx = suggests.indexOf(this.citeSuggest);
    if (idx === -1) return;

    const wantFront = this.suggestWantsFront();
    const target: 'front' | 'back' = wantFront ? 'front' : 'back';
    if (this.suggestPosition === target) return; // already positioned
    this.suggestPosition = target;

    suggests.splice(idx, 1);
    if (target === 'front') suggests.unshift(this.citeSuggest);
    else suggests.push(this.citeSuggest);
  }

  /** Does the current typing context need CiteSuggest at the front of the
   *  EditorSuggest queue? [[@... always; [@... when prioritization is on. */
  private suggestWantsFront(): boolean {
    const view = this.app.workspace.getActiveViewOfType(MarkdownView);
    const editor = view?.editor;
    if (!editor) return false;
    const cursor = editor.getCursor();
    const line = editor.getLine(cursor.line).slice(0, cursor.ch);
    if (/\[\[@/.test(line)) return true;
    if (this.settings.prioritizeCiteKeyCompletion !== false && /\[@/.test(line)) return true;
    return false;
  }

  /** Apply the three decoration underline colors from settings as CSS custom
   *  properties on document.body, overriding the stylesheet defaults.
   *  Only fires when a value has been explicitly saved; unset keys leave the
   *  stylesheet default intact. */
  private applyCitationColors() {
    const { decorationColorUnlinked, decorationColorLinked, decorationColorUnimported } =
      this.settings;
    if (decorationColorUnlinked) {
      document.body.style.setProperty('--lc-citation-underline-color-unlinked', decorationColorUnlinked);
    }
    if (decorationColorLinked) {
      document.body.style.setProperty('--lc-wikilink-linked-color', decorationColorLinked);
    }
    if (decorationColorUnimported) {
      document.body.style.setProperty('--lc-wikilink-unimported-color', decorationColorUnimported);
    }
  }

  async saveSettings(cb?: () => void) {
    document.body.toggleClass(
      'lc-tooltips',
      this.settings.showCitekeyTooltips !== false
    );
    document.body.toggleClass(
      'lc-decorations',
      this.settings.showCitationDecorations ?? true
    );
    this.applyCitationColors();

    this.positionSuggest();

    // Refresh the reference list when settings change
    this.emitSettingsUpdate(cb);
    await this.saveData(this.settings);
  }

  /** Persist the citation index to its own file (`.pandoc/cited-keys.json`),
   *  so settings saves (saveData) can never clobber it. Debounced. */
  persistCitedKeysIndex = debounce(async () => {
    if (!this.bibManager.citedKeysIndexDirty) return;
    // Never write an index that hasn't been fully built (mdCount 0 means the
    // persisted index failed to load or was never built). Writing it would
    // clobber a good on-disk index with a near-empty one.
    if (this.bibManager.indexMdCount <= 0) return;
    try {
      const path = normalizePath(`${this.cacheDir}/cited-keys.json`);
      if (!(await this.app.vault.adapter.exists(normalizePath(this.cacheDir)))) {
        await this.app.vault.adapter.mkdir(normalizePath(this.cacheDir));
      }
      await this.app.vault.adapter.write(
        path,
        JSON.stringify(this.bibManager.serializeCitedKeysIndex())
      );
      this.bibManager.citedKeysIndexDirty = false;
    } catch (e) {
      console.warn('[lc] persistCitedKeysIndex: error', e);
    }
  }, 2000);

  /** Debounced flush of the persistent rendered-citation cache. */
  persistRenderedCache = debounce(async () => {
    await this.bibManager.saveRenderedCache();
  }, 3000);

  /** Debounced flush of the Zotero select-link / PDF maps. */
  persistZLinks = debounce(async () => {
    await this.bibManager.saveZLinks();
  }, 5000);

  /**
   * Verify the persisted citation index is usable and rebuild it once at
   * startup if it is missing, empty, or stale (file count changed while
   * Obsidian was closed). Guards against a transient read/parse failure
   * leaving a near-empty index that would otherwise overwrite the good one
   * via incremental modify/create events.
   */
  async ensureCitedKeysIndex() {
    const { bibManager } = this;
    const mdCount = this.app.vault
      .getMarkdownFiles()
      .filter((f) => bibManager.isIndexablePath(f.path)).length;
    const healthy =
      bibManager.indexMdCount > 0 && bibManager.indexMdCount === mdCount;
    if (healthy) return;
    await bibManager.buildCitedKeysIndex();
    this.persistCitedKeysIndex();
  }

  emitSettingsUpdate = debounce(
    (cb?: () => void) => {
      if (this.initPromise.settled) {
        this.view?.contentEl.toggleClass(
          'collapsed-links',
          !!this.settings.hideLinks
        );

        cb && cb();

        this.processReferences();
      }
    },
    5000,
    true
  );

  openSnapshot(file: TFile, entries: import('./bib/types').PartialCSLEntry[]) {
    new BibSnapshotModal(this.app, this, file, entries).open();
  }

  /**
   * Auto-update stale citekeys in the currently open note only (no modal,
   * no vault scan).  Called automatically after a Zotero sync detects renames.
   */
  async autoUpdateCurrentNote(renameMap: Map<string, string>) {
    const file = this.app.workspace.getActiveViewOfType(MarkdownView)?.file;
    if (!file || !renameMap.size) return;

    const changed = await this.bibManager.applyRenamesInFile(file, renameMap);
    if (changed.length) {
      const summary = changed.map((c) => `@${c.oldKey} → @${c.newKey}`).join(', ');
      new Notice(`Auto-updated citekeys in current note: ${summary}`);
    }
  }

  /**
   * Scan the vault for stale citekeys using the persisted rename history
   * (or `overrideMap` if provided), show a confirmation modal, and on confirm
   * apply the renames and optionally sync literature note filenames.
   */
  async showCitekeyRenameDialog(overrideMap?: Record<string, string>) {
    const renameMap = overrideMap ?? this.settings.citekeyRenameHistory ?? {};
    if (!Object.keys(renameMap).length) {
      new Notice('No citekey rename history found.');
      return;
    }

    const progress = new Notice('Scanning vault for stale citekeys…', 0);
    let plan: import('./modals/citekeyRenameModal').RenamePlan;
    try {
      plan = await this.bibManager.findCitekeyUsagesInVault(renameMap);
    } finally {
      progress.hide();
    }

    if (!plan.size) {
      new Notice('No stale citekeys found in vault notes.');
      return;
    }

    new CitekeyRenameModal(this.app, plan, async (includeLitNotes) => {
      await this.bibManager.applyRenames(plan);
      const fileCount = plan.size;
      let msg = `Updated citekeys in ${fileCount} file${fileCount !== 1 ? 's' : ''}.`;

      if (includeLitNotes) {
        const renamed = await this.bibManager.syncLitNoteFilenames();
        if (renamed.length) {
          msg += `\nRenamed ${renamed.length} literature note${renamed.length !== 1 ? 's' : ''}: ` +
            renamed.map((r) => `${r.from.split('/').pop()} → ${r.to.split('/').pop()}`).join(', ');
        }
      }

      new Notice(msg, 6000);
    }).open();
  }

  /**
   * Open a modal for the unresolved-citation badge in the reference panel.
   * Partitions the file's unresolved citekeys into:
   *   - fixable: keys present in the rename history → plan + "Fix stale citekeys" button
   *   - truly unresolved: no known replacement → listed for info only
   */
  async showUnresolvedCitekeyDialog(file: TFile) {
    const fileCache = this.bibManager.fileCache.get(file);
    if (!fileCache || !fileCache.unresolvedKeys.size) {
      new Notice('No unresolved citations in the current note.');
      return;
    }

    const history = this.settings.citekeyRenameHistory ?? {};
    const fixableMap: Record<string, string> = {};
    const trulyUnresolved: string[] = [];

    for (const key of fileCache.unresolvedKeys) {
      if (history[key]) {
        fixableMap[key] = history[key];
      } else {
        trulyUnresolved.push(key);
      }
    }

    // Build a single-file rename plan from the fixable keys.
    const plan: import('./modals/citekeyRenameModal').RenamePlan = new Map();
    if (Object.keys(fixableMap).length) {
      const changes = await this.bibManager.findCitekeyUsagesInFile(file, fixableMap);
      if (changes.length) plan.set(file, changes);
    }

    new CitekeyRenameModal(
      this.app,
      plan,
      async (_includeLitNotes) => {
        await this.bibManager.applyRenames(plan);
        new Notice(`Updated stale citekeys in current note.`);
      },
      false,        // showLitNotesOption — not relevant for per-note fix
      trulyUnresolved
    ).open();
  }

  processReferences = async () => {
    const run = ++this.processReferencesRun;
    const isCurrent = () => run === this.processReferencesRun;
    const { settings, view } = this;
    const activeView = this.app.workspace.getActiveViewOfType(MarkdownView);
    const scopedSettings = activeView
      ? getScopedSettings(activeView.file)
      : null;

    if (
      !settings.bibliographyPaths?.length &&
      !settings.pullFromZotero &&
      !scopedSettings?.bibliography?.length
    ) {
      return view?.setMessage(
        t(
          'Please provide the path to your bibliography file in the Linked Citations plugin settings.'
        )
      );
    }

    if (activeView) {
      try {
        const fileContent = await this.app.vault.cachedRead(activeView.file);
        if (!isCurrent()) return;
        const bib = await this.bibManager.getReferenceList(
          activeView.file,
          fileContent,
          isCurrent
        );
        if (!isCurrent()) return;
        const cache = this.bibManager.fileCache.get(activeView.file);

        // Only warn about Zotero being unreachable when there is no .bib
        // fallback and some keys are genuinely unresolved.
        if (
          !bib &&
          settings.pullFromZotero &&
          !settings.bibliographyPaths?.length &&
          !(await this.bibManager.isZoteroAvailable()) &&
          isCurrent() &&
          cache?.keys.size
        ) {
          view?.setMessage(t('Cannot connect to Zotero'));
        } else {
          view?.setViewContent(bib);
        }
      } catch (e) {
        console.error(e);
        view?.setMessage((e as Error).message);
      }
    } else {
      view?.setNoContentMessage();
    }
  };
}

/** Modal that lets the user choose a filename and location for the snapshot,
 *  then writes the CSL-JSON file and wires it into the note's frontmatter. */
class BibSnapshotModal extends Modal {
  private plugin: ReferenceList;
  private file: TFile;
  private entries: import('./bib/types').PartialCSLEntry[];

  constructor(
    app: import('obsidian').App,
    plugin: ReferenceList,
    file: TFile,
    entries: import('./bib/types').PartialCSLEntry[]
  ) {
    super(app);
    this.plugin = plugin;
    this.file = file;
    this.entries = entries;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl('h3', { text: t('Save bibliography snapshot') });
    contentEl.createEl('p', {
      text: `${this.entries.length} entries will be saved as a .bib file. The file path will be added to this note's frontmatter "bibliography" key.`,
    });

    const folder = this.file.parent?.path ?? '';
    const stem = this.file.basename;
    const defaultPath = normalizePath(
      (folder ? folder + '/' : '') + stem + '-bibliography.bib'
    );

    const inputWrap = contentEl.createDiv({ cls: 'lc-snapshot-input-wrap' });
    inputWrap.createEl('label', { text: t('Save as') });
    const input = inputWrap.createEl('input', {
      type: 'text',
      value: defaultPath,
      cls: 'lc-snapshot-input',
    });
    input.style.width = '100%';

    const btnRow = contentEl.createDiv({ cls: 'lc-snapshot-btn-row' });
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.gap = '8px';
    btnRow.style.marginTop = '12px';

    const cancelBtn = btnRow.createEl('button', { text: t('Cancel') });
    cancelBtn.addEventListener('click', () => this.close());

    const saveBtn = btnRow.createEl('button', {
      text: t('Save'),
      cls: 'mod-cta',
    });
    saveBtn.addEventListener('click', () => this.doSave(input.value.trim()));

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.doSave(input.value.trim());
      if (e.key === 'Escape') this.close();
    });

    setTimeout(() => { input.select(); }, 50);
  }

  private async doSave(rawPath: string) {
    if (!rawPath) return;
    const savePath = normalizePath(rawPath);

    try {
      // Ensure parent directory exists.
      const dir = savePath.includes('/')
        ? savePath.substring(0, savePath.lastIndexOf('/'))
        : '';
      if (dir && !(await this.app.vault.adapter.exists(dir))) {
        await this.app.vault.adapter.mkdir(dir);
      }

      // Serialize to BibTeX and write.
      await this.app.vault.adapter.write(savePath, cslToBibTeX(this.entries));

      // Compute vault-relative path for the frontmatter link.
      const noteDir = this.file.parent?.path ?? '';
      const relPath = noteDir
        ? normalizePath(savePath).replace(normalizePath(noteDir) + '/', '')
        : savePath;

      // Add path to the note's bibliography frontmatter key.
      // Pandoc and other tools read this; the plugin uses it for colour comparison.
      await this.app.fileManager.processFrontMatter(this.file, (fm) => {
        const existing: string[] = Array.isArray(fm.bibliography)
          ? fm.bibliography
          : fm.bibliography ? [fm.bibliography] : [];
        if (!existing.includes(relPath) && !existing.includes(savePath)) {
          existing.push(relPath);
        }
        fm.bibliography = existing.length === 1 ? existing[0] : existing;
      });

      new Notice(`Bibliography saved to ${savePath}`);
      this.plugin.bibManager.reinit(true);
      this.close();
    } catch (e) {
      new Notice(`Failed to save bibliography: ${(e as Error).message}`);
    }
  }

  onClose() {
    this.contentEl.empty();
  }
}
