import os
import subprocess

def main():
    print("Converting to HTML using marked...")
    subprocess.run("npx --yes marked LawGraphRAG_Technical_Document.md -o LawGraphRAG_Technical_Document_raw.html", shell=True, check=True)
    
    print("Styling HTML...")
    with open('LawGraphRAG_Technical_Document_raw.html', 'r', encoding='utf-8') as f:
        raw_html = f.read()
        
    styled_html = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 1000px;
                margin: 0 auto;
                padding: 20px;
            }}
            h1, h2, h3, h4, h5 {{
                color: #24292e;
                margin-top: 24px;
                margin-bottom: 16px;
                font-weight: 600;
                line-height: 1.25;
            }}
            h1 {{ font-size: 2em; border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
            h2 {{ font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: .3em; }}
            h3 {{ font-size: 1.25em; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
            th, td {{ border: 1px solid #dfe2e5; padding: 6px 13px; }}
            th {{ background-color: #f6f8fa; font-weight: 600; }}
            tr:nth-child(2n) {{ background-color: #f6f8fa; }}
            code {{ background-color: rgba(27,31,35,.05); border-radius: 3px; font-family: monospace; font-size: 85%; padding: .2em .4em; }}
            pre {{ background-color: #f6f8fa; padding: 16px; border-radius: 3px; overflow: auto; }}
        </style>
    </head>
    <body>
        {raw_html}
    </body>
    </html>
    """
    
    with open('LawGraphRAG_Technical_Document.html', 'w', encoding='utf-8') as f:
        f.write(styled_html)
        
    print("HTML created. Converting to PDF...")
    try:
        subprocess.run("npx --yes md-to-pdf LawGraphRAG_Technical_Document.md", shell=True, check=True)
        print("PDF created.")
    except Exception as e:
        print("PDF conversion failed:", e)

if __name__ == '__main__':
    main()
