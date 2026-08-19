import os
import subprocess
import time

def main():
    print("Converting to HTML using marked...")
    subprocess.run("npx --yes marked README_mermaid.md -o README_raw.html", shell=True, check=True)
    
    print("Styling HTML...")
    with open('README_raw.html', 'r', encoding='utf-8') as f:
        raw_html = f.read()
        
    # Replace relative png links with absolute file:// links for Edge and Word to load them properly
    cwd = os.path.abspath(os.getcwd()).replace(chr(92), "/")
    raw_html = raw_html.replace('src="./README_mermaid', f'src="file:///{cwd}/README_mermaid')
        
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
            table {{
                border-collapse: collapse;
                width: 100%;
                margin-bottom: 16px;
            }}
            th, td {{
                border: 1px solid #dfe2e5;
                padding: 6px 13px;
            }}
            th {{
                background-color: #f6f8fa;
                font-weight: 600;
            }}
            tr:nth-child(2n) {{
                background-color: #f6f8fa;
            }}
            code {{
                background-color: rgba(27,31,35,.05);
                border-radius: 3px;
                font-family: SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace;
                font-size: 85%;
                margin: 0;
                padding: .2em .4em;
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            pre {{
                background-color: #f6f8fa;
                border-radius: 3px;
                font-size: 85%;
                line-height: 1.45;
                padding: 16px;
                white-space: pre-wrap;       /* CSS 3 */
                white-space: -moz-pre-wrap;  /* Mozilla, since 1999 */
                white-space: -pre-wrap;      /* Opera 4-6 */
                white-space: -o-pre-wrap;    /* Opera 7 */
                word-wrap: break-word;       /* Internet Explorer 5.5+ */
            }}
            pre code {{
                background-color: transparent;
                border: 0;
                margin: 0;
                padding: 0;
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
            img {{
                max-width: 100%;
                box-sizing: content-box;
            }}
            blockquote {{
                border-left: .25em solid #dfe2e5;
                color: #6a737d;
                padding: 0 1em;
            }}
            /* Additional print styles for PDF */
            @media print {{
                body {{
                    max-width: none;
                    padding: 0;
                }}
                pre, blockquote, img, table {{
                    page-break-inside: avoid;
                }}
                h1, h2, h3, h4, h5 {{
                    page-break-after: avoid;
                }}
            }}
        </style>
    </head>
    <body>
        {raw_html}
    </body>
    </html>
    """
    
    with open('README_styled.html', 'w', encoding='utf-8') as f:
        f.write(styled_html)
        
    print("Done! README_styled.html is ready.")

if __name__ == '__main__':
    main()
