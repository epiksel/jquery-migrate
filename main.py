#!/usr/bin/env python3
"""
jQuery v4 Migration Script
==========================
Scans project files for jQuery v4 incompatible APIs and fixes them.
Third-party libraries are excluded.

Usage:
  python main.py             # Scan and apply
  python main.py --dry-run   # Report only, do not write to files
  python main.py --scan      # List remaining issues only
"""

import os
import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent

# Paths/files under these entries are skipped (third-party)
# The following THIRD_PARTY and SCAN_DIRS array content is given as an example.
# Edit it according to your needs. Empty the array if it is not needed.
THIRD_PARTY = {os.path.normpath(os.path.join(BASE_DIR, p)) for p in [
    "upload/assets/javascript/bootstrap",
    "upload/assets/javascript/jquery",
    "upload/assets/javascript/jqvmap",
    "upload/assets/javascript/jscolor/jscolor.min.js"
]}

# Directories to scan (relative to BASE_DIR). If empty, the entire BASE_DIR is scanned.
SCAN_DIRS = [
    "upload/api/view",
    "upload/catalog/view"
]

EXTENSIONS = {".twig", ".js", ".html"}

DRY_RUN = "--dry-run" in sys.argv
SCAN_ONLY = "--scan" in sys.argv


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(s):
    """'arg1, arg2, ...' → ['arg1', 'arg2', ...] (splits on top-level commas only)"""
    args, current, depth = [], [], 0
    for ch in s:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current).strip())
    return args


def find_close_paren(content, open_pos):
    """Assumes content[open_pos] == '('. Returns the position after the matching ')'."""
    assert content[open_pos] == '(', f"Expected '(' but found '{content[open_pos]}'"
    depth, i = 1, open_pos + 1
    while i < len(content) and depth > 0:
        if content[i] == '(':
            depth += 1
        elif content[i] == ')':
            depth -= 1
        i += 1
    return i  # position after ')'


def find_close_any(content, open_pos):
    """
    Returns the position of the matching closing character for {, (, or [.
    Correctly handles strings, comments, and Twig template syntax.
    The returned value is the position of the closing character itself (not after it).
    """
    open_c = content[open_pos]
    close_c = {'{': '}', '(': ')', '[': ']'}.get(open_c)
    if not close_c:
        return -1
    depth = 0
    i = open_pos
    n = len(content)
    in_str = None
    escape = False
    in_lc = False
    in_bc = False

    while i < n:
        c = content[i]
        if escape:
            escape = False; i += 1; continue
        if in_lc:
            if c == '\n': in_lc = False
            i += 1; continue
        if in_bc:
            if c == '*' and i+1 < n and content[i+1] == '/':
                in_bc = False; i += 2
            else:
                i += 1
            continue
        if in_str:
            if c == '\\': escape = True
            elif c == in_str: in_str = None
            i += 1; continue
        # Twig
        if c == '{' and i+1 < n:
            if content[i+1] == '%':
                e = content.find('%}', i+2); i = e+2 if e != -1 else n; continue
            if content[i+1] == '{':
                e = content.find('}}', i+2); i = e+2 if e != -1 else n; continue
        if c in ('"', "'", '`'):
            in_str = c
        elif c == '/' and i+1 < n:
            if content[i+1] == '/': in_lc = True; i += 2; continue
            if content[i+1] == '*': in_bc = True; i += 2; continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0: return i
        i += 1
    return -1


