import re
import zlib
import base64
import os
import subprocess
import urllib.request
import urllib.error

def encode_kroki(text):
    compressed = zlib.compress(text.encode('utf-8'), 9)
    return base64.urlsafe_b64encode(compressed).decode('ascii')

def process_markdown():
    with open('README.md', 'r', encoding='utf-8') as f:
        content = f.read()

    # Find all mermaid blocks
    pattern = re.compile(r'```mermaid\n(.*?)\n```', re.DOTALL)
    
    counter = 0
    def replacer(match):
        nonlocal counter
        mermaid_code = match.group(1)
        # Remove mermaid directives as they sometimes confuse kroki's mermaid version
        mermaid_code_clean = re.sub(r'%%\{.*?\}%%', '', mermaid_code, flags=re.DOTALL).strip()
        encoded = encode_kroki(mermaid_code_clean)
        img_url = f"https://kroki.io/mermaid/png/{encoded}"
        
        # Download the image locally
        img_filename = f"kroki_{counter}.png"
        req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req) as response, open(img_filename, 'wb') as out_file:
                out_file.write(response.read())
            
            counter += 1
            abs_path = os.path.abspath(img_filename)
            return f'![Mermaid Diagram](file:///{abs_path.replace(chr(92), "/")})'
        except urllib.error.URLError as e:
            print(f"Failed to fetch kroki for diagram {counter}: {e}")
            return f"```mermaid\n{mermaid_code}\n```"
        
    new_content = pattern.sub(replacer, content)
    
    with open('README_kroki.md', 'w', encoding='utf-8') as f:
        f.write(new_content)

def main():
    print("Preprocessing markdown and downloading images...")
    process_markdown()
    
    print("Converting to HTML using marked...")
    subprocess.run("npx --yes marked README_kroki.md -o README_raw.html", shell=True, check=True)
    
    print("Styling HTML...")
    with open('README_raw.html', 'r', encoding='utf-8') as f:
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
            }}
            pre {{
                background-color: #f6f8fa;
                border-radius: 3px;
                font-size: 85%;
                line-height: 1.45;
                overflow: auto;
                padding: 16px;
            }}
            pre code {{
                background-color: transparent;
                border: 0;
                margin: 0;
                padding: 0;
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
