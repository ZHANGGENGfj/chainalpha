import hashlib
import re


def normalize_text(text: str) -> str:
    text = text.strip().lower()

    text = re.sub(r"\s+", " ", text)

    return text


def sha256_text(text: str) -> str:
    return hashlib.sha256(
        normalize_text(text).encode("utf-8")
    ).hexdigest()


def build_url_hash(url: str | None) -> str:
    if not url:
        return ""

    return sha256_text(url)


def build_title_hash(title: str) -> str:
    return sha256_text(title)


def build_content_hash(content: str) -> str:
    """
    对正文做内容指纹。
    """

    content = normalize_text(content)

    if len(content) > 5000:
        content = content[:5000]

    return sha256_text(content)