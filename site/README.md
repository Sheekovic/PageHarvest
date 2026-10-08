# Project website

Static GitHub Pages site: plain HTML, CSS, and JavaScript, with no build step.

Preview from the repository root:

```sh
python -m http.server 8080 --bind 127.0.0.1 --directory site
```

Open http://127.0.0.1:8080. The playground uses local sample records; it does not
scrape remote sites. Product, article, table, JSON/CSV, and usage example controls
work entirely in the browser. Clipboard buttons require HTTPS or localhost.

Pushes changing `site/` on `main` deploy through `.github/workflows/pages.yml`.
GitHub Pages must use GitHub Actions as its publishing source. Keep release
labels and installation examples synchronized with the Python package.

Fonts come from Google Fonts with system fallbacks. There are no analytics,
API keys, frontend dependencies, or backend services.
