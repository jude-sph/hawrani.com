# hawrani.com

Personal site, built as static HTML and hosted on GitHub Pages.

Content lives in `data/*.json` (landing page, projects, blog posts) and
`templates/`. `build.py` renders every published page through the Flask app in
`app.py` and writes the result to `_site/`.

## Local preview

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python build.py --serve      # static build, preview at http://localhost:8000
python app.py                # or: live Flask server with auto-reload
```

## Publishing

```bash
python build.py --publish
```

This builds the site and force-pushes `_site/` to the `gh-pages` branch, which
GitHub Pages serves directly (Settings → Pages → source: `gh-pages` branch).
No GitHub Actions are involved. Push `main` as usual to keep the source backed
up; that alone doesn't change the live site.

The build stops (and nothing is published) if any page errors or any internal
link points at a page that doesn't exist.

## Notes

- **Holding page:** `HOLDING_PAGE = True` in `build.py` publishes just one
  full-screen photo (`static/images/holding.jpg`) at every URL. Set it to
  `False` and push to bring the full site back.
- This repo is public. Keep drafts out of it: entries with `"published": false`
  aren't built, but their JSON would still be readable here.
- `code_formatter.py` turns code from stdin into a JSON string for a blog
  post's `code` block.