def find_prop_at_depth0(content, start, end, prop_name):
    """
    Searches for 'prop_name:' at nesting depth 0 within text[start:end].
    Returns: (prop_start, after_colon) or (-1, -1).
    """
    pat = re.compile(r'(?<![a-zA-Z_$\'"\\])' + re.escape(prop_name) + r'\s*:(?!:)')
    depth = 0
    i = start
    n = len(content)
    in_str = None
    escape = False
    in_lc = False
    in_bc = False

    while i < end:
        c = content[i]
        if escape: escape = False; i += 1; continue
        if in_lc:
            if c == '\n': in_lc = False
            i += 1; continue
        if in_bc:
            if c == '*' and i+1 < n and content[i+1] == '/':
                in_bc = False; i += 2
            else:
                i += 1
            continue
        if in_str:
            if c == '\\': escape = True
            elif c == in_str: in_str = None
            i += 1; continue
        if c == '{' and i+1 < n:
            if content[i+1] == '%':
                e = content.find('%}', i+2); i = e+2 if e != -1 else n; continue
            if content[i+1] == '{':
                e = content.find('}}', i+2); i = e+2 if e != -1 else n; continue
        if c in ('"', "'", '`'): in_str = c; i += 1; continue
        if c == '/' and i+1 < n:
            if content[i+1] == '/': in_lc = True; i += 2; continue
            if content[i+1] == '*': in_bc = True; i += 2; continue
        if c in ('{', '(', '['): depth += 1; i += 1; continue
        if c in ('}', ')', ']'): depth -= 1; i += 1; continue
        if depth == 0:
            m = pat.match(content, i)
            if m:
                return i, m.end()
        i += 1
    return -1, -1


def extract_fn(content, pos):
    """
    Extracts a 'function([args]) { body }' expression starting at pos.
    Returns: (args_str, body_str, end_pos) or None.
    """
    n = len(content)
    i = pos
    if content[i:i+8] != 'function':
        return None
    i += 8
    while i < n and content[i] in ' \t\n\r': i += 1
    # Optional function name
    j = i
    while j < n and (content[j].isalnum() or content[j] in '_$'): j += 1
    i = j
    while i < n and content[i] in ' \t\n\r': i += 1
    if i >= n or content[i] != '(':
        return None
    arg_close = find_close_any(content, i)
    if arg_close == -1:
        return None
    args = content[i:arg_close+1]
    i = arg_close + 1
    while i < n and content[i] in ' \t\n\r': i += 1
    if i >= n or content[i] != '{':
        return None
    body_close = find_close_any(content, i)
    if body_close == -1:
        return None
    body = content[i:body_close+1]
    return args, body, body_close + 1


def is_third_party(path):
    norm = os.path.normpath(path)
    return any(norm == tp or norm.startswith(tp + os.sep) for tp in THIRD_PARTY)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 1: $.inArray(val, arr)  →  arr.indexOf(val)
# ─────────────────────────────────────────────────────────────────────────────

def transform_inarray(content):
    needle = '$.inArray('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1]
        args = parse_args(inner)
        if len(args) == 2:
            result.append(f"{args[1]}.indexOf({args[0]})")
        else:
            result.append(content[pos:end])
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 2: $.isFunction(x)  →  typeof (x) === 'function'
# ─────────────────────────────────────────────────────────────────────────────

def transform_isfunction(content):
    needle = '$.isFunction('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1].strip()
        # No parentheses needed for simple identifiers
        if re.match(r'^[\w$.[\]\'\"]+$', inner):
            result.append(f"typeof {inner} === 'function'")
        else:
            result.append(f"typeof ({inner}) === 'function'")
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 3: $.trim(x)  →  x.trim()
# ─────────────────────────────────────────────────────────────────────────────

def transform_trim(content):
    needle = '$.trim('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1].strip()
        if re.match(r'^[\w$.[\]\'\"]+$', inner):
            result.append(f"{inner}.trim()")
        else:
            result.append(f"({inner}).trim()")
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 4: $.now()  →  Date.now()
# ─────────────────────────────────────────────────────────────────────────────

def transform_now(content):
    return re.sub(r'\$\.now\(\)', 'Date.now()', content)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 5: $.type(x)  →  typeof x
# ─────────────────────────────────────────────────────────────────────────────

def transform_type(content):
    needle = '$.type('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1].strip()
        if re.match(r'^[\w$.[\]\'\"]+$', inner):
            result.append(f"typeof {inner}")
        else:
            result.append(f"typeof ({inner})")
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 6: $.expr[":"] / $.expr[':']  →  $.expr.pseudos
# ─────────────────────────────────────────────────────────────────────────────

def transform_expr_colon(content):
    content = re.sub(r'\$\.expr\s*\[\s*"[:\s]*"\s*\]', '$.expr.pseudos', content)
    content = re.sub(r"\$\.expr\s*\[\s*'[:\s]*'\s*\]", '$.expr.pseudos', content)
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Transform 7: ).size()  →  .length
# ─────────────────────────────────────────────────────────────────────────────

