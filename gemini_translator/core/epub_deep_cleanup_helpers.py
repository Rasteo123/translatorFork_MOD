# -*- coding: utf-8 -*-

import os
import re
from xml.etree import ElementTree as ET

try:
    from bs4 import BeautifulSoup, Comment
except ImportError:  # pragma: no cover - caller gates deep cleanup on bs4.
    BeautifulSoup = None
    Comment = ()


def get_default_deep_cleanup_tag_rules():
    return {
        'p': ('keep', True),
        'div': ('unwrap', True),
        'span': ('unwrap', True),
        'h1': ('keep', True),
        'h2': ('keep', True),
        'h3': ('keep', True),
        'h4': ('keep', True),
        'h5': ('keep', True),
        'h6': ('keep', True),
        'strong': ('keep', True),
        'b': ('keep', True),
        'em': ('keep', True),
        'i': ('keep', True),
        'u': ('keep', True),
        's': ('keep', True),
        'a': ('unwrap', True),
        'img': ('keep', True),
        'svg': ('remove', False),
        'br': ('keep', True),
        'hr': ('keep', True),
        'ul': ('keep', True),
        'ol': ('keep', True),
        'li': ('keep', True),
        'blockquote': ('keep', True),
        'pre': ('keep', True),
        'code': ('keep', True),
        'table': ('keep', True),
        'tr': ('keep', True),
        'td': ('keep', True),
        'th': ('keep', True),
        'thead': ('keep', True),
        'tbody': ('keep', True),
        'sup': ('keep', True),
        'sub': ('keep', True),
    }


DEEP_CLEANUP_RECOMMENDED_TAG_OVERRIDES = {
    'b': ('keep', True),
    'em': ('keep', True),
    'a': ('unwrap', True),
    'svg': ('remove', False),
}


def normalize_deep_cleanup_tag_rules(raw_rules):
    default_rules = get_default_deep_cleanup_tag_rules()
    if not isinstance(raw_rules, dict):
        return default_rules

    normalized = {}
    for tag_name, raw_value in raw_rules.items():
        if not isinstance(tag_name, str):
            continue

        action = 'keep'
        preserve = True
        if isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
            action = str(raw_value[0]).strip().lower() or 'keep'
            preserve = bool(raw_value[1])
        elif isinstance(raw_value, dict):
            action = str(raw_value.get('action', 'keep')).strip().lower() or 'keep'
            preserve = bool(raw_value.get('preserve', True))
        else:
            continue

        if action not in ('keep', 'remove', 'unwrap'):
            action = 'keep'
        normalized[tag_name.strip().lower()] = (action, preserve)

    return normalized or default_rules


class EpubDeepCssAnalyzer:
    def parse_css_content(self, css_content):
        styles = {}
        cleaned_css = re.sub(r'/\*.*?\*/', '', css_content, flags=re.DOTALL)
        for selector_block, declarations in re.findall(r'([^{}]+)\{([^{}]+)\}', cleaned_css):
            parsed_style = self._parse_declarations(declarations)
            if not parsed_style:
                continue
            selectors = [selector.strip() for selector in selector_block.split(',') if selector.strip()]
            for selector in selectors:
                normalized_key = self._normalize_selector(selector)
                if normalized_key:
                    styles[normalized_key] = parsed_style.copy()
        return styles

    def _normalize_selector(self, selector):
        selector = selector.strip()
        if not selector:
            return None

        if selector.startswith('.'):
            selector_body = selector[1:]
            selector_body = re.split(r'[\s>+~:#\[]', selector_body, maxsplit=1)[0]
            return selector_body or None

        if selector.startswith('#') or any(ch in selector for ch in [' ', '>', '+', '~', ':', '[', ']']):
            return None

        return f"_element_{selector.lower()}"

    def _parse_declarations(self, declarations):
        raw_props = {}
        for raw_chunk in declarations.split(';'):
            if ':' not in raw_chunk:
                continue
            key, value = raw_chunk.split(':', 1)
            key = key.strip().lower()
            value = value.strip().lower()
            if key and value:
                raw_props[key] = value

        parsed = {}
        font_style = raw_props.get('font-style')
        if font_style in ('italic', 'oblique'):
            parsed['font_style'] = 'italic'

        font_weight = raw_props.get('font-weight')
        if font_weight:
            if font_weight in ('bold', 'bolder'):
                parsed['font_weight'] = 'bold'
            elif font_weight.isdigit() and int(font_weight) >= 600:
                parsed['font_weight'] = 'bold'

        text_decoration = raw_props.get('text-decoration', '')
        if 'underline' in text_decoration:
            parsed['text_decoration'] = 'underline'
        elif 'line-through' in text_decoration:
            parsed['text_decoration'] = 'strike'

        text_align = raw_props.get('text-align')
        if text_align:
            parsed['text_align'] = text_align

        return parsed

    def apply_styles_to_element(self, element, styles, soup, applied_tags):
        if not styles:
            return

        if styles.get('font_style') == 'italic' and 'em' not in applied_tags:
            self._wrap_content(element, 'em', soup)
            applied_tags.add('em')

        if styles.get('font_weight') == 'bold' and 'strong' not in applied_tags:
            self._wrap_content(element, 'strong', soup)
            applied_tags.add('strong')

        if styles.get('text_decoration') == 'underline' and 'u' not in applied_tags:
            self._wrap_content(element, 'u', soup)
            applied_tags.add('u')
        elif styles.get('text_decoration') == 'strike' and 's' not in applied_tags:
            self._wrap_content(element, 's', soup)
            applied_tags.add('s')

        if styles.get('text_align') == 'center':
            element['style'] = 'text-align: center;'

    def _wrap_content(self, element, tag_name, soup):
        if element.find(tag_name):
            return

        new_tag = soup.new_tag(tag_name)
        contents = list(element.contents)
        if not contents:
            return

        for content in contents:
            new_tag.append(content.extract())
        element.append(new_tag)


