import { FuzzySuggestModal, Platform, PluginSettingTab, Setting, TFile } from 'obsidian';

import { t } from './lang/helpers';
import { findPandoc } from './bib/pandoc';
import { getBibPath } from './bib/helpers';
import { isZotLitSuggestActive } from './zotlit';
import ReferenceList from './main';
import ReactDOM from 'react-dom';
import React from 'react';
import { SettingItem } from './settings/SettingItem';
import { SearchSelect } from './settings/SearchSelect';
import { searchCSL, searchCSLLangs } from './settings/select.helpers';
import { FolderSuggest } from './settings/FolderSuggest';
import { BibFileSuggest } from './settings/BibFileSuggest';
import { cslListRaw } from './bib/cslList';
import { langListRaw } from './bib/cslLangList';
import { ZoteroPullSetting } from './settings/ZoteroPullSetting';
import { ZoteroStylePicker } from './settings/ZoteroStylePicker';

export const DEFAULT_SETTINGS: ReferenceListSettings = {
  pathToPandoc: '',
  bibliographyPaths: [],
  tooltipDelay: 400,
  zoteroGroups: [],
  renderCitations: true,
  renderCitationsReadingMode: true,
  renderLinkCitations: true,
  formatLinkAliases: true,
  showCitationDecorations: true,
  mobileClickAction: 'show',
  enableCiteKeyCompletion: true,
  prioritizeCiteKeyCompletion: true,
  showCitekeyTooltips: true,
  createNotesWithZotLit: true,
  /** Show per-entry PDF-open icons in the bibliography + tooltip link
   *  fallback. Off by default: opening in Zotero already reveals all
   *  attachments, and the lookup costs per-citekey network time. */
  showPdfLinks: false,
  /** Python 3 interpreter for the Document Compiler commands (blank = auto). */
  pathToPython: '',
  /** Vault-relative (or absolute) directory of user .docx export templates. */
  exportTemplatesDir: '',
  /** Default output folder for compiled/exported documents (blank = source folder). */
  defaultOutputDir: '',
  defaultAuthor: '',
  useAccountNameAsAuthor: false,
  styleMappings: [],
  styleMappingsEnabled: true,
};

export interface ZoteroGroup {
  id: number;
  name: string;
  lastUpdate?: number;
  /** Library version used for incremental sync with the native Zotero API. */
  libraryVersion?: number;
}

/**
 * A single callout-type → word-processor style mapping.
 * The `styleName` is the human-readable name as it appears in the template
 * (e.g. "Arabic poetry"); the export pipeline converts it to the
 * format-specific form automatically (ODT: "Arabic_20_poetry", DOCX: as-is).
 */
export interface StyleMapping {
  /** Stable UUID used for per-file enable/disable tracking. */
  id: string;
  /** Whether this mapping is on by default (can be overridden per-export). */
  enabled: boolean;
  /** Callout type or CSS class name, e.g. "arabic-poetry". */
  source: string;
  /** Human-readable style name as defined in the template, e.g. "Arabic poetry". */
  styleName: string;
}

export interface ReferenceListSettings {
  pathToPandoc?: string;
  /** @deprecated migrated to bibliographyPaths on first load */
  pathToBibliography?: string;
  bibliographyPaths: string[];

  cslStyleURL?: string;
  cslStylePath?: string;
  cslLang?: string;

  hideLinks?: boolean;
  showCitekeyTooltips?: boolean;
  showCitationDecorations?: boolean;
  tooltipDelay: number;
  enableCiteKeyCompletion?: boolean;
  prioritizeCiteKeyCompletion?: boolean;
  renderCitations?: boolean;
  renderCitationsReadingMode?: boolean;
  renderLinkCitations?: boolean;
  renderCitationsAsLinks?: boolean;
  /**
   * When true, aliased citation wikilinks of the form [[@key|alias]] are
   * parsed as Pandoc citations. The alias text becomes the citation
   * expression; the `@@` placeholder inside it expands to the link's own
   * citekey (e.g. [[@smith1992|see also @@, 6]] → [see also @smith1992, 6]).
   * Aliases without any citekey (e.g. [[@key|Just a label]]) are left
   * untouched. Controlled together with renderLinkCitations by the single
   * "Process linked citations" toggle. Both default to true.
   */
  formatLinkAliases?: boolean;

