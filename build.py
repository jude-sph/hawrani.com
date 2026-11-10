"""Render the whole site to static files in _site/ for GitHub Pages.

    python build.py            # build into _site/
    python build.py --serve    # build, then preview at http://localhost:8000
    python build.py --publish  # build, then push _site/ to the gh-pages branch

GitHub Pages serves the gh-pages branch as-is, so publishing needs no GitHub
Actions. Each page is rendered through the Flask app's test client, so the
output is exactly what the Flask server would have sent. LessWrong posts are
fetched at build time (with the mirror / seed-file fallbacks in lesswrong.py),
so publishing again refreshes them.
"""

import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import partial
from urllib.parse import urlsplit

import app as site

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, '_site')
DOMAIN = 'hawrani.com'

# While True, the published site is a single holding page showing one photo:
# every URL (the home page and any other address) shows it and nothing else is built.
# Set to False to publish the full site again.
HOLDING_PAGE = True
HOLDING_IMAGE = '/static/images/holding.jpg'


def page_urls():
    """Every URL the static site should contain."""
    urls = ['/', '/projects/', '/blog/']
    for p in site.load_json('projects.json')['projects']:
        if p.get('published', True):
            urls.append(f"/project/{p['id']}/")
    for e in site.load_json('blog.json')['entries']:
        if e.get('published', True):
            urls.append(f"/blog/{e['id']}/")
    return urls


def write(rel_path, body):
    path = os.path.join(OUT, rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(body)


def check_links():
    """Fail if any page links to a local path that isn't in the build."""
    broken = []
    for dirpath, _, files in os.walk(OUT):
        for name in files:
            if not name.endswith('.html'):
                continue
            path = os.path.join(dirpath, name)
            html = open(path, encoding='utf-8').read()
            for ref in re.findall(r'(?:href|src)="(/[^"]*)"', html):
                target = urlsplit(ref).path
                local = os.path.join(OUT, target.lstrip('/'))
                if target.endswith('/'):
                    local = os.path.join(local, 'index.html')
                if not os.path.exists(local):
                    broken.append(f"{os.path.relpath(path, OUT)} -> {ref}")
    return broken


def build_holding_page():
    """Publish only the holding photo, shown at every URL."""
    shutil.rmtree(OUT, ignore_errors=True)
    img = HOLDING_IMAGE.lstrip('/')
    os.makedirs(os.path.dirname(os.path.join(OUT, img)))
    shutil.copy(os.path.join(ROOT, img), os.path.join(OUT, img))
    shutil.copy(os.path.join(ROOT, 'static', 'favicon.ico'), os.path.join(OUT, 'favicon.ico'))
    with site.app.app_context():
        page = site.render_template('holding.html', image=HOLDING_IMAGE).encode()
    # index.html for the home page; 404.html covers every other address.
    for path in ('index.html', '404.html'):
        write(path, page)
    write('CNAME', (DOMAIN + '\n').encode())
    write('.nojekyll', b'')
    print(f'Done (holding page): {OUT}')


def build():
    if HOLDING_PAGE:
        return build_holding_page()
    if site.go is None:
        sys.exit('plotly is not installed; plots would be missing. pip install -r requirements.txt')

    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, 'static'), os.path.join(OUT, 'static'),
                    ignore=shutil.ignore_patterns('.DS_Store'))
    shutil.copy(os.path.join(ROOT, 'static', 'favicon.ico'), os.path.join(OUT, 'favicon.ico'))

    client = site.app.test_client()
    for url in page_urls():
        resp = client.get(url)
        if resp.status_code != 200:
            sys.exit(f'{url} returned {resp.status_code}; refusing to publish a broken site')
        write(url.lstrip('/') + 'index.html', resp.data)
        print(f'  built {url}')

    resp = client.get('/this-page-does-not-exist/')
    assert resp.status_code == 404
    write('404.html', resp.data)

    write('CNAME', (DOMAIN + '\n').encode())
    write('.nojekyll', b'')  # serve files as-is, skip GitHub's Jekyll step

    broken = check_links()
    if broken:
        sys.exit('Broken internal links:\n  ' + '\n  '.join(broken))
    print(f'Done: {OUT}')


def serve(port=8000):
    """Preview _site/ locally, serving 404.html for missing pages like Pages does."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def send_error(self, code, message=None, explain=None):
            if code == 404:
                body = open(os.path.join(OUT, '404.html'), 'rb').read()
                self.send_response(404)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().send_error(code, message, explain)

    handler = partial(Handler, directory=OUT)
    print(f'Previewing at http://localhost:{port}')
    http.server.ThreadingHTTPServer(('', port), handler).serve_forever()


def publish():
    """Push _site/ to the gh-pages branch as a single fresh commit.

    The branch only ever holds the latest build (force-pushed), so old
    versions of the site don't pile up in its history.
    """
    remote = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(OUT, tmp, dirs_exist_ok=True)
        git = lambda *args: subprocess.run(['git', *args], cwd=tmp, check=True)
        git('init', '-q', '-b', 'gh-pages')
        git('add', '-A')
        git('commit', '-q', '-m', 'Built site')
        git('push', '-q', '--force', remote, 'gh-pages')
    print('Published. GitHub Pages usually updates within a minute.')


if __name__ == '__main__':
    build()
    if '--publish' in sys.argv:
        publish()
    if '--serve' in sys.argv:
        serve()
