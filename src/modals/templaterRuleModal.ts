import { App, Modal, Setting } from 'obsidian';
import { t } from '../lang/helpers';

/**
 * Asked when a Templater "Folder templates" rule for the vault root already
 * exists and would be replaced by ScholarWeft's Basic note template. Replacing
 * silently would drop the user's own rule, so we make them choose.
 */
export class TemplaterRuleModal extends Modal {
  private decided = false;

  constructor(
    app: App,
    private existing: string,
    private ours: string,
    private decide: (replace: boolean) => void
  ) {
    super(app);
  }

  private choose(replace: boolean): void {
    if (this.decided) return;
    this.decided = true;
    this.decide(replace);
    this.close();
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.createEl('h3', {
      text: t('A new-note template rule already exists'),
    });
    contentEl.createEl('p', {
      text: t(
        `You already have a rule to apply "${this.existing}" to new notes in "/". What should ScholarWeft do?`
      ),
    });
    new Setting(contentEl)
      .addButton((btn) =>
        btn
          .setButtonText(t(`Keep ${this.existing}`))
          .onClick(() => this.choose(false))
      )
      .addButton((btn) =>
        btn
          .setButtonText(t(`Replace with ${this.ours}`))
          .setCta()
          .onClick(() => this.choose(true))
      );
  }

  onClose(): void {
    // Closing without choosing = keep the user's own rule.
    this.choose(false);
    this.contentEl.empty();
  }
}
