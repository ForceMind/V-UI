"""Default, offline contract checks for the exact locally shipped V-UI identity."""
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from brand_helpers import (BRAND_FILES, BRAND_MEMBERS, FAVICON, MAX_SVG_BYTES,
    BrandHTML, brand_member, parse_svg, validate_brand_assets)

ROOT = Path(__file__).resolve().parents[1]


def fixture_svg(*, height=800):
    # Deliberately synthetic paths; never use these fixture shapes as artwork.
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 {height}" width="800" height="{height}">'
        '<defs><linearGradient id="blue"><stop offset="0" stop-color="#123456"/>'
        '<stop offset="1" stop-color="#abcdef"/></linearGradient>'
        '<symbol id="vui-symbol" viewBox="0 0 800 600">'
        '<path d="M0 0 L1 1 Z" fill="url(#blue)"/>'
        '<path d="M1 1 L2 2 Z" fill="#123456"/>'
        '<path d="M2 2 L3 3 Z" fill="#fff"/></symbol></defs>'
        f'<use href="#vui-symbol" x="0" y="{100 if height == 800 else 0}" width="800" height="600"/>'
        + ('<g fill="#123456" transform="translate(0 600)"><path d="M0 0L1 1Z"/></g>' if height == 830 else '')
        + '</svg>').encode()