def clean_deep_html_content(
    content,
    options=None,
    tag_rules=None,
    css_styles=None,
    css_analyzer=None,
):
    if BeautifulSoup is None:
        raise RuntimeError("beautifulsoup4 is required for EPUB deep cleanup")

    options = options or {}
    tag_rules = normalize_deep_cleanup_tag_rules(tag_rules or get_default_deep_cleanup_tag_rules())
    css_styles = css_styles or {}
    css_analyzer = css_analyzer or EpubDeepCssAnalyzer()

    had_xml_declaration = content.lstrip().startswith('<?xml')
    soup = BeautifulSoup(content, 'html.parser')

    if options.get('apply_css_styles') and css_styles:
        apply_css_to_html(soup, css_styles, css_analyzer)

    for text_node in soup.find_all(string=True):
        if isinstance(text_node, Comment):
            text_node.extract()

    removable_tags = ['script']
    if options.get('remove_css'):
        removable_tags.extend(['style', 'link'])
    for tag in soup.find_all(removable_tags):
        tag.decompose()

    for tag in soup.find_all('meta'):
        if not (tag.get('http-equiv', '').lower() == 'content-type' or tag.get('charset')):
            tag.decompose()

    process_tags_with_rules(soup, tag_rules)
    clean_attributes(soup, remove_css=options.get('remove_css'))
    remove_duplicate_formatting(soup)

    output = str(soup)
    if had_xml_declaration and not output.lstrip().startswith('<?xml'):
        output = '<?xml version="1.0" encoding="utf-8"?>\n' + output
    return output


def apply_css_to_html(soup, css_styles, css_analyzer=None):
    css_analyzer = css_analyzer or EpubDeepCssAnalyzer()
    for element in soup.find_all(True):
        classes = element.get('class', [])
        if isinstance(classes, str):
            classes = classes.split()

        applied_tags = set()
        for child in element.find_all(['em', 'i', 'strong', 'b', 'u', 's']):
            if child.name in ('em', 'i'):
                applied_tags.add('em')
            elif child.name in ('strong', 'b'):
                applied_tags.add('strong')
            else:
                applied_tags.add(child.name)

        selector_keys = [f"_element_{element.name.lower()}"]
        selector_keys.extend([cls for cls in classes if cls])
        for selector_key in selector_keys:
            if selector_key in css_styles:
                css_analyzer.apply_styles_to_element(
                    element,
                    css_styles[selector_key],
                    soup,
                    applied_tags,
                )


def remove_duplicate_formatting(soup):
    for tag in soup.find_all('i'):
        tag.name = 'em'
    for tag in soup.find_all('b'):
        tag.name = 'strong'

    for tag_name in ['em', 'strong', 'u', 's']:
        for tag in soup.find_all(tag_name):
            inner_tag = tag.find(tag_name)
            if inner_tag:
                inner_tag.unwrap()


def process_tags_with_rules(soup, tag_rules):
    all_tags = list(soup.find_all(True))
    for tag in all_tags:
        if not tag.parent:
            continue

        tag_name = tag.name.lower()
        if tag_name != 'svg' and tag.find_parent('svg'):
            continue

        if tag_name not in tag_rules:
            continue

        action, preserve_content = tag_rules[tag_name]
        if action == 'remove':
            if preserve_content:
                tag.unwrap()
            else:
                tag.decompose()
        elif action == 'unwrap':
            tag.unwrap()