  literatureNoteFolder?: string;
  /** Show per-entry PDF-open icons in the bibliography + tooltip link
   *  fallback. Off by default (opening in Zotero shows all attachments; the
   *  PDF lookup costs per-citekey network time). */
  showPdfLinks?: boolean;
  /** Python 3 interpreter path for the Document Compiler commands (blank = auto). */
  pathToPython?: string;
  /** Vault-relative (or absolute) directory of user .docx export templates. */
  exportTemplatesDir?: string;
  /** Default output folder for compiled/exported documents (blank = source folder). */
  defaultOutputDir?: string;
  /** Last export format chosen in the export modal — remembered across opens. */
  lastExportFormat?: 'md' | 'docx' | 'odt';
  /** Default author name used when the note has no `author:` frontmatter property. */
  defaultAuthor?: string;
  /** When true, fall back to the Obsidian account display name if defaultAuthor is also empty. */
  useAccountNameAsAuthor?: boolean;
  /**
   * When true (default), the tooltip's "Create literature note" button hands
   * note creation off to ZotLit when it is available, so the note is rendered
   * with ZotLit's templates. When ZotLit is absent (or this is off), the
   * plugin's own basic template is used instead.
   */
  createNotesWithZotLit?: boolean;
  /** Action to take when a citation is tapped on mobile (no hover available). */
  mobileClickAction?: 'show' | 'copy' | 'link';
  pullFromZotero?: boolean;
  zoteroPort?: string;
  zoteroGroups: ZoteroGroup[];
  /**
   * When true, use the standard Zotero local REST API (Zotero 7/8 native
   * citationKey field) instead of the Better BibTeX JSON-RPC endpoint.
   * Better BibTeX does not need to be installed when this is enabled.
   */
  useNativeZoteroAPI?: boolean;
  /**
   * Set to true after the reference panel has been auto-opened for the first
   * time. Prevents the panel from re-opening on every subsequent desktop
   * restart if the user closes it.
   */
  panelAutoOpened?: boolean;
  /**
   * Persistent history of citekey renames observed from Zotero. Each entry
   * maps an old citekey to its current (most up-to-date) replacement, with
   * chain-following applied so that A→B→C is stored as {A: C, B: C}.
   *
   * Used by "Update stale citekeys in vault notes" to find notes that contain
   * citekeys from before one or more renames. Cleared by the companion
   * "Purge citekey rename history" command.
   */
  citekeyRenameHistory?: Record<string, string>;

  /** Color for the underline under unlinked [@pandoc] citations (source view). */
  decorationColorUnlinked?: string;
  /** Color for the underline under [[@key]] citations that have a lit note. */
  decorationColorLinked?: string;
  /** Color for the underline under [[@key]] citations without a lit note yet. */
  decorationColorUnimported?: string;

  /** User-defined callout-type → word-processor style mappings. */
  styleMappings?: StyleMapping[];
  /** Master switch: when false, no mappings are applied on export. */
  styleMappingsEnabled?: boolean;
}

const BIB_EXTENSIONS = new Set(['bib', 'json', 'yaml', 'yml']);

/**
 * Mobile vault file picker — opens a fuzzy-search modal over all vault files
 * with bibliography-compatible extensions. Calls `onChoose` with the selected
 * vault-relative path. Used as the browse-button action on mobile where the
 * OS file picker can't return a stable file-system path.
 */
class BibFilePickerModal extends FuzzySuggestModal<TFile> {
  constructor(private onChoose: (path: string) => void) {
    super(app);
    this.setPlaceholder(t('Search…'));
  }

  getItems(): TFile[] {
    return app.vault
      .getFiles()
      .filter((f) => BIB_EXTENSIONS.has(f.extension))
      .sort((a, b) => a.path.localeCompare(b.path));
  }

  getItemText(file: TFile): string {
    return file.path;
  }

  onChooseItem(file: TFile): void {
    this.onChoose(file.path);
  }
}

export class ReferenceListSettingsTab extends PluginSettingTab {
  plugin: ReferenceList;

  constructor(plugin: ReferenceList) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;

    containerEl.empty();

