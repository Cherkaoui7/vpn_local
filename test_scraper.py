from pathlib import Path
import re

html_path = Path(r"C:\Users\USER\.gemini\antigravity-ide\brain\24dfbad6-af3a-4e28-8d1a-3ed3c04ee81f\.system_generated\steps\87\content.md")
content = html_path.read_text(encoding="utf-8")

matches = [m.start() for m in re.finditer("UDP", content)]
print(f"Total occurrences of 'UDP': {len(matches)}")
for idx, pos in enumerate(matches[:15]):
    context = content[max(0, pos-80):min(len(content), pos+80)]
    print(f"Match {idx} at {pos}:\n{context}\n{'-'*60}")