class BrandAssetTests(unittest.TestCase):
    def test_three_small_standalone_assets_share_exact_v_geometry_and_gradients(self):
        folder = ROOT / 'web/brand'
        self.assertEqual({path.name for path in folder.iterdir()}, set(BRAND_FILES))
        assets = {}
        for name in BRAND_FILES:
            path = folder / name
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
            assets[name] = path.read_bytes()
        roots = validate_brand_assets(assets)
        light = roots['vui-wordmark-light.svg']
        dark = roots['vui-wordmark-dark.svg']
        self.assertNotEqual(assets['vui-wordmark-light.svg'], assets['vui-wordmark-dark.svg'])
        self.assertNotEqual(next(node for node in light if node.tag.endswith('}g')).get('fill'),
                            next(node for node in dark if node.tag.endswith('}g')).get('fill'))
        # Light/dark variants may change lettering color, not its vector geometry.
        def paths(root):
            return [(node.tag, tuple(sorted((key, value) for key, value in node.attrib.items() if key != 'fill')))
                    for node in root.iter() if node.tag.endswith('}path')]
        self.assertEqual(paths(light), paths(dark))
        self.assertGreater(len(paths(light)), 3, 'Wordmark must contain actual outlined lettering')

    def test_negative_xml_safety_rejects_active_foreign_and_external_content(self):
        original = fixture_svg()
        insertions = (
            b'<script>alert(1)</script>', b'<style>@font-face{src:url(font.woff2)}</style>',
            b'<text font-family="Arial">V-UI</text>', b'<foreignObject><div>HTML</div></foreignObject>',
            b'<image href="https://example.test/remote.svg"/>',
            b'<a href="https://example.test/">link</a>', b'<animate attributeName="x"/>',
            b'<path xmlns="https://example.test/foreign" d="M0 0"/>',
            b'<path d="M0 0" onclick="alert(1)"/>', b'<path d="M0 0" style="fill:red"/>',
            b'<path d="M0 0" fill="url(https://example.test/paint.svg#x)"/>',
            b'<path d="M0 0" fill="url(//example.test/paint.svg#x)"/>',
            b'<path d="M0 0" fill="url(data:image/svg+xml,svg)"/>',
            b'<use href="https://example.test/icon.svg#vui-symbol"/>',
            b'<use href="data:image/svg+xml,svg"/>', b'<use href="javascript:alert(1)"/>',
            b'<path d="M0 0" fill="url(#missing)"/>',
            b'<path id="blue" d="M0 0" fill="#fff"/>',
        )
        declarations = (
            b'<!DOCTYPE svg [<!ENTITY attack "unsafe">]>',
            b'<?xml-stylesheet href="https://example.test/style.css"?>',
            b'<!DOCTYPE svg SYSTEM "file:///etc/passwd">',
        )
        for insertion in insertions:
            with self.subTest(insertion=insertion):
                with self.assertRaises(ValueError):
                    parse_svg(original.replace(b'</svg>', insertion + b'</svg>'))
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                with self.assertRaises(ValueError):
                    parse_svg(declaration + original)

    def test_negative_size_dimensions_and_geometry_contracts(self):
        original = fixture_svg()
        malformed = (
            b'', b' ' * (MAX_SVG_BYTES + 1), b'<svg',
            original.replace(b'width="800"', b'width="0"', 1),
            original.replace(b'width="800"', b'width="100%"', 1),
            original.replace(b'viewBox="0 0 800 800"', b'viewBox="0 0 NaN 800"'),
            original.replace(b'viewBox="0 0 800 800"', b'viewBox="0 0 inf 800"'),
            original.replace(b'viewBox="0 0 800 800"', b'viewBox="0 0 801 800"'),
            original.replace(b'viewBox="0 0 800 800"', b'viewBox="-1 0 800 800"'),
            original.replace(b'href="#vui-symbol"', b'href="#missing"'),
            original.replace(b'url(#blue)', b'url(#vui-symbol)'),
        )
        for data in malformed:
            with self.subTest(data=data[:80]):
                with self.assertRaises(ValueError):
                    parse_svg(data)
        assets = {name: fixture_svg(height=800 if name == 'vui-icon.svg' else 830) for name in BRAND_FILES}
        validate_brand_assets(assets)
        for before, after in ((b'M0 0 L1 1 Z', b'M0 0 L1 2 Z'),
                              (b'#123456', b'#123457'),
                              (b'viewBox="0 0 800 600"', b'viewBox="0 0 800 601"'),
                              (b'fill="#fff"', b'fill="#000"'),
                              (b'translate(0 600)', b'translate(0 601)'),
                              (b'x="0"', b'x="1"')):
            changed = dict(assets)
            changed['vui-wordmark-dark.svg'] = changed['vui-wordmark-dark.svg'].replace(before, after)
            with self.subTest(before=before):
                with self.assertRaises(ValueError):
                    validate_brand_assets(changed)
        with self.assertRaises(ValueError):
            validate_brand_assets({name: data for name, data in assets.items() if name != 'vui-icon.svg'})

    def test_brand_paths_reject_remote_traversal_and_url_ambiguities(self):
        for name, member in zip(BRAND_FILES, BRAND_MEMBERS):
            self.assertEqual(brand_member('/ui/brand/' + name), member)
        for url in ('https://example.test' + FAVICON, '//example.test' + FAVICON,
                    FAVICON + '?v=1', FAVICON + '#x', '/ui/brand/../vui-icon.svg',
                    '/ui/brand/%2e%2e/vui-icon.svg', '/ui/brand//vui-icon.svg',
                    'brand/vui-icon.svg', '/ui/brand/vui-icon.svg/',
                    '/ui/brand/vui-icon.svg%00', '/ui/brand\\vui-icon.svg',
                    'data:image/svg+xml,<svg/>', '/ui/brand/missing.svg'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    brand_member(url)

    def test_every_html_uses_local_favicon_and_accessible_actual_brand_image(self):
        pages = sorted((ROOT / 'web').glob('*.html'))
        self.assertEqual({page.name for page in pages}, {'account.html', 'index.html', 'workspace.html', 'certificates.html'})
        for page in pages:
            with self.subTest(page=page.name):
                markup = BrandHTML(page.read_text())
                self.assertEqual(len(markup.favicons), 1)
                self.assertEqual(markup.favicons[0].get('href'), FAVICON)
                self.assertEqual(markup.favicons[0].get('type'), 'image/svg+xml')
                logos = [image for image in markup.images if 'vui-brand-image' in image.get('class', '').split()]
                self.assertEqual(len(logos), 1)
                self.assertEqual(logos[0].get('src'), FAVICON)
                self.assertEqual(logos[0].get('alt'), 'V-UI')
                self.assertEqual(brand_member(logos[0]['src']), 'web/brand/vui-icon.svg')

    def test_readme_picture_selects_standalone_dark_and_light_wordmarks(self):
        markup = BrandHTML((ROOT / 'README.md').read_text())
        dark = [source for source in markup.picture_sources if source.get('media') == '(prefers-color-scheme: dark)']
        self.assertEqual(len(dark), 1)
        self.assertEqual(dark[0].get('srcset'), 'web/brand/vui-wordmark-dark.svg')
        light = [image for image in markup.picture_images if image.get('src') == 'web/brand/vui-wordmark-light.svg']
        self.assertEqual(len(light), 1)
        self.assertTrue(light[0].get('alt', '').startswith('V-UI'))

    def test_actual_fastapi_static_serves_exact_svg_bytes_and_mime(self):
        # No lifespan or API call: only the real public static mount is consumed;
        # this test neither initializes a database nor provisions credentials.
        import main
        client = TestClient(main.app, base_url='http://127.0.0.1', follow_redirects=False)
        try:
            for name in BRAND_FILES:
                with self.subTest(name=name):
                    response = client.get('/ui/brand/' + name)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['content-type'].split(';')[0], 'image/svg+xml')
                    self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                    self.assertEqual(response.content, (ROOT / 'web/brand' / name).read_bytes())
                    parse_svg(response.content)
            self.assertEqual(client.get('/ui/brand/missing.svg').status_code, 404)
            for path in ('/login', '/account', '/workspace', '/certificates'):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    policy = dict((item.strip().split(None, 1) + [''])[:2]
                                  for item in response.headers['content-security-policy'].split(';') if item.strip())
                    self.assertEqual(policy, {
                        'default-src': "'none'", 'script-src': "'self'",
                        'style-src': "'unsafe-inline'" if path in {'/login', '/account'} else "'self'",
                        'img-src': "'self'", 'connect-src': "'self'", 'form-action': "'self'",
                        'frame-ancestors': "'none'", 'base-uri': "'none'",
                    }, 'Only same-origin images may be added to the existing CSP')
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