    // Pandoc is optional — the plugin uses a built-in JS parser by default.
    // Set this path if you need Pandoc's higher-fidelity .bib/.yaml handling
    // (e.g. @string macros, unusual encodings). Desktop only.
    if (Platform.isDesktop) {
      new Setting(containerEl)
        .setName(t('Path to Pandoc (optional)'))
        .setDesc(
          t(
            'Absolute path to the Pandoc executable. When set, Pandoc is used to convert .bib/.yaml files instead of the built-in parser. Leave blank to use the built-in parser (works on all platforms).'
          )
        )
        .then((setting) => {
          let inputEl: HTMLInputElement;
          setting.addText((text) => {
            inputEl = text.inputEl;
            text
              .setPlaceholder('/usr/local/bin/pandoc')
              .setValue(this.plugin.settings.pathToPandoc ?? '')
              .onChange((value) => {
                this.plugin.settings.pathToPandoc = value;
                this.plugin.saveSettings();
              });
          });

          setting.addExtraButton((b) => {
            b.setIcon('magnifying-glass');
            b.setTooltip(t('Auto-detect Pandoc'));
            b.onClick(async () => {
              const found = await findPandoc();
              if (found) {
                inputEl.value = found;
                this.plugin.settings.pathToPandoc = found;
                this.plugin.saveSettings();
              }
            });
          });
        });
    }

    // Document Compiler commands (outline → markdown → docx) run the bundled
    // scripts/DocumentCompiler.py via Python 3. Desktop only.
    if (Platform.isDesktop) {
      new Setting(containerEl)
        .setName(t('Path to Python 3 (for Document Compiler)'))
        .setDesc(
          t(
            'Absolute path to the python3 interpreter used by the "Compile outline…" and "Compile + export to docx" commands. Leave blank to auto-detect (python3 on PATH, then common install locations).'
          )
        )
        .then((setting) => {
          let inputEl: HTMLInputElement;
          setting.addText((text) => {
            inputEl = text.inputEl;
            text
              .setPlaceholder('/usr/local/bin/python3')
              .setValue(this.plugin.settings.pathToPython ?? '')
              .onChange((value) => {
                this.plugin.settings.pathToPython = value;
                this.plugin.saveSettings();
              });
          });
        });

      new Setting(containerEl)
        .setName(t('Docx export templates directory (optional)'))
        .setDesc(
          t(
            'Directory of your .docx export templates. Vault-relative (e.g. Export Templates) or absolute. Leave blank to use <vault>/Export Templates/, then the templates bundled with the plugin.'
          )
        )
        .then((setting) => {
          setting.addText((text) =>
            text
              .setPlaceholder('Export Templates')
              .setValue(this.plugin.settings.exportTemplatesDir ?? '')
              .onChange((value) => {
                this.plugin.settings.exportTemplatesDir = value;
                this.plugin.saveSettings();
              })
          );
        });

      new Setting(containerEl)
        .setName(t('Default output folder for compiled/exported documents (optional)'))
        .setDesc(
          t(
            'Vault-relative folder where "Compile and export a book" puts the compiled markdown and docx. Leave blank to use the source file\'s own folder. Can be changed per-export in the modal.'
          )
        )
        .then((setting) => {
          setting.addText((text) =>
            text
              .setPlaceholder('Export Compiled')
              .setValue(this.plugin.settings.defaultOutputDir ?? '')
              .onChange((value) => {
                this.plugin.settings.defaultOutputDir = value;
                this.plugin.saveSettings();
              })
          );
        });
    }

