"""Contact (chat list) extraction.

Primary path reads WhatsApp Web's in-page model store, which is far more stable
than CSS classes. The DOM path is a fallback used only when the store is unavailable.
"""

from typing import Any

from playwright.async_api import Page

STORE_AVAILABLE_JS = """
() => {
  try { return !!(window.require && window.require('WAWebCollections').Chat); }
  catch (e) { return false; }
}
"""

STORE_CHATS_JS = """
({ maxChats, includeGroups }) => {
  const { Chat } = window.require('WAWebCollections');
  const ser = (id) => (id && (id._serialized || (id.toString && id.toString()))) || null;
  return Chat.getModelsArray()
    .filter((c) => c && c.id && !c.archive)
    .filter((c) => includeGroups || !(c.isGroup || ser(c.id).endsWith('@g.us')))
    .filter((c) => !ser(c.id).endsWith('@broadcast') && !ser(c.id).endsWith('@newsletter'))
    .sort((a, b) => (b.t || 0) - (a.t || 0))
    .slice(0, maxChats)
    .map((c) => {
      const ct = c.contact || {};
      const pn = ct.phoneNumber ? ser(ct.phoneNumber) : null;
      return {
        id: ser(c.id),
        name: c.formattedTitle || c.name || ct.name || ct.verifiedName || null,
        pushname: ct.pushname || null,
        phone: pn ? pn.split('@')[0] : null,
        isGroup: !!c.isGroup,
        unreadCount: c.unreadCount || 0,
        t: c.t || null,
      };
    });
}
"""

# DOM fallback: visible rows of the chat list only.
DOM_CHATS_JS = """
({ maxChats }) => {
  const pane = document.querySelector('#pane-side') || document.querySelector("[aria-label='Chat list']");
  if (!pane) return [];
  const rows = pane.querySelectorAll("[role='listitem'], [role='row']");
  const out = [];
  for (const row of rows) {
    const title = row.querySelector('span[title]');
    if (!title) continue;
    const unread = row.querySelector("[aria-label*='unread']");
    out.push({ name: title.getAttribute('title'), unreadCount: unread ? parseInt(unread.textContent, 10) || 0 : 0 });
    if (out.length >= maxChats) break;
  }
  return out;
}
"""


class ContactExtractor:
    async def store_available(self, page: Page) -> bool:
        return bool(await page.evaluate(STORE_AVAILABLE_JS))

    async def extract_from_store(self, page: Page, max_chats: int, include_groups: bool) -> list[dict[str, Any]]:
        return await page.evaluate(STORE_CHATS_JS, {"maxChats": max_chats, "includeGroups": include_groups})

    async def extract_from_dom(self, page: Page, max_chats: int) -> list[dict[str, Any]]:
        return await page.evaluate(DOM_CHATS_JS, {"maxChats": max_chats})
