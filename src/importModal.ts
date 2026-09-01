import { App, Modal, Notice, Platform, TFile } from 'obsidian';
import type ReferenceList from './main';
import { runImportScript } from './importCompiler';
import { rewritePandocToLinked } from './pandocToLinked';

declare const require: (id: string) => any;

const LAST_DIR_KEY = 'scholar-weave:import-last-dir';

/**
 * Modal for "Import a Word/ODT document" command.
 *
 * Steps performed on Import:
 *   1. Run zotero-to-md.py to convert the DOCX/ODT (with Zotero citation
 *      fields) to Markdown with pandoc-style citations.
 *   2. Create the resulting note in the vault root.
 *   3. (Optional, default on) Convert [@citekey] citations to [[@citekey]].
 *   4. (Optional, default on) Create literature notes for citations that
 *      don't yet have one.
 */
export class ImportModal extends Modal {
  private plugin: ReferenceList;
  private inputPath = '';
  private fileLabel!: HTMLSpanElement;
  private convertCb!: HTMLInputElement;
  private litNotesCb!: HTMLInputElement;
  private importBtn!: HTMLButtonElement;

  constructor(app: App, plugin: ReferenceList) {
    super(app);
    this.plugin = plugin;
  }

  /** The directory the file picker should open to. */
  private getDefaultDir(): string {
    try {
      const stored = localStorage.getItem(LAST_DIR_KEY);
      if (stored) return stored;
    } catch { /* localStorage unavailable */ }
    // Fall back to the vault root.
    const adapter = this.plugin.app.vault.adapter as any;
    return typeof adapter?.getBasePath === 'function' ? adapter.getBasePath() : '';
  }

  /** Persist the directory of the chosen file for next time. */
  private saveLastDir(filePath: string): void {
    try {
      const nodePath = require('path') as typeof import('path');
      localStorage.setItem(LAST_DIR_KEY, nodePath.dirname(filePath));
    } catch { /* ignore */ }
  }

