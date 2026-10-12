"""Shared acceptance checks for shipped brand assets, not production SVG parsing."""
from html.parser import HTMLParser
from html import escape
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


BRAND_FILES = ('vui-icon.svg', 'vui-wordmark-light.svg', 'vui-wordmark-dark.svg')
BRAND_MEMBERS = tuple('web/brand/' + name for name in BRAND_FILES)
BRAND_PREFIX = '/ui/brand/'
FAVICON = BRAND_PREFIX + BRAND_FILES[0]
SVG_NAMESPACE = 'http://www.w3.org/2000/svg'
MAX_SVG_BYTES = 8 * 1024


def brand_member(url):
    """Accept an exact local URL, not a lookalike, URL escape, or path traversal."""
    if url not in {BRAND_PREFIX + name for name in BRAND_FILES}:
        raise ValueError('Unexpected brand asset URL')
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Brand asset must be local')
    return 'web/brand/' + PurePosixPath(parsed.path).name


def parse_svg(data):
    """Keep brand assets small, standalone and inert, with explicit dimensions."""
    if not 0 < len(data) <= MAX_SVG_BYTES:
        raise ValueError('Brand SVG size is outside the bound')
    text = data.decode('utf-8')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?', text, re.I):
        raise ValueError('SVG declarations and processing instructions are forbidden')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('Malformed brand SVG') from exc
    if root.tag != '{' + SVG_NAMESPACE + '}svg':
        raise ValueError('Expected standalone SVG namespace')
    allowed = {
        'svg': {'viewBox', 'width', 'height', 'role', 'aria-labelledby', 'aria-label'},
        'defs': set(),
        'symbol': {'id', 'viewBox'},
        'use': {'href', 'x', 'y', 'width', 'height'},
        'g': {'id', 'fill', 'transform', 'aria-hidden'},
        'path': {'id', 'd', 'fill', 'fill-rule', 'clip-rule', 'transform'},
        'title': {'id'},
        'desc': {'id'},
        'linearGradient': {'id', 'x1', 'y1', 'x2', 'y2', 'gradientUnits', 'gradientTransform'},
        'stop': {'offset', 'stop-color', 'stop-opacity'},
    }
    identifiers = {}
    references = []
    for element in root.iter():
        if not element.tag.startswith('{' + SVG_NAMESPACE + '}'):
            raise ValueError('Foreign SVG namespace')
        tag = element.tag.split('}', 1)[1]
        if tag not in allowed or not set(element.attrib) <= allowed[tag]:
            raise ValueError('Unsafe SVG element or attribute')
        if element.get('id'):
            identifier = element.get('id')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', identifier) or identifier in identifiers:
                raise ValueError('Invalid or duplicate SVG identifier')
            identifiers[identifier] = tag
        for name, value in element.attrib.items():
            if name == 'href':
                if value != '#vui-symbol':
                    raise ValueError('SVG use must reference its local symbol')
                references.append(('vui-symbol', 'symbol'))
            elif name == 'fill' and value.startswith('url('):
                match = re.fullmatch(r'url\(#([A-Za-z][A-Za-z0-9_-]*)\)', value)
                if not match:
                    raise ValueError('SVG paint must use a local fragment')
                references.append((match.group(1), 'linearGradient'))
            elif re.search(r'url\s*\(|(?:https?:|data:|javascript:|//)', value, re.I):
                raise ValueError('SVG must not reference external resources')
            elif name in {'fill', 'stop-color'} and not re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|none', value):
                raise ValueError('SVG color must be a literal color')
        if tag not in {'title', 'desc'} and element.text and element.text.strip():
            raise ValueError('Logo lettering must not depend on text or fonts')
    for identifier, expected_tag in references:
        if identifiers.get(identifier) != expected_tag:
            raise ValueError('SVG local fragment is missing or has the wrong type')
    if not any(expected_tag == 'symbol' for _, expected_tag in references):
        raise ValueError('SVG must render its own V symbol')
    for dimension in ('width', 'height'):
        if not re.fullmatch(r'[1-9][0-9]*', root.get(dimension, '')):
            raise ValueError('SVG needs positive explicit dimensions')
    try:
        x, y, width, height = (float(part) for part in root.get('viewBox', '').split())
    except ValueError as exc:
        raise ValueError('SVG needs a four-number viewBox') from exc
    if x != 0 or y != 0 or not (0 < width <= 1024 and 0 < height <= 1024):
        raise ValueError('SVG viewBox must be finite, bounded and start at zero')
    if (width, height) != (int(root.get('width')), int(root.get('height'))):
        raise ValueError('SVG dimensions must match its viewBox')
    symbols = [node for node in root.iter() if node.get('id') == 'vui-symbol']
    if len(symbols) != 1 or not list(symbols[0]):
        raise ValueError('Exactly one shared V symbol is required')
    return root


def symbol_geometry(root):
    symbol = next(node for node in root.iter() if node.get('id') == 'vui-symbol')
    # Include every symbol attribute/descendant: moving, recoloring or replacing
    # one copy cannot silently pass a d-only comparison.
    def tree(node):
        return node.tag, tuple(sorted(node.attrib.items())), tuple(tree(child) for child in node)
    definitions = [node for node in root if node.tag == '{' + SVG_NAMESPACE + '}defs']
    return tree(symbol), tuple(tree(node) for node in definitions)


class BrandHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.in_head = False
        self.in_picture = False
        self.favicons = []
        self.images = []
        self.picture_sources = []
        self.picture_images = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'head':
            self.in_head = True
        if tag == 'picture':
            self.in_picture = True
        if tag == 'link' and self.in_head and 'icon' in attrs.get('rel', '').split():
            self.favicons.append(attrs)
        if tag == 'img':
            self.images.append(attrs)
            if self.in_picture:
                self.picture_images.append(attrs)
        if tag == 'source' and self.in_picture:
            self.picture_sources.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        if tag == 'picture':
            self.in_picture = False


def observe_image_csp(page):
    # Install before navigation so an early blocked logo cannot go unrecorded.
    page.add_init_script("""(() => {
        window.__vuiBrandImageViolations = [];
        window.__vuiCheckingImageCsp = false;
        document.addEventListener('securitypolicyviolation', event => {
            if (event.effectiveDirective === 'img-src') {
                window.__vuiBrandImageViolations.push({uri: event.blockedURI,
                    synthetic: window.__vuiCheckingImageCsp});
            }
        });
    })();""")


def fetch_brand_asset(page, base, path):
    """Use browser TLS/its existing leaf pin, never a separate API TLS client."""
    brand_member(path)
    assert urlsplit(page.url).scheme + '://' + urlsplit(page.url).netloc == base
    response = page.evaluate("""async url => {
        const response = await fetch(url, {credentials: 'same-origin'});
        return {status: response.status, type: response.headers.get('content-type'),
                body: Array.from(new Uint8Array(await response.arrayBuffer()))};
    }""", base + path)
    assert response['status'] == 200, (path, response['status'])
    assert response['type'].split(';')[0] == 'image/svg+xml', (path, response['type'])
    data = bytes(response['body'])
    parse_svg(data)
    return data


def check_page_brand(page, base, *, mobile=False):
    """A real consumer must decode local SVGs, not merely find strings in HTML."""
    from playwright.sync_api import expect

    logo = page.locator('img.vui-brand-image')
    expect(logo).to_have_count(1)
    expect(logo).to_have_attribute('alt', 'V-UI')
    expect(logo).to_be_visible()
    src = logo.get_attribute('src')
    brand_member(src)
    assert src == FAVICON, 'Existing application brands use the shared icon'
    page.wait_for_function('''() => {
        const image = document.querySelector('img.vui-brand-image');
        return image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0;
    }''')
    violations = page.evaluate('window.__vuiBrandImageViolations')
    assert violations is not None, 'Image CSP observer must precede navigation'
    assert not [item for item in violations if not item['synthetic']], violations
    dimensions = logo.evaluate('''image => {
        const rect = image.getBoundingClientRect();
        return {width: rect.width, height: rect.height, left: rect.left, right: rect.right,
                naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight,
                viewport: innerWidth};
    }''')
    assert dimensions['width'] > 0 and dimensions['height'] > 0, dimensions
    assert abs(dimensions['width'] / dimensions['height'] - dimensions['naturalWidth'] / dimensions['naturalHeight']) < .03, dimensions
    assert dimensions['left'] >= 0 and dimensions['right'] <= dimensions['viewport'], dimensions
    if mobile:
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    favicon = page.locator('head link[rel="icon"]')
    expect(favicon).to_have_count(1)
    expect(favicon).to_have_attribute('href', FAVICON)
    expect(favicon).to_have_attribute('type', 'image/svg+xml')
    # Browser favicon fetching is discretionary; explicitly consume the actual
    # same-origin endpoint and its SVG bytes rather than assuming a request.
    for path in {FAVICON, src}:
        fetch_brand_asset(page, base, path)



