import { AbstractInputSuggest, App, TFolder } from 'obsidian';

/**
 * Attaches to a text input and suggests vault folder paths as the user types.
 * Used for the "Literature notes folder" setting.
 */
export class FolderSuggest extends AbstractInputSuggest<TFolder> {
  constructor(app: App, inputEl: HTMLInputElement) {
    super(app, inputEl);
  }

  getSuggestions(inputStr: string): TFolder[] {
    const lower = inputStr.toLowerCase();
    const folders: TFolder[] = [];

    this.app.vault.getAllLoadedFiles().forEach((f) => {
      if (f instanceof TFolder && f.path.toLowerCase().includes(lower)) {
        folders.push(f);
      }
    });

    return folders.slice(0, 20);
  }

  renderSuggestion(folder: TFolder, el: HTMLElement): void {
    el.setText(folder.path);
  }

  selectSuggestion(folder: TFolder): void {
    this.inputEl.value = folder.path;
    this.inputEl.trigger('input');
    this.close();
  }
}
