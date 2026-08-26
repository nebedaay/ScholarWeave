import * as BibTeXParser from '@retorquere/bibtex-parser';
import { parseYaml } from 'obsidian';
import { PartialCSLEntry } from './types';

// Mapping from BibTeX/BibLaTeX entry types to CSL types.
const BIBTEX_TYPE_TO_CSL: Record<string, string> = {
  article: 'article-journal',
  book: 'book',
  booklet: 'pamphlet',
  collection: 'book',
  conference: 'paper-conference',
  dataset: 'dataset',
  inbook: 'chapter',
  incollection: 'chapter',
  inproceedings: 'paper-conference',
  manual: 'book',
  mastersthesis: 'thesis',
  misc: 'document',
  online: 'webpage',
  patent: 'patent',
  periodical: 'periodical',
  phdthesis: 'thesis',
  proceedings: 'book',
  report: 'report',
  software: 'software',
  techreport: 'report',
  thesis: 'thesis',
  unpublished: 'manuscript',
  video: 'motion_picture',
  www: 'webpage',
};

/**
 * Safely extract a trimmed, non-empty string from a BibTeX field value.
 *
 * The parser nominally returns `string[]` per field, but in practice the shape
 * can be surprising (undefined, a bare string, nested arrays, numbers).  We
 * accept `unknown`, coerce every item to a string, trim it, and return the
 * first non-empty result — or `undefined` if nothing usable was found.
 */
function fieldStr(val: unknown): string | undefined {
  if (val === null || val === undefined) return undefined;
  const items = Array.isArray(val) ? val : [val];
  for (const item of items) {
    if (item === null || item === undefined) continue;
    const s = (typeof item === 'string' ? item : String(item)).trim();
    if (s) return s;
  }
  return undefined;
}

/**
 * Convert a single BibTeX creator to a CSL name object.
 *
 * Accepts `unknown` so callers never need to cast.  Returns `null` when the
 * input is not a usable object (filtered out by the caller).
 */
function creatorToCSL(
  creator: unknown
): { family?: string; given?: string; literal?: string } | null {
  if (!creator || typeof creator !== 'object' || Array.isArray(creator)) return null;
  const c = creator as Record<string, unknown>;

  if (c.literal) {
    const lit = String(c.literal).trim();
    if (lit) return { literal: lit };
  }

  const result: { family?: string; given?: string } = {};
  if (c.lastName) {
    const family = String(c.lastName).trim();
    if (family) result.family = family;
  }
  if (c.firstName) {
    const given = String(c.firstName).trim();
    if (given) result.given = given;
  }
  // Don't emit an empty name object — it confuses citeproc.
  return (result.family || result.given) ? result : null;
}

function parseIssuedDate(
  fields: Record<string, unknown>
): { 'date-parts': number[][] } | undefined {
  // BibLaTeX: `date` field is ISO format YYYY[-MM[-DD]]
  const dateStr = fieldStr(fields.date);
  if (dateStr) {
    const parts = dateStr
      .split('-')
      .map((s) => parseInt(s, 10))
      .filter((n) => Number.isFinite(n) && n > 0);
    if (parts.length > 0) return { 'date-parts': [parts] };
  }
  // Classic BibTeX: separate `year` (and optional `month`) fields
  const yearStr = fieldStr(fields.year);
  if (yearStr) {
    const year = parseInt(yearStr, 10);
    if (Number.isFinite(year) && year > 0) return { 'date-parts': [[year]] };
  }
  return undefined;
}

/**
 * Last-resort year extraction from a citekey.
 * Matches the first 4-digit sequence in the range 1000–2099 that is
 * surrounded by non-digits (or string boundaries).  Zotero's default
 * citekey format (`smith_2020_title`, `smith-jones-2020`) makes this
 * reliable in practice.
 */
function yearFromKey(key: string): number | undefined {
  const m = key.match(/(?:^|[^0-9])(1[0-9]{3}|20[0-9]{2})(?:[^0-9]|$)/);
  if (!m) return undefined;
  const year = parseInt(m[1], 10);
  return Number.isFinite(year) && year > 0 ? year : undefined;
}

