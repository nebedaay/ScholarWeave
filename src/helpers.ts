// Shared utility helpers used across the plugin.

/**
 * A Promise whose resolve/reject methods are exposed as properties, so the
 * caller can fulfill it from outside the constructor callback.
 */
export class PromiseCapability<T> {
  readonly promise: Promise<T>;
  resolve!: (value: T | PromiseLike<T>) => void;
  reject!: (reason?: unknown) => void;

  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;
    });
  }
}

/** Log only in development builds. */
export function debugLog(...args: unknown[]): void {
  if (process.env.NODE_ENV !== 'production') {
    console.log(...args);
  }
}

/**
 * Copy an HTMLElement's content to the clipboard as both rich-text HTML and
 * plain text, so pasting into a word processor preserves formatting while
 * pasting into a plain-text editor gives readable text.
 */
export async function copyElToClipboard(el: HTMLElement): Promise<void> {
  const html = el.outerHTML;
  const text = el.innerText ?? el.textContent ?? '';

  if (
    typeof ClipboardItem !== 'undefined' &&
    navigator.clipboard?.write
  ) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([text], { type: 'text/plain' }),
        }),
      ]);
      return;
    } catch {
      // Fall through to plain-text fallback.
    }
  }

  await copyTextToClipboard(text);
}

/** Copy a plain string to the clipboard. */
export async function copyTextToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Textarea execCommand fallback for older environments.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
  }
}
