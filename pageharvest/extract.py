"""HTML and JSON extraction without executing page code."""

from dataclasses import dataclass
import json
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
import soupsieve


@dataclass(frozen=True)
class Field:
    """CSS field extraction; attr=None extracts normalized visible text."""

    selector: str
    attr: str | None = None
    many: bool = False
    required: bool = False

    def __post_init__(self):
        if not isinstance(self.selector, str) or not self.selector.strip():
            raise ValueError("Field selector must be a non-empty CSS selector")
        try:
            soupsieve.compile(self.selector)
        except soupsieve.SelectorSyntaxError as exc:
            raise ValueError(f"Invalid CSS selector: {self.selector}") from exc
        if self.attr is not None and (not isinstance(self.attr, str) or not self.attr.strip()):
            raise ValueError("Field attr must be a non-empty attribute name")
        if not isinstance(self.many, bool) or not isinstance(self.required, bool):
            raise ValueError("Field many and required must be booleans")


def normalize_fields(fields):
    if fields is None:
        return {}
    if not isinstance(fields, dict):
        raise ValueError("fields must map field names to CSS strings or Field objects")
    result = {}
    for name, value in fields.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Field names must be non-empty strings")
        if isinstance(value, str):
            value = Field(value)
        if not isinstance(value, Field):
            raise ValueError(f"Invalid field specification for {name}")
        result[name] = value
    return result


def extract_html(content, url, fields):
    soup = BeautifulSoup(content, "html.parser")
    warnings = []
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    metadata = {}
    for node in soup.select("meta[name], meta[property]"):
        key = node.get("property") or node.get("name")
        if key and node.get("content") is not None:
            metadata[key] = node["content"]
    structured = []
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            value = json.loads(node.string or node.get_text())
            structured.extend(value if isinstance(value, list) else [value])
        except (ValueError, TypeError, RecursionError):
            warnings.append("A JSON-LD block could not be parsed")

    def resolve_link(base_url, href):
        try:
            link = urldefrag(urljoin(base_url, href))[0]
            parsed = urlsplit(link)
            if parsed.scheme in ("http", "https") and parsed.hostname:
                return link
        except (ValueError, TypeError):
            pass
        return None

    base = soup.find("base", href=True)
    base_url = (resolve_link(url, base["href"]) if base else None) or url
    links = []
    seen = set()
    for node in soup.find_all("a", href=True):
        link = resolve_link(base_url, node["href"])
        if link and link not in seen:
            seen.add(link)
            links.append(link)
    next_link = soup.select_one('a[rel~="next"], link[rel~="next"]')
    next_url = resolve_link(base_url, next_link.get("href", "")) if next_link else None
    extracted = {}
    missing = []
    for name, field in fields.items():
        nodes = soup.select(field.selector)
        values = []
        for node in nodes:
            value = node.get(field.attr) if field.attr else node.get_text(" ", strip=True)
            if isinstance(value, list):
                value = " ".join(value)
            if value is not None:
                values.append(value)
        extracted[name] = values if field.many else (values[0] if values else None)
        if field.required and not any(value.strip() for value in values):
            missing.append(name)
    has_scripts = bool(soup.find("script", src=True) or soup.select_one("#root, #app, #__next"))
    for node in soup.select("script, style, noscript, template, nav, footer, header"):
        node.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = root.get_text(" ", strip=True)
    # This is a diagnostic heuristic, not proof that a page needs JavaScript.
    needs_render = has_scripts and len(text) < 120
    if needs_render:
        warnings.append("Little text in initial HTML; this page may require JavaScript rendering")
    if missing:
        warnings.append("Required fields missing: " + ", ".join(missing))
    return dict(title=title, text=text, links=links, metadata=metadata,
                structured_data=structured, fields=extracted, warnings=warnings,
                needs_render=needs_render, missing_fields=missing, next_url=next_url)
