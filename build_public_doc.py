import re

with open('README.md', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove sensitive sections
# Remove "### 4) 방화벽" up to "### 5) 법령 데이터 수집"
section_to_remove = re.search(r'### 4\) 방화벽.*?### 5\) 법령 데이터 수집', content, re.DOTALL)
if section_to_remove:
    content = content.replace(section_to_remove.group(0), '### 4) 법령 데이터 수집')

# Update title
content = content.replace('# Law Owly (법 부엉이)', '# Law Owly (법 부엉이) - 기술 문서\n')

with open('LawGraphRAG_Technical_Document.md', 'w', encoding='utf-8') as f:
    f.write(content)

print("Sanitized document created.")
