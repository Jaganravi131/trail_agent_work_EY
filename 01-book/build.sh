#!/usr/bin/env bash
# Build the ebook + paperback source files from the markdown chapters.
# Needs pandoc locally, OR just push — .github/workflows/build-book.yml runs the
# same commands on GitHub's free runners and attaches the artifacts.
set -euo pipefail
cd "$(dirname "$0")"

TITLE="The Night Shift Reset"
OUT="build"
mkdir -p "$OUT"

# 1. Assemble manuscript.md with a pandoc metadata block so the EPUB gets a real title page.
{
  echo "% $TITLE"
  echo "% J. Arek Thorne"
  echo "% epub"
  echo ""
  for f in chapters/*.md; do
    cat "$f"
    printf '\n\\newpage\n\n'
  done
} > manuscript.md

# 2. EPUB for KDP ebook upload
pandoc manuscript.md \
  --from markdown+fancy_lists+east_asian_line_breaks \
  --to epub3 \
  --toc --toc-depth=1 \
  --epub-chapter-level=1 \
  --css=style.css \
  --metadata=lang:en-US \
  --metadata=title:"$TITLE" \
  --metadata=author:"J. Arek Thorne" \
  --metadata=description:"A 28-day eating, caffeine and light-timing system for night-shift workers." \
  --metadata=subject:"shift work; sleep; night shift; nurses; wellness" \
  --metadata=rights:"Copyright 2026 J. Arek Thorne. All rights reserved." \
  --cover-image=../02-kdp/cover-2560x1600.jpg \
  -o "$OUT/night-shift-reset.epub"

# 3. Print PDF for the paperback (6x9, mirrored, 0.5in margins, no bleed)
pandoc manuscript.md \
  --to pdf \
  --pdf-engine=xelatex \
  -V papersize=6in,9in \
  -V geometry:margin=0.75in \
  -V fontsize=11pt \
  -V linestretch=1.3 \
  -V mainfont="Georgia" \
  -V documentclass=book \
  --toc \
  -o "$OUT/night-shift-reset-print.pdf" || {
    echo "xelatex missing — a docx is a usable fallback for KDP paperback:" >&2
    pandoc manuscript.md -o "$OUT/night-shift-reset-print.docx"
  }

# 4. Docx fallback for editing passes (comment in Word/Google Docs free)
pandoc manuscript.md -o "$OUT/night-shift-reset.docx"

echo
echo "Built:"; ls -la "$OUT"
echo
echo "Check the EPUB before upload: open in Calibre's viewer or Thorium (both free),"
echo "then preview on a 400px-wide phone screen — tables and the ASCII boxes in ch. 9"
echo "are where ebook formatting breaks."
