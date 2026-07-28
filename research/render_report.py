"""
Конвертирует REPORT.md в самодостаточный REPORT.html с встроенным CSS.
"""
from pathlib import Path

import markdown

ROOT = Path(__file__).parent
SRC = ROOT / "REPORT.md"
DST = ROOT / "REPORT.html"

CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #fafafa;
    color: #1f2328;
    line-height: 1.55;
    max-width: 1000px;
    margin: 30px auto;
    padding: 0 28px 80px;
    -webkit-font-smoothing: antialiased;
}
h1, h2, h3 {
    color: #0f172a;
    border-bottom: 1px solid #e3e8ee;
    padding-bottom: 6px;
    margin-top: 1.8em;
}
h1 { font-size: 1.9em; margin-top: 0; }
h2 { font-size: 1.45em; }
h3 { font-size: 1.15em; border-bottom: none; }
code {
    background: #eef2f6;
    color: #c7254e;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.92em;
}
pre {
    background: #0f172a;
    color: #e2e8f0;
    padding: 14px 18px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 0.88em;
}
pre code { background: none; color: inherit; padding: 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 18px 0;
    font-size: 0.93em;
    background: #fff;
    box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
th, td {
    padding: 9px 12px;
    text-align: left;
    border-bottom: 1px solid #e3e8ee;
}
th {
    background: #f1f5f9;
    color: #334155;
    font-weight: 600;
    border-bottom: 2px solid #cbd5e1;
}
tr:hover td { background: #f8fafc; }
td:nth-child(n+4) { text-align: right; font-variant-numeric: tabular-nums; }
ul, ol { margin: 10px 0 14px; padding-left: 24px; }
li { margin: 4px 0; }
blockquote {
    border-left: 4px solid #94a3b8;
    margin: 14px 0;
    padding: 6px 16px;
    background: #f1f5f9;
    color: #475569;
}
strong { color: #0f172a; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
.toc {
    background: #fff;
    border: 1px solid #e3e8ee;
    border-radius: 6px;
    padding: 14px 22px;
    margin: 24px 0;
}
.toc h3 { margin-top: 0; }
@media (prefers-color-scheme: dark) {
    body { background: #0f172a; color: #e2e8f0; }
    h1, h2, h3, strong { color: #f8fafc; }
    h1, h2 { border-bottom-color: #334155; }
    code { background: #1e293b; color: #fbbf24; }
    table { background: #1e293b; box-shadow: none; }
    th { background: #334155; color: #e2e8f0; border-bottom-color: #475569; }
    td { border-bottom-color: #334155; }
    tr:hover td { background: #273449; }
    blockquote { background: #1e293b; border-left-color: #64748b; color: #cbd5e1; }
    .toc { background: #1e293b; border-color: #334155; }
}
"""


def main() -> None:
    md = SRC.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
    )

    title = "Scalp Research Report"
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    DST.write_text(html, encoding="utf-8")
    print(f"✅ Готово: {DST}")
    print(f"   Размер: {DST.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
