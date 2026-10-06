#!/usr/bin/env python3
"""Static checks of spec 12.1. Standard library only; external tools: docker compose and .bin/.

Usage: python3 scripts/check.py [--only render,size,compose,ports,env,secrets,targets,limits,validators,lint,dashboards,bundle]
"""

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402

BIN = Path(os.environ.get("GC_BIN_DIR", str(ROOT / ".bin")))
# Variables documented in .env.example but read by nobody in the compose (spec 11).
DOC_ONLY_VARS = {"ALLOY_INTERNAL_URL"}
# check.py size warns when the compose gets this close to its base64 budget.
SIZE_WARN_MARGIN = 4096
VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:?+-][^}]*)?\}")
# ${VAR:-default} with a non-empty default: Coolify skips it for a variable emptied in its UI.
FALLBACK_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?-([^}]+)\}")
# ${VAR:?message} / ${VAR?message}: Coolify does not refuse to deploy, it sets VAR=message.
REQUIRED_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")
# `SERVICE_FQDN_ALLOY_12347:` with no value: Coolify magic variable declared in `environment:`.
NULL_ENV_KEY_RE = re.compile(r"^\s+([A-Z][A-Z0-9_]*):\s*$")
# Spec 12.1.6: token|password|secret|salt|key. "key" alone is a common data field name
# ({"key": "service.name"}), so it only counts as a suffix: api_key, FARO_API_KEY, private-key.
SECRET_RE = re.compile(
    r"(?i)(?P<key>[a-z0-9_.-]*(?:token|password|secret|salt|[_.-]key|apikey)[a-z0-9_.-]*)[\"']?(?P<sep>\s*[:=]\s*)(?P<value>\S.*)$"
)
BARE_SECRET_RE = re.compile(r"[A-Za-z0-9+/_=.-]+")
# Bare YAML/dotenv values that are not secrets.
NOT_SECRETS = {"true", "false", "null", "none", "yes", "no", "~"}
TAG_RE = re.compile(r"^grafana-setup-content-v[1-9][0-9]*$")


def env_example_keys(text):
    keys = set()
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def template_variables(template_text):
    """${VAR} references plus null-valued environment keys (Coolify magic variables); comments ignored."""
    names = set()
    for line in template_text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        names.update(VAR_REF_RE.findall(line))
        match = NULL_ENV_KEY_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def fallback_defaults(template_text):
    """{VAR: default} of every `${VAR:-default}` / `${VAR-default}` with a non-empty default."""
    defaults = {}
    for line in template_text.splitlines():
        if not line.lstrip().startswith("#"):
            defaults.update(FALLBACK_RE.findall(line))
    return defaults


def env_var_mismatches(template_text, env_example_text, doc_only=frozenset(DOC_ONLY_VARS)):
    used = template_variables(template_text)
    documented = env_example_keys(env_example_text)
    errors = [f"{name}: used in compose.template.yaml but missing from .env.example" for name in sorted(used - documented)]
    errors += [f"{name}: in .env.example but unused by compose.template.yaml" for name in sorted(documented - used - set(doc_only))]
    errors += [f"{name}: declared documentary but used by the template" for name in sorted(set(doc_only) & used)]
    required = sorted({name for line in template_text.splitlines() if not line.lstrip().startswith("#") for name in REQUIRED_REF_RE.findall(line)})
    errors += [f"{name}: ${{{name}:?...}} becomes the value of {name} under Coolify: use ${{{name}:-}} and check it in guard.sh" for name in required]
    return errors


def is_literal_secret(value, code_assignment=False):
    """True when `value` is written in clear: a quoted string, or a bare YAML/dotenv value.

    A bare word after a spaced `=` (`self.token = token`) is a code reference, not a literal.
    """
    value = value.strip().rstrip(",;")
    if not value:
        return False
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        content = value[1:end] if end > 0 else value[1:]
        return bool(content) and "$" not in content and "{{" not in content
    token = value.split()[0].rstrip(",;")
    if token.startswith("$") or not BARE_SECRET_RE.fullmatch(token) or token.lower() in NOT_SECRETS:
        return False
    if code_assignment and token.replace("_", "").replace(".", "").isalpha():
        return False
    return True


