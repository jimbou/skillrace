# SkillRACE — project website

The public research page for **SkillRACE: Reasoning-Augmented Concolic Execution
for Coding-Agent Skills**. Its purpose is to promote the paper and make the idea
easy to grasp and cite — not to sell anything.

## Files

- `index.html` — the single-page site (semantic HTML, one inline SVG pipeline figure)
- `styles.css` — self-contained styles; light + dark via `prefers-color-scheme`
- `script.js` — progressive enhancement only (scroll progress, reveal-on-scroll with a
  visibility failsafe, copy-BibTeX). The page is fully readable with JavaScript disabled.
- `skillrace-icon.png`, `logo.png` — brand assets

## Design notes

- **Editorial, minimal, academic.** Newsreader (serif display) + Inter (body) +
  JetBrains Mono, loaded from Google Fonts with system-font fallbacks.
- **The pipeline figure is a hand-authored inline SVG** that mirrors the paper's
  Figure 1, color-coded by cost (expensive agent · cheap model · plain code · artifact).
- **Content is drawn directly from the paper** (`../paper/skillrace.tex`): the abstract,
  the `fix-failing-test` / `collections.py` motivating defect, the properties-as-oracle
  bright line, and the RQ1–RQ3 design. Headline numbers are intentionally omitted until
  the camera-ready rather than shown as placeholders.
- **Theme-aware and responsive.** No horizontal scroll on mobile; the wide SVG figure
  scrolls inside its own container.

## Run locally

```bash
cd /home/jim/skillrace/website
python3 -m http.server 8080
# open http://localhost:8080/
```

## Updating the paper links

`index.html` links to `../paper/skillrace.tex` and `../README.md`. When a public PDF or
anonymized artifact URL exists, swap those `href`s and fill in the BibTeX block near the
bottom of `index.html` (`id="bibtex"`).
