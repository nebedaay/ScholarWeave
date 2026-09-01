import { App, Modal, Notice, Platform, TFile } from 'obsidian';
import type ReferenceList from './main';
import type { StyleMapping } from './settings';
import { runDocumentCompiler } from './exportCompiler';

export type ExportFormat = 'md' | 'docx' | 'odt' | 'pdf';
export type DocType = 'book' | 'article' | 'custom';

export interface ExportOptions {
  format: ExportFormat;
  docType: DocType;
  /** Template filename (with extension, e.g. "book.docx"). Empty = no template. */
  template: string;
  toc: boolean;
  /** true = restart footnote numbering at each top-level heading; false = continuous. */
  restartFootnotes: boolean;
  /** true = each top-level heading starts on a new page. */
  newPageHeadings: boolean;
  /** Absolute or vault-relative output folder; '' = same folder as source. */
  outputDir: string;
  /** Desired output filename (basename + extension). */
  outputFilename: string;
  /** PDF only: keep the intermediate docx/odt after conversion. */
  keepIntermediate: boolean;
  /** PDF only: intermediate format to export through ('odt' or 'docx'). */
  pdfIntermediate: 'docx' | 'odt';
  /** IDs of StyleMappings enabled for this export (subset of settings.styleMappings). */
  enabledMappingIds: string[];
}

