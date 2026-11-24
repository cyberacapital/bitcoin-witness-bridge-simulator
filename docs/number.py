"""Numbers the paragraphs of README.md the way a policy paper does.

Run with:  python docs/number.py           (rewrites README.md; run it after editing the text)
           python docs/number.py --check   (changes nothing; fails if the numbers are out of date)

Paragraphs under "## Chapter 3: ..." become 3.1, 3.2, ... and those under
"## Annex A: ..." become A.1, A.2, ... Everything else is left alone: headings,
lists, tables, code, quoted boxes, pictures, and the captions and source lines
of tables and figures. Old numbers are dropped first, so it is safe to run twice.

The text should point at chapters, tables, figures and boxes, never at a
paragraph number, because those move when a paragraph is added.
"""
import pathlib
import re
import sys

README = pathlib.Path(__file__).resolve().parent.parent / "README.md"
CHAPTER = re.compile(r"## (?:Chapter (\d+)|Annex ([A-Z]))\b")
NUMBER = re.compile(r"\*\*(?:\d+|[A-Z])\.\d+\*\* ")
NOT_A_PARAGRAPH = re.compile(r"#|[-*] |\d+\. |\||<|>|\s|\*\*(?:Table|Figure|Box) ")


def numbered(text: str) -> str:
    out, chapter, count, in_code, after_blank = [], None, 0, False, True
    for line in text.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code:
            line = NUMBER.sub("", line, count=1) if NUMBER.match(line) else line
            if line.startswith("## "):
                found = CHAPTER.match(line)
                chapter, count = (found.group(1) or found.group(2) if found else None), 0
            elif chapter and after_blank and line and not NOT_A_PARAGRAPH.match(line):
                count += 1
                line = f"**{chapter}.{count}** {line}"
        after_blank = not line.strip()
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    old = README.read_text()
    new = numbered(old)
    if "--check" in sys.argv:
        sys.exit("README.md paragraph numbers are out of date; run python docs/number.py" if new != old else 0)
    README.write_text(new)
    print("numbered", len(re.findall(r"(?m)^\*\*(?:\d+|[A-Z])\.\d+\*\* ", new)), "paragraphs")
