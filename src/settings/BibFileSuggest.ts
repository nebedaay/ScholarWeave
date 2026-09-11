import { AbstractInputSuggest, App, TFile } from 'obsidian';

const BIB_EXTENSIONS = new Set(['bib', 'json', 'yaml', 'yml']);

/**
 * Attaches to a text input and suggests bibliography files (.bib, .json,
 * .yaml, .yml) from the vault as the user types.
 */
export class BibFileSuggest extends AbstractInputSuggest<TFile> {
  // See FolderSuggest.ts for why we keep our own reference: AbstractInputSuggest
  // no longer exposes inputEl publicly.
  private textInputEl: HTMLInputElement;

  constructor(app: App, inputEl: HTMLInputElement) {
    super(app, inputEl);
    this.textInputEl = inputEl;
  }

  getSuggestions(inputStr: string): TFile[] {
    const lower = inputStr.toLowerCase();
    const files: TFile[] = [];

    this.app.vault.getAllLoadedFiles().forEach((f) => {
      if (
        f instanceof TFile &&
        BIB_EXTENSIONS.has(f.extension) &&
        f.path.toLowerCase().includes(lower)
      ) {
        files.push(f);
      }
    });

    return files.slice(0, 20);
  }

  renderSuggestion(file: TFile, el: HTMLElement): void {
    el.setText(file.path);
  }

  selectSuggestion(file: TFile): void {
    this.setValue(file.path);
    this.textInputEl.trigger('input');
    this.close();
  }
}
