import { App, Modal, TFile } from 'obsidian';

/** A single citekey change found in one file. */
export interface CitekeyChange {
  /** The stale citekey as it appears in the note. */
  oldKey: string;
  /** The current (replacement) citekey. */
  newKey: string;
  /** 1-based line numbers where the old key appears. */
  lines: number[];
}

/** Map from each affected file to the list of changes to apply. */
export type RenamePlan = Map<TFile, CitekeyChange[]>;

/**
 * Confirmation modal for reviewing unresolved citations and optionally fixing
 * any that have a known citekey replacement.
 *
 * When `plan` is non-empty, the modal shows the standard rename summary +
 * file list and an "Update" button.  When `alsoUnresolved` is provided, a
 * secondary "no known replacement" section is appended.  Either section can
 * be absent.
 *
 * The "Update literature note filenames" checkbox is shown only when
 * `showLitNotesOption` is true (default for the vault-wide command; false for
 * the per-note unresolved-badge flow).
 */
export class CitekeyRenameModal extends Modal {
  private plan: RenamePlan;
  private onConfirm: (includeLitNotes: boolean) => Promise<void>;
  private showLitNotesOption: boolean;
  /** Citekeys that are unresolved but have no known rename replacement. */
  private alsoUnresolved: string[];

  constructor(
    app: App,
    plan: RenamePlan,
    onConfirm: (includeLitNotes: boolean) => Promise<void>,
    showLitNotesOption = true,
    alsoUnresolved: string[] = []
  ) {
    super(app);
    this.plan = plan;
    this.onConfirm = onConfirm;
    this.showLitNotesOption = showLitNotesOption;
    this.alsoUnresolved = alsoUnresolved;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();

    const hasFixable = this.plan.size > 0;
    const hasUnfixable = this.alsoUnresolved.length > 0;

    // ── Title ────────────────────────────────────────────────────────────────
    contentEl.createEl('h2', { text: 'Unresolved citations' });

    // ── Fixable section ───────────────────────────────────────────────────────
    if (hasFixable) {
      let totalOccurrences = 0;
      const uniqueRenames = new Map<string, string>();

      for (const changes of this.plan.values()) {
        for (const change of changes) {
          totalOccurrences += change.lines.length;
          uniqueRenames.set(change.oldKey, change.newKey);
        }
      }

      const fileCount = this.plan.size;
      const renameCount = uniqueRenames.size;

      contentEl.createEl('p', {
        text:
          `Found ${totalOccurrences} occurrence${totalOccurrences !== 1 ? 's' : ''} of ` +
          `${renameCount} stale citekey${renameCount !== 1 ? 's' : ''} ` +
          `across ${fileCount} file${fileCount !== 1 ? 's' : ''} ` +
          `with known replacements:`,
      });

      const mappingList = contentEl.createEl('ul', { cls: 'lc-rename-mapping' });
      for (const [oldKey, newKey] of uniqueRenames) {
        mappingList.createEl('li', { text: `@${oldKey}  →  @${newKey}` });
      }

      if (fileCount > 1) {
        // Collapsible file list — only useful when more than one file is affected.
        const details = contentEl.createEl('details', { cls: 'lc-rename-details' });
        const summary = details.createEl('summary', {
          text: `Affected files (${fileCount})`,
        });
        summary.style.cursor = 'pointer';
        summary.style.marginBottom = '6px';

        const fileList = details.createEl('ul', { cls: 'lc-rename-file-list' });
        fileList.style.maxHeight = '200px';
        fileList.style.overflowY = 'auto';
        fileList.style.paddingLeft = '1.4em';
        fileList.style.marginTop = '6px';

        for (const [file, changes] of this.plan) {
          const li = fileList.createEl('li');
          li.createEl('strong', { text: file.path });

          const sub = li.createEl('ul');
          sub.style.paddingLeft = '1.2em';
          sub.style.fontSize = '0.87em';
          sub.style.color = 'var(--text-muted)';

          const byPair = new Map<string, number[]>();
          for (const change of changes) {
            const pairKey = `${change.oldKey}→${change.newKey}`;
            if (!byPair.has(pairKey)) byPair.set(pairKey, []);
            byPair.get(pairKey)!.push(...change.lines);
          }
          for (const [pair, lines] of byPair) {
            const [oldK, newK] = pair.split('→');
            sub.createEl('li', {
              text: `@${oldK} → @${newK}  (${lines.map((l) => `L${l}`).join(', ')})`,
            });
          }
        }
      }
    }

    // ── Truly unresolved section (no fix available) ───────────────────────────
    if (hasUnfixable) {
      const heading = contentEl.createEl('p', {
        text: hasFixable
          ? 'Also unresolved (no known replacement):'
          : 'These citations have no known replacement:',
      });
      heading.style.marginTop = hasFixable ? '14px' : '0';
      heading.style.color = 'var(--text-muted)';

      const ul = contentEl.createEl('ul', { cls: 'lc-unresolved-list' });
      ul.style.color = 'var(--text-muted)';
      ul.style.fontSize = '0.9em';
      for (const key of this.alsoUnresolved) {
        ul.createEl('li', { text: `@${key}` });
      }
    }

    // ── Lit-note filenames checkbox (vault-wide command only) ─────────────────
    let litNotesCheckbox: HTMLInputElement | null = null;
    if (this.showLitNotesOption && hasFixable) {
      const checkRow = contentEl.createDiv({ cls: 'lc-rename-checkbox-row' });
      checkRow.style.display = 'flex';
      checkRow.style.alignItems = 'center';
      checkRow.style.gap = '8px';
      checkRow.style.marginTop = '14px';

      litNotesCheckbox = checkRow.createEl('input', { type: 'checkbox' }) as HTMLInputElement;
      litNotesCheckbox.id = 'lc-lit-notes-checkbox';
      litNotesCheckbox.checked = true;

      const label = checkRow.createEl('label', {
        text: 'Update literature note filenames to current citekeys',
      });
      label.htmlFor = 'lc-lit-notes-checkbox';
    }

    // ── Button row ────────────────────────────────────────────────────────────
    const buttonRow = contentEl.createDiv({ cls: 'lc-rename-buttons' });
    buttonRow.style.display = 'flex';
    buttonRow.style.justifyContent = 'flex-end';
    buttonRow.style.gap = '8px';
    buttonRow.style.marginTop = '16px';

    if (hasFixable) {
      const cancelBtn = buttonRow.createEl('button', { text: 'Cancel' });
      cancelBtn.addEventListener('click', () => this.close());

      const confirmBtn = buttonRow.createEl('button', {
        text: 'Fix stale citekeys',
        cls: 'mod-cta',
      });
      confirmBtn.addEventListener('click', async () => {
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Updating…';
        try {
          await this.onConfirm(litNotesCheckbox?.checked ?? false);
        } finally {
          this.close();
        }
      });
    } else {
      // Nothing to fix — just a Close button.
      const closeBtn = buttonRow.createEl('button', { text: 'Close', cls: 'mod-cta' });
      closeBtn.addEventListener('click', () => this.close());
    }
  }

  onClose() {
    this.contentEl.empty();
  }
}