    new Setting(containerEl)
      .setName(t('Default author name (optional)'))
      .setDesc(
        t(
          'Used as the document author when the note has no `author:` frontmatter property. Leave blank to omit the author field in exported documents.'
        )
      )
      .addText((text) =>
        text
          .setPlaceholder('First Last')
          .setValue(this.plugin.settings.defaultAuthor ?? '')
          .onChange((value) => {
            this.plugin.settings.defaultAuthor = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Use Obsidian account name as author fallback'))
      .setDesc(
        t(
          'If enabled and no `author:` property or default author name is set, the display name from your Obsidian account (if signed in) is used instead.'
        )
      )
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.useAccountNameAsAuthor ?? false)
          .onChange((value) => {
            this.plugin.settings.useAccountNameAsAuthor = value;
            this.plugin.saveSettings();
          })
      );

    // ── Custom style mappings ─────────────────────────────────────────────
    {
      // Helper: render / re-render the full mapping list into `listEl`.
      const renderMappingList = (listEl: HTMLElement) => {
        listEl.empty();
        const mappings = this.plugin.settings.styleMappings ?? [];
        if (mappings.length === 0) {
          listEl.createEl('p', {
            text: t('No mappings yet. Click "+ Add" to create one.'),
            cls: 'lc-mapping-empty',
          });
          return;
        }
        for (let i = 0; i < mappings.length; i++) {
          const m = mappings[i];
          const row = listEl.createDiv({ cls: 'lc-mapping-row' });

          // Global-default enable checkbox
          const cb = row.createEl('input', { type: 'checkbox' });
          cb.title = t('Enable this mapping by default on export');
          cb.checked = m.enabled;
          cb.addEventListener('change', () => {
            mappings[i].enabled = cb.checked;
            this.plugin.saveSettings();
          });

          // Source input
          const srcInput = row.createEl('input', { type: 'text' });
          srcInput.placeholder = 'callout-type';
          srcInput.value = m.source;
          srcInput.title = t('Callout type or CSS class name (e.g. arabic-poetry)');
          srcInput.classList.add('lc-mapping-input');
          srcInput.addEventListener('change', () => {
            mappings[i].source = srcInput.value.trim();
            this.plugin.saveSettings();
          });

          row.createSpan({ text: '→', cls: 'lc-mapping-arrow' });

          // Style name input
          const nameInput = row.createEl('input', { type: 'text' });
          nameInput.placeholder = 'Style name';
          nameInput.value = m.styleName;
          nameInput.title = t('Style name as defined in the template (e.g. Arabic poetry)');
          nameInput.classList.add('lc-mapping-input', 'lc-mapping-style');
          nameInput.addEventListener('change', () => {
            mappings[i].styleName = nameInput.value.trim();
            this.plugin.saveSettings();
          });

          // Delete button
          const del = row.createEl('button', { text: '🗑', cls: 'lc-mapping-del' });
          del.title = t('Remove this mapping');
          del.addEventListener('click', () => {
            mappings.splice(i, 1);
            this.plugin.saveSettings();
            renderMappingList(listEl);
          });
        }
      };

      new Setting(containerEl)
        .setName(t('Custom style mappings'))
        .setDesc(
          t(
            'Map callout types (e.g. arabic-poetry) to word-processor style names. ' +
            'Applied during DOCX and ODT export. For multi-style or per-line formatting, ' +
            'add a .lua filter to your Export Templates folder instead.'
          )
        )
        .addToggle((toggle) =>
          toggle
            .setValue(this.plugin.settings.styleMappingsEnabled ?? true)
            .onChange((value) => {
              this.plugin.settings.styleMappingsEnabled = value;
              this.plugin.saveSettings();
            })
        );

      const listEl = containerEl.createDiv({ cls: 'lc-mapping-list' });
      renderMappingList(listEl);

      const addBtn = containerEl.createEl('button', {
        text: t('+ Add mapping'),
        cls: 'lc-mapping-add',
      });
      addBtn.addEventListener('click', () => {
        if (!this.plugin.settings.styleMappings) this.plugin.settings.styleMappings = [];
        this.plugin.settings.styleMappings.push({
          id: crypto.randomUUID(),
          enabled: true,
          source: '',
          styleName: '',
        });
        this.plugin.saveSettings();
        renderMappingList(listEl);
      });
    }

    new Setting(containerEl)
      .setName(t('Bibliography files'))
      .setDesc(
        t(
          'One or more bibliography files (.bib, .json, or .yaml). Vault-relative paths work on all platforms; absolute paths work on desktop only. All files are merged — Zotero wins on conflict. Can be overridden per-note via the "bibliography" frontmatter key.'
        )
      )
      .addButton((btn) => {
        btn.setButtonText(t('Add file')).onClick(() => {
          this.plugin.settings.bibliographyPaths.push('');
          this.plugin.saveSettings();
          this.display();
        });
      });

    this.plugin.settings.bibliographyPaths.forEach((bibPath, index) => {
      const setting = new Setting(containerEl);
      setting.setClass('lc-bib-path-entry');

      let inputEl: HTMLInputElement;

      setting.addText((text) => {
        inputEl = text.inputEl;
        text
          .setPlaceholder('references.bib')
          .setValue(bibPath)
          .onChange((value) => {
            this.plugin.settings.bibliographyPaths[index] = value;
            this.plugin.saveSettings(() => this.plugin.bibManager.reinit(true));
          });

        new BibFileSuggest(this.app, text.inputEl);

        text.inputEl.addEventListener('blur', async () => {
          const raw = text.inputEl.value.trim();
          if (!raw) return;
          try {
            const resolved = await getBibPath(raw);
            if (resolved !== raw) {
              text.setValue(resolved);
              this.plugin.settings.bibliographyPaths[index] = resolved;
              this.plugin.saveSettings();
            }
          } catch {
            // Path unresolvable — leave as-is.
          }
        });
      });

      setting.addExtraButton((btn) => {
        btn.setIcon('folder-open').setTooltip(t('Browse…'));
        btn.onClick(() => {
          if (Platform.isDesktop) {
            const fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = '.bib,.json,.yaml,.yml';
            fileInput.onchange = async () => {
              const file = fileInput.files?.[0];
              const fsPath: string | undefined = (file as any)?.path;
              if (!fsPath) return;
              let resolved = fsPath;
              try { resolved = await getBibPath(fsPath); } catch { /* keep absolute */ }
              inputEl.value = resolved;
              inputEl.dispatchEvent(new Event('input'));
              this.plugin.settings.bibliographyPaths[index] = resolved;
              this.plugin.saveSettings(() => this.plugin.bibManager.reinit(true));
            };
            fileInput.click();
          } else {
            new BibFilePickerModal((path) => {
              inputEl.value = path;
              inputEl.dispatchEvent(new Event('input'));
              this.plugin.settings.bibliographyPaths[index] = path;
              this.plugin.saveSettings(() => this.plugin.bibManager.reinit(true));
            }).open();
          }
        });
      });

      setting.addExtraButton((btn) => {
        btn.setIcon('trash').setTooltip(t('Remove'));
        btn.onClick(() => {
          this.plugin.settings.bibliographyPaths.splice(index, 1);
          this.plugin.saveSettings(() => this.plugin.bibManager.reinit(true));
          this.display();
        });
      });
    });

    ReactDOM.render(
      <ZoteroPullSetting plugin={this.plugin} />,
      containerEl.createDiv('setting-item lc-setting-item-wrapper')
    );

    const configuredStyle = this.plugin.settings.cslStyleURL;
    const defaultStyle =
      cslListRaw.find((item) => item.value === configuredStyle) ||
      (configuredStyle
        ? { value: configuredStyle, label: configuredStyle }
        : undefined);

    ReactDOM.render(
      <SettingItem name={t('Citation style')}>
        <SearchSelect
          placeholder={t('Search...')}
          defaultValue={defaultStyle}
          search={searchCSL}
          isClearable
          onChange={(selection) => {
            this.plugin.settings.cslStyleURL = selection?.value;
            this.plugin.saveSettings(() =>
              this.plugin.bibManager.reinit(false)
            );
          }}
        />
      </SettingItem>,
      containerEl.createDiv('lc-setting-item setting-item')
    );

    new Setting(containerEl)
      .setName(t('Custom citation style'))
      .setDesc(
        t(
          'Path to a CSL file (vault-relative or absolute). Overrides the style selected above. Can be overridden per-note via the "csl" or "citation-style" frontmatter key. A URL can be supplied when setting the style via frontmatter.'
        )
      )
      .then((setting) => {
        let pathText: any;
        setting.addText((text) => {
          pathText = text;
          text.setValue(this.plugin.settings.cslStylePath ?? '').onChange((value) => {
            this.plugin.settings.cslStylePath = value;
            this.plugin.saveSettings(() =>
              this.plugin.bibManager.reinit(false)
            );
          });
        });
        if (Platform.isDesktop) {
          setting.addButton((btn) => {
            btn.setButtonText(t('Browse Zotero styles…')).onClick(() => {
              new ZoteroStylePicker(this.app, this.plugin, (style) => {
                this.plugin.settings.cslStylePath = style.path;
                this.plugin.saveSettings(() =>
                  this.plugin.bibManager.reinit(false)
                );
                // Refresh the text input to show the chosen path.
                if (pathText?.inputEl) {
                  pathText.inputEl.value = style.path;
                }
                this.display();
              }).open();
            });
          });
        }
      });

    const defaultLanguage = langListRaw.find(
      (item) => item.value === this.plugin.settings.cslLang
    );

    ReactDOM.render(
      <SettingItem
        name={t('Citation style language')}
        description={
          <>
            {t(
              `This can be overridden on a per-file basis by setting "lang" or "citation-language" in the file's frontmatter. A language code must be used when setting the language via frontmatter.`
            )}{' '}
            <a
              href="https://github.com/citation-style-language/locales/blob/master/locales.json"
              target="_blank"
            >
              {t('See here for a list of available language codes')}
            </a>
            .
          </>
        }
      >
        <SearchSelect
          placeholder={t('Search...')}
          defaultValue={defaultLanguage}
          search={searchCSLLangs}
          isClearable
          onChange={(selection) => {
            if (selection) {
              this.plugin.settings.cslLang = selection.value;
              this.plugin.saveSettings(() =>
                this.plugin.bibManager.reinit(false)
              );
            }
          }}
        />
      </SettingItem>,
      containerEl.createDiv('lc-setting-item setting-item')
    );

    new Setting(containerEl)
      .setName(t('Literature notes folder'))
      .setDesc(
        t(
          'Folder where the plugin\'s own literature notes are created (vault-relative). Leave blank to create at the vault root. Used for the "Create literature note" button when ZotLit is not handling creation. ZotLit uses its own configured folder.'
        )
      )
      .addText((text) => {
        text
          .setPlaceholder('_2 Bibliographic notes')
          .setValue(this.plugin.settings.literatureNoteFolder ?? '')
          .onChange((value) => {
            this.plugin.settings.literatureNoteFolder = value;
            this.plugin.saveSettings();
          });
        new FolderSuggest(this.app, text.inputEl);
      });

    new Setting(containerEl)
      .setName(t('Create literature notes with ZotLit'))
      .setDesc(
        t(
          'When ZotLit is available, the tooltip\'s "Create literature note" button creates the note with ZotLit\'s templates instead of the plugin\'s basic template. Falls back to the plugin template when ZotLit is absent or this is off.'
        )
      )
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.createNotesWithZotLit !== false)
          .onChange((value) => {
            this.plugin.settings.createNotesWithZotLit = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Hide links in references'))
      .setDesc(t('Replace links with link icons to save space.'))
      .addToggle((text) =>
        text.setValue(!!this.plugin.settings.hideLinks).onChange((value) => {
          this.plugin.settings.hideLinks = value;
          this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName(t('Show PDF links in references'))
      .setDesc(
        t(
          'Add per-entry PDF-open icons to the bibliography and use PDFs as the tooltip link fallback. Off by default: "Open in Zotero" already reveals every attachment, and fetching the PDF list costs a per-citekey Zotero request.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(this.plugin.settings.showPdfLinks !== false)
          .onChange((value) => {
            this.plugin.settings.showPdfLinks = value;
            this.plugin.saveSettings();
            // PDF state lives in the rendered bibliography — refresh the
            // active view so buttons appear/disappear immediately.
            this.plugin.processReferences();
          })
      );

    new Setting(containerEl)
      .setName(t('Render live preview inline citations'))
      .setDesc(
        t(
          'Convert [@pandoc] citations to formatted inline citations in live preview mode.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(!!this.plugin.settings.renderCitations)
          .onChange((value) => {
            this.plugin.settings.renderCitations = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Render reading mode inline citations'))
      .setDesc(
        t(
          'Convert [@pandoc] citations to formatted inline citations in reading mode.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(!!this.plugin.settings.renderCitationsReadingMode)
          .onChange((value) => {
            this.plugin.settings.renderCitationsReadingMode = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Process linked citations'))
      .setDesc(
        t(
          'Recognize [[@key]] and [[@key|see @, p. 6]] linked citations: include them in the reference list and render them as formatted inline citations in live preview. The @@ placeholder inside an alias expands to the link\'s own citekey. Aliases without a citekey (e.g. [[@key|Just a label]]) are left untouched. On by default — this is the plugin\'s core feature.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(this.plugin.settings.renderLinkCitations !== false)
          .onChange((value) => {
            this.plugin.settings.renderLinkCitations = value;
            this.plugin.settings.formatLinkAliases = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Link citations to literature notes'))
      .setDesc(
        t(
          'Make rendered [@citekey] citations clickable links to their literature note. Only applies when a note with the matching citekey name exists — dead-link citations are not linked.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(!!this.plugin.settings.renderCitationsAsLinks)
          .onChange((value) => {
            this.plugin.settings.renderCitationsAsLinks = value;
            this.plugin.saveSettings();
          })
      );

    const zotlitActive = isZotLitSuggestActive(this.app);
    new Setting(containerEl)
      .setName(t('Show citekey suggestions'))
      .setDesc(
        zotlitActive
          ? t(
              'ZotLit detected — [@key completions are handled by ZotLit. This plugin still provides bare @key suggestions (outside brackets) and for .bib file entries.'
            )
          : t(
              'When enabled, an autocomplete dialog will display when typing citation keys.'
            )
      )
      .addToggle((text) =>
        text
          .setValue(!!this.plugin.settings.enableCiteKeyCompletion)
          .onChange((value) => {
            this.plugin.settings.enableCiteKeyCompletion = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Prioritize citation completion'))
      .setDesc(
        t(
          'Use this plugin\'s citation search for "@" completions. When ON, typing "[@key" (or "[[" followed by "@") searches the Zotero/bibliography index with citekey-first fuzzy matching. When OFF, plain "[@key" yields to another plugin\'s suggester (e.g. ZotLit); "[[@key" is still always handled by this plugin since Obsidian\'s link search can\'t see unimported references.'
        )
      )
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.prioritizeCiteKeyCompletion ?? true)
          .onChange((value) => {
            this.plugin.settings.prioritizeCiteKeyCompletion = value;
            this.plugin.saveSettings();
          })
      );

    const showDeco = this.plugin.settings.showCitationDecorations ?? true;

    new Setting(containerEl)
      .setName(t('Citation decoration'))
      .setDesc(
        t(
          'Underline citation keys in the editor to show their status at a glance: pandoc citations get a faint dotted underline, [[@linked]] citations with a literature note get a coloured underline, and [[@linked]] citations without one get a different colour. Use the colour pickers below to customise each state.'
        )
      )
      .addToggle((toggle) =>
        toggle
          .setValue(showDeco)
          .onChange((value) => {
            this.plugin.settings.showCitationDecorations = value;
            this.plugin.saveSettings();
            this.display();
          })
      );

    // ── Decoration color pickers (only when decoration is on) ───────────────
    if (showDeco) {
      const makeColorPicker = (
        name: string,
        desc: string,
        settingKey: 'decorationColorUnlinked' | 'decorationColorLinked' | 'decorationColorUnimported',
        cssVar: string
      ) => {
        new Setting(containerEl)
          .setName(t(name))
          .setDesc(t(desc))
          .then((setting) => {
            const savedVal: string | undefined = (this.plugin.settings as any)[settingKey];
            const cssVal = savedVal ??
              getComputedStyle(document.body).getPropertyValue(cssVar).trim();
            const m = cssVal.match(/^(#[0-9a-fA-F]{3,8})/);
            const hexRaw = m ? m[1] : '#888888';
            const expandHex = (h: string) => {
              if (h.length === 4) return '#' + h[1].repeat(2) + h[2].repeat(2) + h[3].repeat(2);
              if (h.length === 5) return '#' + h[1].repeat(2) + h[2].repeat(2) + h[3].repeat(2);
              return h.slice(0, 7);
            };

            const colorInput = setting.controlEl.createEl('input') as HTMLInputElement;
            colorInput.type = 'color';
            colorInput.value = expandHex(hexRaw);
            Object.assign(colorInput.style, {
              width: '2.4em', height: '1.8em', padding: '1px 2px', cursor: 'pointer',
              border: '1px solid var(--background-modifier-border)',
              borderRadius: 'var(--radius-s)',
              background: 'var(--background-modifier-form-field)',
              marginRight: '6px', verticalAlign: 'middle',
            });

            const textInput = setting.controlEl.createEl('input') as HTMLInputElement;
            textInput.type = 'text';
            textInput.value = savedVal ?? cssVal;
            textInput.setAttribute('spellcheck', 'false');
            Object.assign(textInput.style, {
              width: '7em', fontFamily: 'var(--font-monospace)', fontSize: 'var(--font-ui-small)',
            });

            const applyColor = (val: string) => {
              (this.plugin.settings as any)[settingKey] = val;
              document.body.style.setProperty(cssVar, val);
              this.plugin.saveSettings();
            };

            colorInput.addEventListener('input', () => {
              textInput.value = colorInput.value;
              applyColor(colorInput.value);
            });

            textInput.addEventListener('change', () => {
              const val = textInput.value.trim();
              const m6 = val.match(/^#([0-9a-fA-F]{6})$/);
              const m3 = val.match(/^#([0-9a-fA-F]{3})$/);
              if (m6) colorInput.value = val;
              else if (m3) colorInput.value = '#' + m3[1][0].repeat(2) + m3[1][1].repeat(2) + m3[1][2].repeat(2);
              applyColor(val);
            });
          });
      };

      makeColorPicker(
        'Pandoc citation underline color',
        'Underline color for unlinked [@pandoc] citations in the editor.',
        'decorationColorUnlinked', '--lc-citation-underline-color-unlinked'
      );
      makeColorPicker(
        'Linked [[@]] citation underline — has note',
        'Underline color for [[@key]] citations that have a matching literature note.',
        'decorationColorLinked', '--lc-wikilink-linked-color'
      );
      makeColorPicker(
        'Linked [[@]] citation underline — no note yet',
        'Underline color for [[@key]] citations that do not yet have a literature note.',
        'decorationColorUnimported', '--lc-wikilink-unimported-color'
      );
    }

    // ── Decoration preview: source markup → reading-mode output ─────────────
    {
      const row = containerEl.createDiv({ cls: 'setting-item' });
      const info = row.createDiv({ cls: 'setting-item-info' });
      info.createDiv({ cls: 'setting-item-name', text: t('Preview') });
      info.createDiv({
        cls: 'setting-item-description',
        text: t('Editor markup (left) → reading-mode output (right).'),
      });
      const control = row.createDiv({ cls: 'setting-item-control' });
      const preview = control.createDiv({
        cls: 'lc-deco-preview' + (showDeco ? ' lc-decorations' : ''),
      });

      // Helper: one row of markup (plain left) → rendered text (decorated right).
      // keyCls carries the status class (lc-prev-pandoc etc.); the CSS decoration
      // is applied to the RIGHT span so the raw markup stays undecorated.
      const addRow = (
        open: string, key: string, close: string,
        keyCls: string,
        rendered: string, renderedCls = ''
      ) => {
        const r = preview.createDiv({ cls: 'lc-prev-row' });
        const left = r.createSpan({ cls: 'lc-prev-left' });
        left.createSpan({ cls: 'lc-prev-bracket', text: open });
        left.createSpan({ cls: 'lc-prev-key', text: key });
        left.createSpan({ cls: 'lc-prev-bracket', text: close });
        r.createSpan({ cls: 'lc-prev-arrow', text: '→' });
        // keyCls on the rendered span so CSS decoration targets the right side.
        r.createSpan({ cls: `lc-prev-rendered ${keyCls} ${renderedCls}`.trim(), text: rendered });
      };

      // [@jones1999] — pandoc citation, resolved (unlinked)
      addRow('[', '@jones1999', ']', 'lc-prev-pandoc', '(Jones 1999)');
      // [[@smith2000|@, has note]] — wikilink, has literature note
      addRow('[[', '@smith2000|@, has note', ']]', 'lc-prev-linked', '(Smith 2000, has note)');
      // [[@sanchez2001|@, no note]] — wikilink, no literature note yet
      addRow('[[', '@sanchez2001|@, no note', ']]', 'lc-prev-unimported', '(Sanchez 2001, no note)');
      // [[@nothing1899]] — unresolved citekey
      addRow('[[', '@nothing1899', ']]', 'lc-prev-unresolved-key', '@nothing1899', 'lc-prev-unresolved-val');
    }

    new Setting(containerEl)
      .setName(t('Show citekey tooltips'))
      .setDesc(
        t(
          'Hovering over a citekey opens a tooltip showing the formatted citation, an abstract preview, and buttons to open the item in Zotero, open its PDF, and create or navigate to its literature note.'
        )
      )
      .addToggle((text) =>
        text
          .setValue(!!this.plugin.settings.showCitekeyTooltips)
          .onChange((value) => {
            this.plugin.settings.showCitekeyTooltips = value;
            this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName(t('Tooltip delay'))
      .setDesc(
        t(
          'Set the amount of time (in milliseconds) to wait before displaying tooltips.'
        )
      )
      .addSlider((slider) => {
        slider
          .setDynamicTooltip()
          .setLimits(0, 7000, 100)
          .setValue(this.plugin.settings.tooltipDelay)
          .onChange((value) => {
            this.plugin.settings.tooltipDelay = value;
            this.plugin.saveSettings();
          });
      });

    new Setting(containerEl)
      .setName(t('Mobile tap action'))
      .setDesc(
        t(
          'What happens when you tap a citation on mobile. On desktop, hover tooltips are used instead.'
        )
      )
      .addDropdown((dd) =>
        dd
          .addOption('show', t('Show citation info'))
          .addOption('copy', t('Copy citation to clipboard'))
          .addOption('link', t('Open link (Zotero → PDF → URL)'))
          .setValue(this.plugin.settings.mobileClickAction ?? 'show')
          .onChange((value) => {
            this.plugin.settings.mobileClickAction = value as 'show' | 'copy' | 'link';
            this.plugin.saveSettings();
          })
      );
  }
}