def find_hardcoded_secrets(text, name):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            continue
        match = SECRET_RE.search(line)
        if not match:
            continue
        separator = match.group("sep")
        code_assignment = "=" in separator and separator != "="
        if is_literal_secret(match.group("value"), code_assignment):
            findings.append(f"{name}:{number}: literal value for '{match.group('key')}'")
    return findings


def port_violations(compose_json):
    return [f"{name}: declares ports: {service['ports']}" for name, service in sorted(compose_json["services"].items()) if service.get("ports")]


def limit_violations(compose_json):
    """Every service has mem_limit and cpus (spec 9.4)."""
    errors = []
    for name, service in sorted(compose_json["services"].items()):
        for key in ("mem_limit", "cpus"):
            if not service.get(key):
                errors.append(f"{name}: {key} missing")
    return errors


def target_collisions(mounts):
    """[(service, target)] of the content volumes -> errors for a target used twice.

    Coolify keeps one file storage per container path of an application: two content volumes
    with the same target would overwrite each other (spec 4.2).
    """
    services = {}
    for service, target in mounts:
        services.setdefault(target, []).append(service)
    return [f"{target}: content target of {', '.join(names)}" for target, names in sorted(services.items()) if len(names) > 1]


def bundle_errors(tag, files, git_show, tag_exists):
    """Errors of the grafana-setup content tag (spec 4.3): `tag` must be a grafana-setup-content-v<N>
    tag of this repository holding every file of `files` ([(path under config/grafana-setup/,
    sha256)]) byte for byte.

    git_show(tag, path) -> bytes or None; tag_exists(tag) -> bool.
    """
    if not TAG_RE.match(tag or ""):
        return [f"GRAFANA_SETUP_TAG={tag!r} must look like grafana-setup-content-v<N>"]
    if not tag_exists(tag):
        return [f"tag {tag} absent: create it on the final commit (git tag -a {tag} -m ...) and push it with the branch"]
    errors = []
    for path, digest in files:
        data = git_show(tag, f"{render.GRAFANA_SETUP_DIR}/{path}")
        if data is None:
            errors.append(f"{path}: absent from tag {tag}: move the unpushed tag, or bump the tag suffix")
        elif hashlib.sha256(data).hexdigest() != digest:
            errors.append(f"{path}: differs between tag {tag} and the working tree: move the unpushed tag, or bump the tag suffix")
    return errors


