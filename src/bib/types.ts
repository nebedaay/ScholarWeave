export interface CSLName {
  family?: string;
  given?: string;
  literal?: string;
}

export interface PartialCSLEntry {
  id: string;
  title: string;
  groupID?: number;
  /** CSL author list — used by Fuse title-biased search. */
  author?: CSLName[];
  /** CSL editor list. */
  editor?: CSLName[];
  /** Which source this entry was loaded from. Internal — not a CSL field. */
  _source?: 'bib' | 'zotero';
  /** ISO dateModified from Zotero, used to resolve cross-group duplicates. */
  _dateModified?: string;
  /** Internal Zotero item key (8-char, e.g. "ABC12345"). Used to write the
   *  zotero-key frontmatter field that ZotLit needs to index a literature note. */
  _zoteroKey?: string;
  /** Zotero item version — increments on every change to the item. Used for
   *  per-entry cache invalidation (only notes citing a changed entry re-render). */
  _version?: number;
}

export type CSLList = PartialCSLEntry[];