def transform_size(content):
    # Only targets .size() in jQuery chains — always follows a closing parenthesis
    return re.sub(r'\)\.size\(\)', '.length', content)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 8: .bind(e, fn)  →  .on(e, fn)
#              .unbind(e, fn) →  .off(e, fn)
# ─────────────────────────────────────────────────────────────────────────────

def transform_bind(content):
    # Only targets ).bind( / ).unbind( to avoid collisions with Function.prototype.bind().
    content = re.sub(r'(?<=\))\.bind\(', '.on(', content)
    content = re.sub(r'(?<=\))\.unbind\(', '.off(', content)
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Transform 9: .delegate(sel, event, fn)  →  .on(event, sel, fn)
# ─────────────────────────────────────────────────────────────────────────────

def transform_delegate(content):
    needle = '.delegate('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        # Skip if preceded by an identifier character — not a jQuery call
        pre = content[pos - 1] if pos > 0 else ''
        if pre.isalpha() or pre == '_':
            result.append(content[i: pos + 1])
            i = pos + 1
            continue
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1]
        args = parse_args(inner)
        if len(args) >= 3:
            sel, event, *rest = args
            rest_str = (', ' + ', '.join(rest)) if rest else ''
            result.append(f".on({event}, {sel}{rest_str})")
        else:
            result.append(content[pos:end])
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 10: .undelegate(sel, event)  →  .off(event, sel)
# ─────────────────────────────────────────────────────────────────────────────

def transform_undelegate(content):
    needle = '.undelegate('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        pre = content[pos - 1] if pos > 0 else ''
        if pre.isalpha() or pre == '_':
            result.append(content[i: pos + 1])
            i = pos + 1
            continue
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1]
        args = parse_args(inner)
        if len(args) == 2:
            result.append(f".off({args[1]}, {args[0]})")
        elif len(args) == 0:
            result.append('.off()')
        else:
            result.append(content[pos:end])
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 11: $.proxy(fn, ctx)  →  fn.bind(ctx)
# ─────────────────────────────────────────────────────────────────────────────

def transform_proxy(content):
    needle = '$.proxy('
    result, i = [], 0
    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break
        result.append(content[i:pos])
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1]
        args = parse_args(inner)
        if len(args) >= 2:
            fn, ctx = args[0], args[1]
            extra = (', ' + ', '.join(args[2:])) if len(args) > 2 else ''
            result.append(f"{fn}.bind({ctx}{extra})")
        else:
            result.append(content[pos:end])
        i = end
    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 12: e.which / event.which  →  e.key / e.button
# ─────────────────────────────────────────────────────────────────────────────

# Keyboard key code → event.key mapping
KEY_MAP = {
    '8':  "'Backspace'",
    '9':  "'Tab'",
    '13': "'Enter'",
    '27': "'Escape'",
    '32': "' '",
    '37': "'ArrowLeft'",
    '38': "'ArrowUp'",
    '39': "'ArrowRight'",
    '40': "'ArrowDown'",
    '46': "'Delete'",
}
# Mouse button → event.button mapping (jQuery .which: 1=left, 2=middle, 3=right)
BUTTON_MAP = {'1': '0', '2': '1', '3': '2'}


def transform_which(content):
    def replacer(m):
        var = m.group(1)   # e or event
        op  = m.group(2)   # ===, ==, !==, !=
        val = m.group(3)

        if val in KEY_MAP:
            strict = '!==' if op in ('!==', '!=') else '==='
            return f"{var}.key {strict} {KEY_MAP[val]}"

        if val in BUTTON_MAP:
            strict = '!==' if op in ('!==', '!=') else '==='
            return f"{var}.button {strict} {BUTTON_MAP[val]}"

        # Unknown key code — add comment for manual review
        return m.group(0) + ' /* TODO: .which removed */'

    # Comparison usages
    content = re.sub(
        r'\b(e|event)\.which\s*(===|==|!==|!=)\s*(\d+)',
        replacer, content
    )
    # Remaining standalone .which accesses
    content = re.sub(
        r'\b(e|event)\.which\b',
        r'\1.key /* TODO: .which removed */',
        content
    )
    return content


