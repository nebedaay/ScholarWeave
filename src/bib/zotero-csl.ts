import { PartialCSLEntry } from './types';

// Pure Zotero-item-to-CSL-JSON conversion. No I/O, no Obsidian deps.

export const ZOTERO_TYPE_TO_CSL: Record<string, string> = {
  artwork: 'graphic',
  audioRecording: 'song',
  bill: 'bill',
  blogPost: 'post-weblog',
  book: 'book',
  bookSection: 'chapter',
  case: 'legal_case',
  computerProgram: 'software',
  conferencePaper: 'paper-conference',
  dataset: 'dataset',
  dictionaryEntry: 'entry-dictionary',
  document: 'document',
  email: 'personal_communication',
  encyclopediaArticle: 'entry-encyclopedia',
  film: 'motion_picture',
  forumPost: 'post',
  hearing: 'hearing',
  instantMessage: 'personal_communication',
  interview: 'interview',
  journalArticle: 'article-journal',
  letter: 'personal_communication',
  magazineArticle: 'article-magazine',
  manuscript: 'manuscript',
  map: 'map',
  newspaperArticle: 'article-newspaper',
  note: 'document',
  patent: 'patent',
  podcast: 'broadcast',
  preprint: 'article',
  presentation: 'speech',
  radioBroadcast: 'broadcast',
  report: 'report',
  standard: 'standard',
  statute: 'legislation',
  thesis: 'thesis',
  tvBroadcast: 'broadcast',
  videoRecording: 'motion_picture',
  webpage: 'webpage',
};

export function parseZoteroDate(dateStr: string): any {
  if (!dateStr) return undefined;
  const fullMatch = dateStr.match(/(\d{4})-(\d{2})-(\d{2})/);
  if (fullMatch) return { 'date-parts': [[+fullMatch[1], +fullMatch[2], +fullMatch[3]]] };
  const ymMatch = dateStr.match(/(\d{4})-(\d{2})/);
  if (ymMatch) return { 'date-parts': [[+ymMatch[1], +ymMatch[2]]] };
  const yMatch = dateStr.match(/(\d{4})/);
  if (yMatch) return { 'date-parts': [[+yMatch[1]]] };
  return { raw: dateStr };
}

function zoteroCreatorToCSL(creator: any): any {
  if (creator.name) return { literal: creator.name };
  const r: any = {};
  if (creator.lastName) r.family = creator.lastName;
  if (creator.firstName) r.given = creator.firstName;
  return r;
}

const CREATOR_TYPE_TO_CSL_ROLE: Record<string, string> = {
  author: 'author',
  editor: 'editor',
  translator: 'translator',
  contributor: 'contributor',
  bookAuthor: 'container-author',
  seriesEditor: 'collection-editor',
  director: 'director',
  interviewer: 'interviewer',
  interviewee: 'author',
  composer: 'composer',
  producer: 'producer',
  scriptwriter: 'script-writer',
  reviewedAuthor: 'reviewed-author',
  performer: 'performer',
  wordsBy: 'lyricist',
  recipient: 'recipient',
  witness: 'witness',
  castMember: 'performer',
};

export function zoteroItemToCSL(item: any, groupId: number): PartialCSLEntry | null {
  const data = item.data;
  if (!data?.citationKey) return null;

  const csl: any = {
    id: data.citationKey,
    type: ZOTERO_TYPE_TO_CSL[data.itemType] || 'document',
    groupID: groupId,
  };

  if (data.title) csl.title = data.title;

  if (data.creators?.length) {
    const byRole: Record<string, any[]> = {};
    for (const creator of data.creators) {
      const role = CREATOR_TYPE_TO_CSL_ROLE[creator.creatorType] || 'author';
      if (!byRole[role]) byRole[role] = [];
      byRole[role].push(zoteroCreatorToCSL(creator));
    }
    for (const [role, names] of Object.entries(byRole)) csl[role] = names;
  }

  if (data.date) csl.issued = parseZoteroDate(data.date);

  const containerTitle =
    data.publicationTitle ?? data.bookTitle ?? data.encyclopediaTitle ??
    data.dictionaryTitle ?? data.blogTitle ?? data.websiteTitle ??
    data.forumTitle ?? data.proceedingsTitle ?? data.programTitle;
  if (containerTitle) csl['container-title'] = containerTitle;
  if (data.journalAbbreviation) csl['container-title-short'] = data.journalAbbreviation;

  if (data.volume) csl.volume = data.volume;
  if (data.issue) csl.issue = data.issue;
  if (data.pages) csl.page = data.pages;
  if (data.numberOfVolumes) csl['number-of-volumes'] = data.numberOfVolumes;
  if (data.numberOfPages) csl['number-of-pages'] = data.numberOfPages;
  if (data.edition) csl.edition = data.edition;
  if (data.publisher) csl.publisher = data.publisher;
  if (data.institution) csl.publisher = data.institution;
  if (data.university) csl.publisher = data.university;
  if (data.place) csl['publisher-place'] = data.place;
  if (data.DOI) csl.DOI = data.DOI;
  if (data.URL) csl.URL = data.URL;
  if (data.ISBN) csl.ISBN = data.ISBN;
  if (data.ISSN) csl.ISSN = data.ISSN;
  if (data.callNumber) csl['call-number'] = data.callNumber;
  if (data.abstractNote) csl.abstract = data.abstractNote;
  if (data.language) csl.language = data.language;
  if (data.thesisType) csl.genre = data.thesisType;
  if (data.reportType) csl.genre = data.reportType;
  if (data.reportNumber) csl.number = data.reportNumber;
  if (data.patentNumber) csl.number = data.patentNumber;
  if (data.country) csl.jurisdiction = data.country;
  if (data.applicationNumber) csl['call-number'] = data.applicationNumber;
  if (data.series) csl['collection-title'] = data.series;
  if (data.seriesTitle) csl['collection-title'] = data.seriesTitle;
  if (data.seriesNumber) csl['collection-number'] = data.seriesNumber;
  if (data.conferenceName) csl['event-title'] = data.conferenceName;
  if (data.section) csl.section = data.section;

  // Internal metadata — not CSL fields.
  if (data.dateModified) csl._dateModified = data.dateModified;
  // item.key is the 8-char Zotero item key (top-level, not inside data).
  // ZotLit needs this to index a literature note via the "zotero-key" frontmatter field.
  if (item.key) csl._zoteroKey = item.key;
  // item.version increments on every change to the item in Zotero. Used to
  // invalidate only the cached citations that reference this entry, instead of
  // re-rendering every note when anything in the library changes.
  if (item.version != null) csl._version = item.version;

  return csl as PartialCSLEntry;
}
