#!/usr/bin/env python3
"""Render compose.template.yaml into docker-compose.yaml (deployed by Coolify) and compose.dev.yaml.

Standard library only. Rules (spec 4.1-4.4):
- every `content: "@@CONTENT@@"` line becomes a YAML literal block holding the bytes of the
  `source:` file of the same volume item, copied byte for byte once strip_comments has run (see budget);
- content-addressed paths: the first 8 hex digits of the file's SHA-256 are inserted in the
  `source:` and the `target:` of the volume (loki.yaml -> loki.<sha8>.yaml), and every reference
  to the target in the same service (command, environment) is rewritten. Coolify keys a file
  storage by its mount path per application and, once it exists, reuses the stored content and
  ignores the compose `content:`: a new name per content is the only way a changed config
  reaches the host, and two services can never share a mount path;
- `@@NAME@@` placeholders come from tools/versions.env, plus two computed values:
  CONFIG_GUARD_EXPECTED ("/guard/<hashed path under config/>=sha256;..." for every content file
  of the other services: config-guard mounts ./config at /guard) and GUARD_SHA256 (hash of
  guard.sh), and GRAFANA_SETUP_FILES ("<file>=sha256;..." for every file setup.py downloads: the
  .py and .json files of config/grafana-setup/ but setup.py, paths relative to that directory);
- budget: inlined YAML and Alloy files lose their full-line comments and blank lines
  (strip_comments); hashes and content-addressed names are computed on the stripped text, which
  is what Coolify writes; config/ keeps the comments;
- compose.dev.yaml mounts config/ directly: it keeps the repository names (no hash);
- the output is deterministic.
"""

import argparse
import base64
import difflib
import hashlib
import re
import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_BYTES = 120 * 1024
HEADER = (
    "# GENERATED — DO NOT EDIT. Source: compose.template.yaml + config/ (python3 scripts/render.py).\n"
    "# GÉNÉRÉ — NE PAS MODIFIER. Relancer `python3 scripts/render.py` après toute modification.\n"
)
DEV_HEADER = (
    "# GENERATED — DO NOT EDIT (python3 scripts/render.py). Local Docker test bench only:\n"
    "# config/ is bind-mounted directly and a test Grafana is added. Never deployed by Coolify.\n"
)
GUARD_SCRIPT = "config/config-guard/guard.sh"
GUARD_PREFIX = "/guard/"
GRAFANA_SETUP_DIR = "config/grafana-setup"
# The only grafana-setup file inlined in the compose; it downloads the others (spec 4.3).
GRAFANA_SETUP_INLINE = "setup.py"
# Full-line comment markers of the inlined files that strip_comments shortens.
COMMENT_PREFIXES = {".alloy": "//", ".yaml": "#", ".yml": "#"}
HASH_LEN = 8
CONTENT_RE = re.compile(r'^(?P<indent> *)content: "@@CONTENT@@"$')
ITEM_KEY_RE = re.compile(r"^(?P<indent> *)(?:- )?(?P<key>source|target): (?P<value>\S+)$")
SERVICE_RE = re.compile(r"^  (?P<name>[a-z0-9][a-z0-9-]*):$")
PLACEHOLDER_RE = re.compile(r"@@([A-Z0-9_]+)@@")
TOP_SERVICES_RE = re.compile(r"^services:\n", re.MULTILINE)
DEV_GRAFANA = """  grafana:
    image: grafana/grafana:@@GRAFANA_VERSION@@
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_AUTH_ANONYMOUS_ENABLED: "false"
    ports:
      - "127.0.0.1:3000:3000"
"""


# One `content: "@@CONTENT@@"` volume of the template; *_index are 0-based line numbers.
ContentVolume = namedtuple("ContentVolume", "service indent source target index source_index target_index")
# The same volume with its file: text, SHA-256 and content-addressed source and target.
ContentItem = namedtuple("ContentItem", ContentVolume._fields + ("text", "sha256", "hashed_source", "hashed_target"))


class RenderError(Exception):
    """Raised when the template or a config file cannot be rendered faithfully."""


def load_versions(path):
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RenderError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_config(root, source):
    """Return the text of a config file referenced by a `source: ./config/...` line."""
    if not source.startswith("./config/"):
        raise RenderError(f"content source must live under ./config/: {source}")
    path = (Path(root) / source).resolve()
    config_dir = (Path(root) / "config").resolve()
    if config_dir not in path.parents:
        raise RenderError(f"content source escapes config/: {source}")
    if not path.is_file():
        raise RenderError(f"content source is not a regular file: {source}")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError(f"{source}: not valid UTF-8 ({exc})") from exc
    return text


