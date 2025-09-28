import re
import html
from html.parser import HTMLParser
from urllib.parse import urlparse

"""RU: Утилиты для безопасной HTML-разметки: whitelist-теги и очистка."""

ALLOWED_TAGS = {
    "a", "b", "strong", "i", "em", "code", "s", "strike", "del", "u", "pre"
}

SAFE_SCHEMES = {"http", "https"}

class WhitelistHTMLSanitizer(HTMLParser):
    """RU: Санитайзер HTML, пропускающий только разрешённые теги Telegram."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            # не пишем тег, но текст внутри будет обработан через handle_data
            self.tag_stack.append(None)
            return

        if tag == "a":
            # пропускаем только безопасный href
            href = None
            for k, v in attrs:
                if k.lower() == "href":
                    v = html.unescape(v or "")
                    if _is_safe_href(v):
                        href = v
            if href:
                self.out.append(f'<a href="{html.escape(href, quote=True)}">')
                self.tag_stack.append("a")
            else:
                # нет безопасного href — не открываем тег, но стэк сохраняем как None
                self.tag_stack.append(None)
            return

        if tag == "pre" or tag == "code":
            # не пропускаем атрибуты (можно расширить при желании)
            self.out.append(f"<{tag}>")
            self.tag_stack.append(tag)
            return

        # остальные разрешённые без атрибутов
        self.out.append(f"<{tag}>")
        self.tag_stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.tag_stack:
            return
        top = self.tag_stack.pop()
        if top == tag:
            self.out.append(f"</{tag}>")
        # если top is None — соответствующий старт-тег был отброшен

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag in ALLOWED_TAGS:
            self.out.append("<br>")

    def handle_data(self, data):
        # внутри <pre>/<code> — ничего не трогаем (markdown снимем позже, с плейсхолдерами)
        self.out.append(data)

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def get_html(self):
        return "".join(self.out)

def _is_safe_href(url: str) -> bool:
    """RU: Проверяет, безопасен ли href (разрешены http/https и относительные ссылки)."""
    try:
        p = urlparse(url)
        # относительные ссылки тоже ок
        return (p.scheme == "" and p.netloc == "" and url.startswith(("/", "#"))) or (p.scheme in SAFE_SCHEMES)
    except Exception:
        return False

def remove(text: str) -> str:
    """RU: Удаляет небезопасные теги и нормализует HTML-форматирование."""
    if not text:
        return ""

    # 1) Нормализуем сущности
    text = html.unescape(text)

    # 2) Прогоняем через HTML-саницайзер c whitelist
    parser = WhitelistHTMLSanitizer()
    parser.feed(text)
    sanitized = parser.get_html()

    return sanitized.strip()
