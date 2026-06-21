from __future__ import annotations

from html import escape
import re


def render_markdown_html(
    markdown: str,
    *,
    title: str = "ChainAlpha Report",
    brand_name: str | None = None,
    brand_logo: str | None = None,
) -> str:
    body = _markdown_to_html(markdown)
    page_title = escape(title)
    brand_html = _brand_header_html(brand_name=brand_name, brand_logo=brand_logo)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --paper: #ffffff;
      --text: #1f2933;
      --muted: #697586;
      --line: #d9e0e8;
      --accent: #0f766e;
      --accent-soft: #e6f4f1;
      --code: #243b53;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.72;
    }}
    main {{
      width: min(1080px, calc(100% - 32px));
      margin: 32px auto;
      padding: 44px 52px;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
    }}
    .brand-header {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin: -12px 0 28px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }}
    .brand-logo {{
      width: 42px;
      height: 42px;
      object-fit: contain;
      border-radius: 4px;
      background: #fff;
      border: 1px solid var(--line);
      padding: 3px;
    }}
    .brand-name {{
      font-size: 18px;
      font-weight: 700;
      color: #102a43;
      letter-spacing: 0;
    }}
    .brand-subtitle {{
      margin-top: 2px;
      font-size: 12px;
      color: var(--muted);
    }}
    h1, h2, h3 {{
      line-height: 1.3;
      letter-spacing: 0;
      color: #102a43;
    }}
    h1 {{
      margin: 0 0 18px;
      font-size: 30px;
      border-bottom: 2px solid var(--accent);
      padding-bottom: 16px;
    }}
    h2 {{
      margin: 34px 0 14px;
      font-size: 22px;
      padding-left: 10px;
      border-left: 4px solid var(--accent);
    }}
    h3 {{
      margin: 26px 0 10px;
      font-size: 17px;
      color: var(--accent);
    }}
    p {{ margin: 10px 0; }}
    ul {{ margin: 8px 0 18px 1.2em; padding: 0; }}
    li {{ margin: 5px 0; }}
    blockquote {{
      margin: 14px 0 22px;
      padding: 12px 16px;
      color: var(--muted);
      background: var(--accent-soft);
      border-left: 4px solid var(--accent);
      border-radius: 4px;
    }}
    code {{
      color: var(--code);
      background: #eef2f6;
      padding: 2px 5px;
      border-radius: 4px;
      font-family: Consolas, "SFMono-Regular", monospace;
      font-size: 0.92em;
    }}
    pre {{
      overflow-x: auto;
      background: #102a43;
      color: #f8fafc;
      padding: 14px 16px;
      border-radius: 6px;
    }}
    pre code {{ background: transparent; color: inherit; padding: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    figure.report-figure {{
      margin: 18px 0 28px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    figure.report-figure img {{
      display: block;
      width: 100%;
      height: auto;
      border-radius: 4px;
    }}
    figure.report-figure figcaption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
    }}
    hr {{ border: 0; border-top: 1px solid var(--line); margin: 28px 0; }}
    @media (max-width: 720px) {{
      main {{
        width: 100%;
        margin: 0;
        padding: 24px 18px;
        border: 0;
        border-radius: 0;
      }}
      h1 {{ font-size: 24px; }}
      h2 {{ font-size: 19px; }}
    }}
  </style>
</head>
<body>
  <main>
{brand_html}
{body}
  </main>
</body>
</html>
"""


def _brand_header_html(*, brand_name: str | None, brand_logo: str | None) -> str:
    name = (brand_name or "").strip()
    logo = (brand_logo or "").strip()
    if not name and not logo:
        return ""

    logo_html = ""
    if logo:
        logo_html = f'    <img class="brand-logo" src="{escape(logo, quote=True)}" alt="{escape(name or "logo", quote=True)}">'
    name_html = escape(name or "Investment Research")
    return "\n".join(
        [
            '    <section class="brand-header">',
            logo_html,
            "      <div>",
            f'        <div class="brand-name">{name_html}</div>',
            '        <div class="brand-subtitle">Investment Research Report</div>',
            "      </div>",
            "    </section>",
        ]
    )


def _markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    html: list[str] = []
    list_stack: list[int] = []
    in_code = False
    code_lines: list[str] = []
    paragraph: list[str] = []
    in_quote = False
    quote_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(part.strip() for part in paragraph if part.strip())
            if text:
                html.append(f"    <p>{_inline_markup(text)}</p>")
            paragraph = []

    def close_lists(target_indent: int = -1) -> None:
        while list_stack and list_stack[-1] > target_indent:
            html.append("    " + "  " * len(list_stack) + "</ul>")
            list_stack.pop()

    def flush_quote() -> None:
        nonlocal in_quote, quote_lines
        if in_quote:
            html.append("    <blockquote>")
            for item in quote_lines:
                html.append(f"      <p>{_inline_markup(item)}</p>")
            html.append("    </blockquote>")
            quote_lines = []
            in_quote = False

    for raw_line in lines:
        line = raw_line.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            flush_quote()
            close_lists()
            if in_code:
                html.append("    <pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            flush_paragraph()
            flush_quote()
            close_lists()
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            flush_paragraph()
            flush_quote()
            close_lists()
            level = len(heading.group(1))
            html.append(f"    <h{level}>{_inline_markup(heading.group(2).strip())}</h{level}>")
            continue

        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line.strip())
        if image:
            flush_paragraph()
            flush_quote()
            close_lists()
            alt = escape(image.group(1).strip() or "report image", quote=True)
            src = escape(image.group(2).strip(), quote=True)
            caption = escape(image.group(1).strip())
            html.append('    <figure class="report-figure">')
            html.append(f'      <img src="{src}" alt="{alt}">')
            if caption:
                html.append(f"      <figcaption>{caption}</figcaption>")
            html.append("    </figure>")
            continue

        if line.startswith(">"):
            flush_paragraph()
            close_lists()
            in_quote = True
            quote_lines.append(line[1:].strip())
            continue

        bullet = re.match(r"^(\s*)-\s+(.*)$", raw_line)
        if bullet:
            flush_paragraph()
            flush_quote()
            indent = len(bullet.group(1)) // 2
            while len(list_stack) <= indent:
                list_stack.append(len(list_stack))
                html.append("    " + "  " * len(list_stack) + "<ul>")
            close_lists(indent)
            html.append("    " + "  " * (len(list_stack) + 1) + f"<li>{_inline_markup(bullet.group(2).strip())}</li>")
            continue

        paragraph.append(line)

    flush_paragraph()
    flush_quote()
    close_lists()
    if in_code:
        html.append("    <pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(html)


def _inline_markup(text: str) -> str:
    safe = escape(text)
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', safe)
    return safe
