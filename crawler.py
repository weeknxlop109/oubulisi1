"""采集层：仅用标准库 urllib + 正则，带 UA、超时、状态码、编码探测与失败重试。

采集结果统一落到 data/inbox/*.md，后续走与手工投放完全一致的流水线。
"""
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from clean import clean_text
import config
from utils import log_line, now_iso

UA = "MedQA-KG/1.0 (+educational research; contact: local)"
_TAG_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_TEXT_BREAK_RE = re.compile(r"(?i)</(p|div|li|h[1-6]|tr|section|article)>|<br\s*/?>")

MAX_RETRIES = 2
RETRY_DELAY = 1.5


def fetch(url: str, timeout: int = 10, retries: int = MAX_RETRIES) -> str | None:
    """带重试的抓取：网络错误/超时/5xx 重试（间隔递增），4xx 不重试。"""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        reason = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                break
        except urllib.error.HTTPError as exc:
            if exc.code < 500:  # 4xx 属资源问题，重试无意义
                log_line("crawl", f"fetch failed (http {exc.code}, no retry): {url}")
                return None
            reason = f"http {exc.code}"
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            reason = str(exc)[:80]
        if attempt < retries:
            log_line("crawl", f"fetch retry {attempt + 1}/{retries} after: {url} ({reason})")
            time.sleep(RETRY_DELAY * (attempt + 1))
            continue
        log_line("crawl", f"fetch failed after {retries + 1} tries: {url} ({reason})")
        return None
    try:
        return raw.decode(charset, errors="ignore")
    except LookupError:
        return raw.decode("utf-8", errors="ignore")


def html_to_text(markup: str) -> tuple[str, str]:
    title = ""
    m = _TITLE_RE.search(markup or "")
    if m:
        title = clean_text(re.sub(r"<[^>]+>", "", m.group(1)))
    body = _TAG_RE.sub(" ", markup or "")
    body = _TEXT_BREAK_RE.sub("\n", body)
    return title, clean_text(body)


def crawl(url: str, name: str = "", save: bool = True) -> Path | None:
    markup = fetch(url)
    if not markup:
        return None
    title, text = html_to_text(markup)
    if len(text) < 40:
        return None
    doc = (
        f"# {name or title or url}\n"
        f"来源: {url}\n"
        f"url: {url}\n"
        f"采集时间: {now_iso()}\n\n"
        f"{text[:6000]}\n"
    )
    if not save:
        return None
    config.ensure_dirs()
    stem = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", (name or title or "page"))[:40] or "page"
    path = config.INBOX_DIR / f"crawl_{stem}.md"
    path.write_text(doc, encoding="utf-8")
    return path


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        saved = crawl(arg)
        print(("saved: " + str(saved)) if saved else ("failed: " + arg))
