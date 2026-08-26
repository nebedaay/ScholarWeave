import type { TFile } from 'obsidian';

export const API_VERSION = 1;

export interface LinkedCitationsApi {
  version: typeof API_VERSION;
  focusReferenceListView(): Promise<void>;
  getCitekeysForFile(file?: TFile): Promise<string[]>;
}
