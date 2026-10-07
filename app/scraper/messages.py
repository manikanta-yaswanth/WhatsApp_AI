"""Message extraction: newest N messages per chat."""

from typing import Any

from playwright.async_api import Page

STORE_MESSAGES_JS = """
async ({ chatId, limit }) => {
  const { Chat } = window.require('WAWebCollections');
  const chat = Chat.get(chatId);
  if (!chat) return [];
  const load = () => chat.msgs.getModelsArray().filter((m) => !m.isNotification);
  let msgs = load();
  if (msgs.length < limit) {
    try {
      const loader = window.require('WAWebChatLoadMessages');
      await loader.loadEarlierMsgs(chat);
      msgs = load();
    } catch (e) { /* older messages unavailable; keep what is loaded */ }
  }
  return msgs
    .sort((a, b) => (b.t || 0) - (a.t || 0))
    .slice(0, limit)
    .map((m) => ({
      id: m.id ? (m.id._serialized || m.id.toString()) : null,
      fromMe: !!(m.id && m.id.fromMe),
      type: m.type,
      body: m.type === 'chat' ? m.body : null,
      caption: m.caption || null,
      t: m.t,
      author: m.author ? (m.author._serialized || m.author.toString()) : null,
    }));
}
"""

DOM_OPEN_AND_READ_JS = """
async ({ name, limit }) => {
  const pane = document.querySelector('#pane-side') || document.querySelector("[aria-label='Chat list']");
  const title = pane && [...pane.querySelectorAll('span[title]')].find((s) => s.getAttribute('title') === name);
  if (!title) return [];
  const target = title.closest("[role='listitem'], [role='row']") || title;
  for (const type of ['mousedown', 'mouseup', 'click']) {
    target.dispatchEvent(new MouseEvent(type, { bubbles: true }));
  }
  await new Promise((r) => setTimeout(r, 1500));
  const nodes = [...document.querySelectorAll('#main [data-pre-plain-text]')];
  return nodes.slice(-limit).map((n) => {
    const row = n.closest('[data-id]');
    const textEl = n.querySelector('span.selectable-text, span[dir]');
    return {
      id: row ? row.getAttribute('data-id') : null,
      fromMe: !!n.closest('.message-out'),
      prePlainText: n.getAttribute('data-pre-plain-text'),
      text: textEl ? textEl.innerText : null,
    };
  });
}
"""


class MessageExtractor:
    async def extract_from_store(self, page: Page, chat_id: str, limit: int) -> list[dict[str, Any]]:
        return await page.evaluate(STORE_MESSAGES_JS, {"chatId": chat_id, "limit": limit})

    async def extract_from_dom(self, page: Page, chat_name: str, limit: int) -> list[dict[str, Any]]:
        return await page.evaluate(DOM_OPEN_AND_READ_JS, {"name": chat_name, "limit": limit})