def git_show(tag, path):
    result = subprocess.run(["git", "show", f"refs/tags/{tag}:{path}"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return result.stdout if result.returncode == 0 else None


def git_tag_exists(tag):
    result = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"], cwd=ROOT, capture_output=True, check=False)
    return result.returncode == 0


def git_is_shallow():
    result = subprocess.run(["git", "rev-parse", "--is-shallow-repository"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() == "true"


def tool(name):
    path = BIN / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tools/fetch-binaries.sh")
    return str(path)


def run(cmd, env=None):
    result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    return result.returncode, result.stdout


def load_env(path):
    values = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value
    return values


@functools.cache
def compose_json():
    """`docker compose config` on the deployed compose with its content: lines removed (spec 12.1.3)."""
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    versions = render.load_versions(ROOT / "tools" / "versions.env")
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "docker-compose.stub.yaml"
        stub.write_text(render.HEADER + render.render_text(template, ROOT, versions, strip_content=True), encoding="utf-8")
        env_file = Path(tmp) / "check.env"
        keys = env_example_keys((ROOT / ".env.example").read_text(encoding="utf-8"))
        env_file.write_text("".join(f"{key}=check-value\n" for key in sorted(keys)), encoding="utf-8")
        cmd = ["docker", "compose", "-f", str(stub), "--project-directory", str(ROOT), "--env-file", str(env_file), "config", "--format", "json"]
        code, output = run(cmd)
        if code != 0:
            raise RuntimeError(f"docker compose config failed:\n{output}")
        return json.loads(output)


YAML_BLOCK_SCALAR_RE = re.compile(r":\s*[|>][-+0-9]*\s*$")
SHELL_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?[A-Za-z_]")


def shell_open_quote_lines(text):
    """1-based numbers of the lines of a shell script that start inside a quoted string.

    A small scanner: quotes ' and " (backslash escapes outside single quotes) and # comments
    at the start of a word. Enough for the inlined scripts; a heredoc is reported apart.
    """
    lines = []
    quote = None
    for number, line in enumerate(text.split("\n"), 1):
        if quote:
            lines.append(number)
        index = 0
        while index < len(line):
            char = line[index]
            if quote == "'":
                if char == "'":
                    quote = None
            elif char == "\\":
                index += 1
            elif quote == '"':
                if char == '"':
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1] in " \t;&|()"):
                break
            index += 1
    return lines


def strip_hazards(source, text):
    """Lines of an inlined file where the line-based strip_comments could cut a multi-line value.

    YAML: a block scalar (key: | or key: >); Alloy: a line with an odd number of backticks
    (a raw string spanning several lines); shell: a heredoc, a line starting inside a quoted
    string or a backslash continuation followed by a comment line. Python is stripped with
    tokenize; other files are not stripped.
    """
    suffix = Path(source).suffix
    errors = []
    for number, line in enumerate(text.split("\n"), 1):
        if suffix in (".yaml", ".yml") and YAML_BLOCK_SCALAR_RE.search(line):
            errors.append(f"{source}:{number}: YAML block scalar, strip_comments is line-based: use a quoted or flow value")
        elif suffix == ".alloy" and line.count("`") % 2:
            errors.append(f"{source}:{number}: multi-line raw string, strip_comments is line-based: keep each raw string on one line")
        elif suffix == ".sh" and SHELL_HEREDOC_RE.search(line):
            errors.append(f"{source}:{number}: heredoc, strip_comments is line-based: use printf")
    if suffix == ".sh":
        lines = text.split("\n")
        for number, (line, following) in enumerate(zip(lines, lines[1:], strict=False), 1):
            if line.endswith("\\") and following.lstrip().startswith("#"):
                errors.append(f"{source}:{number}: line continuation before a comment line, strip_comments would join it with the next command")
        for number in shell_open_quote_lines(text):
            errors.append(f"{source}:{number}: multi-line quoted string, strip_comments is line-based: keep each string on one line")
    return errors


def check_render():
    errors = []
    for path, text in render.outputs(ROOT).items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            errors.append(f"{path.name} is stale: run python3 scripts/render.py")
    template_text = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    for item in render.content_items(ROOT, template_text, stripped=False):
        errors.extend(strip_hazards(item.source, item.text))
    return errors


def check_size(size=None):
    if size is None:
        size = render.base64_size((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
    if size > render.BUDGET_BYTES:
        return [f"docker-compose.yaml is {size} bytes in base64, budget is {render.BUDGET_BYTES}"]
    print(f"    base64 size {size} / {render.BUDGET_BYTES} bytes")
    margin = render.BUDGET_BYTES - size
    if margin < SIZE_WARN_MARGIN:
        print(f"    WARN: only {margin} bytes of margin left (under {SIZE_WARN_MARGIN})")
    return []


def check_compose():
    compose_json()
    keys = env_example_keys((ROOT / ".env.example").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "check.env"
        env_file.write_text("".join(f"{key}=check-value\n" for key in sorted(keys)), encoding="utf-8")
        code, output = run(["docker", "compose", "-f", "compose.dev.yaml", "--env-file", str(env_file), "config", "--quiet"])
    return [] if code == 0 else [f"compose.dev.yaml rejected by docker compose config:\n{output}"]


def check_ports():
    return port_violations(compose_json())


def check_env():
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    return env_var_mismatches(template, (ROOT / ".env.example").read_text(encoding="utf-8"))


def check_secrets():
    findings = []
    for path in sorted((ROOT / "config").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        name = str(path.relative_to(ROOT))
        try:
            findings += find_hardcoded_secrets(path.read_text(encoding="utf-8"), name)
        except UnicodeDecodeError:
            findings.append(f"{name}: not UTF-8 text")
    return findings


def validator_commands(sources):
    """Official validator command for each config file referenced by the template."""
    commands = []
    for source in sorted(set(sources)):
        path = str(ROOT / source)
        if source.endswith("/loki/loki.yaml"):
            commands.append([tool("loki"), f"-config.file={path}", "-config.expand-env=true", "-verify-config"])
        elif source.endswith("/tempo/tempo.yaml"):
            commands.append([tool("tempo"), f"-config.file={path}", "-config.expand-env=true", "-config.verify=true"])
        elif source.endswith("/prometheus/prometheus.yml"):
            commands.append([tool("promtool"), "check", "config", path])
        elif source.endswith(".alloy"):
            commands.append([tool("alloy"), "validate", "--stability.level=public-preview", path])
    return commands


def check_targets():
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    return target_collisions((item.service, item.hashed_target) for item in render.content_items(ROOT, template))


def check_limits():
    return limit_violations(compose_json())


def check_validators():
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    sources = [volume.source for volume in render.scan_template(template)]
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env.update(load_env(ROOT / "harness" / "harness.env"))
        env.update({"BIND_ADDR": "127.0.0.1", "LOKI_DATA_DIR": f"{tmp}/loki", "TEMPO_DATA_DIR": f"{tmp}/tempo", "ALLOY_QUEUE_DIR": f"{tmp}/queue"})
        # Second pass as Coolify runs it when the operator empties a variable (spike S5): the
        # compose fallback is skipped, the config must still load with its own defaults.
        emptied = {**env, **dict.fromkeys(fallback_defaults(template), "")}
        for label, values in (("", env), (" with every fallback variable empty", emptied)):
            for cmd in validator_commands(sources):
                code, output = run(cmd, env=values)
                if code != 0:
                    errors.append(f"{Path(cmd[0]).name} rejected {cmd[-1] if 'check' in cmd else cmd[1:]}{label}:\n{output.strip()}")
    return errors


def check_lint():
    errors = []
    code, output = run([tool("ruff"), "check", "."])
    if code != 0:
        errors.append(f"ruff:\n{output.strip()}")
    for cmd in (
        [tool("shellcheck"), "-x", "tools/fetch-binaries.sh"],
        [tool("shellcheck"), "-s", "sh", "config/config-guard/guard.sh"],
        [tool("shellcheck"), "-s", "sh", "config/prometheus/start.sh"],
    ):
        code, output = run(cmd)
        if code != 0:
            errors.append(f"shellcheck {cmd[-1]}:\n{output.strip()}")
    return errors


def check_dashboards():
    code, output = run([sys.executable, str(ROOT / "scripts" / "build_dashboards.py"), "--check"])
    return [] if code == 0 else [output.strip()]


def check_bundle():
    tag = render.load_versions(ROOT / "tools" / "versions.env").get("GRAFANA_SETUP_TAG", "")
    if not git_tag_exists(tag) and git_is_shallow():
        print(f"    skipped: tag {tag} not found locally and this clone is shallow (git fetch --tags, or --unshallow, to check it)")
        return []
    return bundle_errors(tag, render.grafana_setup_files(ROOT), git_show, git_tag_exists)


CHECKS = {
    "render": check_render,
    "size": check_size,
    "compose": check_compose,
    "ports": check_ports,
    "env": check_env,
    "secrets": check_secrets,
    "targets": check_targets,
    "limits": check_limits,
    "validators": check_validators,
    "lint": check_lint,
    "dashboards": check_dashboards,
    "bundle": check_bundle,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify static checks (spec 12.1)")
    parser.add_argument("--only", help="comma-separated subset of: " + ",".join(CHECKS))
    args = parser.parse_args(argv)
    names = args.only.split(",") if args.only else list(CHECKS)
    failed = False
    for name in names:
        try:
            errors = CHECKS[name]()
        except (RuntimeError, FileNotFoundError, render.RenderError) as exc:
            errors = [str(exc)]
        status = "FAIL" if errors else "PASS"
        print(f"check: [{status}] {name}")
        for error in errors:
            print("    " + error.replace("\n", "\n    "))
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