def literal_block(text, key_col, name="<content>"):
    """YAML literal block scalar whose parsed value is exactly `text`.

    Uses an explicit indentation indicator (2) and a chomping indicator chosen from the
    trailing newlines, so leading spaces, tabs and blank lines survive byte for byte.
    """
    if text == "":
        raise RenderError(f"{name}: empty file")
    if "\r" in text:
        raise RenderError(f"{name}: carriage return found (YAML would normalise line breaks)")
    if text.startswith("﻿"):
        raise RenderError(f"{name}: byte order mark found")
    for char in text:
        code = ord(char)
        if char not in "\n\t" and (code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F):
            raise RenderError(f"{name}: control character U+{code:04X} cannot be stored in YAML")
    if text.endswith("\n\n"):
        chomp = "+"
    elif text.endswith("\n"):
        chomp = ""
    else:
        chomp = "-"
    body = text[:-1] if text.endswith("\n") else text
    pad = " " * (key_col + 2)
    lines = [(pad + line) if line else "" for line in body.split("\n")]
    return f"|2{chomp}\n" + "\n".join(lines) + "\n"


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_template(template_text):
    """Yield a ContentVolume for every content placeholder of the template."""
    service = None
    item = {}
    for index, line in enumerate(template_text.split("\n")):
        match = SERVICE_RE.match(line)
        if match:
            service = match.group("name")
            item = {}
            continue
        if line.lstrip().startswith("- "):
            item = {}
        match = ITEM_KEY_RE.match(line)
        if match:
            item[match.group("key")] = (len(match.group("indent")), match.group("value"), index)
            continue
        match = CONTENT_RE.match(line)
        if match:
            indent = len(match.group("indent"))
            if "source" not in item or "target" not in item:
                raise RenderError(f"line {index + 1}: content placeholder without source and target")
            for key in ("source", "target"):
                if item[key][0] not in (indent, indent - 2):
                    raise RenderError(f"line {index + 1}: {key} is not part of the same volume item")
            yield ContentVolume(service, indent, item["source"][1], item["target"][1], index, item["source"][2], item["target"][2])
            item = {}


def hashed_path(path, digest):
    """Insert the first HASH_LEN hex digits of `digest` before the extension: loki.yaml -> loki.<h>.yaml."""
    head, slash, name = path.rpartition("/")
    stem, dot, extension = name.rpartition(".")
    short = digest[:HASH_LEN]
    hashed = f"{stem}.{short}.{extension}" if dot and stem else f"{name}.{short}"
    return head + slash + hashed


def strip_comments(text, source):
    """Drop the full-line comments and blank lines of a YAML or Alloy file; other files unchanged.

    Only lines whose first non-blank characters are the comment marker go: end-of-line comments
    and markers inside values (https://, #anchor) stay.
    """
    prefix = COMMENT_PREFIXES.get(Path(source).suffix)
    if prefix is None:
        return text
    lines = [line for line in text.split("\n") if line.strip() and not line.lstrip().startswith(prefix)]
    return "\n".join(lines) + "\n" if lines else ""


def content_items(root, template_text, stripped=True):
    """[ContentItem] of the template, in template order; stripped=False keeps the repository text."""
    items = []
    for volume in scan_template(template_text):
        text = read_config(root, volume.source)
        if stripped:
            text = strip_comments(text, volume.source)
        digest = sha256_text(text)
        items.append(ContentItem(*volume, text, digest, hashed_path(volume.source, digest), hashed_path(volume.target, digest)))
    return items


def rewrite_path(line, old, new):
    """Replace the container path `old` by `new` where it appears as a whole path."""
    return re.sub(r"(?<![\w./-])" + re.escape(old) + r"(?![\w./-])", lambda _m: new, line)


def grafana_setup_files(root):
    """[(path relative to config/grafana-setup/, sha256)] of the files setup.py downloads, sorted."""
    base = Path(root) / GRAFANA_SETUP_DIR
    files = []
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base).as_posix()
        if path.is_file() and path.suffix in (".py", ".json") and relative != GRAFANA_SETUP_INLINE and "__pycache__" not in path.parts:
            files.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return files


