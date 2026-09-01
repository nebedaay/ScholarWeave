import { Platform, TFile, setIcon } from 'obsidian';

import { t } from './lang/helpers';
import { copyElToClipboard } from './helpers';
import ReferenceList from './main';
import clip from 'text-clipper';

export class TooltipManager {
  plugin: ReferenceList;
  tooltip: HTMLDivElement;
  isHoveringTooltip = false;
  isScrollBound = false;

  constructor(plugin: ReferenceList) {
    this.plugin = plugin;
    plugin.register(() => this.hideTooltip());
  }

  showTooltip(el: HTMLSpanElement) {
    if (this.tooltip) {
      this.hideTooltip();
    }

    if (!el.dataset.source) return;

    const file = app.vault.getAbstractFileByPath(el.dataset.source);
    if (!file && !(file instanceof TFile)) {
      return;
    }

    const view = (el as any).win || el.ownerDocument?.defaultView || window;
    view.clearTimeout(this.previewDBTimer);
    view.clearTimeout(this.previewDBTimerClose);

    const keys = el.dataset.citekey.split('|');

    let content: DocumentFragment | HTMLElement = null;

    if (el.dataset.noteIndex) {
      content = createDiv();
      const html = this.plugin.bibManager.getNoteForNoteIndex(
        file as TFile,
        el.dataset.noteIndex
      );
      content.append(...html);
    } else {
      for (const key of keys) {
        const html = this.plugin.bibManager.getBibForCiteKey(
          file as TFile,
          key
        ) as HTMLElement;

        if (html) {
          if (!content) content = createFragment();
          if (keys.length > 1) {
            let target = html.find('.csl-right-inline');
            if (!target) target = html.find('.csl-entry');
            if (!target) target = html;
            const inner = target.innerHTML;
            const clipped = clip(inner, 100, { html: true });
            target.innerHTML = clipped;
          }
          content.append(html);
        }
      }
    }

    const modClasses = this.plugin.settings.hideLinks ? ' collapsed-links' : '';
    const doc = ((el as any).doc || el.ownerDocument || document) as Document;
    const tooltip = (this.tooltip = doc.body.createDiv({
      cls: `lc-tooltip${modClasses}`,
    }));
    const rect = el.getBoundingClientRect();

    if (rect.x === 0 && rect.y === 0) {
      return this.hideTooltip();
    }

    if (this.plugin.settings.hideLinks) {
      tooltip.addClass('collapsed-links');
    }

    if (content) {
      tooltip.append(content);
    } else {
      tooltip.addClass('is-missing');
      tooltip.createEl('em', {
        text: t('No citation found for ') + el.dataset.citekey,
      });
    }

    tooltip.addEventListener('pointerover', () => {
      this.isHoveringTooltip = true;
    });
    tooltip.addEventListener('pointerout', () => {
      this.isHoveringTooltip = false;
    });
    tooltip.addEventListener('click', (evt) => {
      if (evt.targetNode.instanceOf(HTMLElement)) {
        if (
          evt.targetNode.tagName === 'A' ||
          evt.targetNode.hasClass('clickable-icon')
        ) {
          this.hideTooltip();
        }
      }
    });

    view.setTimeout(() => {
      const viewport = view.visualViewport;
      const divRect = tooltip.getBoundingClientRect();

      tooltip.style.left =
        rect.x + divRect.width + 10 > viewport.width
          ? `${rect.x - (rect.x + divRect.width + 10 - viewport.width)}px`
          : `${rect.x}px`;
      tooltip.style.top =
        rect.bottom + divRect.height + 10 > viewport.height
          ? `${rect.y - divRect.height - 5}px`
          : `${rect.bottom + 5}px`;
    });

    this.isScrollBound = true;
    this.boundScroll = () => {
      if (this.isScrollBound) {
        this.hideTooltip();
      }
    };
    view.addEventListener('scroll', this.boundScroll, { capture: true });
  }

  boundScroll: () => void;

  hideTooltip() {
    this.isHoveringTooltip = false;
    this.isScrollBound = false;
    const tView =
      (this.tooltip as any)?.win ||
      this.tooltip?.ownerDocument?.defaultView ||
      window;
    tView.removeEventListener('scroll', this.boundScroll);
    this.tooltip?.remove();
    this.tooltip = null;
    this.boundScroll = null;
  }

  // ── Mobile citation card ─────────────────────────────────────────────────

