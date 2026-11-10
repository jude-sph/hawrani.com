"""The site as a Flask app. build.py renders every page to static HTML for
GitHub Pages; running this file directly gives a live local preview."""

from flask import Flask, render_template, send_from_directory, request
import json
import os
from datetime import datetime

import lesswrong

try:
    import plotly.graph_objects as go
    import plotly.express as px
    import plotly.io
except ImportError as e:
    print(f"Warning: Could not import plotly: {e}")
    go = None
    px = None
    plotly = None

app = Flask(__name__)

# Helper function to load JSON data
def load_json(filename):
    filepath = os.path.join('data', filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

# Helper function to generate interactive plots
def generate_plot(plot_data):
    """
    Generate an interactive Plotly plot with consistent browser rendering.
    
    plot_data format:
    {
        "type": "line|scatter|bar",
        "title": "Plot Title",
        "xlabel": "X Axis Label",
        "ylabel": "Y Axis Label",
        "data": [{"x": [values], "y": [values], "label": "Series Name"}, ...],
        "width": 10,
        "height": 6
    }
    """
    if go is None:
        print("Warning: plotly not available")
        return None
    
    try:
        fig = go.Figure()
        default_plot_type = plot_data.get('type', 'line')
        
        # Add each data series
        for series in plot_data.get('data', []):
            # Allow each series to specify its own plot type, fall back to default
            plot_type = series.get('type', default_plot_type)
            
            # Handle x-axis processing for non-histogram types
            x_values = None
            if plot_type != 'hist' and 'x' in series:
                if isinstance(series['x'][0], str):
                    try:
                        x_values = [datetime.fromisoformat(date_str) for date_str in series['x']]
                    except:
                        x_values = series['x']  # Keep as strings if parsing fails
                else:
                    x_values = series['x']
            
            if plot_type == 'scatter':
                fig.add_trace(go.Scatter(x=x_values, y=series['y'], mode='markers', name=series.get('label', ''), marker=dict(size=8)))
            elif plot_type == 'bar':
                fig.add_trace(go.Bar(x=x_values, y=series['y'], name=series.get('label', '')))
            elif plot_type == 'hist':
                # Histogram - uses y values as the data to histogram
                nbins = series.get('nbins', 20)  # Default 20 bins, can be customized
                fig.add_trace(go.Histogram(x=series['y'], nbinsx=nbins, name=series.get('label', '')))
            else:  # default to line
                # Support dash style: 'solid', 'dot', 'dash', 'longdash', 'dashdot', 'longdashdot'
                dash_style = series.get('dash', 'solid')
                fig.add_trace(go.Scatter(x=x_values, y=series['y'], mode='lines+markers', name=series.get('label', ''), marker=dict(size=6), line=dict(dash=dash_style)))
        
        # Update layout with the light, editorial theme (matches style.css).
        fig.update_layout(
            title=dict(text=plot_data.get('title', ''), font=dict(size=13, color='#8A8272', family='IBM Plex Mono, monospace')),
            colorway=['#123D2C', '#B0532B', '#3B4EA0', '#7A8A5C', '#9A6BB0'],
            xaxis=dict(title=plot_data.get('xlabel', ''), title_font=dict(size=12, color='#8A8272'), tickfont=dict(color='#A79F90', family='IBM Plex Mono, monospace', size=10), gridcolor='#EAE4D8', zerolinecolor='#EAE4D8', showgrid=True),
            yaxis=dict(title=plot_data.get('ylabel', ''), title_font=dict(size=12, color='#8A8272'), tickfont=dict(color='#A79F90', family='IBM Plex Mono, monospace', size=10), gridcolor='#EAE4D8', zerolinecolor='#EAE4D8', showgrid=True),
            plot_bgcolor='rgba(0, 0, 0, 0)',
            paper_bgcolor='rgba(0, 0, 0, 0)',
            font=dict(color='#6E675B', family='Public Sans, sans-serif'),
            hovermode='x unified',
            legend=dict(
                bgcolor='rgba(252, 251, 247, 0.95)',
                bordercolor='#E7E1D5',
                borderwidth=1,
                font=dict(size=10, color='#6E675B', family='IBM Plex Mono, monospace'),
                x=0.0,
                y=1.0,
                xanchor='left',
                yanchor='top'
            ),
            margin=dict(l=50, r=50, t=80, b=50),
            autosize=True,
            height=500
        )
        
        # Only set categoryorder for plots that have x-axis data (not histograms or date plots)
        if plot_data['data'] and 'x' in plot_data['data'][0]:
            first_x = plot_data['data'][0]['x']
            # Only use categoryorder if x values are not dates (not strings in ISO format)
            if first_x and not isinstance(first_x[0], str):
                fig.update_xaxes(
                    categoryorder="array",
                    categoryarray=first_x
                )
        
        # Convert to HTML div with inline script
        import json
        import uuid
        
        plot_id = f'plotly-plot-{uuid.uuid4().hex[:8]}'
        config = {
            'responsive': True,
            'displaylogo': False,
            'toImageButtonOptions': {
                'format': 'png',
                'filename': 'plot',
                'height': 500,
                'width': 700,
                'scale': 1
            }
        }
        
        # Serialize figure to dict then to JSON
        fig_dict = fig.to_dict()
        
        # Convert datetime objects to ISO format strings for JSON serialization
        def convert_datetimes(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            elif isinstance(obj, dict):
                return {k: convert_datetimes(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_datetimes(item) for item in obj]
            return obj
        
        fig_dict_converted = convert_datetimes(fig_dict)
        data_json = json.dumps(fig_dict_converted.get('data', []))
        layout_json = json.dumps(fig_dict_converted.get('layout', {}))
        config_json = json.dumps(config)
        
        html = f'''<div id="{plot_id}" style="width:100%;height:500px;"></div>
<script>
    if (window.Plotly) {{
        Plotly.newPlot("{plot_id}", {data_json}, {layout_json}, {config_json});
    }}
</script>'''
        return html
    except Exception as e:
        import traceback
        print(f"Error generating plot: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return None

@app.route('/')
def home():
    """Landing page"""
    try:
        landing_data = load_json('landing.json')
        return render_template('index.html', **landing_data)
    except Exception as e:
        import traceback
        print(f"Error loading landing page: {e}")
        traceback.print_exc()
        return f"Error loading landing page: {str(e)}", 500

@app.route('/projects/')
def projects():
    """Projects page with all project tiles"""
    projects_data = load_json('projects.json')
    # Unpublished projects stay in projects.json but are kept off the live site
    # (published defaults to True when absent).
    visible = [p for p in projects_data['projects'] if p.get('published', True)]

    # A project with no local thumbnail that links to a LessWrong-backed write-up
    # borrows that post's first image as its cover, fetched live (and cached) so
    # it stays in sync if the post changes.
    blog_entries = {e['id']: e for e in load_json('blog.json')['entries']}
    for project in visible:
        if not project.get('thumbnail') and project.get('blog_post_id'):
            entry = blog_entries.get(project['blog_post_id'])
            if entry and entry.get('lesswrong_id'):
                lw = lesswrong.fetch_post(entry['lesswrong_id'])
                if lw and lw.get('image'):
                    project['thumbnail'] = lw['image']

    return render_template('projects.html', projects=visible)

@app.route('/project/<string:project_id>/')
def project_detail(project_id):
    """Individual project detail page"""
    projects_data = load_json('projects.json')
    project = next((p for p in projects_data['projects'] if p['id'] == project_id), None)

    # Missing or unpublished projects are treated as non-existent on the live site.
    if not project or not project.get('published', True):
        return render_template('error.html',
                               error_title='Project not found',
                               error_body="That project doesn't exist — it may have been renamed or removed."), 404

    return render_template('project_detail.html', project=project)

def enrich_lesswrong_entry(entry):
    """For a blog entry backed by LessWrong, fill in title/date/excerpt from the
    live (cached) LessWrong post. Local fields in blog.json take precedence when
    set, so a thumbnail/excerpt can be overridden locally. Never raises: if the
    fetch fails, the entry keeps whatever local values it has."""
    post = lesswrong.fetch_post(entry['lesswrong_id'])
    if post:
        entry.setdefault('title', post['title'])
        entry.setdefault('date', post['date'])
        entry.setdefault('author', post['author'])
    # Sensible fallbacks so the listing never breaks if LessWrong is unreachable
    # and nothing was cached.
    entry.setdefault('title', 'Untitled')
    entry.setdefault('date', '')
    entry.setdefault('excerpt', '')
    return entry


def _sort_date(entry):
    """Parse an entry's date for sorting; undated entries sort last."""
    try:
        return datetime.fromisoformat(entry['date'])
    except (KeyError, ValueError):
        return datetime.min


@app.route('/blog/')
def blog():
    """Blog page listing all blog entries"""
    try:
        blog_data = load_json('blog.json')
        # The writing index shows only posts that are both published and listed.
        # 'listed: false' posts (e.g. project write-ups) stay reachable at their
        # /blog/<id> URL -- linked from the Projects page -- but are kept off
        # this curated list. Both flags default to True when absent.
        visible_entries = [e for e in blog_data['entries']
                           if e.get('published', True) and e.get('listed', True)]
        # Pull live metadata for any LessWrong-backed entries so they display
        # and sort alongside local posts.
        for entry in visible_entries:
            if entry.get('lesswrong_id'):
                enrich_lesswrong_entry(entry)
        # Sort by date, newest first
        entries = sorted(visible_entries, key=_sort_date, reverse=True)
        return render_template('blog.html', entries=entries)
    except Exception as e:
        print(f"Error loading blog: {e}")
        return f"Error loading blog: {str(e)}", 500

@app.route('/blog/<string:blog_id>/')
def blog_post(blog_id):
    """Individual blog post page"""
    blog_data = load_json('blog.json')
    post = next((p for p in blog_data['entries'] if p['id'] == blog_id), None)

    # Hidden (unpublished) posts are treated as non-existent on the live site.
    if not post or not post.get('published', True):
        return render_template('error.html',
                               error_title='Blog post not found',
                               error_body="That post doesn't exist here — it may be unpublished or have moved."), 404

    # A post can be a project's write-up (linked from the Projects page), and can
    # also be listed in Writing (e.g. the J-lens import is in both). Its "back"
    # link should return to wherever the visitor came from. Callers pass ?ref=
    # to say; otherwise we infer: a project write-up that isn't listed in Writing
    # belongs to Projects, anything else defaults to Writing.
    projects_data = load_json('projects.json')
    ref = request.args.get('ref')
    is_project_writeup = any(
        p.get('blog_post_id') == blog_id
        for p in projects_data['projects'] if p.get('published', True)
    )
    if ref == 'projects':
        from_projects = True
    elif ref == 'writing':
        from_projects = False
    else:
        from_projects = is_project_writeup and not post.get('listed', True)

    # LessWrong-backed post: fetch the live (cached) content and render it.
    if post.get('lesswrong_id'):
        lw = lesswrong.fetch_post(post['lesswrong_id'])
        if lw:
            # Live metadata wins for display; local fields (image, tags) are kept.
            post.setdefault('title', lw['title'])
            post.setdefault('date', lw['date'])
            post.setdefault('author', lw['author'])
            post['lesswrong_html'] = lw['html']
            post['lesswrong_url'] = lw['url']
        else:
            # Fetch failed and nothing cached: render a graceful fallback that
            # links out to LessWrong so the post is still reachable.
            post.setdefault('title', 'Post on LessWrong')
            post['lesswrong_url'] = (
                f"https://www.lesswrong.com/posts/{post['lesswrong_id']}/"
            )
            post['lesswrong_error'] = True
        return render_template('blog_post.html', post=post, from_projects=from_projects)

    # Generate plots and build content blocks
    if 'content_blocks' in post:
        processed_blocks = []
        for block in post['content_blocks']:
            try:
                if block['type'] == 'text':
                    processed_blocks.append(block)
                elif block['type'] == 'plot':
                    if 'data' in block:
                        plot_html = generate_plot(block['data'])
                        processed_blocks.append({
                            'type': 'plot',
                            'html': plot_html,
                            'caption': block.get('caption', '')
                        })
                    else:
                        print(f"Warning: Plot block missing 'data' field in post {blog_id}")
                elif block['type'] == 'image':
                    processed_blocks.append({
                        'type': 'image',
                        'src': block.get('src', ''),
                        'caption': block.get('caption', ''),
                        'alt': block.get('alt', 'Image')
                    })
                elif block['type'] == 'video':
                    processed_blocks.append({
                        'type': 'video',
                        'url': block.get('url', ''),
                        'src': block.get('src', ''),
                        'caption': block.get('caption', ''),
                        'title': block.get('title', 'Video')
                    })
                elif block['type'] == 'code':
                    processed_blocks.append({
                        'type': 'code',
                        'code': block.get('code', ''),
                        'language': block.get('language', 'python'),
                        'caption': block.get('caption', '')
                    })
            except Exception as e:
                print(f"Error processing content block in post {blog_id}: {e}")
        post['processed_blocks'] = processed_blocks

    return render_template('blog_post.html', post=post, from_projects=from_projects)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon"""
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.errorhandler(404)
def not_found(e):
    """Styled 404 for any unmatched route."""
    return render_template('error.html',
                           error_title='Page not found',
                           error_body="The page you're after doesn't exist — it may have moved, or never lived here at all."), 404

if __name__ == '__main__':
    app.run(debug=True)