# ─────────────────────────────────────────────────────────────────────────────
# Transform 13: $(sel).load(url)  →  $.get(url).then(fn)
# ─────────────────────────────────────────────────────────────────────────────

def _split_load_url(url_arg):
    """Split a jQuery .load() URL string into (clean_url, fragment_selector).
    jQuery treats 'url #sel' — text after the first space — as a CSS selector.
    Returns (url_arg, None) when no embedded fragment is found or arg is not a string literal.
    """
    s = url_arg.strip()
    if len(s) < 2 or s[0] not in ('"', "'") or s[-1] != s[0]:
        return url_arg, None
    inner = s[1:-1]
    space_pos = inner.find(' ')
    if space_pos == -1:
        return url_arg, None
    frag = inner[space_pos:].strip()
    if not frag:
        return url_arg, None
    return f"{s[0]}{inner[:space_pos]}{s[0]}", frag


def transform_load(content):
    """
    $(sel).load(url)              →  $.get(url).then(function(data) {\n    $(sel).html(data);\n})
    $(sel).load('url #frag')      →  $.get('url').then(function(data) {\n    $(sel).html($(data).find('#frag'));\n})
    $(sel).load(url, callbackRef) →  $.get(url).then(function(data) {\n    $(sel).html(data);\n    callbackRef(data);\n})
    """
    needle = '.load('
    result, i = [], 0

    while i < len(content):
        pos = content.find(needle, i)
        if pos == -1:
            result.append(content[i:])
            break

        # Skip if 'load' appears as a string literal (e.g. as an event name)
        if re.search(r"'load'|\"load\"", content[pos:pos+30]):
            result.append(content[i: pos + 1])
            i = pos + 1
            continue

        # Scan backwards from the ) before .load( to find the matching $(...).
        pre_pos = pos - 1
        while pre_pos >= 0 and content[pre_pos] in ' \t\n\r':
            pre_pos -= 1

        sel_expr = None
        sel_abs_start = None

        if pre_pos >= 0 and content[pre_pos] == ')':
            depth = 1
            j = pre_pos - 1
            while j >= 0 and depth > 0:
                if content[j] == ')':
                    depth += 1
                elif content[j] == '(':
                    depth -= 1
                j -= 1
            open_paren = j + 1  # position of the matching '('
            k = open_paren - 1
            while k >= 0 and content[k] in ' \t':
                k -= 1
            if k >= 0 and content[k] == '$':
                sel_abs_start = k
                sel_expr = content[k: pre_pos + 1]

        # Parse .load( arguments
        end = find_close_paren(content, pos + len(needle) - 1)
        inner = content[pos + len(needle): end - 1]
        url_args = parse_args(inner)

        if sel_expr is not None and url_args:
            url_expr = url_args[0]
            url_clean, frag_sel = _split_load_url(url_expr)

            # Line indentation for multi-line output
            sol = content.rfind('\n', 0, sel_abs_start)
            sol = sol + 1 if sol != -1 else 0
            indent = ''
            k = sol
            while k < sel_abs_start and content[k] in ' \t':
                indent += content[k]
                k += 1
            step = '\t' if (indent and indent[0] == '\t') else '    '
            body_indent = indent + step

            css_sel = frag_sel  # may be None (from embedded URL fragment)
            callback_ref = None
            can_transform = True

            if len(url_args) == 2:
                second = url_args[1].strip()
                if re.match(r'^[\'"]', second):
                    # Quoted string → fragment selector as a separate argument
                    m2 = re.match(r'^([\'"])(.*)\1$', second)
                    css_sel = m2.group(2).strip() if m2 else None
                    if css_sel is None:
                        can_transform = False
                elif re.match(r'^[\w$][\w$.\[\]]*$', second):
                    # Simple identifier or dotted chain → callable reference
                    callback_ref = second
                else:
                    can_transform = False
            elif len(url_args) > 2:
                can_transform = False

            if can_transform:
                html_arg = f"$(data).find('{css_sel}')" if css_sel else 'data'
                result.append(content[i: sel_abs_start])
                lines = [f"$.get({url_clean}).then(function(data) {{"]
                lines.append(f"{body_indent}{sel_expr}.html({html_arg});")
                if callback_ref:
                    lines.append(f"{body_indent}{callback_ref}(data);")
                lines.append(f"{indent}}})")
                result.append('\n'.join(lines))
                i = end
                continue

        # Cannot transform — leave as is, add TODO
        result.append(content[i: pos])
        result.append('.load( /* TODO: jQuery v4 .load() removed */')
        i = pos + len(needle)

    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 14: $.ajax({ success/error/complete }) → .then()/.catch()/.always()