def computed_values(root, template_text, hashed=True):
    expected = set()
    # compose.dev.yaml (hashed=False) mounts the repository files, comments included.
    for item in content_items(root, template_text, stripped=hashed):
        if item.service != "config-guard":
            source = item.hashed_source if hashed else item.source
            expected.add(f"{GUARD_PREFIX}{source[len('./config/'):]}={item.sha256}")
    return {
        "CONFIG_GUARD_EXPECTED": ";".join(sorted(expected)),
        "GUARD_SHA256": sha256_text(read_config(root, "./" + GUARD_SCRIPT)),
        "GRAFANA_SETUP_FILES": ";".join(f"{path}={digest}" for path, digest in grafana_setup_files(root)),
    }


def substitute(text, values):
    def replace(match):
        name = match.group(1)
        if name == "CONTENT":
            return match.group(0)
        if name not in values:
            raise RenderError(f"unknown placeholder @@{name}@@")
        return values[name]

    return PLACEHOLDER_RE.sub(replace, text)


def render_text(template_text, root, versions, strip_content=False, hashed=True):
    """Render the template.

    strip_content=True drops the content lines (compose.dev / validation); hashed=False keeps the
    repository file names (compose.dev.yaml mounts config/ directly).
    """
    values = dict(versions)
    values.update(computed_values(root, template_text, hashed))
    lines = template_text.split("\n")
    contents = {}
    sources = {}
    renames = {}
    for item in content_items(root, template_text):
        if strip_content:
            contents[item.index] = None
        else:
            # Drop only the final newline: "\n".join() below adds it back. Blank lines kept by
            # the "+" chomping indicator must stay in the output.
            contents[item.index] = " " * item.indent + "content: " + literal_block(item.text, item.indent, item.source)[:-1]
        if hashed:
            sources[item.source_index] = (item.source, item.hashed_source)
            renames.setdefault(item.service, []).append((item.target, item.hashed_target))
    out = []
    service = None
    for index, line in enumerate(lines):
        match = SERVICE_RE.match(line)
        if match:
            service = match.group("name")
        if index in contents:
            if contents[index] is not None:
                out.append(contents[index])
            continue
        if index in sources:
            line = line.replace(*sources[index], 1)
        for old, new in renames.get(service, []):
            line = rewrite_path(line, old, new)
        out.append(substitute(line, values))
    return "\n".join(out)


def render_dev(template_text, root, versions):
    stub = render_text(template_text, root, versions, strip_content=True, hashed=False)
    grafana = substitute(DEV_GRAFANA, versions)
    rendered, count = TOP_SERVICES_RE.subn(lambda _m: "services:\n" + grafana, stub, count=1)
    if count != 1:
        raise RenderError("template has no top-level services: key")
    return DEV_HEADER + rendered


def base64_size(text):
    return len(base64.b64encode(text.encode("utf-8")))


def outputs(root):
    root = Path(root)
    template_text = (root / "compose.template.yaml").read_text(encoding="utf-8")
    versions = load_versions(root / "tools" / "versions.env")
    return {
        root / "docker-compose.yaml": HEADER + render_text(template_text, root, versions),
        root / "compose.dev.yaml": render_dev(template_text, root, versions),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    parser.add_argument("--check", action="store_true", help="fail if the committed files are stale")
    parser.add_argument("--stub", metavar="PATH", help="write docker-compose.yaml without content: lines")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        rendered = outputs(root)
        if args.stub:
            template_text = (root / "compose.template.yaml").read_text(encoding="utf-8")
            versions = load_versions(root / "tools" / "versions.env")
            Path(args.stub).write_text(HEADER + render_text(template_text, root, versions, strip_content=True), encoding="utf-8")
            return 0
    except RenderError as exc:
        print(f"render: ERROR: {exc}", file=sys.stderr)
        return 1
    stale = []
    for path, text in rendered.items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if args.check:
            if current != text:
                stale.append(path.name)
                diff = difflib.unified_diff(current.splitlines(), text.splitlines(), path.name, "rendered", lineterm="", n=1)
                print("\n".join(list(diff)[:40]), file=sys.stderr)
        elif current != text:
            path.write_text(text, encoding="utf-8")
            print(f"render: wrote {path.name}")
    if stale:
        print(f"render: stale files (run python3 scripts/render.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    size = base64_size(rendered[root / "docker-compose.yaml"])
    print(f"render: docker-compose.yaml base64 size {size} bytes (budget {BUDGET_BYTES})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