  /**
   * Show a bottom-sheet citation card on mobile.
   * Reuses the same HTML that the desktop tooltip uses but displays it as a
   * full-width panel with a dismiss backdrop.
   */
  showMobileCard(el: HTMLElement) {
    const file = app.vault.getAbstractFileByPath(el.dataset.source ?? '');
    if (!(file instanceof TFile)) return;

    const keys = (el.dataset.citekey ?? '').split('|').filter(Boolean);
    if (!keys.length) return;

    let content: DocumentFragment | HTMLElement | null = null;
    for (const key of keys) {
      const html = this.plugin.bibManager.getBibForCiteKey(file, key) as HTMLElement | null;
      if (html) {
        if (!content) content = createFragment();
        content.append(html);
      }
    }

    const doc = ((el as any).doc ?? el.ownerDocument ?? document) as Document;
    const backdrop = doc.body.createDiv({ cls: 'lc-mobile-backdrop' });

    // Tap outside the card → dismiss.
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) backdrop.remove();
    });

    const card = backdrop.createDiv({ cls: 'lc-mobile-card lc-reference-list' });

    // Header with close button.
    const header = card.createDiv({ cls: 'lc-mobile-card-header' });
    const closeBtn = header.createDiv({ cls: 'clickable-icon' });
    setIcon(closeBtn, 'x');
    closeBtn.setAttribute('aria-label', t('Close'));
    closeBtn.onClickEvent(() => backdrop.remove());

    if (content) {
      card.append(content);
    } else {
      card.createEl('em', { text: t('No citation found for ') + (el.dataset.citekey ?? '') });
    }

    // Allow links/buttons inside the card to close it after being tapped.
    card.addEventListener('click', (evt) => {
      const target = evt.target as HTMLElement;
      if (target.tagName === 'A' || target.closest('.clickable-icon')) {
        backdrop.remove();
      }
    });
  }

  /**
   * Dispatch the appropriate mobile tap action for the given citation element.
   * Called on `click` events on mobile — replaces the hover-tooltip flow.
   */
  handleMobileTap(el: HTMLElement) {
    const action = this.plugin.settings.mobileClickAction ?? 'show';
    const file = app.vault.getAbstractFileByPath(el.dataset.source ?? '');
    if (!(file instanceof TFile)) return;

    const keys = (el.dataset.citekey ?? '').split('|').filter(Boolean);
    if (!keys.length) return;

    if (action === 'show') {
      this.showMobileCard(el);
      return;
    }

    if (action === 'copy') {
      // Collect formatted citation HTML and copy as rich text + markdown.
      const entries: HTMLElement[] = [];
      for (const key of keys) {
        const html = this.plugin.bibManager.getBibForCiteKey(file, key) as HTMLElement | null;
        if (html) entries.push(html);
      }
      if (entries.length) {
        const wrapper = createDiv();
        entries.forEach((e) => wrapper.append(e));
        copyElToClipboard(wrapper).catch(console.error);
      }
      return;
    }

    if (action === 'link') {
      for (const key of keys) {
        // Priority: Zotero select → PDF file (only when the setting is on)
        // → URL/DOI
        const zLink = this.plugin.bibManager.zCitekeyToLinks.get(key);
        if (zLink) { activeWindow.open(zLink, '_blank'); return; }

        if (this.plugin.settings.showPdfLinks !== false) {
          const pdfLinks = this.plugin.bibManager.zCitekeyToPDFLinks.get(key);
          if (pdfLinks?.length) {
            activeWindow.open(`file://${encodeURI(pdfLinks[0])}`, '_blank');
            return;
          }
        }

        const entry = this.plugin.bibManager.bibCache.get(key) as unknown as Record<string, unknown> | undefined;
        const url = entry?.URL as string | undefined;
        const doi = entry?.DOI as string | undefined;
        if (url) { activeWindow.open(url, '_blank'); return; }
        if (doi) { activeWindow.open(`https://doi.org/${doi}`, '_blank'); return; }
      }
      // Nothing to open — fall back to showing the card.
      this.showMobileCard(el);
    }
  }

  previewDBTimer = 0;
  previewDBTimerClose = 0;
  bindPreviewTooltipHandler(el: HTMLElement) {
    if (Platform.isMobile) {
      // On mobile there's no hover — use a tap/click action instead.
      el.addEventListener('click', () => this.handleMobileTap(el));
      return;
    }

    el.addEventListener('pointerover', (evt) => {
      if (!this.plugin.settings.showCitekeyTooltips) return;
      evt.view.clearTimeout(this.previewDBTimer);
      evt.view.clearTimeout(this.previewDBTimerClose);
      this.previewDBTimer = evt.view.setTimeout(() => {
        this.showTooltip(el);
      }, this.plugin.settings.tooltipDelay);
    });

    el.addEventListener('pointerout', (evt) => {
      evt.view.clearTimeout(this.previewDBTimer);
      if (!this.tooltip) return;
      this.previewDBTimerClose = evt.view.setTimeout(() => {
        if (this.isHoveringTooltip) {
          this.handleToolipHover();
        } else {
          this.hideTooltip();
        }
      }, 150);
    });
  }

  handleToolipHover() {
    if (this.isHoveringTooltip) {
      const { tooltip } = this;
      const outhandler = (evt: PointerEvent) => {
        evt.view.clearTimeout(this.previewDBTimerClose);
        this.previewDBTimerClose = evt.view.setTimeout(() => {
          tooltip.removeEventListener('pointerout', outhandler);
          tooltip.removeEventListener('pointerenter', outhandler);
          if (this.isHoveringTooltip) {
            this.handleToolipHover();
          } else {
            this.hideTooltip();
          }
        }, 150);
      };
      const enterHandler = (evt: PointerEvent) => {
        evt.view.clearTimeout(this.previewDBTimerClose);
      };
      tooltip.addEventListener('pointerout', outhandler);
      tooltip.addEventListener('pointerenter', enterHandler);
    }
  }

  getEditorTooltipHandler() {
    let dbOverTimer = 0;
    let dbOutTimer = 0;
    let isClosing = false;
    let activeKey: string;

    // ── Long-press detection (mobile editor only) ────────────────────────────
    // Tap  → cursor placement as normal (no interception).
    // Hold ≥ LP_DELAY ms without significant movement → citation action.
    //
    // Uses touch events rather than pointer events: `touchcancel` fires when
    // the browser takes over a touch sequence (scroll, pinch-zoom, system
    // interrupt) — pointer events don't surface this reliably in WebView.
    const LP_DELAY = 500; // ms
    const LP_SLOP  = 10;  // px of movement allowed before cancelling
    let lpTimer  = 0;
    let lpTarget: HTMLElement | null = null;
    let lpFired  = false;
    let lpStartX = 0;
    let lpStartY = 0;

    const lpCancel = (win: Window) => {
      if (lpTimer) { win.clearTimeout(lpTimer); lpTimer = 0; }
      lpTarget = null;
    };

    return {
      scroll: (evt: UIEvent) => {
        if (activeKey) {
          evt.view?.clearTimeout(dbOutTimer);
          evt.view?.clearTimeout(dbOverTimer);
          activeKey = null;
        }
        if (lpTimer) lpCancel((evt.view as Window) ?? window);
      },

      // Start the long-press timer when a finger touches a citation span.
      touchstart: (evt: TouchEvent) => {
        if (!Platform.isMobile) return;
        const target = evt.target as HTMLElement | null;
        if (!target?.dataset?.citekey || target.classList.contains('is-link')) return;

        // Cursor already inside this span → OS handles native selection.
        const sel = (target.ownerDocument ?? document).getSelection();
        if (sel?.rangeCount && target.contains(sel.getRangeAt(0).commonAncestorContainer)) return;

        const touch = evt.touches[0];
        lpStartX = touch.clientX;
        lpStartY = touch.clientY;
        lpTarget = target;
        lpFired  = false;
        const win = (evt.view as Window) ?? window;
        win.clearTimeout(lpTimer);
        lpTimer = win.setTimeout(() => {
          lpFired = true;
          lpTimer = 0;
          const t = lpTarget;
          lpTarget = null;
          if (t) this.handleMobileTap(t);
        }, LP_DELAY);
      },

      // Cancel if the finger moves enough to be a scroll gesture.
      touchmove: (evt: TouchEvent) => {
        if (!lpTimer) return;
        const touch = evt.changedTouches[0];
        if (Math.hypot(touch.clientX - lpStartX, touch.clientY - lpStartY) > LP_SLOP) {
          lpCancel((evt.view as Window) ?? window);
        }
      },

      // Regular tap — finger lifted before timer, let click through normally.
      touchend: (evt: TouchEvent) => {
        if (lpTimer) lpCancel((evt.view as Window) ?? window);
      },

      // Browser took over the touch (scroll, pinch, system interrupt) — cancel.
      touchcancel: (evt: TouchEvent) => {
        if (lpTimer) lpCancel((evt.view as Window) ?? window);
      },

      // Swallow the synthetic click that follows a completed long-press so the
      // editor doesn't also reposition the cursor after showing the action.
      click: (evt: MouseEvent) => {
        if (!Platform.isMobile) return;
        if (lpFired) { lpFired = false; evt.preventDefault(); }
        // Normal taps fall through — editor handles cursor placement.
      },

      pointerover: (evt: PointerEvent) => {
        if (Platform.isMobile) return; // handled by touch long-press on mobile
        const target = evt.targetNode;
        if (target.instanceOf(HTMLElement)) {
          const citekey = target.dataset.citekey;
          if (citekey) {
            evt.view.clearTimeout(dbOutTimer);
            isClosing = false;
            if (citekey !== activeKey) {
              if (activeKey) {
                this.hideTooltip();
                activeKey = null;
              }
              evt.view.clearTimeout(dbOverTimer);
              dbOverTimer = evt.view.setTimeout(() => {
                this.showTooltip(target);
                activeKey = citekey;
              }, this.plugin.settings.tooltipDelay);
            }
            return;
          }
        }
        evt.view.clearTimeout(dbOverTimer);
        if (activeKey && !isClosing) {
          if (!this.tooltip) return;
          isClosing = true;
          dbOutTimer = evt.view.setTimeout(() => {
            if (this.isHoveringTooltip) {
              isClosing = false;
            } else {
              this.hideTooltip();
              activeKey = null;
              isClosing = false;
            }
          }, 150);
        }
      },
    };
  }
}