# ─────────────────────────────────────────────────────────────────────────────

_AJAX_PAT = re.compile(r'\$\.ajax\s*\(')
_AJAX_PROPS = ['complete', 'success', 'error']


def transform_ajax_callbacks(content):
    """
    $.ajax({ ..., complete: fn, success: fn, error: fn })
    → $.ajax({ ... }).always(fn).then(fn).catch(fn)
    """
    result = []
    i = 0
    n = len(content)

    while i < n:
        m = _AJAX_PAT.search(content, i)
        if not m:
            result.append(content[i:])
            break

        result.append(content[i:m.start()])

        # '(' position: end of match - 1 (because \( is included in the pattern)
        paren_pos = m.end() - 1
        paren_close = find_close_any(content, paren_pos)
        if paren_close == -1:
            result.append(content[m.start():])
            break

        # Argument must start with {
        obj_start = paren_pos + 1
        while obj_start < paren_close and content[obj_start] in ' \t\n\r':
            obj_start += 1

        if obj_start >= paren_close or content[obj_start] != '{':
            result.append(content[m.start():paren_close+1])
            i = paren_close + 1
            continue

        obj_close = find_close_any(content, obj_start)
        if obj_close == -1 or obj_close > paren_close:
            result.append(content[m.start():paren_close+1])
            i = paren_close + 1
            continue

        # Extract callbacks
        callbacks = {}
        for prop in _AJAX_PROPS:
            ns, ac = find_prop_at_depth0(content, obj_start+1, obj_close, prop)
            if ns == -1:
                continue
            fi = ac
            while fi < obj_close and content[fi] in ' \t\n\r': fi += 1
            fn = extract_fn(content, fi)
            if fn is None:
                continue
            args, body, fn_end = fn
            j = fn_end
            while j < obj_close and content[j] in ' \t\n\r': j += 1
            prop_end = j + 1 if j < obj_close and content[j] == ',' else fn_end
            callbacks[prop] = (args, body, ns, prop_end)

        if not callbacks:
            result.append(content[m.start():paren_close+1])
            i = paren_close + 1
            continue

        # Remove callbacks from options in reverse order to avoid position shifting
        opts = list(content[obj_start:obj_close+1])
        for prop in reversed(_AJAX_PROPS):
            if prop not in callbacks:
                continue
            _, _, rm_s, rm_e = callbacks[prop]
            for k in range(rm_s - obj_start, min(rm_e - obj_start, len(opts))):
                opts[k] = ''
        new_opts = ''.join(opts)
        # Clean up dangling commas and blank lines
        new_opts = re.sub(r',(\s*})', r'\1', new_opts)
        new_opts = re.sub(r',(\s*),', r',\1', new_opts)
        new_opts = re.sub(r'(\n[ \t]*){3,}', '\n\n', new_opts)

        # Indentation for chain alignment
        ls = content.rfind('\n', 0, m.start())
        ls = ls + 1 if ls != -1 else 0
        indent = ''
        k = ls
        while k < m.start() and content[k] in ' \t':
            indent += content[k]; k += 1

        chain = []
        if 'complete' in callbacks:
            _, body, _, _ = callbacks['complete']
            chain.append(f'.always(function() {body})')
        if 'success' in callbacks:
            args, body, _, _ = callbacks['success']
            chain.append(f'.then(function{args} {body})')
        if 'error' in callbacks:
            err_args, body, _, _ = callbacks['error']
            params = [p.strip() for p in err_args.strip('()').split(',') if p.strip()]
            catch_param = params[0] if params else 'xhr'
            # jQuery error callback: (jqXHR, textStatus, errorThrown)
            # .catch() receives only jqXHR; derive the rest from it.
            decls = []
            if len(params) >= 2:
                decls.append(f'var {params[1]} = {catch_param}.statusText;')
            if len(params) >= 3:
                decls.append(f'var {params[2]} = {catch_param}.statusText;')
            if decls:
                body = '{ ' + ' '.join(decls) + ' ' + body[1:]
            chain.append(f'.catch(function({catch_param}) {body})')

        result.append(f'$.ajax({new_opts})\n{indent}' + f'\n{indent}'.join(chain))
        i = paren_close + 1

    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform 15: $.get(url, function(data){}) → $.get(url).then(function(data){})
