"""In-page JavaScript used to build a compact, ref-addressable page snapshot.

Every visible interactive element gets a ``data-agent-ref`` attribute (``e1``,
``e2`` ...). The agent then acts by ``ref`` instead of guessing CSS selectors,
which is what makes the loop robust against markup changes.
"""

SNAPSHOT_JS = r"""
() => {
  const INTERACTIVE = [
    'a[href]', 'button', 'input:not([type=hidden])', 'textarea', 'select',
    'summary', '[contenteditable=""]', '[contenteditable="true"]',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]',
    '[role=tab]', '[role=menuitem]', '[role=option]', '[role=switch]',
    '[onclick]'
  ].join(',');

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const isVisible = (el) => {
    if (!el || el.disabled) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none'
      && style.opacity !== '0' && el.offsetParent !== null;
  };

  const accessibleName = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const label = document.getElementById(labelledBy);
      if (label) return clean(label.innerText);
    }
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    const control = el.closest('label');
    if (control) return clean(control.innerText);
    return clean(
      el.getAttribute('placeholder') || el.getAttribute('title')
      || el.getAttribute('name') || el.innerText || el.value || ''
    ).slice(0, 140);
  };

  const elements = Array.from(document.querySelectorAll(INTERACTIVE)).filter(isVisible);
  elements.forEach((el, i) => el.setAttribute('data-agent-ref', 'e' + (i + 1)));

  const described = elements.map((el, i) => ({
    ref: 'e' + (i + 1),
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    name: accessibleName(el),
    value: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ? (el.value || '') : '',
  }));

  const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
    .filter(isVisible)
    .slice(0, 40)
    .map((h) => ({ level: h.tagName.toLowerCase(), text: clean(h.innerText).slice(0, 160) }));

  return {
    url: location.href,
    title: document.title,
    elements: described,
    headings: headings,
    text: clean(document.body ? document.body.innerText : ''),
  };
}
"""


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"