/** Per-file export history stored in plugin settings. */
interface FileExportHistory {
  format: ExportFormat;
  docType: DocType;
  template: string;
  toc: boolean;
  restartFootnotes: boolean;
  newPageHeadings: boolean;
  outputDir: string;
  outputFilename: string;
  keepIntermediate: boolean;
  pdfIntermediate: 'docx' | 'odt';
  enabledMappingIds: string[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * List template filenames (WITH extension) from a directory that match the
 * given format, or [] on any error.
 *
 * format 'docx' → only .docx files
 * format 'odt'  → only .odt files
 * format 'pdf'  → both (template is for the intermediate docx/odt)
 * format 'md'   → both (unlikely to be used, but show all)
 */
function listTemplates(dir: string, format: ExportFormat): string[] {
  try {
    const fs = require('fs') as typeof import('fs');
    if (!fs.existsSync(dir)) return [];
    const exts =
      format === 'docx' ? ['.docx'] :
      format === 'odt'  ? ['.odt']  :
      ['.docx', '.odt']; // pdf, md — show both
    return (fs.readdirSync(dir) as string[])
      .filter((f) => exts.some((ext) => f.toLowerCase().endsWith(ext)))
      .sort();
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

/**
 * Modal for the "Compile / export document" command.
 *
 * Format selector comes first so the user knows which format they're targeting
 * before choosing a template.  Template list is filtered to show only files
 * whose extension matches the selected format (.docx for DOCX, .odt for ODT,
 * both for MD).  Template filenames include their extension so the user can
 * tell them apart at a glance.  The template selector is disabled for MD
 * output since that mode compiles to markdown only and uses no template.
 */
export class ExportModal extends Modal {
  private plugin: ReferenceList;
  private file: TFile;

  private templateSelect!: HTMLSelectElement;
  private formatSelect!: HTMLSelectElement;
  private filenameInput!: HTMLInputElement;
  private outputDirInput!: HTMLInputElement;
  private sameSourceCb!: HTMLInputElement;
  private docTypeBook!: HTMLInputElement;
  private docTypeArticle!: HTMLInputElement;
  private docTypeCustom!: HTMLInputElement;
  private tocCb!: HTMLInputElement;
  private footnotesCb!: HTMLInputElement;
  private newPageCb!: HTMLInputElement;
  private keepIntermediateCb!: HTMLInputElement;
  private keepIntermediateRow!: HTMLElement;
  private pdfIntermediateSelect!: HTMLSelectElement;
  private pdfIntermediateRow!: HTMLElement;
  /** Map from StyleMapping.id → checkbox, for reading enabled state in options(). */
  private mappingCheckboxes: Map<string, HTMLInputElement> = new Map();
  private runButton!: HTMLButtonElement;

  /** Absolute paths to template directories (set in onOpen, used when rebuilding). */
  private pluginTplDir = '';
  private userTplDir = '';

  constructor(app: App, plugin: ReferenceList, file: TFile) {
    super(app);
    this.plugin = plugin;
    this.file = file;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass('lc-export-modal');
    contentEl.createEl('h3', { text: 'Compile / export document' });

    const adapter = this.plugin.app.vault.adapter as any;
    const vaultBase: string =
      typeof adapter?.getBasePath === 'function' ? adapter.getBasePath() : '';

    // Absolute path of the note's containing folder — used as the fallback
    // output directory when no previous export directory has been saved.
    const noteFolder: string =
      vaultBase && this.file.parent && this.file.parent.path !== '/'
        ? `${vaultBase}/${this.file.parent.path}`
        : vaultBase;

    // Resolve template directories once; stored for rebuilding on format change.
    this.pluginTplDir =
      vaultBase && this.plugin.manifest.dir
        ? `${vaultBase}/${this.plugin.manifest.dir}/templates`
        : '';
    const userTplDirRaw =
      this.plugin.settings.exportTemplatesDir || 'Export Templates';
    this.userTplDir =
      userTplDirRaw.startsWith('/') || userTplDirRaw.startsWith('~')
        ? userTplDirRaw
        : vaultBase
        ? `${vaultBase}/${userTplDirRaw}`
        : '';

    // ── Format selector (FIRST) ───────────────────────────────────────────
    const fmtWrap = contentEl.createDiv({ cls: 'lc-export-row' });
    fmtWrap.createEl('label', { text: 'Output format' });
    this.formatSelect = fmtWrap.createEl('select', {
      cls: 'lc-export-format-select',
    });
    this.formatSelect.style.cssText = 'width:100%;margin-top:4px';
    const formats: { value: ExportFormat; label: string }[] = [
      { value: 'md',   label: 'Compiled Markdown only (no export)' },
      { value: 'docx', label: 'Word document (.docx)' },
      { value: 'odt',  label: 'LibreOffice document (.odt)' },
      { value: 'pdf',  label: 'PDF (via intermediate ODT or DOCX)' },
    ];
    for (const { value, label } of formats) {
      const opt = this.formatSelect.createEl('option', { text: label });
      opt.value = value;
    }
    // Default to last used; when no history, use docx if a frontmatter template
    // is set, otherwise md.
    const fmTpl = this.templateFromFrontmatter();
    // Per-file history takes priority over global last-used settings.
    const fileHistory = this.getFileHistory();
    const lastFmt = this.plugin.settings.lastExportFormat;
    this.formatSelect.value = fileHistory?.format ?? lastFmt ?? (fmTpl ? 'docx' : 'md');

    // ── Template dropdown (SECOND, filtered by format) ────────────────────
    const tplWrap = contentEl.createDiv({ cls: 'lc-export-row' });
    tplWrap.style.marginTop = '10px';
    tplWrap.createEl('label', { text: 'Template' });
    this.templateSelect = tplWrap.createEl('select', {
      cls: 'lc-export-template-select',
    });
    this.templateSelect.style.cssText = 'width:100%;margin-top:4px';

    // Populate for the initially-selected format.
    // Priority: per-file history > last-used global > YAML frontmatter.
    // templateSwitched only applies when there is no file history AND the
    // note's frontmatter template differs from the globally last-used one.
    const lastTpl = fileHistory?.template ??
      (this.plugin.settings as any).lastTemplate ?? '';
    const lastTplStem = lastTpl.replace(/\.(docx|odt)$/i, '');
    const templateSwitched = !fileHistory && !!fmTpl && fmTpl !== lastTplStem;
    this.buildTemplateDropdown(
      this.formatSelect.value as ExportFormat,
      fileHistory ? lastTpl : (templateSwitched ? fmTpl : (lastTpl || fmTpl)),
    );

    // ── Document type radio ───────────────────────────────────────────────
    const dtWrap = contentEl.createDiv({ cls: 'lc-export-row' });
    dtWrap.style.marginTop = '10px';
    dtWrap.createEl('label', { text: 'Document type' });
    const dtRow = dtWrap.createDiv();
    dtRow.style.cssText = 'display:flex;gap:16px;margin-top:4px';
    const makeRadio = (value: string, label: string): HTMLInputElement => {
      const wrap = dtRow.createDiv();
      wrap.style.cssText = 'display:flex;align-items:center;gap:4px';
      const r = wrap.createEl('input', { type: 'radio' });
      r.name = 'lc-doc-type';
      r.value = value;
      r.id = `lc-dt-${value}`;
      const lbl = wrap.createEl('label', { text: label });
      lbl.htmlFor = r.id;
      return r;
    };
    this.docTypeBook    = makeRadio('book',    'Book');
    this.docTypeArticle = makeRadio('article', 'Article');
    this.docTypeCustom  = makeRadio('custom',  'Custom');

    // ── Output filename ───────────────────────────────────────────────────
    const fnWrap = contentEl.createDiv({ cls: 'lc-export-row' });
    fnWrap.style.marginTop = '10px';
    fnWrap.createEl('label', { text: 'Output filename' });
    this.filenameInput = fnWrap.createEl('input', {
      type: 'text',
      cls: 'lc-export-filename-input',
    });
    this.filenameInput.style.cssText = 'width:100%;margin-top:4px';
    this.refreshFilename(); // sets initial value

    // ── Output directory ──────────────────────────────────────────────────
    const outWrap = contentEl.createDiv({ cls: 'lc-export-row' });
    outWrap.style.cssText =
      'margin-top:10px;display:flex;gap:6px;align-items:flex-end';
    const outLeft = outWrap.createDiv();
    outLeft.style.flex = '1';
    outLeft.createEl('label', { text: 'Output directory' });
    // Default: last-used directory; fall back to the current note's folder.
    const initialOutputDir =
      fileHistory?.outputDir ?? (this.plugin.settings as any).lastOutputDir ?? noteFolder;
    this.outputDirInput = outLeft.createEl('input', {
      type: 'text',
      value: initialOutputDir,
      placeholder: '(same folder as source)',
      cls: 'lc-export-outdir-input',
    });
    this.outputDirInput.style.cssText = 'width:100%;margin-top:4px';
    const chooseBtn = outWrap.createEl('button', { text: 'Choose…' });
    chooseBtn.style.cssText = 'white-space:nowrap;flex-shrink:0';
    chooseBtn.addEventListener('click', () => this.pickOutputDir());

    // "Same folder as source" checkbox — checked when the dir box is empty,
    // clears the box (→ same-as-source) when checked, unchecked on any edit.
    const sameSourceRow = contentEl.createDiv({ cls: 'lc-export-check-row' });
    sameSourceRow.style.marginTop = '4px';
    this.sameSourceCb = sameSourceRow.createEl('input', { type: 'checkbox' });
    this.sameSourceCb.id = 'lc-export-same-source';
    this.sameSourceCb.checked = !this.outputDirInput.value.trim();
    const sameSourceLbl = sameSourceRow.createEl('label', {
      text: 'Use same folder as source file',
    });
    sameSourceLbl.htmlFor = 'lc-export-same-source';
    this.sameSourceCb.addEventListener('change', () => {
      if (this.sameSourceCb.checked) {
        this.outputDirInput.value = '';
      }
    });
    this.outputDirInput.addEventListener('input', () => {
      this.sameSourceCb.checked = false;
    });

    // ── Checkboxes ────────────────────────────────────────────────────────
    const checksWrap = contentEl.createDiv({ cls: 'lc-export-checks' });
    checksWrap.style.marginTop = '12px';

    const makeCheckRow = (
      id: string,
      labelText: string
    ): HTMLInputElement => {
      const row = checksWrap.createDiv({ cls: 'lc-export-check-row' });
      const cb = row.createEl('input', { type: 'checkbox' });
      cb.id = id;
      const lbl = row.createEl('label', { text: labelText });
      lbl.htmlFor = id;
      return cb;
    };

    this.tocCb = makeCheckRow('lc-export-toc', 'Include table of contents (TOC)');
    this.footnotesCb = makeCheckRow(
      'lc-export-fn',
      'Restart footnote numbering per chapter'
    );
    this.newPageCb = makeCheckRow(
      'lc-export-np',
      'Top-level headings start on a new page'
    );

    // ── PDF-specific controls (hidden unless format = pdf) ────────────────
    this.keepIntermediateRow = checksWrap.createDiv({ cls: 'lc-export-check-row' });
    this.keepIntermediateCb = this.keepIntermediateRow.createEl('input', { type: 'checkbox' });
    this.keepIntermediateCb.id = 'lc-export-keep-inter';
    const keepInterLbl = this.keepIntermediateRow.createEl('label', {
      text: 'Keep intermediate file (ODT/DOCX)',
    });
    keepInterLbl.htmlFor = 'lc-export-keep-inter';
    this.keepIntermediateCb.checked = fileHistory?.keepIntermediate ?? false;

    this.pdfIntermediateRow = contentEl.createDiv({ cls: 'lc-export-row' });
    this.pdfIntermediateRow.style.marginTop = '8px';
    this.pdfIntermediateRow.createEl('label', { text: 'Intermediate format' });
    this.pdfIntermediateSelect = this.pdfIntermediateRow.createEl('select');
    this.pdfIntermediateSelect.style.cssText = 'width:100%;margin-top:4px';
    for (const [val, lbl] of [['odt', 'ODT (recommended)'], ['docx', 'DOCX']] as const) {
      const o = this.pdfIntermediateSelect.createEl('option', { text: lbl });
      o.value = val;
    }
    this.pdfIntermediateSelect.value = fileHistory?.pdfIntermediate ?? 'odt';

    // ── Style mappings (collapsible, only when mappings exist) ───────────
    this.buildMappingsSection(contentEl, fileHistory?.enabledMappingIds);

    // Initialise document-type radio and checkboxes from file history or
    // template defaults.  templateSwitched drives the fallback when there
    // is no file-level history for this note.
    this.applyDocSettings(templateSwitched);
    // And reflect whether the initial format requires the template select.
    this.syncFormatState(this.formatSelect.value as ExportFormat);

    // ── Event wiring ──────────────────────────────────────────────────────
    this.formatSelect.addEventListener('change', () => {
      const fmt = this.formatSelect.value as ExportFormat;
      const prevTpl = this.templateSelect.value;
      this.buildTemplateDropdown(fmt, prevTpl);
      this.applyDocSettings();
      this.refreshFilename();
      this.syncFormatState(fmt);
    });
    this.templateSelect.addEventListener('change', () =>
      this.applyDocSettings()
    );
    // Radio → preset checkboxes; checkboxes → auto-switch to Custom.
    [this.docTypeBook, this.docTypeArticle, this.docTypeCustom].forEach(r => {
      r.addEventListener('change', () => {
        if (r.checked) this.applyDocTypePreset(r.value as DocType);
      });
    });
    [this.tocCb, this.footnotesCb, this.newPageCb].forEach(cb => {
      cb.addEventListener('change', () => {
        this.docTypeBook.checked    = false;
        this.docTypeArticle.checked = false;
        this.docTypeCustom.checked  = true;
      });
    });

    // ── Buttons ───────────────────────────────────────────────────────────
    const btnRow = contentEl.createDiv({ cls: 'lc-export-btn-row' });
    btnRow.style.cssText =
      'display:flex;justify-content:flex-end;gap:8px;margin-top:14px';
    const cancelBtn = btnRow.createEl('button', { text: 'Cancel' });
    cancelBtn.addEventListener('click', () => this.close());
    this.runButton = btnRow.createEl('button', {
      text: 'Compile',
      cls: 'mod-cta',
    });
    this.runButton.addEventListener('click', () => this.run());
    this.filenameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.run();
      if (e.key === 'Escape') this.close();
    });

    setTimeout(() => this.runButton.focus(), 50);
  }

  // ── Helpers ──────────────────────────────────────────────────────────────

  /**
   * Build the collapsible "Style mappings" section at the bottom of the modal.
   * Hidden entirely when no mappings exist (or global toggle is off in settings).
   * `savedIds` is the set of IDs that were enabled last time this file was exported.
   */
  private buildMappingsSection(
    container: HTMLElement,
    savedIds: string[] | undefined
  ): void {
    const mappings: StyleMapping[] = this.plugin.settings.styleMappings ?? [];
    const globalEnabled = this.plugin.settings.styleMappingsEnabled ?? true;
    if (!globalEnabled || mappings.length === 0) return;

    this.mappingCheckboxes.clear();

    // Default: if no saved state, use each mapping's own `enabled` default.
    const savedSet = savedIds ? new Set(savedIds) : null;

    const details = container.createEl('details', { cls: 'lc-mapping-details' });
    const enabledCount = mappings.filter(
      m => savedSet ? savedSet.has(m.id) : m.enabled
    ).length;
    const summary = details.createEl('summary');
    const updateSummaryText = () => {
      const on = Array.from(this.mappingCheckboxes.values()).filter(cb => cb.checked).length;
      summary.setText(`Style mappings (${on} of ${mappings.length} enabled)`);
    };

    const listEl = details.createDiv({ cls: 'lc-mapping-modal-list' });
    for (const m of mappings) {
      if (!m.source || !m.styleName) continue; // skip incomplete entries
      const row = listEl.createDiv({ cls: 'lc-mapping-modal-row' });
      const cb = row.createEl('input', { type: 'checkbox' });
      cb.id = `lc-map-${m.id}`;
      cb.checked = savedSet ? savedSet.has(m.id) : m.enabled;
      cb.addEventListener('change', updateSummaryText);
      const lbl = row.createEl('label', { text: `${m.source} → ${m.styleName}` });
      lbl.htmlFor = cb.id;
      this.mappingCheckboxes.set(m.id, cb);
    }

    if (this.mappingCheckboxes.size === 0) {
      details.remove(); // all entries were incomplete
      return;
    }

    listEl.createEl('p', {
      text: 'Manage mappings in Settings → Custom style mappings.',
      cls: 'lc-mapping-modal-note',
    });

    updateSummaryText();
  }

  /**
   * Disable template selector (and TOC / new-page checkboxes) when the format
   * is MD, since that mode compiles to markdown only and skips pandoc entirely.
   */
  private syncFormatState(format: ExportFormat): void {
    const isMd  = format === 'md';
    const isPdf = format === 'pdf';
    this.templateSelect.disabled = isMd;
    this.tocCb.disabled      = isMd;
    this.newPageCb.disabled  = isMd;
    // footnotesCb stays active — the compile step still uses it.
    // PDF-specific rows: show only when format is pdf.
    const pdfDisplay = isPdf ? '' : 'none';
    this.keepIntermediateRow.style.display  = pdfDisplay;
    this.pdfIntermediateRow.style.display   = pdfDisplay;
  }

  /**
   * Rebuild the template <select> options for the given format.
   *
   * `preferredValue` may be a full filename ("book.docx") or a bare stem
   * ("book") from a previous session or frontmatter.  We try an exact match
   * first, then a stem match so old stem-only values still work.
   *
   * IMPORTANT: this method deliberately does NOT call applyTemplateDefaults(),
   * because it may be invoked before the checkboxes are created.  Callers must
   * call applyTemplateDefaults() themselves once checkboxes exist.
   */
  private buildTemplateDropdown(format: ExportFormat, preferredValue = ''): void {
    this.templateSelect.empty();

    // Built-in plugin templates.
    const pluginTpls = this.pluginTplDir
      ? listTemplates(this.pluginTplDir, format)
      : [];
    if (pluginTpls.length > 0) {
      const grp = this.templateSelect.createEl('optgroup') as HTMLOptGroupElement;
      grp.label = 'Built-in templates';
      for (const t of pluginTpls) {
        const o = grp.createEl('option', { text: t });
        o.value = t;
      }
    }

    // User templates.
    const userTpls = this.userTplDir
      ? listTemplates(this.userTplDir, format)
      : [];
    if (userTpls.length > 0) {
      const grp = this.templateSelect.createEl('optgroup') as HTMLOptGroupElement;
      grp.label = 'Your templates';
      for (const t of userTpls) {
        const o = grp.createEl('option', { text: t });
        o.value = t;
      }
    }

    // Try to restore the preferred selection (exact match, then stem match).
    const trySelect = (val: string): boolean => {
      if (!val) return false;
      this.templateSelect.value = val;
      if (this.templateSelect.value === val) return true;
      // Stem match: "book" selects "book.docx".
      const stem = val.replace(/\.(docx|odt)$/i, '');
      for (const opt of Array.from(this.templateSelect.options)) {
        if (opt.value.replace(/\.(docx|odt)$/i, '') === stem) {
          this.templateSelect.value = opt.value;
          return true;
        }
      }
      return false;
    };

    if (!trySelect(preferredValue)) {
      trySelect(this.templateFromFrontmatter());
    }
  }

  private templateFromFrontmatter(): string {
    const cache = this.app.metadataCache.getFileCache(this.file);
    const tpl = (cache?.frontmatter as Record<string, unknown> | undefined)
      ?.template;
    // Return the stem (no extension) so callers can do stem matching.
    return typeof tpl === 'string' ? tpl.replace(/\.(docx|odt)$/i, '') : '';
  }

  /** Read this file's export history entry, or null if none exists. */
  private getFileHistory(): FileExportHistory | null {
    const hist = (this.plugin.settings as any).fileExportHistory as
      Record<string, FileExportHistory> | undefined;
    return hist?.[this.file.path] ?? null;
  }

  /**
   * Apply preset checkbox values for a given document type.
   * 'custom' is a no-op — leave whatever is already checked.
   */
  private applyDocTypePreset(docType: DocType): void {
    if (docType === 'book') {
      this.tocCb.checked       = true;
      this.footnotesCb.checked = true;
      this.newPageCb.checked   = true;
    } else if (docType === 'article') {
      this.tocCb.checked       = false;
      this.footnotesCb.checked = false;
      this.newPageCb.checked   = false;
    }
  }

  /**
   * Initialise the document-type radio and checkboxes.
   *
   * Priority order:
   *   1. Per-file history (restored exactly as last used)
   *   2. Template-name heuristic (book* / article*) when fresh=true or no history
   *
   * Callers must ensure both the radio buttons and checkboxes exist.
   */
  private applyDocSettings(fresh = false): void {
    const history = this.getFileHistory();
    if (history && !fresh) {
      // Restore everything exactly as it was last time this file was exported.
      this.docTypeBook.checked    = history.docType === 'book';
      this.docTypeArticle.checked = history.docType === 'article';
      this.docTypeCustom.checked  = history.docType === 'custom';
      this.tocCb.checked          = history.toc;
      this.footnotesCb.checked    = history.restartFootnotes;
      this.newPageCb.checked      = history.newPageHeadings;
    } else {
      // Derive document type from template name stem.
      const stem = this.templateSelect.value.replace(/\.(docx|odt)$/i, '');
      const docType: DocType =
        stem.startsWith('book')    ? 'book' :
        stem.startsWith('article') ? 'article' : 'custom';
      this.docTypeBook.checked    = docType === 'book';
      this.docTypeArticle.checked = docType === 'article';
      this.docTypeCustom.checked  = docType === 'custom';
      this.applyDocTypePreset(docType);
    }
  }

  /**
   * Keep the filename input in sync with the format selector.
   * Only auto-updates while the name still looks like the auto-generated
   * default (avoids clobbering a name the user typed manually).
   */
  private refreshFilename(): void {
    const fmt = this.formatSelect.value as ExportFormat;
    const ext = fmt === 'odt' ? 'odt' : fmt === 'md' ? 'md' : fmt === 'pdf' ? 'pdf' : 'docx';
    const noteBase = this.file.basename;
    // Use saved filename stem from per-file history when available.
    const history = this.getFileHistory();
    const savedStem = history?.outputFilename
      ? history.outputFilename.replace(/\.[^.]+$/, '')
      : noteBase;
    const current = this.filenameInput?.value ?? '';
    const looksDefault =
      !current ||
      current === `${savedStem}.md` ||
      current === `${savedStem}.docx` ||
      current === `${savedStem}.odt` ||
      current === `${savedStem}.pdf` ||
      current === `${noteBase}.md` ||
      current === `${noteBase}.docx` ||
      current === `${noteBase}.odt` ||
      current === `${noteBase}.pdf`;
    if (looksDefault && this.filenameInput) {
      this.filenameInput.value = `${savedStem}.${ext}`;
    }
  }

  private async pickOutputDir(): Promise<void> {
    try {
      // Obsidian on macOS/Windows ships with @electron/remote for dialog access.
      // Try the modern package first, then fall back to the legacy remote API.
      let dialog: any = null;
      try { dialog = require('@electron/remote').dialog; } catch { /* not bundled */ }
      if (!dialog) dialog = (require('electron') as any).remote?.dialog ?? null;
      if (!dialog) throw new Error('no remote dialog');
      // Start the browser at the current input value, then the last-used
      // directory, so the user doesn't have to navigate from Downloads each time.
      const startPath =
        this.outputDirInput.value.trim() ||
        this.getFileHistory()?.outputDir ||
        (this.plugin.settings as any).lastOutputDir ||
        '';
      const result = await dialog.showOpenDialog({
        title: 'Select output directory',
        properties: ['openDirectory', 'createDirectory'],
        ...(startPath ? { defaultPath: startPath } : {}),
      });
      if (!result.canceled && result.filePaths.length > 0) {
        this.outputDirInput.value = result.filePaths[0];
        this.sameSourceCb.checked = false;
      }
    } catch {
      new Notice(
        'Directory picker unavailable — type the path into the box above.'
      );
    }
  }

  private options(): ExportOptions {
    const docType: DocType =
      this.docTypeBook.checked ? 'book' :
      this.docTypeArticle.checked ? 'article' : 'custom';
    return {
      format: this.formatSelect.value as ExportFormat,
      docType,
      template: this.templateSelect.value,
      toc: this.tocCb.checked,
      restartFootnotes: this.footnotesCb.checked,
      newPageHeadings: this.newPageCb.checked,
      outputDir: this.outputDirInput.value.trim(),
      outputFilename: this.filenameInput.value.trim(),
      keepIntermediate: this.keepIntermediateCb.checked,
      pdfIntermediate: this.pdfIntermediateSelect.value as 'docx' | 'odt',
      enabledMappingIds: Array.from(this.mappingCheckboxes.entries())
        .filter(([, cb]) => cb.checked)
        .map(([id]) => id),
    };
  }

  private async run() {
    if (!Platform.isDesktop) {
      new Notice('Document compile/export is only available on desktop.');
      return;
    }
    const opts = this.options();

    // Persist settings: per-file history (keyed by vault path) plus
    // global lastExportFormat for files with no history yet.
    const entry: FileExportHistory = {
      format:           opts.format,
      docType:          opts.docType,
      template:         opts.template,
      toc:              opts.toc,
      restartFootnotes: opts.restartFootnotes,
      newPageHeadings:  opts.newPageHeadings,
      outputDir:        opts.outputDir,
      outputFilename:   opts.outputFilename,
      keepIntermediate: opts.keepIntermediate,
      pdfIntermediate:  opts.pdfIntermediate,
      enabledMappingIds: opts.enabledMappingIds,
    };
    const s = this.plugin.settings as any;
    if (!s.fileExportHistory) s.fileExportHistory = {};
    s.fileExportHistory[this.file.path] = entry;
    // Keep history bounded (oldest-first; drop entries beyond 200).
    const entries = Object.entries(s.fileExportHistory as Record<string, unknown>);
    if (entries.length > 200)
      s.fileExportHistory = Object.fromEntries(entries.slice(entries.length - 200));
    // Also update global last-used format for new-file defaults.
    this.plugin.settings.lastExportFormat = opts.format;
    s.lastTemplate = opts.template;
    await this.plugin.saveSettings();

    this.close();

    const label =
      opts.format === 'md'   ? 'Compiling outline…' :
      opts.format === 'odt'  ? 'Compiling + exporting to ODT…' :
      opts.format === 'pdf'  ? 'Compiling + exporting to PDF…' :
                               'Compiling + exporting to DOCX…';
    const progress = new Notice(label, 0);

    const res = await runDocumentCompiler(this.plugin, this.file, opts);
    progress.hide();

    if (!res.ok) {
      new Notice(`Document compiler failed:\n${res.stderr}`, 8000);
      console.error('[scholar-weave] DocumentCompiler failed:', res.stderr);
      return;
    }

    const outPath =
      res.outputPath ?? res.stdout.trim().split('\n').pop() ?? '';
    const doneLabel =
      opts.format === 'md' ? `Compiled: ${outPath}` : `Exported: ${outPath}`;
    new Notice(doneLabel, 6000);
  }

  onClose() {
    this.contentEl.empty();
  }
}
