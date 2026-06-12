from __future__ import annotations


class VisualMarkdownStreamFilter:
    """Prevent model-written chart HTML links from being rendered as broken images."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += str(text or "")
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []
        text = self._buffer
        while text:
            start = text.find("![")
            if start < 0:
                if final:
                    output.append(text)
                    text = ""
                else:
                    keep = 1 if text.endswith("!") else 0
                    if len(text) > keep:
                        output.append(text[:-keep] if keep else text)
                        text = text[-keep:] if keep else ""
                break
            output.append(text[:start])
            alt_end = text.find("]", start + 2)
            if alt_end < 0:
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            if alt_end + 1 >= len(text):
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            if text[alt_end + 1] != "(":
                output.append(text[start : start + 1])
                text = text[start + 1 :]
                continue
            close = text.find(")", alt_end + 2)
            if close < 0:
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            alt = text[start + 2 : alt_end].strip() or "图表"
            url = text[alt_end + 2 : close].strip()
            if _is_broken_chart_image_url(url):
                output.append(f"[查看图表：{alt}]({url})")
            else:
                output.append(text[start : close + 1])
            text = text[close + 1 :]
        self._buffer = text
        return "".join(output)


def _is_broken_chart_image_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if value.startswith("chart:"):
        return True
    if not value:
        return False
    path = value.split("?", 1)[0].split("#", 1)[0]
    return ("/chart/" in path and path.endswith((".html", ".json"))) or path.endswith(".html")
