/**
 * In-app documentation. The docs/*.md files are bundled into main.js by
 * esbuild (see esbuild.config.mjs) and rendered with Obsidian's
 * MarkdownRenderer, so users can read the guides inside the settings modal
 * instead of going to GitHub. A GitHub link is kept per page.
 */
import { App, Component, MarkdownRenderer, Modal } from 'obsidian';
import { BUNDLED_ASSETS } from 'bundled:assets';

export const GITHUB_DOCS_BASE =
  'https://github.com/nebedaay/ScholarWeft/blob/main/docs/';

export interface DocEntry {
  /** Filename within docs/, e.g. "setup.md". */
  name: string;
  /** Menu label. */
  title: string;
  markdown: string;
}

/** Menu order + labels. Any doc not listed is appended automatically. */
const DOC_ORDER: [string, string][] = [
  ['setup.md', 'Setup'],
  ['dependencies.md', 'Dependencies'],
  ['bibliography.md', 'Bibliography'],
  ['citations.md', 'Citations and references'],
  ['linked-citations.md', 'Linked citations'],
  ['zotero.md', 'Zotero'],
  ['literature-notes.md', 'Literature notes'],
  ['zotlit-import-templates.md', 'ZotLit import templates'],
  ['import-export.md', 'Document import and export'],
  ['commands.md', 'Commands'],
  ['mobile.md', 'Mobile'],
];

/** Replace `<img src="./images/…">` with a data URI from the bundled image, so
 *  the README (which links to repo-relative images) renders in-app. */
function inlineBundledImages(markdown: string): string {
  return markdown.replace(
    /(<img\b[^>]*\bsrc=")(?:\.?\/?)(images\/[^"]+\.(?:png|jpe?g|gif|svg|webp))(")/gi,
    (m, pre: string, rel: string, post: string) => {
      const asset = BUNDLED_ASSETS[rel];
      if (!asset?.content) return m;
      const lower = rel.toLowerCase();
      const mime = lower.endsWith('.svg')
        ? 'image/svg+xml'
        : lower.endsWith('.jpg') || lower.endsWith('.jpeg')
        ? 'image/jpeg'
        : lower.endsWith('.gif')
        ? 'image/gif'
        : lower.endsWith('.webp')
        ? 'image/webp'
        : 'image/png';
      const base64 = asset.binary
        ? asset.content
        : btoa(unescape(encodeURIComponent(asset.content)));
      return `${pre}data:${mime};base64,${base64}${post}`;
    }
  );
}

export function getDocs(): DocEntry[] {
  const out: DocEntry[] = [];
  const seen = new Set<string>();

  // The repo README, rendered as the "Overview" page. Its `./docs/…md` links
  // are intercepted by DocsModal.show() and navigate in-app; its image is
  // inlined from the bundle above.
  const readme = BUNDLED_ASSETS['README.md']?.content;
  if (readme) {
    out.push({ name: 'README.md', title: 'Overview', markdown: inlineBundledImages(readme) });
    seen.add('README.md');
  }

  const take = (name: string, title: string) => {
    const asset = BUNDLED_ASSETS[`docs/${name}`];
    if (!asset?.content) return;
    out.push({ name, title, markdown: asset.content });
    seen.add(name);
  };
  for (const [name, title] of DOC_ORDER) take(name, title);
  for (const key of Object.keys(BUNDLED_ASSETS)) {
    if (!key.startsWith('docs/')) continue;
    const name = key.slice('docs/'.length);
    if (seen.has(name)) continue;
    take(name, name.replace(/\.md$/, ''));
  }

  // NOTICE (repo root) — last, so the README's [NOTICE.md] link resolves in-app.
  const notice = BUNDLED_ASSETS['NOTICE.md']?.content;
  if (notice) out.push({ name: 'NOTICE.md', title: 'Notice', markdown: notice });

  return out;
}

export function githubDocUrl(name: string): string {
  return GITHUB_DOCS_BASE + name;
}

/** Modal with a docs navigation list and a rendered markdown pane. */
export class DocsModal extends Modal {
  private body!: HTMLElement;
  private navItems = new Map<string, HTMLElement>();
  private component = new Component();

  constructor(app: App, private initial = 'README.md') {
    super(app);
  }

  onOpen(): void {
    this.component.load();
    // Width lives on the modal element (contentEl sits inside it).
    this.modalEl.addClass('sw-docs-modal');
    const { contentEl } = this;
    contentEl.addClass('sw-docs-modal');
    contentEl.createEl('h3', { text: 'ScholarWeft documentation' });
    const wrap = contentEl.createDiv({ cls: 'sw-docs-wrap' });
    const nav = wrap.createDiv({ cls: 'sw-docs-nav' });
    this.body = wrap.createDiv({ cls: 'sw-docs-body' });

    for (const doc of getDocs()) {
      const item = nav.createEl('a', { text: doc.title, cls: 'sw-docs-nav-item' });
      item.addEventListener('click', (e) => {
        e.preventDefault();
        this.show(doc.name);
      });
      this.navItems.set(doc.name, item);
    }
    this.show(this.initial);
  }

  /** Render a doc; internal `./other.md` links navigate within the modal. */
  private show(name: string): void {
    const docs = getDocs();
    const doc = docs.find((d) => d.name === name) ?? docs[0];
    if (!doc) return;

    for (const [n, el] of this.navItems) {
      el.toggleClass('is-active', n === doc.name);
    }

    this.body.empty();
    const gh = this.body.createDiv({ cls: 'sw-docs-gh' });
    gh.createEl('a', {
      text: 'Open on GitHub',
      href: githubDocUrl(doc.name),
    }).setAttr('target', '_blank');

    const content = this.body.createDiv({ cls: 'sw-docs-content' });
    content.addEventListener('click', (e) => {
      const a = (e.target as HTMLElement).closest('a');
      if (!a) return;
      const href = a.getAttribute('href') ?? '';
      if (/^https?:/i.test(href)) return;
      const m = href.match(/([\w-]+\.md)(?:#.*)?$/);
      if (m && docs.some((d) => d.name === m[1])) {
        e.preventDefault();
        this.show(m[1]);
      }
    });
    void MarkdownRenderer.render(this.app, doc.markdown, content, '', this.component);
    this.body.scrollTop = 0;
  }

  onClose(): void {
    this.component.unload();
    this.contentEl.empty();
  }
}

/** Open the docs modal (optionally at a specific page). */
export function openDocs(name = 'README.md'): void {
  new DocsModal(app, name).open();
}