# ─────────────────────────────────────────────────────────────────────────────

_GET_PAT = re.compile(r'\$\.get\s*\(')


def transform_get_callback(content):
    """$.get(url, callback) → $.get(url).then(callback)"""
    result = []
    i = 0
    n = len(content)

    while i < n:
        m = _GET_PAT.search(content, i)
        if not m:
            result.append(content[i:])
            break

        result.append(content[i:m.start()])
        paren_pos = m.end() - 1
        paren_close = find_close_any(content, paren_pos)
        if paren_close == -1:
            result.append(content[m.start():])
            break

        # Find comma at depth 0
        depth = 0
        comma = -1
        j = paren_pos + 1
        in_str = None
        escape = False
        while j < paren_close:
            c = content[j]
            if escape: escape = False; j += 1; continue
            if in_str:
                if c == '\\': escape = True
                elif c == in_str: in_str = None
                j += 1; continue
            if c in ('"', "'", '`'): in_str = c; j += 1; continue
            if c in ('{', '(', '['): depth += 1
            elif c in ('}', ')', ']'): depth -= 1
            elif c == ',' and depth == 0:
                comma = j; break
            j += 1

        if comma == -1:
            result.append(content[m.start():paren_close+1])
            i = paren_close + 1
            continue

        cb_start = comma + 1
        while cb_start < paren_close and content[cb_start] in ' \t\n\r': cb_start += 1

        if content[cb_start:cb_start+8] != 'function':
            result.append(content[m.start():paren_close+1])
            i = paren_close + 1
            continue

        url_part = content[paren_pos+1:comma]
        cb_text = content[cb_start:paren_close].rstrip()

        # Line indentation for multi-line formatting
        ls = content.rfind('\n', 0, m.start())
        ls = ls + 1 if ls != -1 else 0
        indent = ''
        k = ls
        while k < m.start() and content[k] in ' \t':
            indent += content[k]; k += 1
        step = '\t' if (indent and indent[0] == '\t') else '    '

        # Expand single-line inline function to multi-line
        formatted = None
        cb = cb_text.strip()
        if '\n' not in cb:
            fn_result = extract_fn(cb, 0)
            if fn_result:
                fn_args, fn_body, _ = fn_result
                body_inner = fn_body[1:-1].strip()
                formatted = (
                    f'$.get({url_part}).then(function{fn_args} {{\n'
                    f'{indent}{step}{body_inner}\n'
                    f'{indent}}})'
                )

        result.append(formatted if formatted else f'$.get({url_part}).then({cb_text})')
        i = paren_close + 1

    return ''.join(result)


# ─────────────────────────────────────────────────────────────────────────────
# Transform list (applied in order)
# ─────────────────────────────────────────────────────────────────────────────

TRANSFORMS = [
    ("$.inArray()",       transform_inarray),
    ("$.isFunction()",    transform_isfunction),
    ("$.trim()",          transform_trim),
    ("$.now()",           transform_now),
    ("$.type()",          transform_type),
    ("$.expr[':']",       transform_expr_colon),
    (".size()",           transform_size),
    (".bind()/.unbind()", transform_bind),
    (".delegate()",       transform_delegate),
    (".undelegate()",     transform_undelegate),
    ("$.proxy()",         transform_proxy),
    ("e.which",           transform_which),
    ("$(sel).load()",     transform_load),
    ("ajax callbacks",    transform_ajax_callbacks),
    ("$.get callback",    transform_get_callback),
]

