import { App, Modal, Setting } from 'obsidian';

/** A plugin that also provides a reference-list sidebar. */
export interface ConflictingPlugin {
  id: string;
  name: string;
}

/**
 * Shown once per conflicting plugin when ScholarWeft loads and another
 * reference-list plugin is enabled (the ancestral "Pandoc Reference List",
 * Bripey/Alias Citations, or the fork's earlier names). Says what it found and
 * offers to disable it. Reports the user's decision back so it isn't asked
 * again.
 */
export class ConflictModal extends Modal {
  private conflicts: ConflictingPlugin[];
  private onResolve: (disableIds: string[], dontAskAgain: boolean) => void;

  constructor(
    app: App,
    conflicts: ConflictingPlugin[],
    onResolve: (disableIds: string[], dontAskAgain: boolean) => void
  ) {
    super(app);
    this.conflicts = conflicts;
    this.onResolve = onResolve;
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    const names = this.conflicts.map((c) => `“${c.name}”`).join(', ');
    contentEl.createEl('h2', { text: 'Another reference-list plugin is enabled' });
    contentEl.createEl('p', {
      text:
        `${names} also ${this.conflicts.length > 1 ? 'provide' : 'provides'} a reference ` +
        `list. ScholarWeft has its own, so with ${this.conflicts.length > 1 ? 'them' : 'it'} ` +
        `enabled you'll see more than one. Disable ` +
        `${this.conflicts.length > 1 ? 'them' : 'it'}, or keep both and stop this prompt.`,
    });
    const disableLabel =
      this.conflicts.length === 1
        ? `Disable “${this.conflicts[0].name}”`
        : 'Disable these plugins';
    new Setting(contentEl)
      .addButton((b) =>
        b.setButtonText(disableLabel).setCta().onClick(() => {
          this.onResolve(this.conflicts.map((c) => c.id), false);
          this.close();
        })
      )
      .addButton((b) =>
        b.setButtonText("Keep both and don't ask again").onClick(() => {
          this.onResolve([], true);
          this.close();
        })
      );
  }

  onClose(): void {
    this.contentEl.empty();
  }
}