/**
 * Convert a raw DOI value to a bare DOI string.
 * Zotero sometimes stores the full URL in the DOI field.
 */
function normalizeDOI(raw: string): string {
  return raw.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').trim();
}

function getExt(filePath: string): string {
  const base = filePath.split('/').pop() ?? filePath;
  const dot = base.lastIndexOf('.');
  return dot < 0 ? '' : base.slice(dot).toLowerCase();
}

export function parseBibTeX(raw: string): PartialCSLEntry[] {
  // Nothing to parse.
  if (!raw?.trim()) return [];

  const options: BibTeXParser.ParserOptions = {
    errorHandler: (err: unknown) => {
      console.warn('scholar-weave: BibTeX parse warning:', err);
    },
  };

  let parsed: BibTeXParser.Bibliography;
  try {
    parsed = BibTeXParser.parse(raw, options) as BibTeXParser.Bibliography;
  } catch (err) {
    console.error('scholar-weave: BibTeX parser threw — file may be severely malformed:', err);
    return [];
  }

  // Log any structural errors the parser reported with enough detail to diagnose.
  (parsed?.errors ?? []).forEach((e: unknown) => {
    const detail =
      e instanceof Error
        ? e.message
        : typeof e === 'object' && e !== null
        ? JSON.stringify(e)
        : String(e);
    console.warn('scholar-weave: BibTeX parse error:', detail);
  });

  const entries = parsed?.entries;
  if (!Array.isArray(entries) || entries.length === 0) return [];

  const results: PartialCSLEntry[] = [];

  for (const entry of entries) {
    // Reject completely non-object entries.
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) continue;

    // key must be a non-empty string.
    const key = typeof entry.key === 'string' ? entry.key.trim() : '';
    if (!key) continue;

    try {
      // fields: must be a plain object — reject arrays / primitives.
      const f: Record<string, unknown> =
        entry.fields !== null &&
        entry.fields !== undefined &&
        typeof entry.fields === 'object' &&
        !Array.isArray(entry.fields)
          ? (entry.fields as Record<string, unknown>)
          : {};

      // type: normalise to lowercase; default to 'document'.
      const rawType =
        typeof entry.type === 'string' ? entry.type.toLowerCase().trim() : '';

      const csl: Record<string, unknown> = {
        id: key,
        type: BIBTEX_TYPE_TO_CSL[rawType] ?? 'document',
      };

      // ── Title ────────────────────────────────────────────────────────────
      const title = fieldStr(f.title);
      if (title) csl.title = title;

      // ── Container title (journal / book / proceedings / …) ───────────────
      const container =
        fieldStr(f.journaltitle) ??
        fieldStr(f.journal) ??
        fieldStr(f.booktitle) ??
        fieldStr(f.maintitle);
      if (container) csl['container-title'] = container;

      const abbrev = fieldStr(f.shortjournal);
      if (abbrev) csl['container-title-short'] = abbrev;

      // ── Creators ──────────────────────────────────────────────────────────
      // @retorquere/bibtex-parser v9+ stores author/editor arrays directly in
      // entry.fields[role] (e.g. fields.author = [{firstName, lastName}]).
      // Earlier versions (and some forks) put them in entry.creators[role].
      // We check entry.creators first for backwards-compat, then fall back to
      // entry.fields so both layouts work without any version detection.
      const rawCreators = entry.creators;
      const creatorsMap: Record<string, unknown> =
        rawCreators !== null &&
        rawCreators !== undefined &&
        typeof rawCreators === 'object' &&
        !Array.isArray(rawCreators)
          ? (rawCreators as Record<string, unknown>)
          : {};

      const CREATOR_ROLES: Record<string, string> = {
        author: 'author',
        editor: 'editor',
        translator: 'translator',
        bookauthor: 'container-author',
      };

      for (const [bibRole, cslRole] of Object.entries(CREATOR_ROLES)) {
        // Prefer entry.creators[role]; fall back to entry.fields[role].
        const roleList = Array.isArray(creatorsMap[bibRole])
          ? creatorsMap[bibRole]
          : f[bibRole];
        if (!Array.isArray(roleList) || roleList.length === 0) continue;
        const mapped = roleList
          .map(creatorToCSL)
          .filter((c): c is NonNullable<ReturnType<typeof creatorToCSL>> => c !== null);
        if (mapped.length > 0) csl[cslRole] = mapped;
      }

      // ── Date ─────────────────────────────────────────────────────────────
      const issued = parseIssuedDate(f);
      if (issued) csl.issued = issued;

      // ── Volume / issue / pages / edition ─────────────────────────────────
      const volume = fieldStr(f.volume);
      if (volume) csl.volume = volume;

      // `number` is the classic BibTeX issue field; BibLaTeX uses `issue`.
      const issue = fieldStr(f.issue) ?? fieldStr(f.number);
      if (issue) csl.issue = issue;

      const pages = fieldStr(f.pages);
      // Normalise any run of hyphens/en-dashes to a proper en-dash.
      if (pages) csl.page = pages.replace(/--+/g, '–');

      const edition = fieldStr(f.edition);
      if (edition) csl.edition = edition;

      // ── Publisher / place ────────────────────────────────────────────────
      const publisher =
        fieldStr(f.publisher) ?? fieldStr(f.institution) ?? fieldStr(f.school);
      if (publisher) csl.publisher = publisher;

      const place = fieldStr(f.location) ?? fieldStr(f.address);
      if (place) csl['publisher-place'] = place;

      // ── Identifiers (trim whitespace that .bib files occasionally embed) ──
      const rawDoi = fieldStr(f.doi);
      // Normalise: Zotero sometimes stores "https://doi.org/10.xxx" in the DOI field.
      if (rawDoi) csl.DOI = normalizeDOI(rawDoi);

      const url = fieldStr(f.url);
      if (url) csl.URL = url;

      const isbn = fieldStr(f.isbn);
      if (isbn) csl.ISBN = isbn;

      const issn = fieldStr(f.issn);
      if (issn) csl.ISSN = issn;

      // ── Series ───────────────────────────────────────────────────────────
      const series = fieldStr(f.series);
      if (series) csl['collection-title'] = series;

      // ── Miscellaneous ────────────────────────────────────────────────────
      const abstract = fieldStr(f.abstract);
      if (abstract) csl.abstract = abstract;

      const language = fieldStr(f.language);
      if (language) csl.language = language;

      // `type` inside an entry is the thesis/report subtype, maps to CSL `genre`.
      const genre = fieldStr(f.type);
      if (genre) csl.genre = genre;

      const note = fieldStr(f.note);
      if (note) csl.note = note;

      // ── Recovery: fill in missing fields from what we can derive ──────────
      // These run after all normal extraction so they never overwrite real data.

      // If no date was found in any field, try extracting a year from the citekey.
      // Zotero's citekey format (`smith_2020_title`) makes this reliable.
      if (!csl.issued) {
        const year = yearFromKey(key);
        if (year) csl.issued = { 'date-parts': [[year]] };
      }

      // If no title was found, use the citekey humanised as a readable fallback
      // so the entry is at least identifiable in a reference list.
      if (!csl.title) {
        csl.title = key.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
      }

      results.push(csl as unknown as PartialCSLEntry);
    } catch (err) {
      // A single bad entry must not abort the rest of the bibliography.
      console.warn(`scholar-weave: skipping entry '${key}' due to unexpected error:`, err);
    }
  }

  return results;
}

export function parseCSLJSON(raw: string): PartialCSLEntry[] {
  return JSON.parse(raw);
}

export function parseCSLYAML(raw: string): PartialCSLEntry[] {
  const data = parseYaml(raw);
  // pandoc CSL-YAML: top-level array or { references: [...] }
  if (Array.isArray(data)) return data as PartialCSLEntry[];
  if (Array.isArray(data?.references)) return data.references as PartialCSLEntry[];
  return [];
}

export function parseBibFile(raw: string, filePath: string): PartialCSLEntry[] {
  const ext = getExt(filePath);
  switch (ext) {
    case '.json': return parseCSLJSON(raw);
    case '.yaml':
    case '.yml':  return parseCSLYAML(raw);
    default:      return parseBibTeX(raw);
  }
}