  /** Update the UI to reflect a chosen file, and enable the Import button. */
  private selectFile(filePath: string, fileName: string): void {
    this.inputPath = filePath;
    this.fileLabel.textContent = fileName;
    this.fileLabel.classList.remove('lc-import-drop-hint');
    this.importBtn.disabled = false;
    this.saveLastDir(filePath);
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.addClass('lc-import-modal');
    contentEl.createEl('h3', { text: 'Import document' });
    contentEl.createEl('p', {
      text: 'Import a Word (.docx) or LibreOffice (.odt) file with Zotero citation fields into your vault as a Markdown note. Requires Zotero to be running.',
      cls: 'lc-export-modal-note',
    });

    // ── Drop zone + file picker ───────────────────────────────────────────────
    const dropZone = contentEl.createDiv({ cls: 'lc-import-drop-zone' });
    dropZone.style.cssText = [
      'border: 2px dashed var(--background-modifier-border)',
      'border-radius: 6px',
      'padding: 16px 12px',
      'margin-bottom: 12px',
      'display: flex',
      'align-items: center',
      'gap: 10px',
      'cursor: default',
      'transition: background 0.15s',
    ].join(';');

    const browseBtn = dropZone.createEl('button', { text: 'Browse…' });

    this.fileLabel = dropZone.createSpan({ cls: 'lc-import-file-label lc-import-drop-hint' });
    this.fileLabel.textContent = 'No file selected — or drop a .docx/.odt here';
    this.fileLabel.style.cssText = 'flex:1;color:var(--text-muted);font-size:0.9em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';

    // Drag-and-drop handlers on the whole drop zone.
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.style.background = 'var(--background-secondary)';
    });
    dropZone.addEventListener('dragleave', () => {
      dropZone.style.background = '';
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.style.background = '';
      const file = e.dataTransfer?.files?.[0] as any;
      if (!file) return;
      // Electron exposes .path on dropped File objects.
      const filePath: string | undefined = file.path;
      if (!filePath) {
        new Notice('[ScholarWeave] Could not read the file path from the dropped file.');
        return;
      }
      const lower = filePath.toLowerCase();
      if (!lower.endsWith('.docx') && !lower.endsWith('.odt')) {
        new Notice('[ScholarWeave] Please drop a .docx or .odt file.');
        return;
      }
      this.selectFile(filePath, file.name as string);
    });

    // Browse button: native file dialog via @electron/remote, with last-used dir.
    browseBtn.addEventListener('click', async () => {
      let filePath: string | undefined;
      let fileName: string | undefined;

      try {
        const { dialog } = require('@electron/remote');
        const result = await dialog.showOpenDialog({
          defaultPath: this.getDefaultDir(),
          properties: ['openFile'],
          filters: [{ name: 'Documents', extensions: ['docx', 'odt'] }],
        });
        if (!result.canceled && result.filePaths.length > 0) {
          filePath = result.filePaths[0];
          const parts = filePath.replace(/\\/g, '/').split('/');
          fileName = parts[parts.length - 1];
        }
      } catch {
        // @electron/remote unavailable – fall back to <input type="file">
        await new Promise<void>(resolve => {
          const input = document.createElement('input');
          input.type = 'file';
          input.accept = '.docx,.odt';
          input.addEventListener('change', () => {
            const f = input.files?.[0] as any;
            if (f?.path) { filePath = f.path; fileName = f.name; }
            resolve();
          });
          input.addEventListener('cancel', () => resolve());
          input.click();
        });
      }

      if (filePath && fileName) {
        this.selectFile(filePath, fileName);
      }
    });

    // ── Options ──────────────────────────────────────────────────────────────
    const convertRow = contentEl.createDiv({ cls: 'lc-export-check-row' });
    this.convertCb = convertRow.createEl('input', { type: 'checkbox' });
    this.convertCb.id = 'lc-import-convert';
    this.convertCb.checked = true;
    const convertLabel = convertRow.createEl('label', {
      text: 'Convert citations to linked format ([[@citekey]])',
    });
    convertLabel.htmlFor = 'lc-import-convert';

    const litRow = contentEl.createDiv({ cls: 'lc-export-check-row' });
    this.litNotesCb = litRow.createEl('input', { type: 'checkbox' });
    this.litNotesCb.id = 'lc-import-litnotes';
    this.litNotesCb.checked = true;
    const litLabel = litRow.createEl('label', {
      text: 'Create literature notes for citations that lack them',
    });
    litLabel.htmlFor = 'lc-import-litnotes';

    // ── Buttons ──────────────────────────────────────────────────────────────
    const btnRow = contentEl.createDiv({ cls: 'lc-export-btn-row' });
    btnRow.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;margin-top:16px';

    const cancelBtn = btnRow.createEl('button', { text: 'Cancel' });
    cancelBtn.addEventListener('click', () => this.close());

    this.importBtn = btnRow.createEl('button', { text: 'Import', cls: 'mod-cta' });
    this.importBtn.disabled = true;
    this.importBtn.addEventListener('click', () => this.run());

    setTimeout(() => browseBtn.focus(), 50);
  }

  private async run() {
    if (!this.inputPath) return;
    if (!Platform.isDesktop) {
      new Notice('Document import is only available on desktop.');
      return;
    }

    // Capture options before closing (onClose empties the DOM).
    const doConvert = this.convertCb.checked;
    const doLitNotes = this.litNotesCb.checked;
    this.close();

    const nodePath = require('path') as typeof import('path');
    const fs = require('fs') as typeof import('fs');
    const os = require('os') as typeof import('os');

    const basename = nodePath.basename(this.inputPath).replace(/\.(docx|odt)$/i, '');
    const tmpOutput = nodePath.join(os.tmpdir(), `${basename}.sw-import.md`);

    const progress = new Notice('Importing document… Zotero must be running.', 0);

    const result = await runImportScript(this.plugin, this.inputPath, tmpOutput);

    if (!result.ok) {
      progress.hide();
      new Notice(`[ScholarWeave] Import failed:\n${result.stderr}`, 10000);
      console.error('[scholar-weave] Import failed:', result.stderr);
      return;
    }

    // Read the temp file.
    let mdContent: string;
    try {
      mdContent = fs.readFileSync(tmpOutput, 'utf-8');
      try { fs.unlinkSync(tmpOutput); } catch { /* ignore */ }
    } catch (e) {
      progress.hide();
      new Notice(`[ScholarWeave] Import failed: could not read converted file.\n${e}`, 8000);
      return;
    }

    // Convert pandoc citations → linked citations in-memory before writing the
    // vault file.  Done here rather than in a post-creation step so the note
    // lands in the vault already converted.  Citations from Zotero are trusted
    // (allowUnresolved = true) regardless of bib-cache state.
    if (doConvert) {
      let body = mdContent;
      let frontmatter = '';
      const fm = /^---\n[\s\S]*?\n---\n?/.exec(mdContent);
      if (fm) { frontmatter = fm[0]; body = mdContent.slice(fm[0].length); }
      const { out } = rewritePandocToLinked(body, new Set(), /* allowUnresolved */ true);
      mdContent = frontmatter + out;
    }

    // Pick a unique vault path (root level).
    let vaultRelPath = `${basename}.md`;
    let suffix = 0;
    while (await this.app.vault.adapter.exists(vaultRelPath)) {
      suffix++;
      vaultRelPath = `${basename} (${suffix}).md`;
    }

    let newFile: TFile;
    try {
      newFile = await this.app.vault.create(vaultRelPath, mdContent);
    } catch (e) {
      progress.hide();
      new Notice(`[ScholarWeave] Import failed: could not create note in vault.\n${e}`, 8000);
      return;
    }

    // Open the new note in the current leaf.
    await this.app.workspace.getLeaf(false).openFile(newFile);
    progress.hide();
    new Notice(`Imported: ${newFile.basename}`, 5000);

    // Step 3 (optional): Create missing literature notes.
    if (doLitNotes) {
      const litProgress = new Notice('Creating missing literature notes…', 0);
      try {
        const { created, missingKeys } = await this.plugin.bibManager.createMissingLitNotes(
          { file: newFile },
          (done: number, total: number) => (litProgress as any).setProgress?.(done, total)
        );
        litProgress.hide();
        if (missingKeys.length) {
          new Notice(
            `Created ${created} of ${missingKeys.length} missing literature note(s).`,
            5000
          );
        }
      } catch (e) {
        litProgress.hide();
        new Notice(`[ScholarWeave] Literature note creation failed: ${e}`, 6000);
        console.error('[scholar-weave] lit note creation error:', e);
      }
    }

    this.plugin.processReferences();
  }

  onClose() {
    this.contentEl.empty();
  }
}
