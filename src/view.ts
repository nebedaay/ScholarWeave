import { ItemView, MarkdownView, WorkspaceLeaf, setIcon } from 'obsidian';

import { copyElToClipboard } from './helpers';
import { t } from './lang/helpers';
import ReferenceList from './main';

export const viewType = 'ReferenceListView';

export class ReferenceListView extends ItemView {
  plugin: ReferenceList;
  activeMarkdownLeaf: MarkdownView;

  constructor(leaf: WorkspaceLeaf, plugin: ReferenceList) {
    super(leaf);
    this.plugin = plugin;

    this.contentEl.addClass('lc-reference-list');
    this.contentEl.toggleClass(
      'collapsed-links',
      !!this.plugin.settings.hideLinks
    );
    this.setNoContentMessage();
  }

  setViewContent(bib: HTMLElement) {
    if (bib && this.contentEl.firstChild !== bib) {
      let count = 0;
      const activeView = this.plugin.app.workspace.getActiveViewOfType(MarkdownView);
      const fileCache = activeView?.file
        ? this.plugin.bibManager.fileCache.get(activeView.file)
        : null;
      const unresolvedCount = fileCache?.unresolvedKeys.size ?? 0;
      const globalOnlyCount = fileCache?.globalOnlyKeys.size ?? 0;
      bib.findAll('.csl-entry').forEach((e) => {
        count++;
        const leafRoot = this.leaf.getRoot();
        if (leafRoot) {
          const tooltipPos =
            (leafRoot as any).side === 'right' ? 'left' : 'right';
          e.setAttribute('aria-label-position', tooltipPos);
        }
      });

      this.contentEl.empty();
      this.contentEl.createDiv(
        {
          cls: 'lc-reference-list__title',
        },
        (div) => {
          div.createDiv({ text: this.getDisplayText() });
          div.createDiv({}, (div) => {
            if (count) {
              div.createDiv({
                cls: 'lc-reference-list__count',
                text: count.toString(),
              });
            }
            if (unresolvedCount) {
              const unresolvedBadge = div.createDiv({
                cls: 'lc-reference-list__unresolved-count clickable-icon',
                text: unresolvedCount.toString(),
                attr: {
                  'aria-label': t('Update unresolved citations'),
                },
              });
              if (activeView?.file) {
                const file = activeView.file;
                unresolvedBadge.onClickEvent(() => {
                  this.plugin.showUnresolvedCitekeyDialog(file);
                });
              }
            }
            if (globalOnlyCount) {
              div.createDiv({
                cls: 'lc-reference-list__global-only-count',
                text: globalOnlyCount.toString(),
                attr: {
                  'aria-label': t('Citations not in local bibliography snapshot'),
                },
              });
            }
            div.createDiv(
              {
                cls: 'clickable-icon',
                attr: {
                  'aria-label': t('Copy list'),
                },
              },
              (btn) => {
                setIcon(btn, 'lucide-copy');
                btn.onClickEvent(() => copyElToClipboard(bib));
              }
            );
            // Snapshot button — only shown when there are citations to save.
            if (activeView?.file) {
              div.createDiv(
                {
                  cls: 'clickable-icon',
                  attr: {
                    'aria-label': t('Save bibliography snapshot'),
                  },
                },
                (btn) => {
                  setIcon(btn, 'lucide-camera');
                  btn.onClickEvent(() => {
                    const file = activeView.file;
                    const entries = this.plugin.bibManager.snapshotEntries(file);
                    if (entries?.length) this.plugin.openSnapshot(file, entries);
                  });
                }
              );
            }
          });
        }
      );

      if (count > 1) {
        const searchWrap = this.contentEl.createDiv({ cls: 'lc-search-wrap' });
        const input = searchWrap.createEl('input', {
          cls: 'lc-search-input',
          attr: { type: 'search', placeholder: t('Filter references…') },
        });
        input.addEventListener('input', () => {
          const q = input.value.toLowerCase().trim();
          bib.findAll('.csl-entry-wrapper').forEach((wrapper) => {
            const visible = !q || (wrapper.textContent ?? '').toLowerCase().includes(q);
            (wrapper as HTMLElement).style.display = visible ? '' : 'none';
          });
        });
      }

      this.contentEl.append(bib);
    } else if (!bib) {
      this.setNoContentMessage();
    }
  }

  setNoContentMessage() {
    this.setMessage(t('No citations found in the current document.'));
  }

  setMessage(message: string) {
    this.contentEl.empty();
    this.contentEl.createDiv({
      cls: 'pane-empty',
      text: message,
    });
  }

  getViewType() {
    return viewType;
  }

  getDisplayText() {
    return t('References');
  }

  getIcon() {
    return 'quote-glyph';
  }
}
