import { AbstractInputSuggest, App, TFolder } from 'obsidian';

/**
 * Attaches to a text input and suggests vault folder paths as the user types.
 * Used for the "Literature notes folder" setting.
 */
export class FolderSuggest extends AbstractInputSuggest<TFolder> {
  // Our own reference to the input element — AbstractInputSuggest no longer
  // exposes one publicly (use this.setValue()/this.getValue() for the
  // sanctioned read/write path); we still need the raw element ourselves to
  // fire an 'input' event so Obsidian's own change listeners pick up the
  // programmatic value change.
  private textInputEl: HTMLInputElement;

  constructor(app: App, inputEl: HTMLInputElement) {
    super(app, inputEl);
    this.textInputEl = inputEl;
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
    this.setValue(folder.path);
    this.textInputEl.trigger('input');
    this.close();
  }
}
