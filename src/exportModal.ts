import { App, Modal, Notice, Platform, TFile } from 'obsidian';
import type ReferenceList from './main';
import { runBookCompiler } from './exportCompiler';

export interface ExportOptions {
  export: boolean;
  toc: boolean;
  globalFootnotes: boolean;
  outputDir: string; // vault-relative; '' = same folder as source
}

/**
 * Modal for the "Compile/export book" command. Lets the user override the
 * template-aware defaults (TOC on for book* templates, off for article*;
 * footnotes restart per chapter for book*, global for article*) and choose
 * where the compiled markdown / docx land.
 */
export class ExportModal extends Modal {
  private plugin: ReferenceList;
  private file: TFile;
  private tocCb!: HTMLInputElement;
  private footnotesCb!: HTMLInputElement;
  private outputInput!: HTMLInputElement;
  private runButton!: HTMLButtonElement;

  constructor(app: App, plugin: ReferenceList, file: TFile) {
    super(app);
    this.plugin = plugin;
    this.file = file;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass('lc-export-modal');
    contentEl.createEl('h3', { text: 'Compile / export book' });

    // Template-aware defaults: book* → TOC on, footnotes per chapter;
    // article* → TOC off, footnotes global. Anything else (document) → off.
    const template = this.templateFromFrontmatter();
    const isBook = template.startsWith('book');
    const isArticle = template.startsWith('article');
    const defaultToc = isBook;
    const defaultGlobal = isArticle;

    const tplNote = contentEl.createEl('p', { cls: 'lc-export-modal-note' });
    tplNote.createEl('strong', { text: this.file.basename });
    tplNote.createSpan({
      text: template
        ? ` — template: ${template} (TOC ${defaultToc ? 'on' : 'off'}, footnotes ${defaultGlobal ? 'global' : 'per-chapter'})`
        : ' — no template property (document defaults)',
    });

    // TOC checkbox
    const tocRow = contentEl.createDiv({ cls: 'lc-export-check-row' });
    this.tocCb = tocRow.createEl('input', { type: 'checkbox' });
    this.tocCb.checked = defaultToc;
    tocRow.createEl('label', { text: 'Include a table of contents (TOC)' });

    // Footnotes checkbox
    const fnRow = contentEl.createDiv({ cls: 'lc-export-check-row' });
    this.footnotesCb = fnRow.createEl('input', { type: 'checkbox' });
    this.footnotesCb.checked = defaultGlobal;
    fnRow.createEl('label', {
      text: 'Global footnote numbering (unchecked = restart per chapter)',
    });

    // Output folder
    const outWrap = contentEl.createDiv({ cls: 'lc-export-output-wrap' });
    outWrap.createEl('label', {
      text: 'Output folder (vault-relative; blank = same folder as source)',
    });
    this.outputInput = outWrap.createEl('input', {
      type: 'text',
      value: this.plugin.settings.defaultOutputDir ?? '',
      placeholder: 'Export Compiled',
      cls: 'lc-export-output-input',
    });
    this.outputInput.style.width = '100%';

    const btnRow = contentEl.createDiv({ cls: 'lc-export-btn-row' });
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.gap = '8px';
    btnRow.style.marginTop = '12px';

    const cancelBtn = btnRow.createEl('button', { text: 'Cancel' });
    cancelBtn.addEventListener('click', () => this.close());

    const compileBtn = btnRow.createEl('button', {
      text: 'Compile to markdown',
    });
    compileBtn.addEventListener('click', () => this.run(false));

    this.runButton = btnRow.createEl('button', {
      text: 'Compile & export to docx',
      cls: 'mod-cta',
    });
    this.runButton.addEventListener('click', () => this.run(true));

    this.outputInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.run(true);
      if (e.key === 'Escape') this.close();
    });

    setTimeout(() => this.runButton.focus(), 50);
  }

  private templateFromFrontmatter(): string {
    const cache = this.app.metadataCache.getFileCache(this.file);
    const tpl = (cache?.frontmatter as Record<string, unknown> | undefined)
      ?.template;
    return typeof tpl === 'string' ? tpl.replace(/\.docx$/, '') : '';
  }

  private options(exportDocx: boolean): ExportOptions {
    return {
      export: exportDocx,
      toc: this.tocCb.checked,
      globalFootnotes: this.footnotesCb.checked,
      outputDir: this.outputInput.value.trim(),
    };
  }

  private async run(exportDocx: boolean) {
    if (!Platform.isDesktop) {
      new Notice('Book compile/export is only available on desktop.');
      return;
    }
    const opts = this.options(exportDocx);
    this.close();

    const progress = new Notice(
      exportDocx ? 'Compiling + exporting to docx…' : 'Compiling outline…',
      0
    );
    const res = await runBookCompiler(this.plugin, this.file, {
      export: exportDocx,
      toc: opts.toc,
      globalFootnotes: opts.globalFootnotes,
      outputDir: opts.outputDir,
    });
    progress.hide();

    if (!res.ok) {
      new Notice(`Book compiler failed:\n${res.stderr}`, 8000);
      console.error('[scholar-weave] BookCompiler failed:', res.stderr);
      return;
    }

    const lastLine = res.stdout.trim().split('\n').pop() ?? '';
    new Notice(
      exportDocx ? `Exported: ${lastLine}` : `Compiled: ${lastLine}`,
      6000
    );
  }

  onClose() {
    const { contentEl } = this;
    contentEl.empty();
  }
}