def clean_attributes(soup, remove_css=True):
    for tag in soup.find_all(True):
        if tag.name == 'svg' or tag.find_parent('svg'):
            continue

        attrs_to_keep = {'id', 'name'}
        if not remove_css:
            attrs_to_keep.update({'class', 'style'})
        if tag.name == 'a':
            attrs_to_keep.update({'href', 'title'})
        elif tag.name == 'img':
            attrs_to_keep.update({'src', 'alt', 'width', 'height'})
        elif tag.name in ['html', 'body', 'head']:
            attrs_to_keep.update({'lang', 'xml:lang', 'dir'})
            for attr_name in list(tag.attrs.keys()):
                if attr_name.startswith('xmlns'):
                    attrs_to_keep.add(attr_name)
        elif tag.name == 'meta':
            attrs_to_keep.update({'http-equiv', 'content', 'charset'})

        if 'style' in tag.attrs and 'text-align: center' in tag.get('style', '').lower():
            attrs_to_keep.add('style')

        for attr_name in list(tag.attrs.keys()):
            if attr_name not in attrs_to_keep:
                del tag[attr_name]


def find_opf_file(temp_dir):
    container_path = os.path.join(temp_dir, 'META-INF', 'container.xml')
    if os.path.exists(container_path):
        try:
            tree = ET.parse(container_path)
            root = tree.getroot()
            rootfile = root.find('.//{*}rootfile')
            if rootfile is not None:
                full_path = rootfile.attrib.get('full-path')
                if full_path:
                    resolved = os.path.join(temp_dir, full_path.replace('/', os.sep))
                    if os.path.exists(resolved):
                        return resolved
        except Exception:
            pass

    for root, _, files in os.walk(temp_dir):
        for file_name in files:
            if file_name.lower().endswith('.opf'):
                return os.path.join(root, file_name)
    return None


def clean_opf_file(
    temp_dir,
    opf_path,
    options=None,
    removed_css_files=None,
    removed_nav_files=None,
    removed_font_files=None,
):
    options = options or {}
    removed_css_files = set(removed_css_files or [])
    removed_nav_files = set(removed_nav_files or [])
    removed_font_files = set(removed_font_files or [])

    ET.register_namespace('', 'http://www.idpf.org/2007/opf')
    ET.register_namespace('dc', 'http://purl.org/dc/elements/1.1/')
    ET.register_namespace('epub', 'http://www.idpf.org/2007/ops')

    tree = ET.parse(opf_path)
    root = tree.getroot()
    namespaces = {'opf': 'http://www.idpf.org/2007/opf'}
    manifest = root.find('.//opf:manifest', namespaces)
    removed_ids = set()

    if manifest is not None:
        opf_dir_rel = os.path.relpath(os.path.dirname(opf_path), temp_dir).replace('\\', '/')
        if opf_dir_rel == '.':
            opf_dir_rel = ''

        for item in list(manifest.findall('opf:item', namespaces)):
            href = item.get('href', '')
            normalized_href = resolve_opf_href(opf_dir_rel, href)
            media_type = (item.get('media-type') or '').lower()
            properties = set((item.get('properties') or '').split())
            should_remove = False

            if options.get('remove_css'):
                should_remove = (
                    normalized_href in removed_css_files or
                    href.lower().endswith('.css') or
                    media_type == 'text/css'
                )

            if not should_remove and options.get('remove_nav'):
                should_remove = (
                    normalized_href in removed_nav_files or
                    'nav' in properties
                )

            if not should_remove and options.get('remove_fonts'):
                should_remove = (
                    normalized_href in removed_font_files or
                    href.lower().endswith(('.ttf', '.otf', '.woff', '.woff2')) or
                    media_type.startswith('font/') or
                    media_type in (
                        'application/font-sfnt',
                        'application/vnd.ms-opentype',
                        'application/x-font-ttf',
                        'application/x-font-opentype',
                        'application/x-font-woff',
                    )
                )

            if should_remove:
                item_id = item.get('id')
                if item_id:
                    removed_ids.add(item_id)
                manifest.remove(item)

    spine = root.find('.//opf:spine', namespaces)
    if spine is not None:
        for itemref in list(spine.findall('opf:itemref', namespaces)):
            idref = itemref.get('idref', '')
            if idref in removed_ids:
                spine.remove(itemref)

    tree.write(opf_path, encoding='utf-8', xml_declaration=True)
    return removed_ids


def resolve_opf_href(opf_dir_rel, href):
    href = (href or '').replace('\\', '/')
    if not href:
        return href

    pieces = []
    if opf_dir_rel:
        pieces.append(opf_dir_rel)
    pieces.append(href)
    combined = '/'.join([piece.strip('/') for piece in pieces if piece])
    normalized = []
    for part in combined.split('/'):
        if not part or part == '.':
            continue
        if part == '..':
            if normalized:
                normalized.pop()
        else:
            normalized.append(part)
    return '/'.join(normalized)