# Detection patterns for quick scanning (--scan mode)
SCAN_PATTERNS = [
    (r'\$\.inArray\(',                              '$.inArray()'),
    (r'\$\.isFunction\(',                           '$.isFunction()'),
    (r'\$\.trim\(',                                 '$.trim()'),
    (r'\$\.now\(\)',                                '$.now()'),
    (r'\$\.type\(',                                 '$.type()'),
    (r'\$\.expr\s*\[',                              '$.expr[":"]'),
    (r'\)\.size\(\)',                               '.size()'),
    (r'(?<=\))\.bind\(',                            '.bind()'),
    (r'(?<=\))\.unbind\(',                          '.unbind()'),
    (r'\.delegate\(',                               '.delegate()'),
    (r'\.undelegate\(',                             '.undelegate()'),
    (r'\$\.proxy\(',                                '$.proxy()'),
    (r'\b(?:e|event)\.which\b',                     'e.which'),
    (r'\.load\(',                                   '$(sel).load()'),
    (r'(?<!\w)(?:success|error|complete)\s*:\s*function', 'ajax callback'),
    (r'\$\.get\s*\([^,)]+,\s*function',             '$.get() callback'),
]


# ─────────────────────────────────────────────────────────────────────────────
# File management
# ─────────────────────────────────────────────────────────────────────────────

def collect_files():
    dirs_to_scan = (
        [BASE_DIR]
        if not SCAN_DIRS
        else [BASE_DIR / d for d in SCAN_DIRS]
    )
    files = []
    for scan_dir in dirs_to_scan:
        abs_dir = os.path.normpath(scan_dir)
        if not os.path.isdir(abs_dir):
            print(f"  WARNING: directory not found, skipping — {abs_dir}")
            continue
        for root, dirs, fnames in os.walk(abs_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in fnames:
                if os.path.splitext(fname)[1].lower() not in EXTENSIONS:
                    continue
                path = os.path.join(root, fname)
                if is_third_party(path):
                    continue
                files.append(path)
    return sorted(files)


def scan_file(path):
    """Detect remaining deprecated patterns in a file."""
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        return []

    hits = []
    for pattern, label in SCAN_PATTERNS:
        for m in re.finditer(pattern, content):
            lineno = content[:m.start()].count('\n') + 1
            line = content.splitlines()[lineno - 1].strip()[:100]
            hits.append((lineno, label, line))
    return hits


def fix_file(path):
    """Apply all transforms to a file and write it if changed."""
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            original = f.read()
    except Exception as e:
        print(f"  ERROR (read): {path} — {e}")
        return False

    content = original
    for label, fn in TRANSFORMS:
        try:
            content = fn(content)
        except Exception as e:
            print(f"  ERROR ({label}): {path} — {e}")

    if content == original:
        return False

    if not DRY_RUN:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            print(f"  ERROR (write): {path} — {e}")
            return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main flow
# ─────────────────────────────────────────────────────────────────────────────

def main():
    files = collect_files()
    print(f"Files to scan: {len(files)}")
    print(f"Mode: {'SCAN ONLY' if SCAN_ONLY else 'DRY RUN' if DRY_RUN else 'APPLY'}\n")
    if not files:
        print("No files found. Check BASE_DIR and SCAN_DIRS in the configuration.")
        return

    if SCAN_ONLY:
        total_hits = 0
        for path in files:
            hits = scan_file(path)
            if hits:
                rel = os.path.relpath(path, BASE_DIR)
                print(f"\n{rel}")
                for lineno, label, line in hits:
                    print(f"  L{lineno:>4}  [{label}]  {line}")
                total_hits += len(hits)
        print(f"\nTotal issues: {total_hits}")
        return

    changed, unchanged = [], []
    for path in files:
        if fix_file(path):
            changed.append(os.path.relpath(path, BASE_DIR))
        else:
            unchanged.append(path)

    print(f"Modified files ({len(changed)}):")
    for f in changed:
        print(f"  OK: {f}")

    if not changed:
        print("  (no changes — already clean)")

    print(f"\nUnchanged: {len(unchanged)} files")
    if DRY_RUN:
        print("(--dry-run: files were not written)")


if __name__ == "__main__":
    main()