def validate_brand_assets(assets):
    if set(assets) != set(BRAND_FILES):
        raise ValueError('All three standalone brand assets are required')
    roots = {name: parse_svg(data) for name, data in assets.items()}
    geometry = symbol_geometry(roots['vui-icon.svg'])
    for name, root in roots.items():
        uses = list(root.iter('{' + SVG_NAMESPACE + '}use'))
        expected_use = {'href': '#vui-symbol', 'x': '0', 'y': '100' if name == 'vui-icon.svg' else '0',
                        'width': '800', 'height': '600'}
        if len(uses) != 1 or uses[0].attrib != expected_use or uses[0] not in list(root):
            raise ValueError('Brand symbol must be rendered once at its agreed placement')
        expected = ('800', '800') if name == 'vui-icon.svg' else ('800', '830')
        if (root.get('width'), root.get('height')) != expected:
            raise ValueError('Brand asset dimensions changed')
        if symbol_geometry(root) != geometry:
            raise ValueError('Brand V symbol or gradient definitions drifted')
        symbol = next(node for node in root.iter() if node.get('id') == 'vui-symbol')
        if symbol.tag != '{' + SVG_NAMESPACE + '}symbol' or symbol.get('viewBox') != '0 0 800 600':
            raise ValueError('Shared V symbol coordinate system changed')
        paths = list(symbol.iter('{' + SVG_NAMESPACE + '}path'))
        if len(paths) != 3:
            raise ValueError('V symbol must retain two ribbons and its white channel')
        if sum(node.get('fill', '').lower() in {'#fff', '#ffffff'} for node in paths) != 1:
            raise ValueError('V channel must remain white')
    lettering = []
    for name in ('vui-wordmark-light.svg', 'vui-wordmark-dark.svg'):
        groups = [node for node in roots[name] if node.tag == '{' + SVG_NAMESPACE + '}g']
        if len(groups) != 1 or not groups[0].get('fill'):
            raise ValueError('Wordmark must contain a separately colored lettering group')
        group = groups[0]
        def tree(node):
            attributes = node.attrib if node is not group else {key: value for key, value in node.attrib.items() if key != 'fill'}
            content = '' if node.tag == '{' + SVG_NAMESPACE + '}desc' else (node.text or '').strip()
            return node.tag, tuple(sorted(attributes.items())), content, tuple(tree(child) for child in node)
        lettering.append(tree(roots[name]))
    if lettering[0] != lettering[1]:
        raise ValueError('Light/dark SVGs may differ only in description and lettering group color')
    return roots


def check_image_csp(page):
    """Prove that the image-only CSP change still blocks data and external URLs."""
    violations = page.evaluate('''() => new Promise((resolve, reject) => {
        const images = [], violations = [];
        window.__vuiCheckingImageCsp = true;
        const timer = setTimeout(() => finish(new Error('Missing enforced image CSP violations')), 3000);
        const finish = error => {
            clearTimeout(timer);
            document.removeEventListener('securitypolicyviolation', listener);
            images.forEach(image => image.remove());
            window.__vuiCheckingImageCsp = false;
            if (error) reject(error); else resolve(violations);
        };
        const listener = event => {
            if (event.effectiveDirective !== 'img-src') return;
            violations.push({uri: event.blockedURI, disposition: event.disposition});
            if (violations.length === 2) finish();
        };
        document.addEventListener('securitypolicyviolation', listener);
        for (const source of ['https://brand-csp.invalid/blocked.svg',
                'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7']) {
            const image = new Image(); images.push(image); image.src = source;
        }
    })''')
    assert len(violations) == 2, violations
    assert all(item['disposition'] == 'enforce' for item in violations), violations
    assert sum(item['uri'] == 'data' for item in violations) == 1, violations
    assert sum(urlsplit(item['uri']).netloc == 'brand-csp.invalid' for item in violations) == 1, violations


def check_readme_picture(context, base, readme):
    """Decode both served wordmarks and exercise real browser picture selection."""
    markup = BrandHTML(readme)
    source = next(item for item in markup.picture_sources
                  if item.get('media') == '(prefers-color-scheme: dark)')
    fallback = next(item for item in markup.picture_images
                    if item.get('src') == 'web/brand/vui-wordmark-light.svg')
    assert source['srcset'] == 'web/brand/vui-wordmark-dark.svg'
    paths = {'light': base + '/ui/' + fallback['src'].removeprefix('web/'),
             'dark': base + '/ui/' + source['srcset'].removeprefix('web/')}
    page = context.new_page()
    observe_image_csp(page)
    external = []
    fixture_url = base + '/__vui_brand_picture_fixture__'
    fixture = ('<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="icon" href="' + escape(paths['light'], quote=True) + '"></head><body><picture>'
        '<source media="' + escape(source['media'], quote=True) +
        '" srcset="' + escape(paths['dark'], quote=True) + '"><img alt="' +
        escape(fallback['alt'], quote=True) + '" src="' +
        escape(paths['light'], quote=True) + '"></picture></body></html>')
    def local_only(route):
        if route.request.url == fixture_url and route.request.resource_type == 'document':
            # Only this transient document is a fixture. Each SVG below still
            # comes from the actual source/installed HTTPS static mount.
            route.fulfill(status=200, content_type='text/html', body=fixture, headers={
                'Content-Security-Policy': "default-src 'none'; img-src 'self'; connect-src 'self'"})
        elif route.request.url in paths.values():
            route.continue_()
        else:
            external.append(route.request.url); route.abort()
    page.route('**/*', local_only)
    try:
        page.emulate_media(color_scheme='light')
        page.goto(fixture_url)
        for scheme in ('light', 'dark', 'light'):
            page.emulate_media(color_scheme=scheme)
            page.wait_for_function("""expected => {
                const image = document.querySelector('picture img');
                return image.currentSrc === expected && image.complete &&
                    image.naturalWidth === 800 && image.naturalHeight === 830;
            }""", arg=paths[scheme])
            assert page.locator('picture img').evaluate('image => new URL(image.currentSrc).origin') == base
            fetch_brand_asset(page, base, urlsplit(paths[scheme]).path)
        assert page.evaluate('window.__vuiBrandImageViolations') == []
        assert external == [], external
    finally:
        page.close()
