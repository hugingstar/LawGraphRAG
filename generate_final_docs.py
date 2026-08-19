import subprocess
import os

def main():
    # 1. Convert markdown to raw HTML
    print("Converting markdown to raw HTML...")
    subprocess.run("npx --yes marked LawGraphRAG_Technical_Document.md -o LawGraphRAG_Technical_Document_raw.html", shell=True, check=True)
    
    with open('LawGraphRAG_Technical_Document_raw.html', 'r', encoding='utf-8') as f:
        raw_html = f.read()

    # 2. Inject HTML template, Mermaid JS, and styling
    # We will find the second <pre><code class="language-mermaid"> and wrap it in <div class="landscape-diagram">
    
    parts = raw_html.split('<pre><code class="language-mermaid">')
    if len(parts) > 2:
        # parts[0] is before first diagram
        # parts[1] is inside first diagram
        # parts[2] is inside second diagram
        # Replace the 2nd diagram wrapper
        reconstructed = parts[0] + '<div class="mermaid">' + parts[1].replace('</code></pre>', '</div>', 1)
        reconstructed += '<div class="landscape-diagram"><div class="mermaid">' + parts[2].replace('</code></pre>', '</div></div>', 1)
        # Handle the rest
        for i in range(3, len(parts)):
            reconstructed += '<div class="mermaid">' + parts[i].replace('</code></pre>', '</div>', 1)
        raw_html = reconstructed
    else:
        # Fallback if parsing fails
        raw_html = raw_html.replace('<pre><code class="language-mermaid">', '<div class="mermaid">')
        raw_html = raw_html.replace('</code></pre>', '</div>')

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>LawGraphRAG Technical Document</title>
    <script type="module">
      import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
      mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
    </script>
    <style>
        /* General styling */
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1000px;
            margin: 0 auto;
            padding: 20px;
        }}
        h1, h2, h3, h4, h5 {{ color: #24292e; margin-top: 24px; margin-bottom: 16px; font-weight: 600; line-height: 1.25; }}
        h1 {{ font-size: 2em; border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
        h2 {{ font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; page-break-inside: avoid; }}
        th, td {{ border: 1px solid #dfe2e5; padding: 6px 13px; }}
        th {{ background-color: #f6f8fa; font-weight: 600; }}
        tr:nth-child(2n) {{ background-color: #f6f8fa; }}
        code {{ background-color: rgba(27,31,35,.05); border-radius: 3px; font-family: monospace; font-size: 85%; padding: .2em .4em; }}
        pre {{ background-color: #f6f8fa; padding: 16px; border-radius: 3px; overflow: auto; page-break-inside: avoid; }}
        
        .mermaid {{ text-align: center; margin: 20px 0; }}

        /* Print Media Setup */
        @media print {{
            @page {{
                size: A4 portrait;
                margin: 10mm; /* Narrow margin */
            }}
            @page landscape-page {{
                size: A4 landscape;
                margin: 10mm;
            }}
            .landscape-diagram {{
                page: landscape-page;
                width: 100%;
                display: block;
                page-break-before: always;
                page-break-after: always;
            }}
            .landscape-diagram .mermaid svg {{
                max-width: 100%;
                max-height: 100%;
            }}
            body {{ padding: 0; max-width: 100%; }}
        }}
    </style>
</head>
<body>
    {raw_html}
</body>
</html>
"""

    with open('LawGraphRAG_Technical_Document.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("HTML created successfully.")

    # 3. Print to PDF using headless Chrome
    print("Printing to PDF using Chrome...")
    chrome_path = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    html_file = os.path.abspath("LawGraphRAG_Technical_Document.html")
    pdf_file = os.path.abspath("LawGraphRAG_Technical_Document.pdf")
    
    # Wait for mermaid to render? Headless print-to-pdf might print before JS executes.
    # To fix this, Chrome's --virtual-time-budget allows JS to run.
    cmd = [
        chrome_path,
        "--headless",
        "--disable-gpu",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={pdf_file}",
        f"file:///{html_file.replace(chr(92), '/')}"
    ]
    subprocess.run(cmd, check=True)
    print("PDF created successfully.")

if __name__ == '__main__':
    main()
