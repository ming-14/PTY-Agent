/**
 * 终端基础设施：滚动辅助函数
 */

export function scrollTermToTop(term) {
  try {
    if (term.scrollToTop) term.scrollToTop();
    else term.scrollLines(-term.buffer.active.length);
  } catch (e) {}
}

export function isTermAtBottom(term) {
  try {
    const buf = term.buffer.active;
    return buf.viewportY + term.rows >= buf.length;
  } catch (e) {
    return true;
  }
}

export function scrollTermToBottom(term) {
  try {
    if (term.scrollToBottom) term.scrollToBottom();
    else term.scrollLines(term.buffer.active.length);
  } catch (e) {}
}
