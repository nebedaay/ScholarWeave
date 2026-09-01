import { CSLList, CSLName, PartialCSLEntry } from './types';

// ─── BibTeX serializer ───────────────────────────────────────────────────────
// Converts a list of CSL-JSON entries back to a .bib file string.
// Used by the "Save bibliography snapshot" command.

/** Map from CSL type to BibTeX entry type. */
const CSL_TO_BIBTEX_TYPE: Record<string, string> = {
  'article-journal': 'article',
  'article-magazine': 'article',
  'article-newspaper': 'article',
  article: 'article',
  book: 'book',
  chapter: 'incollection',
  'paper-conference': 'inproceedings',
  thesis: 'phdthesis',
  manuscript: 'unpublished',
  report: 'techreport',
  webpage: 'misc',
  post: 'misc',
  'post-weblog': 'misc',
  dataset: 'misc',
  software: 'misc',
  motion_picture: 'misc',
  speech: 'misc',
  interview: 'misc',
  broadcast: 'misc',
  map: 'misc',
  figure: 'misc',
  graphic: 'misc',
  entry: 'misc',
  'entry-dictionary': 'misc',
  'entry-encyclopedia': 'misc',
  pamphlet: 'misc',
  patent: 'misc',
  review: 'misc',
  'review-book': 'misc',
  song: 'misc',
  treaty: 'misc',
};

function formatNames(names: CSLName[]): string {
  return names
    .map((n) => {
      if (n.literal) return n.literal;
      if (n.family && n.given) return `${n.family}, ${n.given}`;
      return n.family ?? n.given ?? '';
    })
    .join(' and ');
}

function escapeField(value: string): string {
  // Wrap in braces to prevent BibTeX from lowercasing titles, etc.
  return value.replace(/[{}\\]/g, (c) => (c === '\\' ? '\\\\' : c));
}

function getYear(entry: PartialCSLEntry): string | undefined {
  const issued = (entry as any).issued;
  if (!issued) return undefined;
  const parts = issued['date-parts']?.[0];
  if (parts?.[0]) return String(parts[0]);
  if (issued.raw) return issued.raw.slice(0, 4);
  return undefined;
}

function entryToBibTeX(entry: PartialCSLEntry): string {
  const type = CSL_TO_BIBTEX_TYPE[(entry as any).type ?? ''] ?? 'misc';
  const citekey = entry.id;
  const fields: string[] = [];

  const title = (entry as any).title as string | undefined;
  if (title) fields.push(`  title = {${escapeField(title)}}`);

  const authors = entry.author;
  if (authors?.length) fields.push(`  author = {${formatNames(authors)}}`);

  const editors = entry.editor;
  if (editors?.length) fields.push(`  editor = {${formatNames(editors)}}`);

  const year = getYear(entry);
  if (year) fields.push(`  year = {${year}}`);

  const journal = (entry as any)['container-title'] as string | undefined;
  if (journal && (type === 'article')) {
    fields.push(`  journal = {${escapeField(journal)}}`);
  }

  const booktitle = (entry as any)['container-title'] as string | undefined;
  if (booktitle && (type === 'incollection' || type === 'inproceedings')) {
    fields.push(`  booktitle = {${escapeField(booktitle)}}`);
  }

  const volume = (entry as any).volume as string | undefined;
  if (volume) fields.push(`  volume = {${escapeField(String(volume))}}`);

  const issue = (entry as any).issue as string | undefined;
  if (issue) fields.push(`  number = {${escapeField(String(issue))}}`);

  const page = (entry as any).page as string | undefined;
  if (page) fields.push(`  pages = {${escapeField(String(page))}}`);

  const publisher = (entry as any).publisher as string | undefined;
  if (publisher) fields.push(`  publisher = {${escapeField(publisher)}}`);

  const publisherPlace = (entry as any)['publisher-place'] as string | undefined;
  if (publisherPlace) fields.push(`  address = {${escapeField(publisherPlace)}}`);

  const url = (entry as any).URL as string | undefined;
  if (url) fields.push(`  url = {${escapeField(url)}}`);

  const doi = (entry as any).DOI as string | undefined;
  if (doi) fields.push(`  doi = {${escapeField(doi)}}`);

  const isbn = (entry as any).ISBN as string | undefined;
  if (isbn) fields.push(`  isbn = {${escapeField(isbn)}}`);

  const issn = (entry as any).ISSN as string | undefined;
  if (issn) fields.push(`  issn = {${escapeField(issn)}}`);

  const abstract = (entry as any).abstract as string | undefined;
  if (abstract) fields.push(`  abstract = {${escapeField(abstract)}}`);

  return `@${type}{${citekey},\n${fields.join(',\n')}\n}`;
}

/**
 * Convert an array of CSL-JSON entries to a BibTeX file string.
 */
export function cslToBibTeX(entries: CSLList): string {
  return entries.map(entryToBibTeX).join('\n\n') + '\n';
}
