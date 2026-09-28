# Plan A — ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer le package Docker Compose `grafana-coolify` (Alloy, passerelle OTLP, Loki, Tempo, Prometheus, node-exporter, `config-guard`, sources de données et dossiers de `grafana-setup`) qui reçoit, masque, stocke et relie logs, traces et métriques, sécurisé et prouvé par des tests exécutés sur un banc natif sans Docker.

**Architecture:** Les configurations lisibles vivent dans `config/` ; `scripts/render.py` les insère octet pour octet dans les blocs `content:` du `docker-compose.yaml` généré à partir de `compose.template.yaml`, et `config-guard` vérifie leurs empreintes au démarrage. `alloy` porte tout le traitement (OTLP interne et Faro public), `alloy-gateway` relaie l'OTLP public authentifié par Traefik, et les middlewares vivent dans la configuration dynamique Traefik hors du dépôt. Comme aucun conteneur ne peut tourner ici, `harness/` lance les binaires officiels avec les mêmes commandes, variables et fichiers que le compose, chacun sur sa propre adresse de boucle locale, et `scripts/smoke.py` et `scripts/security.py` vérifient le comportement de bout en bout.

**Tech Stack:** Grafana Alloy v1.20.0 (OTTL v0.161.0), Loki 3.7.8, Tempo 2.10.8, Prometheus 3.15.0, node-exporter 1.12.1, Traefik v3.7.13 et Grafana 13.2.2 (banc uniquement), Python 3.12 (bibliothèque standard ; PyYAML 6 seulement dans `tests/` et `harness/`), POSIX sh (busybox), `unittest`, ruff 0.16.9, ShellCheck v0.11.0, CLI Docker Compose (validation seulement).

**Spec:** `docs/superpowers/specs/2026-09-27-grafana-coolify-design.md` (révision 4). Le plan A couvre les § 3 à § 9, `config-guard`, les § 10.1–10.2 et les tests § 12.1 à § 12.5. Le plan B (§ 10.3, § 10.4, § 12.6) est hors périmètre.

## Global Constraints

- Versions épinglées, source unique `tools/versions.env` : `ALLOY_VERSION=v1.20.0`, `LOKI_VERSION=3.7.8`, `TEMPO_VERSION=2.10.8` (rester en Tempo 2.x), `PROMETHEUS_VERSION=3.15.0`, `NODE_EXPORTER_VERSION=1.12.1`, `PYTHON_IMAGE=python:3.13-alpine`, `ALPINE_IMAGE=alpine:3.22`, `TRAEFIK_VERSION=v3.7.13` (banc), `GRAFANA_VERSION=13.2.2` (banc), `RUFF_VERSION=0.16.9`, `SHELLCHECK_VERSION=v0.11.0`.
- Images : uniquement celles de l'éditeur ou officielles Docker, aux tags `grafana/alloy:v1.20.0`, `grafana/loki:3.7.8`, `grafana/tempo:2.10.8`, `prom/prometheus:v3.15.0`, `quay.io/prometheus/node-exporter:v1.12.1`, `python:3.13-alpine`, `alpine:3.22` ; aucune image maison, aucun registre privé, aucune CI de build.
- Binaires du banc : téléchargés par `tools/fetch-binaries.sh` depuis les publications officielles et vérifiés contre leurs empreintes SHA-256 publiées (ShellCheck : empreinte épinglée, faute de fichier publié).
- Langue : prose du plan, README, `docs/` et `.env.example` en **français** ; code, identifiants, commentaires, noms de fichiers, noms de tests, messages de commit et branches en **anglais**.
- Bibliothèque standard seulement dans `scripts/*.py` et `config/grafana-setup/setup.py` ; PyYAML autorisé seulement dans `tests/` et `harness/`.
- Tests : `python3 -m unittest discover -s tests -v` (pas de pytest) ; un fichier seul : `python3 -m unittest discover -s tests -p <fichier> -v` ; les tests du banc demandent `GC_HARNESS=1`.
- Chaque test est prouvé capable d'échouer : lancé avant l'implémentation (TDD), ou rejoué contre une configuration volontairement cassée quand le comportement existe déjà.
- Aucun secret en dur dans `config/` (`check.py`, motifs `token|password|secret|salt|key`) ; aucun hash htpasswd ni regex d'origines dans un label : ils vivent dans la configuration dynamique Traefik, hors du dépôt.
- `docker-compose.yaml` et `compose.dev.yaml` sont **générés** (`python3 scripts/render.py`) et versionnés ; ne jamais les modifier à la main. Toute modification de `config/` ou du gabarit se termine par `render.py` et par le commit des deux fichiers générés.
- Budget : `docker-compose.yaml` encodé en base64 ≤ **120 Kio** (122880 octets), vérifié par `check.py`.
- Alloy tourne avec `--stability.level=public-preview`, jamais plus bas ; `alloy validate` doit accepter les deux configs à ce niveau.
- Aucun `ports:` dans le compose déployé, seulement `expose:` (§ 3.2).
- Enveloppe mémoire ≈ 6 Go : `mem_limit` loki 1536m, tempo 1536m, prometheus 1536m, alloy 768m, alloy-gateway 256m, node-exporter 64m, config-guard 32m, grafana-setup 128m (total ≈ 5,7 Gio).
- `alloy` et `alloy-gateway` : `user: "473:473"`, `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, aucun montage de l'hôte hors de leur propre fichier de config en lecture seule.
- Variables résolues par les outils eux-mêmes : Alloy `sys.env("VAR")`, Loki et Tempo `${VAR}` avec `-config.expand-env=true`, Prometheus par son `command:`, `grafana-setup` par son environnement ; chaque config écoute sur `BIND_ADDR` (compose : `0.0.0.0`).
- Rétention par défaut : Loki 7 j (`168h`) et `env="prod"` 30 j (`720h`), Tempo 7 j, Prometheus 90 j / 100GB.
- Git : branche `feat/plan-a-ingestion` ; auteur `Henoc Djabia <henoc35@gmail.com>` ; messages conventionnels en anglais ; **aucune** ligne `Co-Authored-By` ni mention d'outil dans les messages ; ne jamais ajouter ce plan aux commits des tâches.
- Environnement d'exécution : le CLI Docker est présent mais aucun conteneur ne peut démarrer ; `python3` 3.12, `jq`, `curl`, `openssl`, `git`, `sudo` sans mot de passe et l'accès à GitHub et `dl.grafana.com` sont disponibles ; pytest, ruff, shellcheck, htpasswd et yq ne le sont pas (les deux linters viennent de `.bin/`).
- Les spikes Coolify (§ 16) ne sont pas exécutables par un agent : la tâche 19 les écrit, l'opérateur les joue.

## Review Focus

Les cinq classes d'entrées ou modes de défaillance que la spec implique sans les tester, les plus susceptibles de frapper un utilisateur, du plus probable au moins probable. Chacun a son test dans la tâche qui possède le code.

1. **Faux positifs du hachage d'IP** : une heure `01:30:29`, un appel statique PHP `App\User::find` ou une version navigateur `128.0.0.0` ne doivent pas être remplacés par une empreinte. Attendu : texte intact. Test : section `masking` (tâche 10, heure et `::`) et restriction du hachage Faro au texte libre (tâche 11, section `ip-parity`).
2. **`FARO_API_KEY` laissée vide** : le point public Faro ne doit jamais s'ouvrir. Attendu : 401 avec ou sans en-tête `x-api-key`. Test : `tests/test_faro_closed.py` (tâche 11).
3. **Variable d'exécution mal formée** : un sel contenant `"` ou `$`, un `HOST_MAP` sans `:env`, une `TENANT_HOST_REGEX` sans groupe `sub` casseraient silencieusement les gabarits Alloy. Attendu : déploiement refusé avec un message clair. Test : `tests/test_config_guard.py` (tâche 3).
4. **Grafana rend par défaut la première source créée** : un second passage de `grafana-setup` croirait à un changement. Attendu : second passage sans aucune écriture, `isDefault` conservé. Test : `test_second_run_changes_nothing` et `test_changed_url_is_updated_and_default_flag_kept` avec un faux Grafana qui reproduit ce comportement (tâche 16).
5. **Port du banc déjà pris par un autre programme sur `0.0.0.0`** : les tests parleraient silencieusement au mauvais serveur (constaté avec un serveur de développement sur 3000). Attendu : `up` refuse en nommant l'adresse. Test : `test_busy_address_is_reported` (tâche 8).

---

## Structure des fichiers

| Fichier | Responsabilité unique |
|---|---|
| `LICENSE` | Licence MIT, « Copyright (c) 2026 SoftArtisan ». |
| `.gitignore` | Exclut `.bin/`, `.harness/`, `.env`, caches Python. |
| `ruff.toml` | Configuration ruff du projet (résultats indépendants de la machine). |
| `tools/versions.env` | Source unique des versions et de l'empreinte ShellCheck. |
| `tools/fetch-binaries.sh` | Télécharge et vérifie les binaires officiels dans `.bin/`. |
| `compose.template.yaml` | Gabarit : services, labels, limites, jetons `@@…@@`. |
| `docker-compose.yaml` | Généré : déployé par Coolify. |
| `compose.dev.yaml` | Généré : banc Docker local (montages directs + Grafana de test). |
| `.env.example` | Liste exhaustive des variables (§ 11), fait foi. |
| `config/config-guard/guard.sh` | Vérifie fichiers de config et variables avant tout démarrage. |
| `config/loki/loki.yaml` | Loki : OTLP, labels, métadonnées, rétention. |
| `config/tempo/tempo.yaml` | Tempo : OTLP, rétention, metrics-generator. |
| `config/prometheus/prometheus.yml` | Prometheus : promotion OTLP, hors-ordre, scrape de la stack. |
| `config/alloy/config.alloy` | Alloy : OTLP et Faro, env court, masquage, filtrage, routage, file persistante. |
| `config/alloy-gateway/config.alloy` | Relais OTLP/HTTP sans traitement ni file. |
| `config/grafana-setup/setup.py` | Sources de données, corrélations, dossiers (idempotent). |
| `traefik/grafana-coolify.yaml.example` | Modèle des middlewares `@file`, sans secret. |
| `scripts/render.py` | Gabarit → composes générés, contenu octet pour octet. |
| `scripts/check.py` | Contrôles statiques du § 12.1. |
| `scripts/gclib.py` | Aide partagée : HTTP, charges OTLP/Faro JSON, requêtes des stockages. |
| `scripts/smoke.py` | Bout en bout du § 12.3, par sections. |
| `scripts/security.py` | Sécurité du § 12.4 (banc ou déploiement). |
| `harness/harness.env` | Valeurs de test des variables. |
| `harness/stack.py` | Banc natif : services du compose en processus. |
| `harness/edge.py` | Bordure du banc : Traefik (routes Coolify simulées) et Grafana de test. |
| `tests/support.py` | Aide des tests : chemins, binaires, commandes, environnement des validateurs. |
| `tests/test_*.py` | Un fichier par unité testée (voir chaque tâche). |
| `README.md` | Déploiement pas à pas (FR). |
| `docs/spikes.md` | Checklist des spikes Coolify pour l'opérateur (FR). |

Ordre des tâches, du plus simple au plus exigeant : outillage (1–3), configurations des stockages validées par leurs outils officiels (4–6), gabarit et contrôles statiques (7), banc (8), puis le pipeline Alloy, qui dépend de tout ce qui précède et se construit par couches testées de bout en bout (9–13), robustesse (14), bordure et sécurité (15), `grafana-setup`, qui a besoin du Grafana de la bordure (16), documentation (17), vérification finale (18) et spikes opérateur (19).

---

## Tasks

### Task 1: Squelette du dépôt et binaires épinglés

Pose la licence, les exclusions Git, la configuration `ruff` du projet, la source unique des
versions (`tools/versions.env`) et le script qui télécharge et vérifie tous les binaires
officiels utilisés par `check.py` et par le banc natif. Le test prouve que chaque binaire tourne
à la version épinglée. `ruff.toml` est indispensable : sans lui, `ruff` applique la configuration
personnelle de la machine (constaté : règles `FURB`, `PLW`, `RUF` actives), et les résultats ne
sont pas reproductibles.

**Files:**

- Create: `LICENSE`
- Create: `.gitignore`
- Create: `ruff.toml`
- Create: `tools/versions.env`
- Create: `tools/fetch-binaries.sh`
- Create: `tests/support.py`
- Test: `tests/test_fetch_binaries.py`

**Interfaces:**

- Consumes : rien.
- Produces : `tools/versions.env` : `ALLOY_VERSION=v1.20.0`, `LOKI_VERSION=3.7.8`, `TEMPO_VERSION=2.10.8`, `PROMETHEUS_VERSION=3.15.0`, `NODE_EXPORTER_VERSION=1.12.1`, `PYTHON_IMAGE=python:3.13-alpine`, `ALPINE_IMAGE=alpine:3.22`, `TRAEFIK_VERSION=v3.7.13`, `GRAFANA_VERSION=13.2.2`, `RUFF_VERSION=0.16.9`, `SHELLCHECK_VERSION=v0.11.0`, `SHELLCHECK_SHA256`.
- Produces : `tools/fetch-binaries.sh` (bash, idempotent, `GC_BIN_DIR` pour changer la cible) installe dans `.bin/` : `alloy`, `loki`, `tempo`, `prometheus`, `promtool`, `node_exporter`, `traefik`, `ruff`, `shellcheck`, `grafana/` (arborescence Grafana OSS, binaire `grafana/bin/grafana`).
- Produces : `tests/support.py` : `ROOT`, `BIN`, `load_env_file(path) -> dict`, `versions() -> dict`, `binary(name) -> Path` (AssertionError explicite si absent), `run(cmd, env=None, cwd=None, timeout=120) -> CompletedProcess` (stdout+stderr fusionnés, texte), `require_harness(test_case)` (SkipTest sauf si `GC_HARNESS=1`).

- [ ] **Étape 1 : créer la branche et fixer l'auteur des commits**

Run : `git switch -c feat/plan-a-ingestion && git config user.name "Henoc Djabia" && git config user.email "henoc35@gmail.com"`

Attendu : `Switched to a new branch 'feat/plan-a-ingestion'`. Le plan (`docs/superpowers/plans/…`) reste non suivi : ne jamais l'ajouter aux commits des tâches.

- [ ] **Étape 2 : écrire le test qui échoue**

Fichier complet `tests/support.py` :

```python
"""Shared helpers for the unittest suite (python3 -m unittest discover -s tests -v)."""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(os.environ.get("GC_BIN_DIR", str(ROOT / ".bin")))


def load_env_file(path):
    """Parse a KEY=VALUE file (comments and blank lines ignored, no quoting)."""
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def versions():
    return load_env_file(ROOT / "tools" / "versions.env")


def binary(name):
    """Absolute path of a binary installed by tools/fetch-binaries.sh."""
    path = BIN / name
    if not path.exists():
        raise AssertionError(f"{path} missing: run tools/fetch-binaries.sh first")
    return path


def run(cmd, env=None, cwd=None, timeout=120):
    """Run a command and return CompletedProcess with text stdout+stderr merged."""
    return subprocess.run(
        [str(c) for c in cmd],
        env=env,
        cwd=cwd or ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def require_harness(test_case):
    """Skip unless GC_HARNESS=1: the test needs the native harness (sudo, loopback IPs)."""
    if os.environ.get("GC_HARNESS") != "1":
        raise unittest.SkipTest("needs the native harness: set GC_HARNESS=1")
```

Fichier complet `tests/test_fetch_binaries.py` :

```python
import unittest

from support import binary, run, versions


class FetchBinariesTest(unittest.TestCase):
    """Every harness binary is installed at the version pinned in tools/versions.env."""

    def assert_version(self, cmd, expected):
        result = run(cmd)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stdout)

    def test_alloy(self):
        self.assert_version([binary("alloy"), "--version"], "version " + versions()["ALLOY_VERSION"])

    def test_loki(self):
        self.assert_version([binary("loki"), "-version"], "version " + versions()["LOKI_VERSION"])

    def test_tempo(self):
        self.assert_version([binary("tempo"), "-version"], "version " + versions()["TEMPO_VERSION"])

    def test_prometheus_and_promtool(self):
        expected = "version " + versions()["PROMETHEUS_VERSION"]
        self.assert_version([binary("prometheus"), "--version"], expected)
        self.assert_version([binary("promtool"), "--version"], expected)

    def test_node_exporter(self):
        self.assert_version([binary("node_exporter"), "--version"], "version " + versions()["NODE_EXPORTER_VERSION"])

    def test_traefik(self):
        self.assert_version([binary("traefik"), "version"], versions()["TRAEFIK_VERSION"].lstrip("v"))

    def test_grafana(self):
        self.assert_version([binary("grafana/bin/grafana"), "--version"], "version " + versions()["GRAFANA_VERSION"])

    def test_ruff(self):
        self.assert_version([binary("ruff"), "--version"], "ruff " + versions()["RUFF_VERSION"])

    def test_shellcheck(self):
        self.assert_version([binary("shellcheck"), "--version"], "version: " + versions()["SHELLCHECK_VERSION"].lstrip("v"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 3 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_fetch_binaries.py -v`

Attendu : FAIL, 9 échecs « .bin/alloy missing: run tools/fetch-binaries.sh first » (et de même pour chaque binaire).

- [ ] **Étape 4 : implémentation minimale**

Fichier complet `LICENSE` :

```text
MIT License

Copyright (c) 2026 SoftArtisan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Fichier complet `.gitignore` :

```gitignore
# Downloaded release binaries (tools/fetch-binaries.sh)
/.bin/
# Native harness state: logs, data, pids (harness/stack.py)
/.harness/
# Local operator values
/.env
__pycache__/
*.pyc
```

Fichier complet `ruff.toml` :

```toml
# Project ruff configuration: overrides any user-level ruff config so results are reproducible.
line-length = 160
target-version = "py312"

[lint]
select = ["E", "F", "W", "I", "B", "UP"]

[lint.isort]
known-first-party = ["support", "render", "check", "gclib", "setup", "stack", "edge"]
```

Fichier complet `tools/versions.env` :

```dotenv
# Single source of truth for pinned versions.
# scripts/render.py substitutes @@NAME@@ placeholders of compose.template.yaml from this file;
# tools/fetch-binaries.sh downloads the harness binaries at the same versions.
ALLOY_VERSION=v1.20.0
LOKI_VERSION=3.7.8
TEMPO_VERSION=2.10.8
PROMETHEUS_VERSION=3.15.0
NODE_EXPORTER_VERSION=1.12.1
PYTHON_IMAGE=python:3.13-alpine
ALPINE_IMAGE=alpine:3.22
# Harness only (never deployed by Coolify)
TRAEFIK_VERSION=v3.7.13
GRAFANA_VERSION=13.2.2
# Static analysis tools fetched by tools/fetch-binaries.sh
RUFF_VERSION=0.16.9
SHELLCHECK_VERSION=v0.11.0
# ShellCheck publishes no checksum file: hash of shellcheck-v0.11.0.linux.x86_64.tar.gz
SHELLCHECK_SHA256=b7af85e41cc99489dcc21d66c6d5f3685138f06d34651e6d34b42ec6d54fe6f6
```

ShellCheck ne publie pas de fichier d'empreintes : son SHA-256 est épinglé dans `versions.env` (valeur relevée sur l'archive officielle). Ne jamais commencer un commentaire par `# shellcheck` dans un fichier lu par shellcheck : il le prend pour une directive (erreur SC1073).

Fichier complet `tools/fetch-binaries.sh` :

```bash
#!/usr/bin/env bash
# Download the pinned release binaries used by scripts/check.py and the native
# harness (harness/stack.py), and verify every archive against its SHA-256.
# Usage: tools/fetch-binaries.sh            (installs into ./.bin, idempotent)
#        GC_BIN_DIR=/path tools/fetch-binaries.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=tools/versions.env
. "$ROOT/tools/versions.env"
BIN="${GC_BIN_DIR:-$ROOT/.bin}"
WORK="$BIN/.work"
GH="https://github.com"

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "fetch-binaries: only linux/x86_64 is supported (got $(uname -s)/$(uname -m))" >&2
  exit 1
fi
for tool in curl sha256sum tar unzip; do
  command -v "$tool" >/dev/null || { echo "fetch-binaries: missing $tool" >&2; exit 1; }
done
mkdir -p "$BIN" "$WORK"

download() { # url destination
  curl -fsSL --retry 3 -o "$2" "$1"
}

verify_sha() { # file expected_sha256
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  if [ "$actual" != "$2" ]; then
    echo "fetch-binaries: checksum mismatch for $(basename "$1"): got $actual, want $2" >&2
    exit 1
  fi
}

verify_from_sums() { # archive sums_file
  local name expected
  name="$(basename "$1")"
  expected="$(awk -v n="$name" '$2 == n || $2 == "*" n { print $1; exit }' "$2")"
  if [ -z "$expected" ]; then
    echo "fetch-binaries: no checksum for $name in $(basename "$2")" >&2
    exit 1
  fi
  verify_sha "$1" "$expected"
}

is_installed() { # marker version
  [ "$(cat "$BIN/.$1.version" 2>/dev/null || true)" = "$2" ]
}

mark_installed() { # marker version
  printf '%s\n' "$2" > "$BIN/.$1.version"
}

fetch_zip() { # marker version base_url asset sums_asset member target
  local marker=$1 version=$2 base=$3 asset=$4 sums=$5 member=$6 target=$7
  if is_installed "$marker" "$version" && [ -x "$BIN/$target" ]; then return; fi
  download "$base/$asset" "$WORK/$asset"
  download "$base/$sums" "$WORK/$marker.sums"
  verify_from_sums "$WORK/$asset" "$WORK/$marker.sums"
  unzip -o -q "$WORK/$asset" "$member" -d "$WORK/$marker"
  install -m 0755 "$WORK/$marker/$member" "$BIN/$target"
  mark_installed "$marker" "$version"
}

fetch_tar() { # marker version base_url asset sums_asset (member:target)...
  local marker=$1 version=$2 base=$3 asset=$4 sums=$5 pair
  shift 5
  if is_installed "$marker" "$version"; then return; fi
  download "$base/$asset" "$WORK/$asset"
  download "$base/$sums" "$WORK/$marker.sums"
  verify_from_sums "$WORK/$asset" "$WORK/$marker.sums"
  rm -rf "${WORK:?}/$marker" && mkdir -p "$WORK/$marker"
  tar -xzf "$WORK/$asset" -C "$WORK/$marker"
  for pair in "$@"; do
    install -m 0755 "$WORK/$marker/${pair%%:*}" "$BIN/${pair#*:}"
  done
  mark_installed "$marker" "$version"
}

# Grafana Alloy
fetch_zip alloy "$ALLOY_VERSION" "$GH/grafana/alloy/releases/download/$ALLOY_VERSION" \
  alloy-linux-amd64.zip SHA256SUMS alloy-linux-amd64 alloy

# Loki
fetch_zip loki "$LOKI_VERSION" "$GH/grafana/loki/releases/download/v$LOKI_VERSION" \
  loki-linux-amd64.zip SHA256SUMS loki-linux-amd64 loki

# Tempo
fetch_tar tempo "$TEMPO_VERSION" "$GH/grafana/tempo/releases/download/v$TEMPO_VERSION" \
  "tempo_${TEMPO_VERSION}_linux_amd64.tar.gz" SHA256SUMS tempo:tempo

# Prometheus + promtool
fetch_tar prometheus "$PROMETHEUS_VERSION" "$GH/prometheus/prometheus/releases/download/v$PROMETHEUS_VERSION" \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64.tar.gz" sha256sums.txt \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64/prometheus:prometheus" \
  "prometheus-$PROMETHEUS_VERSION.linux-amd64/promtool:promtool"

# node-exporter
fetch_tar node_exporter "$NODE_EXPORTER_VERSION" "$GH/prometheus/node_exporter/releases/download/v$NODE_EXPORTER_VERSION" \
  "node_exporter-$NODE_EXPORTER_VERSION.linux-amd64.tar.gz" sha256sums.txt \
  "node_exporter-$NODE_EXPORTER_VERSION.linux-amd64/node_exporter:node_exporter"

# Traefik (harness edge only)
fetch_tar traefik "$TRAEFIK_VERSION" "$GH/traefik/traefik/releases/download/$TRAEFIK_VERSION" \
  "traefik_${TRAEFIK_VERSION}_linux_amd64.tar.gz" "traefik_${TRAEFIK_VERSION}_checksums.txt" traefik:traefik

# ruff (publishes one .sha256 file per asset)
fetch_tar ruff "$RUFF_VERSION" "$GH/astral-sh/ruff/releases/download/$RUFF_VERSION" \
  ruff-x86_64-unknown-linux-gnu.tar.gz ruff-x86_64-unknown-linux-gnu.tar.gz.sha256 \
  ruff-x86_64-unknown-linux-gnu/ruff:ruff

# ShellCheck binary (no checksum file upstream: pinned hash in tools/versions.env)
if ! is_installed shellcheck "$SHELLCHECK_VERSION"; then
  asset="shellcheck-$SHELLCHECK_VERSION.linux.x86_64.tar.gz"
  download "$GH/koalaman/shellcheck/releases/download/$SHELLCHECK_VERSION/$asset" "$WORK/$asset"
  verify_sha "$WORK/$asset" "$SHELLCHECK_SHA256"
  rm -rf "${WORK:?}/shellcheck" && mkdir -p "$WORK/shellcheck"
  tar -xzf "$WORK/$asset" -C "$WORK/shellcheck"
  install -m 0755 "$WORK/shellcheck/shellcheck-$SHELLCHECK_VERSION/shellcheck" "$BIN/shellcheck"
  mark_installed shellcheck "$SHELLCHECK_VERSION"
fi

# Grafana OSS (harness only; the .sha256 file holds the bare hash)
if ! is_installed grafana "$GRAFANA_VERSION"; then
  asset="grafana-$GRAFANA_VERSION.linux-amd64.tar.gz"
  base="https://dl.grafana.com/oss/release"
  download "$base/$asset" "$WORK/$asset"
  download "$base/$asset.sha256" "$WORK/grafana.sha256"
  verify_sha "$WORK/$asset" "$(tr -d '[:space:]' < "$WORK/grafana.sha256")"
  rm -rf "${BIN:?}/grafana" "${WORK:?}/grafana" && mkdir -p "$WORK/grafana"
  tar -xzf "$WORK/$asset" -C "$WORK/grafana"
  mv "$WORK/grafana/grafana-$GRAFANA_VERSION" "$BIN/grafana"
  mark_installed grafana "$GRAFANA_VERSION"
fi

rm -rf "$WORK"
echo "fetch-binaries: all binaries installed in $BIN"
```

Run : `chmod +x tools/fetch-binaries.sh && tools/fetch-binaries.sh`

Attendu : « fetch-binaries: all binaries installed in …/.bin » (≈ 1,3 Go, 1 à 3 minutes). Un second lancement se termine en moins d'une seconde.

- [ ] **Étape 5 : lancer les tests — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_fetch_binaries.py -v`

Attendu : `Ran 9 tests` … `OK`.

Run : `.bin/shellcheck -x tools/fetch-binaries.sh && .bin/ruff check .`

Attendu : aucune sortie de shellcheck, puis « All checks passed! ».

- [ ] **Étape 6 : commit**

```bash
git add LICENSE .gitignore ruff.toml tools/versions.env tools/fetch-binaries.sh tests/support.py tests/test_fetch_binaries.py
git commit -m "chore: scaffold repository and pinned binary fetcher"
```


### Task 2: `scripts/render.py` : gabarit → compose déployé

Le générateur insère chaque fichier de `config/` dans un bloc `content:` **sans aucune
transformation** (spec § 4.2) : scalaire littéral YAML avec indicateur d'indentation explicite
(`|2`) et indicateur de troncature choisi selon les fins de ligne (`|2-` sans saut final, `|2` pour
un seul, `|2+` pour plusieurs). Il refuse ce que YAML ne peut pas restituer à l'identique
(fichier vide, `\r`, BOM, caractères de contrôle, UTF-8 invalide). Piège vérifié : ne jamais
appliquer `rstrip("\n")` au bloc généré, cela supprime les lignes vides que `|+` doit garder.
Les tests travaillent sur un gabarit de test dans un dossier temporaire ; le vrai gabarit arrive
à la tâche 7.

**Files:**

- Create: `scripts/render.py`
- Test: `tests/test_render.py`

**Interfaces:**

- Consumes : `tests/support.py` (tâche 1) : `ROOT`.
- Produces : `scripts/render.py` (stdlib) : `RenderError`, `BUDGET_BYTES = 122880`, `HEADER` (commence par `# GENERATED — DO NOT EDIT`), `load_versions(path) -> dict`, `read_config(root, source) -> str`, `literal_block(text, key_col, name) -> str`, `scan_template(text)` → itère `(service, indent, source, target, line_index)` pour chaque `content: "@@CONTENT@@"`, `computed_values(root, text) -> {'CONFIG_GUARD_EXPECTED', 'GUARD_SHA256'}`, `render_text(template_text, root, versions, strip_content=False) -> str`, `render_dev(template_text, root, versions) -> str`, `base64_size(text) -> int`, `outputs(root) -> {Path: str}` (`docker-compose.yaml` et `compose.dev.yaml`), CLI `python3 scripts/render.py [--check] [--stub PATH] [--root DIR]`.
- Produces : Contrat du gabarit : chaque volume `content:` s'écrit `- type: bind` / `source: ./config/...` / `target: ...` / `content: "@@CONTENT@@"` ; les autres jetons `@@NOM@@` viennent de `tools/versions.env` ; `CONFIG_GUARD_EXPECTED` = `/guard/<chemin sous config/>=<sha256>` joints par `;`, triés, pour chaque fichier `content:` des services autres que `config-guard` ; `GUARD_SHA256` = SHA-256 de `config/config-guard/guard.sh`.

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_render.py` :

```python
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402

# Everything Coolify must copy byte for byte: $-expressions, tabs, trailing blank lines,
# non-ASCII text, a line starting with spaces and trailing spaces.
TRICKY = "  first line indented\n$__rate_interval ${DS_X} $$ $1\n\tkey:\tvalue  \nnon-ASCII: é 日本 ✓\n\n\n"

TEMPLATE = """# test template
services:
  config-guard:
    image: @@ALPINE_IMAGE@@
    environment:
      CONFIG_GUARD_EXPECTED: "@@CONFIG_GUARD_EXPECTED@@"
      GUARD: "@@GUARD_SHA256@@"
    volumes:
      - type: bind
        source: ./config/config-guard/guard.sh
        target: /opt/config-guard/guard.sh
        content: "@@CONTENT@@"
      - type: bind
        source: ./config
        target: /guard
        read_only: true
  app:
    image: example/app:@@APP_VERSION@@
    volumes:
      - type: bind
        source: ./config/app/tricky.txt
        target: /etc/app/tricky.txt
        content: "@@CONTENT@@"
      - app-data:/data
volumes:
  app-data:
"""


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config" / "app").mkdir(parents=True)
        (self.root / "config" / "config-guard").mkdir(parents=True)
        (self.root / "config" / "app" / "tricky.txt").write_bytes(TRICKY.encode("utf-8"))
        (self.root / "config" / "config-guard" / "guard.sh").write_text("#!/bin/sh\necho guard\n", encoding="utf-8")
        self.versions = {"ALPINE_IMAGE": "alpine:3.22", "APP_VERSION": "1.2.3"}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, template=TEMPLATE, **kwargs):
        return render.render_text(template, self.root, self.versions, **kwargs)

    def test_content_blocks_are_byte_exact(self):
        doc = yaml.safe_load(self.render())
        app_volume = doc["services"]["app"]["volumes"][0]
        self.assertEqual(app_volume["content"].encode("utf-8"), TRICKY.encode("utf-8"))
        guard_script = doc["services"]["config-guard"]["volumes"][0]
        self.assertEqual(guard_script["content"], "#!/bin/sh\necho guard\n")
        self.assertNotIn("content", doc["services"]["config-guard"]["volumes"][1])

    def test_every_chomping_and_indentation_case_round_trips(self):
        cases = ["x", "x\n", "x\n\n", "\n\nstarts blank\n", " \n  \n", "\tfirst tab\n", "end  \n\n\n", "a\n\n\nb"]
        for text in cases:
            with self.subTest(text=text):
                (self.root / "config" / "app" / "tricky.txt").write_text(text, encoding="utf-8")
                doc = yaml.safe_load(self.render())
                self.assertEqual(doc["services"]["app"]["volumes"][0]["content"], text)

    def test_yaml_structure_is_preserved(self):
        doc = yaml.safe_load(self.render())
        self.assertEqual(doc["services"]["app"]["image"], "example/app:1.2.3")
        self.assertEqual(doc["services"]["app"]["volumes"][1], "app-data:/data")
        self.assertEqual(doc["services"]["app"]["volumes"][0]["target"], "/etc/app/tricky.txt")
        self.assertIn("app-data", doc["volumes"])

    def test_output_is_deterministic(self):
        self.assertEqual(self.render(), self.render())

    def test_guard_expectations_cover_the_other_services_files(self):
        env = yaml.safe_load(self.render())["services"]["config-guard"]["environment"]
        tricky_sha = hashlib.sha256(TRICKY.encode("utf-8")).hexdigest()
        self.assertEqual(env["CONFIG_GUARD_EXPECTED"], f"/guard/app/tricky.txt={tricky_sha}")
        guard_sha = hashlib.sha256(b"#!/bin/sh\necho guard\n").hexdigest()
        self.assertEqual(env["GUARD"], guard_sha)

    def test_strip_content_removes_every_content_line(self):
        text = self.render(strip_content=True)
        self.assertNotIn("content:", text)
        doc = yaml.safe_load(text)
        self.assertEqual(doc["services"]["app"]["volumes"][0]["source"], "./config/app/tricky.txt")

    def test_unknown_placeholder_is_an_error(self):
        with self.assertRaisesRegex(render.RenderError, "@@NOPE@@"):
            self.render(TEMPLATE.replace("@@APP_VERSION@@", "@@NOPE@@"))

    def test_rejects_unfaithful_files(self):
        bad = {"empty": b"", "cr": b"a\r\nb\n", "bom": "﻿x\n".encode(), "ctrl": b"a\x01b\n", "latin1": b"caf\xe9\n"}
        for name, data in bad.items():
            with self.subTest(name=name):
                (self.root / "config" / "app" / "tricky.txt").write_bytes(data)
                with self.assertRaises(render.RenderError):
                    self.render()

    def test_rejects_source_outside_config(self):
        with self.assertRaisesRegex(render.RenderError, "config"):
            self.render(TEMPLATE.replace("./config/app/tricky.txt", "./secrets.txt"))

    def test_base64_size(self):
        self.assertEqual(render.base64_size("abc"), 4)
        self.assertEqual(render.base64_size("é"), 4)

    def test_dev_variant_adds_grafana_and_no_content(self):
        dev = render.render_dev(TEMPLATE, self.root, dict(self.versions, GRAFANA_VERSION="13.2.2"))
        self.assertNotIn("content:", dev)
        doc = yaml.safe_load(dev)
        self.assertEqual(doc["services"]["grafana"]["image"], "grafana/grafana:13.2.2")
        self.assertIn("app", doc["services"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_render.py -v`

Attendu : ERROR « ModuleNotFoundError: No module named 'render' ».

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `scripts/render.py` :

```python
#!/usr/bin/env python3
"""Render compose.template.yaml into docker-compose.yaml (deployed by Coolify) and compose.dev.yaml.

Standard library only. Rules (spec 4.1-4.4):
- every `content: "@@CONTENT@@"` line becomes a YAML literal block holding the bytes of the
  `source:` file of the same volume item, copied without any transformation;
- `@@NAME@@` placeholders come from tools/versions.env, plus two computed values:
  CONFIG_GUARD_EXPECTED ("/guard/<path under config/>=sha256;..." for every content file of the
  other services: config-guard mounts ./config at /guard) and GUARD_SHA256 (hash of guard.sh);
- the output is deterministic.
"""

import argparse
import base64
import difflib
import hashlib
import re
import sys
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
    """Yield (service, indent, source, target, line_index) for every content placeholder."""
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
            item[match.group("key")] = (len(match.group("indent")), match.group("value"))
            continue
        match = CONTENT_RE.match(line)
        if match:
            indent = len(match.group("indent"))
            if "source" not in item or "target" not in item:
                raise RenderError(f"line {index + 1}: content placeholder without source and target")
            for key in ("source", "target"):
                if item[key][0] not in (indent, indent - 2):
                    raise RenderError(f"line {index + 1}: {key} is not part of the same volume item")
            yield service, indent, item["source"][1], item["target"][1], index
            item = {}


def computed_values(root, template_text):
    expected = set()
    for service, _indent, source, _target, _index in scan_template(template_text):
        if service != "config-guard":
            path = GUARD_PREFIX + source[len("./config/"):]
            expected.add(f"{path}={sha256_text(read_config(root, source))}")
    return {
        "CONFIG_GUARD_EXPECTED": ";".join(sorted(expected)),
        "GUARD_SHA256": sha256_text(read_config(root, "./" + GUARD_SCRIPT)),
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


def render_text(template_text, root, versions, strip_content=False):
    """Render the template. strip_content=True drops the content lines (compose.dev / validation)."""
    values = dict(versions)
    values.update(computed_values(root, template_text))
    lines = template_text.split("\n")
    contents = {}
    for _service, indent, source, _target, index in scan_template(template_text):
        if strip_content:
            contents[index] = None
        else:
            text = read_config(root, source)
            # Drop only the final newline: "\n".join() below adds it back. Blank lines kept by
            # the "+" chomping indicator must stay in the output.
            contents[index] = " " * indent + "content: " + literal_block(text, indent, source)[:-1]
    out = []
    for index, line in enumerate(lines):
        if index in contents:
            if contents[index] is not None:
                out.append(contents[index])
            continue
        out.append(substitute(line, values))
    return "\n".join(out)


def render_dev(template_text, root, versions):
    stub = render_text(template_text, root, versions, strip_content=True)
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
```

Run : `chmod +x scripts/render.py`

Attendu : aucune sortie.

- [ ] **Étape 4 : lancer le test — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_render.py -v`

Attendu : `Ran 11 tests` … `OK`.

Run : `.bin/ruff check .`

Attendu : « All checks passed! ».

- [ ] **Étape 5 : commit**

```bash
git add scripts/render.py tests/test_render.py
git commit -m "feat(render): render compose template with byte-exact content blocks"
```


### Task 3: `config-guard` : garde des fichiers et des variables

Script POSIX pour `alpine` (busybox) : il échoue si un fichier attendu est absent, vide, un
dossier ou d'empreinte différente, et, décision du plan (hors spec), si une variable injectée
dans une config la casserait : `IP_HASH_SALT` est inséré dans une instruction OTTL et dans un
gabarit Go, `HOST_MAP` et `RESERVED_SUBDOMAINS` sont découpés par des gabarits, et
`TENANT_HOST_REGEX` doit définir le groupe `sub`. Mieux vaut un échec clair au déploiement qu'un
masquage silencieusement faux. Les tests lancent le script avec le `sh` de la machine (dash,
POSIX).

**Files:**

- Create: `config/config-guard/guard.sh`
- Test: `tests/test_config_guard.py`

**Interfaces:**

- Consumes : `tests/support.py` : `ROOT`, `run`.
- Produces : `config/config-guard/guard.sh` : lit `CONFIG_GUARD_EXPECTED` (`chemin=sha256;…`), `IP_HASH_SALT` (≥ 16 `[A-Za-z0-9]`), `HOST_MAP` (vide ou `hote=service:env[,…]`), `RESERVED_SUBDOMAINS` (vide ou `a,b`), `TENANT_HOST_REGEX` (vide ou contient `(?P<sub>`). Code de sortie 0 et « config-guard: all checks passed », sinon 1 et « config-guard: FAILED - no service will start ».

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_config_guard.py` :

```python
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from support import ROOT, run

GUARD = ROOT / "config" / "config-guard" / "guard.sh"
VALID_ENV = {
    "IP_HASH_SALT": "harnessSalt0123456789",
    "HOST_MAP": "example.me=guest-front:prod",
    "RESERVED_SUBDOMAINS": "www,api",
    "TENANT_HOST_REGEX": r"^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$",
}


class ConfigGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "loki.yaml"
        self.file.write_text("auth_enabled: false\n", encoding="utf-8")
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, expected=None, **overrides):
        env = {"PATH": os.environ["PATH"], **VALID_ENV}
        env["CONFIG_GUARD_EXPECTED"] = f"{self.file}={self.sha}" if expected is None else expected
        env.update(overrides)
        return run(["sh", GUARD], env=env)

    def assert_fails(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(message, result.stdout)
        self.assertIn("no service will start", result.stdout)

    def test_valid_file_passes(self):
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"ok {self.file}", result.stdout)

    def test_several_entries(self):
        other = self.dir / "tempo.yaml"
        other.write_text("server: {}\n", encoding="utf-8")
        sha = hashlib.sha256(other.read_bytes()).hexdigest()
        result = self.guard(expected=f"{self.file}={self.sha};{other}={sha}")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"ok {other}", result.stdout)

    def test_missing_file(self):
        self.file.unlink()
        self.assert_fails(self.guard(), "missing")

    def test_empty_file(self):
        self.file.write_bytes(b"")
        self.assert_fails(self.guard(), "empty")

    def test_directory_instead_of_file(self):
        self.file.unlink()
        self.file.mkdir()
        self.assert_fails(self.guard(), "is a directory")

    def test_altered_content(self):
        self.file.write_text("auth_enabled: true\n", encoding="utf-8")
        self.assert_fails(self.guard(), "differs from source")

    def test_empty_expectations(self):
        self.assert_fails(self.guard(expected=""), "CONFIG_GUARD_EXPECTED is empty")

    def test_malformed_entry(self):
        self.assert_fails(self.guard(expected=f"{self.file}"), "malformed")

    def test_salt_rules(self):
        for salt in ["", "short", 'with"quote0123456789', "with space 0123456789", "with$dollar0123456789"]:
            with self.subTest(salt=salt):
                self.assert_fails(self.guard(IP_HASH_SALT=salt), "IP_HASH_SALT")

    def test_host_map_rules(self):
        self.assertEqual(self.guard(HOST_MAP="").returncode, 0)
        self.assertEqual(self.guard(HOST_MAP="a.me=web:prod,b.me=api:preprod").returncode, 0)
        for value in ["example.me", "Example.me=web:prod", "a.me=web", "a.me=web:prod,"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(HOST_MAP=value), "HOST_MAP")

    def test_reserved_subdomains_rules(self):
        self.assertEqual(self.guard(RESERVED_SUBDOMAINS="").returncode, 0)
        for value in ["www,", "WWW", "www api", "www|api"]:
            with self.subTest(value=value):
                self.assert_fails(self.guard(RESERVED_SUBDOMAINS=value), "RESERVED_SUBDOMAINS")

    def test_tenant_regex_needs_sub_group(self):
        self.assertEqual(self.guard(TENANT_HOST_REGEX="").returncode, 0)
        self.assert_fails(self.guard(TENANT_HOST_REGEX=r"^([a-z]+)\.example\.me$"), "TENANT_HOST_REGEX")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_config_guard.py -v`

Attendu : FAIL sur les 12 tests (le script n'existe pas : « sh: 0: cannot open …/guard.sh »).

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `config/config-guard/guard.sh` :

```sh
#!/bin/sh
# config-guard (spec 4.4): refuse to start the stack when a config file written by Coolify is
# missing, empty, a directory, or differs from its source (SHA-256), or when a runtime variable
# used inside a config would break it. POSIX sh + busybox (alpine).
#
# CONFIG_GUARD_EXPECTED="path=sha256;path=sha256;..." is computed by scripts/render.py.
set -u
set -f

failed=0

error() {
  printf 'config-guard: ERROR: %s\n' "$*" >&2
  failed=1
}

matches() { # value extended_regex
  printf '%s\n' "$1" | grep -Eq "$2"
}

check_file() { # path expected_sha256
  if [ -d "$1" ]; then
    error "$1: is a directory (Coolify file-mount regression)"
  elif [ ! -e "$1" ]; then
    error "$1: missing"
  elif [ ! -f "$1" ]; then
    error "$1: not a regular file"
  elif [ ! -s "$1" ]; then
    error "$1: empty"
  else
    actual=$(sha256sum "$1" | cut -d' ' -f1)
    if [ "$actual" = "$2" ]; then
      printf 'config-guard: ok %s\n' "$1"
    else
      error "$1: sha256 $actual differs from source $2"
    fi
  fi
}

expected=${CONFIG_GUARD_EXPECTED:-}
if [ -z "$expected" ]; then
  error "CONFIG_GUARD_EXPECTED is empty"
fi
IFS=';'
for entry in $expected; do
  [ -n "$entry" ] || continue
  path=${entry%%=*}
  sha=${entry#*=}
  if [ "$path" = "$entry" ] || ! matches "$sha" '^[0-9a-f]{64}$'; then
    error "malformed CONFIG_GUARD_EXPECTED entry: $entry"
    continue
  fi
  check_file "$path" "$sha"
done
unset IFS

# The salt is spliced into an OTTL statement and a Go template: letters and digits only.
if ! matches "${IP_HASH_SALT:-}" '^[A-Za-z0-9]{16,}$'; then
  error "IP_HASH_SALT must be at least 16 characters from [A-Za-z0-9]"
fi
host_map=${HOST_MAP:-}
if [ -n "$host_map" ] && ! matches "$host_map" '^[a-z0-9.-]+=[a-z0-9-]+:[a-z0-9-]+(,[a-z0-9.-]+=[a-z0-9-]+:[a-z0-9-]+)*$'; then
  error "HOST_MAP must look like host=service:env[,host=service:env...] (lowercase)"
fi
reserved=${RESERVED_SUBDOMAINS:-}
if [ -n "$reserved" ] && ! matches "$reserved" '^[a-z0-9-]+(,[a-z0-9-]+)*$'; then
  error "RESERVED_SUBDOMAINS must look like www,api (lowercase, comma separated)"
fi
tenant_regex=${TENANT_HOST_REGEX:-}
if [ -n "$tenant_regex" ]; then
  case $tenant_regex in
    *'(?P<sub>'*) ;;
    *) error "TENANT_HOST_REGEX must define the named group (?P<sub>...)" ;;
  esac
fi

if [ "$failed" -ne 0 ]; then
  echo "config-guard: FAILED - no service will start" >&2
  exit 1
fi
echo "config-guard: all checks passed"
```

- [ ] **Étape 4 : lancer le test — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_config_guard.py -v`

Attendu : `Ran 12 tests` … `OK`.

Run : `.bin/shellcheck -s sh config/config-guard/guard.sh`

Attendu : aucune sortie.

- [ ] **Étape 5 : commit**

```bash
git add config/config-guard/guard.sh tests/test_config_guard.py
git commit -m "feat(config-guard): verify config files and runtime variables before start"
```


### Task 4: Configuration Loki

Loki mono-binaire : ingestion OTLP avec `ignore_defaults: true` (sinon Loki indexe
`service_instance_id`), labels indexés `project`, `env`, `service.name` (→ `service_name`),
`tenant` en métadonnée structurée, schéma tsdb v13 à `index.period: 24h`, rétention par le
compacteur avec `delete_request_store: filesystem`, 7 j par défaut et 30 j pour `{env="prod"}`.
Toutes les valeurs d'exécution passent par `${VAR}` avec `-config.expand-env=true`. Choix vérifié
sur Loki 3.7.8 : le gRPC interne (9095) écoute sur `127.0.0.1` et l'anneau `inmemory` s'y
enregistre (`instance_addr: 127.0.0.1`) ; seul le HTTP (3100) suit `BIND_ADDR`. `trace_id` et
`span_id` d'un log OTLP sont stockés en métadonnées structurées par Loki lui-même. Cette tâche
crée aussi `harness/harness.env`, les valeurs de test partagées par les validateurs et le banc.

**Files:**

- Create: `harness/harness.env`
- Modify: `tests/support.py`
- Create: `config/loki/loki.yaml`
- Test: `tests/test_loki_config.py`

**Interfaces:**

- Consumes : `tests/support.py` : `binary`, `run`, `load_env_file`.
- Consumes : `.bin/loki` (tâche 1).
- Produces : `harness/harness.env` : toutes les clés de `.env.example` avec des valeurs de test (`IP_HASH_SALT=harnessSalt0123456789`, `FARO_API_KEY=harness-faro-key-0123456789`, `HOST_MAP=example.me=guest-front:prod`, `RESERVED_SUBDOMAINS=www,api`, `TENANT_HOST_REGEX=^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$`, rétentions par défaut, `PROJECTS=demo,other-project`…).
- Produces : `tests/support.py` : `harness_env() -> dict`, `validator_env(tmpdir) -> dict` (environnement + `harness.env` + `BIND_ADDR=127.0.0.1`, `LOKI_DATA_DIR`, `TEMPO_DATA_DIR`, `ALLOY_QUEUE_DIR` sous `tmpdir`).
- Produces : `config/loki/loki.yaml` : variables `BIND_ADDR`, `LOKI_DATA_DIR`, `LOKI_RETENTION_DEFAULT`, `LOKI_RETENTION_PROD` ; ports 3100 (HTTP, `BIND_ADDR`), 9095 (gRPC, `127.0.0.1`).

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `harness/harness.env` :

```dotenv
# Test values for the native harness and the config validators. Never deployed.
# Same keys as .env.example; secrets here are throwaway test values.
IP_HASH_SALT=harnessSalt0123456789
FARO_API_KEY=harness-faro-key-0123456789
FARO_RATE=100
FARO_BURST=200
FARO_MAX_PAYLOAD=5MiB
HOST_MAP=example.me=guest-front:prod
RESERVED_SUBDOMAINS=www,api
TENANT_HOST_REGEX=^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$
LOKI_RETENTION_PROD=720h
LOKI_RETENTION_DEFAULT=168h
TEMPO_RETENTION=168h
TEMPO_MAX_ACTIVE_SERIES=100000
PROM_RETENTION_TIME=90d
PROM_RETENTION_SIZE=100GB
PROM_ENABLE_FEATURES=
ENABLE_EXEMPLARS=false
LOKI_INTERNAL_URL=http://loki:3100
TEMPO_INTERNAL_URL=http://tempo:3200
PROMETHEUS_INTERNAL_URL=http://prometheus:9090
GRAFANA_URL=http://127.0.10.101:3300
GRAFANA_SA_TOKEN=replaced-at-runtime-by-harness
PROJECTS=demo,other-project
```

Ajouter à la fin de `tests/support.py` — le bloc commence par deux lignes vides :

```python


def harness_env():
    return load_env_file(ROOT / "harness" / "harness.env")


def validator_env(tmpdir):
    """Environment for the official validators: harness values, loopback bind, temp data dirs."""
    env = dict(os.environ)
    env.update(harness_env())
    env.update(
        {
            "BIND_ADDR": "127.0.0.1",
            "LOKI_DATA_DIR": str(Path(tmpdir) / "loki"),
            "TEMPO_DATA_DIR": str(Path(tmpdir) / "tempo"),
            "ALLOY_QUEUE_DIR": str(Path(tmpdir) / "alloy-queue"),
        }
    )
    return env
```

Fichier complet `tests/test_loki_config.py` :

```python
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run, validator_env

CONFIG = ROOT / "config" / "loki" / "loki.yaml"


class LokiConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, path):
        cmd = [binary("loki"), f"-config.file={path}", "-config.expand-env=true", "-verify-config"]
        return run(cmd, env=validator_env(self.tmp.name))

    def test_official_validator_accepts_config(self):
        result = self.verify(CONFIG)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("config is valid", result.stdout)

    def test_official_validator_rejects_broken_config(self):
        broken = Path(self.tmp.name) / "broken.yaml"
        broken.write_text(CONFIG.read_text(encoding="utf-8").replace("retention_enabled", "retention_enabledd"), encoding="utf-8")
        self.assertNotEqual(self.verify(broken).returncode, 0)

    def test_otlp_labels_and_metadata(self):
        otlp = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["limits_config"]["otlp_config"]["resource_attributes"]
        self.assertIs(otlp["ignore_defaults"], True)
        by_action = {rule["action"]: rule["attributes"] for rule in otlp["attributes_config"]}
        self.assertEqual(by_action["index_label"], ["project", "env", "service.name"])
        self.assertEqual(by_action["structured_metadata"], ["tenant"])

    def test_schema_and_retention(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        schema = doc["schema_config"]["configs"][0]
        self.assertEqual((schema["store"], schema["schema"], schema["index"]["period"]), ("tsdb", "v13", "24h"))
        self.assertIs(doc["compactor"]["retention_enabled"], True)
        self.assertEqual(doc["compactor"]["delete_request_store"], "filesystem")
        limits = doc["limits_config"]
        self.assertEqual(limits["retention_period"], "${LOKI_RETENTION_DEFAULT}")
        self.assertEqual(limits["retention_stream"], [{"selector": '{env="prod"}', "priority": 1, "period": "${LOKI_RETENTION_PROD}"}])
        self.assertIs(limits["allow_structured_metadata"], True)

    def test_binds_to_bind_addr_and_ring_is_local(self):
        doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(doc["server"]["http_listen_address"], "${BIND_ADDR}")
        self.assertEqual(doc["common"]["ring"]["kvstore"]["store"], "inmemory")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_loki_config.py -v`

Attendu : FAIL/ERROR : « FileNotFoundError … config/loki/loki.yaml » et le validateur refuse un fichier absent.

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `config/loki/loki.yaml` :

```yaml
# Loki single-binary configuration for grafana-coolify.
# Environment variables are expanded by Loki itself (-config.expand-env=true); compose.template.yaml
# sets every one of them (BIND_ADDR, LOKI_DATA_DIR, LOKI_RETENTION_*).
auth_enabled: false

server:
  http_listen_address: ${BIND_ADDR}
  http_listen_port: 3100
  # Internal module-to-module gRPC only (single binary).
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: 9095
  log_level: info

common:
  path_prefix: ${LOKI_DATA_DIR}
  instance_addr: 127.0.0.1
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: ${LOKI_DATA_DIR}/chunks
      rules_directory: ${LOKI_DATA_DIR}/rules

schema_config:
  configs:
    - from: "2026-01-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: ${LOKI_DATA_DIR}/tsdb-index
    cache_location: ${LOKI_DATA_DIR}/tsdb-cache

compactor:
  working_directory: ${LOKI_DATA_DIR}/compactor
  compaction_interval: 10m
  retention_enabled: true
  retention_delete_delay: 2h
  delete_request_store: filesystem

limits_config:
  allow_structured_metadata: true
  volume_enabled: true
  discover_log_levels: true
  # Every env except prod falls back to the short default (spec 9.1).
  retention_period: ${LOKI_RETENTION_DEFAULT}
  retention_stream:
    - selector: '{env="prod"}'
      priority: 1
      period: ${LOKI_RETENTION_PROD}
  # Only project, env and service.name become index labels; ignore_defaults stops Loki from
  # indexing service.instance.id and friends. trace_id/span_id of OTLP log records are stored as
  # structured metadata by Loki itself (spec 6.2).
  otlp_config:
    resource_attributes:
      ignore_defaults: true
      attributes_config:
        - action: index_label
          attributes:
            - project
            - env
            - service.name
        - action: structured_metadata
          attributes:
            - tenant

analytics:
  reporting_enabled: false
```

Si `loki -verify-config` refuse une clé avec Loki 3.7.8, corriger d'après <https://grafana.com/docs/loki/v3.7.x/configure/> jusqu'à ce que le test passe, sans retirer d'exigence du test.

- [ ] **Étape 4 : lancer le test — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_loki_config.py -v`

Attendu : `Ran 5 tests` … `OK` (le validateur affiche « config is valid »).

- [ ] **Étape 5 : commit**

```bash
git add harness/harness.env tests/support.py config/loki/loki.yaml tests/test_loki_config.py
git commit -m "feat(loki): add loki config with otlp labels and per-env retention"
```


### Task 5: Configuration Tempo

Tempo 2.x monolithique : récepteur OTLP (4317/4318 sur `BIND_ADDR`), rétention
`compactor.compaction.block_retention`, metrics-generator (`span-metrics` et `service-graphs`
activés dans `overrides.defaults`), dimensions `[project, env, tenant, http.route]`, plafond
`max_active_series`, remote write vers `prometheus:9090` avec `send_exemplars` piloté par
`ENABLE_EXEMPLARS`. Deux pièges vérifiés sur Tempo 2.10.8 : le flag de validation exige une
valeur (`-config.verify=true`, sinon « flag needs an argument ») ; en mono-binaire Tempo
enregistre ses anneaux sur `127.0.0.1`, donc son gRPC interne (9096) et le `frontend_address`
du querier doivent être sur `127.0.0.1`, sinon toute lecture de trace échoue en HTTP 500
(« connection refused 127.0.0.1:9096 »).

**Files:**

- Create: `config/tempo/tempo.yaml`
- Test: `tests/test_tempo_config.py`

**Interfaces:**

- Consumes : `tests/support.py` : `binary`, `run`, `validator_env`.
- Produces : `config/tempo/tempo.yaml` : variables `BIND_ADDR`, `TEMPO_DATA_DIR`, `TEMPO_RETENTION`, `TEMPO_MAX_ACTIVE_SERIES`, `ENABLE_EXEMPLARS` ; ports 3200, 4317, 4318 (`BIND_ADDR`), 9096 (`127.0.0.1`).

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_tempo_config.py` :

```python
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run, validator_env

CONFIG = ROOT / "config" / "tempo" / "tempo.yaml"


class TempoConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, path):
        cmd = [binary("tempo"), f"-config.file={path}", "-config.expand-env=true", "-config.verify=true"]
        return run(cmd, env=validator_env(self.tmp.name))

    def test_official_validator_accepts_config(self):
        result = self.verify(CONFIG)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_official_validator_rejects_broken_config(self):
        broken = Path(self.tmp.name) / "broken.yaml"
        broken.write_text(CONFIG.read_text(encoding="utf-8").replace("block_retention", "block_retentionn"), encoding="utf-8")
        result = self.verify(broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("block_retentionn", result.stdout)

    def test_otlp_receiver_binds_to_bind_addr(self):
        protocols = self.doc["distributor"]["receivers"]["otlp"]["protocols"]
        self.assertEqual(protocols["grpc"]["endpoint"], "${BIND_ADDR}:4317")
        self.assertEqual(protocols["http"]["endpoint"], "${BIND_ADDR}:4318")
        self.assertEqual(self.doc["server"]["http_listen_address"], "${BIND_ADDR}")

    def test_retention(self):
        self.assertEqual(self.doc["compactor"]["compaction"]["block_retention"], "${TEMPO_RETENTION}")

    def test_metrics_generator(self):
        generator = self.doc["metrics_generator"]
        self.assertEqual(generator["processor"]["span_metrics"]["dimensions"], ["project", "env", "tenant", "http.route"])
        remote_write = generator["storage"]["remote_write"]
        self.assertEqual(remote_write, [{"url": "http://prometheus:9090/api/v1/write", "send_exemplars": "${ENABLE_EXEMPLARS}"}])
        defaults = self.doc["overrides"]["defaults"]["metrics_generator"]
        self.assertEqual(defaults["processors"], ["span-metrics", "service-graphs"])
        self.assertEqual(defaults["max_active_series"], "${TEMPO_MAX_ACTIVE_SERIES}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_tempo_config.py -v`

Attendu : ERROR « FileNotFoundError … config/tempo/tempo.yaml ».

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `config/tempo/tempo.yaml` :

```yaml
# Tempo 2.x monolithic configuration for grafana-coolify.
# Environment variables are expanded by Tempo itself (-config.expand-env=true); compose.template.yaml
# sets every one of them (BIND_ADDR, TEMPO_DATA_DIR, TEMPO_RETENTION, TEMPO_MAX_ACTIVE_SERIES, ENABLE_EXEMPLARS).
stream_over_http_enabled: true

server:
  http_listen_address: ${BIND_ADDR}
  http_listen_port: 3200
  # Internal module-to-module gRPC only; single-binary Tempo registers its rings on 127.0.0.1.
  grpc_listen_address: 127.0.0.1
  grpc_listen_port: 9096
  log_level: info

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: ${BIND_ADDR}:4317
        http:
          endpoint: ${BIND_ADDR}:4318

ingester:
  max_block_duration: 5m
  lifecycler:
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1

querier:
  frontend_worker:
    frontend_address: 127.0.0.1:9096

compactor:
  compaction:
    # Tempo 2.x key; re-check before any move to Tempo 3.x (spec 9.1).
    block_retention: ${TEMPO_RETENTION}

# Span-metrics and service graphs replace PHP request counters (spec 7.2).
metrics_generator:
  ring:
    kvstore:
      store: inmemory
  processor:
    span_metrics:
      dimensions:
        - project
        - env
        - tenant
        - http.route
    service_graphs:
      dimensions:
        - project
        - env
  registry:
    external_labels:
      source: tempo
  storage:
    path: ${TEMPO_DATA_DIR}/generator/wal
    remote_write:
      - url: http://prometheus:9090/api/v1/write
        # Exemplars need PROM_ENABLE_FEATURES=exemplar-storage on Prometheus (spec 7.3).
        send_exemplars: ${ENABLE_EXEMPLARS}
  traces_storage:
    path: ${TEMPO_DATA_DIR}/generator/traces

storage:
  trace:
    backend: local
    wal:
      path: ${TEMPO_DATA_DIR}/wal
    local:
      path: ${TEMPO_DATA_DIR}/blocks

overrides:
  defaults:
    metrics_generator:
      processors:
        - span-metrics
        - service-graphs
      max_active_series: ${TEMPO_MAX_ACTIVE_SERIES}

usage_report:
  reporting_enabled: false
```

Si `tempo -config.verify=true` refuse une clé avec Tempo 2.10.8, corriger d'après <https://grafana.com/docs/tempo/v2.10.x/configuration/> jusqu'à ce que le test passe.

- [ ] **Étape 4 : lancer le test — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_tempo_config.py -v`

Attendu : `Ran 5 tests` … `OK`.

- [ ] **Étape 5 : commit**

```bash
git add config/tempo/tempo.yaml tests/test_tempo_config.py
git commit -m "feat(tempo): add tempo config with metrics generator"
```


### Task 6: Configuration Prometheus

`prometheus.yml` porte la promotion OTLP (`project`, `env`, `tenant` ; `service.name` devient
toujours `job`), la fenêtre hors ordre de 30 min et le scrape interne de la stack (§ 7.4). Les
flags (récepteurs OTLP et remote write, rétention, fonctionnalités) sont dans le `command:` du
compose (tâche 7). La cible de Prometheus lui-même est `prometheus:9090`, pas `localhost:9090` :
sur le banc, Prometheus n'écoute que sur sa propre adresse.

**Files:**

- Create: `config/prometheus/prometheus.yml`
- Test: `tests/test_prometheus_config.py`

**Interfaces:**

- Consumes : `tests/support.py` : `binary`, `run`.
- Produces : `config/prometheus/prometheus.yml` : jobs `prometheus`, `loki`, `tempo`, `alloy`, `alloy-gateway`, `node-exporter`.

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_prometheus_config.py` :

```python
import tempfile
import unittest
from pathlib import Path

import yaml

from support import ROOT, binary, run

CONFIG = ROOT / "config" / "prometheus" / "prometheus.yml"
EXPECTED_TARGETS = {
    "prometheus": "prometheus:9090",
    "loki": "loki:3100",
    "tempo": "tempo:3200",
    "alloy": "alloy:12345",
    "alloy-gateway": "alloy-gateway:12345",
    "node-exporter": "node-exporter:9100",
}


class PrometheusConfigTest(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    def test_promtool_accepts_config(self):
        result = run([binary("promtool"), "check", "config", CONFIG])
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_promtool_rejects_broken_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "prometheus.yml"
            broken.write_text(CONFIG.read_text(encoding="utf-8").replace("promote_resource_attributes", "promote_attributes"), encoding="utf-8")
            self.assertNotEqual(run([binary("promtool"), "check", "config", broken]).returncode, 0)

    def test_otlp_promotion(self):
        self.assertEqual(self.doc["otlp"]["promote_resource_attributes"], ["project", "env", "tenant"])
        self.assertNotIn("service.name", self.doc["otlp"]["promote_resource_attributes"])

    def test_out_of_order_window(self):
        self.assertEqual(self.doc["storage"]["tsdb"]["out_of_order_time_window"], "30m")

    def test_scrape_targets(self):
        jobs = {job["job_name"]: job["static_configs"][0]["targets"] for job in self.doc["scrape_configs"]}
        self.assertEqual(jobs, {name: [target] for name, target in EXPECTED_TARGETS.items()})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_prometheus_config.py -v`

Attendu : ERROR « FileNotFoundError … config/prometheus/prometheus.yml ».

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `config/prometheus/prometheus.yml` :

```yaml
# Prometheus configuration for grafana-coolify.
# Flags (listen address, OTLP and remote-write receivers, retention, feature flags) are in the
# `command:` of compose.template.yaml: that is the only place Compose interpolates variables.
global:
  scrape_interval: 30s
  evaluation_interval: 30s

# OTLP: project, env and tenant become labels instead of ending in target_info.
# service.name is always translated to `job` (spec 6.2).
otlp:
  promote_resource_attributes:
    - project
    - env
    - tenant

storage:
  tsdb:
    out_of_order_time_window: 30m

# Internal scrape of the stack (spec 7.4). Names resolve inside the package network.
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["prometheus:9090"]
  - job_name: loki
    static_configs:
      - targets: ["loki:3100"]
  - job_name: tempo
    static_configs:
      - targets: ["tempo:3200"]
  - job_name: alloy
    static_configs:
      - targets: ["alloy:12345"]
  - job_name: alloy-gateway
    static_configs:
      - targets: ["alloy-gateway:12345"]
  - job_name: node-exporter
    static_configs:
      - targets: ["node-exporter:9100"]
```

- [ ] **Étape 4 : lancer le test — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_prometheus_config.py -v`

Attendu : `Ran 5 tests` … `OK` (promtool : « SUCCESS »).

Run : `.bin/ruff check .`

Attendu : « All checks passed! ».

- [ ] **Étape 5 : commit**

```bash
git add config/prometheus/prometheus.yml tests/test_prometheus_config.py
git commit -m "feat(prometheus): add prometheus config with otlp promotion and stack scrape"
```


### Task 7: Gabarit compose (stockage) et contrôles statiques `check.py`

Premier gabarit : `config-guard`, `loki`, `tempo`, `prometheus`, `node-exporter`. Les services
Alloy et `grafana-setup` sont ajoutés par les tâches 9 et 16, avec leurs variables. Décision du
plan, vérifiée sur le budget : `config-guard` monte en lecture seule le **dossier `./config`**
où Coolify écrit les fichiers `content:` des autres services, au lieu de porter une seconde copie
de chaque contenu. Il vérifie ainsi les fichiers réellement montés par les services, et le
compose final pèse 77,6 Ko en base64 au lieu de 139,9 Ko (au-delà du budget de 120 Kio). Son
propre script est vérifié par l'empreinte inscrite dans sa commande. Tous les montages `content:`
sont `read_only: true`. `check.py` réalise les huit contrôles du § 12.1 : `docker compose
config` tourne sur la variante sans `content:` (le CLI Docker valide sans démon), sur
`compose.dev.yaml` aussi ; les variables `${VAR}` sont lues hors commentaires ; le mot `key`
seul n'est pas un motif de secret (`{"key": "service.name"}` est un champ de données), seulement
en suffixe (`api_key`, `FARO_API_KEY`).

**Files:**

- Create: `compose.template.yaml`
- Create: `.env.example`
- Create: `scripts/check.py`
- Create: `docker-compose.yaml (généré par render.py)`
- Create: `compose.dev.yaml (généré par render.py)`
- Modify: `tests/test_render.py`
- Test: `tests/test_check.py`

**Interfaces:**

- Consumes : `scripts/render.py` (tâche 2) : `render_text`, `outputs`, `scan_template`, `load_versions`, `HEADER`, `BUDGET_BYTES`, `base64_size`, `RenderError`.
- Consumes : `config/*` (tâches 3 à 6), `harness/harness.env` (tâche 4), `.bin/` (tâche 1).
- Produces : `compose.template.yaml` : services `config-guard` (`alpine`, `restart: "no"`, `./config` monté sur `/guard` en lecture seule), `loki` (`expose` 3100), `tempo` (3200, 4317), `prometheus` (9090), `node-exporter` (9100, `/proc`, `/sys`, `/` en lecture seule) ; tous dépendent de `config-guard` (`service_completed_successfully`) ; `BIND_ADDR: 0.0.0.0` ; aucun `ports:` ; volumes `loki-data`, `tempo-data`, `prometheus-data`.
- Produces : `scripts/check.py` (stdlib) : `DOC_ONLY_VARS = {'ALLOY_INTERNAL_URL'}`, `env_example_keys(text)`, `template_variables(text)`, `env_var_mismatches(template_text, env_example_text) -> [str]`, `is_literal_secret(value)`, `find_hardcoded_secrets(text, name) -> [str]`, `port_violations(compose_json) -> [str]`, `compose_json()` (mis en cache), CLI `python3 scripts/check.py [--only render,size,compose,ports,env,secrets,validators,lint]`.

- [ ] **Étape 1 : écrire les tests qui échouent**

Fichier complet `tests/test_check.py` :

```python
import sys
import unittest

from support import ROOT, run

sys.path.insert(0, str(ROOT / "scripts"))
import check  # noqa: E402


class SecretScanTest(unittest.TestCase):
    def findings(self, text):
        return check.find_hardcoded_secrets(text, "f")

    def test_literal_values_are_flagged(self):
        for line in [
            "password: hunter2",
            'api_key = "abc"',
            "  token: 'glsa_abc123'",
            "FARO_API_KEY=k3yValue123",
            "ip_hash_salt: s4ltValue99",
            '"secret": "x"',
        ]:
            with self.subTest(line=line):
                self.assertEqual(len(self.findings(line)), 1)

    def test_references_and_code_are_not_flagged(self):
        for line in [
            'api_key = coalesce(sys.env("FARO_API_KEY"), sys.env("IP_HASH_SALT") + "-faro-disabled")',
            "password: ${GRAFANA_PASSWORD}",
            'token = os.environ.get("GRAFANA_SA_TOKEN", "")',
            "self.token = token",
            '"tags": [{"key": "service.name", "value": "service"}],',
            '`keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,',
            "# password: hunter2 (comment)",
            "// api_key = \"abc\" (comment)",
            "token:",
            'if ! matches "${IP_HASH_SALT:-}" \'^[A-Za-z0-9]{16,}$\'; then',
        ]:
            with self.subTest(line=line):
                self.assertEqual(self.findings(line), [])


class EnvVarTest(unittest.TestCase):
    TEMPLATE = """# comment with ${IGNORED}
services:
  a:
    environment:
      SERVICE_FQDN_A_80:
      X: ${X:-1}
      Y: ${Y:?required}
    command: ["--flag=${Z}"]
"""

    def test_exact_match(self):
        env = "SERVICE_FQDN_A_80=\nX=1\nY=\nZ=\nALLOY_INTERNAL_URL=\n"
        self.assertEqual(check.env_var_mismatches(self.TEMPLATE, env), [])

    def test_missing_and_unused(self):
        errors = check.env_var_mismatches(self.TEMPLATE, "X=1\nY=\nZ=\nEXTRA=\n")
        self.assertIn("SERVICE_FQDN_A_80: used in compose.template.yaml but missing from .env.example", errors)
        self.assertIn("EXTRA: in .env.example but unused by compose.template.yaml", errors)
        self.assertFalse(any("IGNORED" in e for e in errors))

    def test_comments_in_env_example_are_ignored(self):
        env = "# X=commented\nSERVICE_FQDN_A_80=\nX=1\nY=\nZ=\n"
        self.assertEqual(check.env_var_mismatches(self.TEMPLATE, env), [])


class PortsTest(unittest.TestCase):
    def test_ports_are_refused(self):
        compose = {"services": {"loki": {"expose": ["3100"]}, "bad": {"ports": [{"target": 3100, "published": "3100"}]}}}
        self.assertEqual(len(check.port_violations(compose)), 1)
        self.assertEqual(check.port_violations({"services": {"loki": {"expose": ["3100"]}}}), [])


class RepositoryCheckTest(unittest.TestCase):
    def test_check_py_passes_on_the_repository(self):
        result = run([sys.executable, ROOT / "scripts" / "check.py"], timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("[FAIL]", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

Dans `tests/test_render.py`, repérer le texte exact suivant (unique dans le fichier) — le bloc commence par deux lignes vides :

```python
if __name__ == "__main__":
```

et insérer juste **avant** lui :

```python


class RepositoryRenderTest(unittest.TestCase):
    """The committed docker-compose.yaml carries every config file byte for byte (spec 12.2)."""

    def test_every_content_block_matches_its_source(self):
        doc = yaml.safe_load((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
        seen = 0
        for name, service in doc["services"].items():
            for volume in service.get("volumes", []):
                if isinstance(volume, dict) and "content" in volume:
                    source = ROOT / volume["source"]
                    with self.subTest(service=name, source=volume["source"]):
                        self.assertEqual(volume["content"].encode("utf-8"), source.read_bytes())
                    seen += 1
        self.assertGreaterEqual(seen, 1)

    def test_header_marks_the_file_as_generated(self):
        self.assertTrue((ROOT / "docker-compose.yaml").read_text(encoding="utf-8").startswith("# GENERATED — DO NOT EDIT"))
```

- [ ] **Étape 2 : lancer les tests — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_check.py -v; python3 -m unittest discover -s tests -p test_render.py -v`

Attendu : ERROR « No module named 'check' », puis ERROR « FileNotFoundError … docker-compose.yaml » dans `RepositoryRenderTest`.

- [ ] **Étape 3 : écrire le gabarit et `.env.example`**

Fichier complet `compose.template.yaml` :

```yaml
# grafana-coolify — compose template (source of docker-compose.yaml; run python3 scripts/render.py).
# - Each content placeholder is replaced by the bytes of the volume's `source:` file (spec 4.1).
# - Image tags come from tools/versions.env; config-guard gets the SHA-256 of every config file.
# - `${VAR}` references are interpolated by Compose/Coolify and must all be in .env.example.
# - Internal services only `expose:` ports, never `ports:` (spec 3.2).
# - Memory envelope: ~5.7 GiB of mem_limit in total (spec 9.4).
services:
  config-guard:
    image: @@ALPINE_IMAGE@@
    restart: "no"
    command:
      - sh
      - -c
      - "echo '@@GUARD_SHA256@@  /opt/config-guard/guard.sh' | sha256sum -c - && exec sh /opt/config-guard/guard.sh"
    environment:
      CONFIG_GUARD_EXPECTED: "@@CONFIG_GUARD_EXPECTED@@"
      IP_HASH_SALT: ${IP_HASH_SALT:?IP_HASH_SALT is required}
      HOST_MAP: ${HOST_MAP:-}
      RESERVED_SUBDOMAINS: ${RESERVED_SUBDOMAINS:-}
      TENANT_HOST_REGEX: ${TENANT_HOST_REGEX:-}
    volumes:
      - type: bind
        source: ./config/config-guard/guard.sh
        target: /opt/config-guard/guard.sh
        content: "@@CONTENT@@"
        read_only: true
      # The host directory where Coolify writes the content of every service below:
      # config-guard checks the very files the services mount (spec 4.4).
      - type: bind
        source: ./config
        target: /guard
        read_only: true
    mem_limit: 32m
    cpus: 0.1

  loki:
    image: grafana/loki:@@LOKI_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    command:
      - -config.file=/etc/loki/loki.yaml
      - -config.expand-env=true
    environment:
      BIND_ADDR: 0.0.0.0
      LOKI_DATA_DIR: /loki
      LOKI_RETENTION_PROD: ${LOKI_RETENTION_PROD:-720h}
      LOKI_RETENTION_DEFAULT: ${LOKI_RETENTION_DEFAULT:-168h}
    volumes:
      - type: bind
        source: ./config/loki/loki.yaml
        target: /etc/loki/loki.yaml
        content: "@@CONTENT@@"
        read_only: true
      - loki-data:/loki
    expose:
      - "3100"
    mem_limit: 1536m
    cpus: 1.0

  tempo:
    image: grafana/tempo:@@TEMPO_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    command:
      - -config.file=/etc/tempo/tempo.yaml
      - -config.expand-env=true
    environment:
      BIND_ADDR: 0.0.0.0
      TEMPO_DATA_DIR: /var/tempo
      TEMPO_RETENTION: ${TEMPO_RETENTION:-168h}
      TEMPO_MAX_ACTIVE_SERIES: ${TEMPO_MAX_ACTIVE_SERIES:-100000}
      ENABLE_EXEMPLARS: ${ENABLE_EXEMPLARS:-false}
    volumes:
      - type: bind
        source: ./config/tempo/tempo.yaml
        target: /etc/tempo/tempo.yaml
        content: "@@CONTENT@@"
        read_only: true
      - tempo-data:/var/tempo
    expose:
      - "3200"
      - "4317"
    mem_limit: 1536m
    cpus: 1.0

  prometheus:
    image: prom/prometheus:v@@PROMETHEUS_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --web.listen-address=0.0.0.0:9090
      - --web.enable-otlp-receiver
      - --web.enable-remote-write-receiver
      - --storage.tsdb.retention.time=${PROM_RETENTION_TIME:-90d}
      - --storage.tsdb.retention.size=${PROM_RETENTION_SIZE:-100GB}
      - --enable-feature=${PROM_ENABLE_FEATURES:-}
    volumes:
      - type: bind
        source: ./config/prometheus/prometheus.yml
        target: /etc/prometheus/prometheus.yml
        content: "@@CONTENT@@"
        read_only: true
      - prometheus-data:/prometheus
    expose:
      - "9090"
    mem_limit: 1536m
    cpus: 1.0

  node-exporter:
    image: quay.io/prometheus/node-exporter:v@@NODE_EXPORTER_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    command:
      - --path.procfs=/host/proc
      - --path.sysfs=/host/sys
      - --path.rootfs=/host/root
      - --web.listen-address=0.0.0.0:9100
    volumes:
      - type: bind
        source: /proc
        target: /host/proc
        read_only: true
      - type: bind
        source: /sys
        target: /host/sys
        read_only: true
      - type: bind
        source: /
        target: /host/root
        read_only: true
    expose:
      - "9100"
    mem_limit: 64m
    cpus: 0.2

volumes:
  loki-data:
  tempo-data:
  prometheus-data:
```

Fichier complet `.env.example` :

```dotenv
# grafana-coolify — variables d'environnement (spec § 11). Ce fichier fait foi :
# scripts/check.py vérifie qu'il correspond exactement aux ${VAR} de compose.template.yaml.
# Format : CLE=valeur, sans guillemets. Les valeurs vides prennent le défaut indiqué.

# --- Masquage (obligatoire) ---
# Sel de l'empreinte SHA-256 des IP : au moins 16 caractères [A-Za-z0-9].
IP_HASH_SALT=

# --- Déduction depuis l'hôte (logs Faro, spec § 6.4) ---
# HOST_MAP : hote=service:env séparés par des virgules, en minuscules.
HOST_MAP=
# RESERVED_SUBDOMAINS : sous-domaines qui ne sont jamais des tenants (ex. www,api).
RESERVED_SUBDOMAINS=
# TENANT_HOST_REGEX : RE2 avec les groupes nommés sub (obligatoire) et dev (optionnel).
# À cocher « Is Literal? » dans Coolify : la valeur contient des $.
TENANT_HOST_REGEX=

# --- Rétention ---
LOKI_RETENTION_PROD=720h
LOKI_RETENTION_DEFAULT=168h
TEMPO_RETENTION=168h
TEMPO_MAX_ACTIVE_SERIES=100000
PROM_RETENTION_TIME=90d
PROM_RETENTION_SIZE=100GB

# --- Exemplars (expérimental, désactivé par défaut) ---
# Activer les deux ensemble : PROM_ENABLE_FEATURES=exemplar-storage et ENABLE_EXEMPLARS=true.
PROM_ENABLE_FEATURES=
ENABLE_EXEMPLARS=false

# --- Adresse interne d'alloy pour les apps du même hôte (documentaire, lue par personne) ---
ALLOY_INTERNAL_URL=
```

- [ ] **Étape 4 : écrire `check.py` et générer les composes**

Fichier complet `scripts/check.py` :

```python
#!/usr/bin/env python3
"""Static checks of spec 12.1. Standard library only; external tools: docker compose and .bin/.

Usage: python3 scripts/check.py [--only render,size,compose,ports,env,secrets,validators,lint]
"""

import argparse
import functools
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
VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:?+-][^}]*)?\}")
# `SERVICE_FQDN_ALLOY_12347:` with no value: Coolify magic variable declared in `environment:`.
NULL_ENV_KEY_RE = re.compile(r"^\s+([A-Z][A-Z0-9_]*):\s*$")
# Spec 12.1.6: token|password|secret|salt|key. "key" alone is a common data field name
# ({"key": "service.name"}), so it only counts as a suffix: api_key, FARO_API_KEY, private-key.
SECRET_RE = re.compile(
    r"(?i)(?P<key>[a-z0-9_.-]*(?:token|password|secret|salt|[_.-]key|apikey)[a-z0-9_.-]*)[\"']?\s*[:=]\s*(?P<value>\S.*)$"
)
BARE_SECRET_RE = re.compile(r"[A-Za-z0-9+/_=.-]{6,}")


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


def env_var_mismatches(template_text, env_example_text, doc_only=frozenset(DOC_ONLY_VARS)):
    used = template_variables(template_text)
    documented = env_example_keys(env_example_text)
    errors = [f"{name}: used in compose.template.yaml but missing from .env.example" for name in sorted(used - documented)]
    errors += [f"{name}: in .env.example but unused by compose.template.yaml" for name in sorted(documented - used - set(doc_only))]
    errors += [f"{name}: declared documentary but used by the template" for name in sorted(set(doc_only) & used)]
    return errors


def is_literal_secret(value):
    value = value.strip().rstrip(",;")
    if not value:
        return False
    if value[0] in "\"'":
        quote = value[0]
        end = value.find(quote, 1)
        content = value[1:end] if end > 0 else value[1:]
        return bool(content) and "$" not in content and "{{" not in content
    token = value.split()[0].rstrip(",;")
    if token.startswith("$") or not BARE_SECRET_RE.fullmatch(token):
        return False
    return any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def find_hardcoded_secrets(text, name):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            continue
        match = SECRET_RE.search(line)
        if match and is_literal_secret(match.group("value")):
            findings.append(f"{name}:{number}: literal value for '{match.group('key')}'")
    return findings


def port_violations(compose_json):
    return [f"{name}: declares ports: {service['ports']}" for name, service in sorted(compose_json["services"].items()) if service.get("ports")]


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


def check_render():
    errors = []
    for path, text in render.outputs(ROOT).items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            errors.append(f"{path.name} is stale: run python3 scripts/render.py")
    return errors


def check_size():
    size = render.base64_size((ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))
    if size > render.BUDGET_BYTES:
        return [f"docker-compose.yaml is {size} bytes in base64, budget is {render.BUDGET_BYTES}"]
    print(f"    base64 size {size} / {render.BUDGET_BYTES} bytes")
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


def check_validators():
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    sources = [source for _svc, _indent, source, _target, _index in render.scan_template(template)]
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env.update(load_env(ROOT / "harness" / "harness.env"))
        env.update({"BIND_ADDR": "127.0.0.1", "LOKI_DATA_DIR": f"{tmp}/loki", "TEMPO_DATA_DIR": f"{tmp}/tempo", "ALLOY_QUEUE_DIR": f"{tmp}/queue"})
        for cmd in validator_commands(sources):
            code, output = run(cmd, env=env)
            if code != 0:
                errors.append(f"{Path(cmd[0]).name} rejected {cmd[-1] if 'check' in cmd else cmd[1:]}:\n{output.strip()}")
    return errors


def check_lint():
    errors = []
    code, output = run([tool("ruff"), "check", "."])
    if code != 0:
        errors.append(f"ruff:\n{output.strip()}")
    for cmd in (
        [tool("shellcheck"), "-x", "tools/fetch-binaries.sh"],
        [tool("shellcheck"), "-s", "sh", "config/config-guard/guard.sh"],
    ):
        code, output = run(cmd)
        if code != 0:
            errors.append(f"shellcheck {cmd[-1]}:\n{output.strip()}")
    return errors


CHECKS = {
    "render": check_render,
    "size": check_size,
    "compose": check_compose,
    "ports": check_ports,
    "env": check_env,
    "secrets": check_secrets,
    "validators": check_validators,
    "lint": check_lint,
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
```

Run : `chmod +x scripts/check.py && python3 scripts/render.py`

Attendu : « render: wrote docker-compose.yaml », « render: wrote compose.dev.yaml », puis la taille base64 (bien sous le budget 122880).

- [ ] **Étape 5 : lancer les contrôles et les tests — succès attendu**

Run : `python3 scripts/check.py`

Attendu : huit lignes `check: [PASS] …` (render, size, compose, ports, env, secrets, validators, lint), code 0.

Run : `python3 -m unittest discover -s tests -v`

Attendu : tous les tests `OK` (dont `test_check_py_passes_on_the_repository` et `RepositoryRenderTest`).

- [ ] **Étape 6 : prouver qu'un contrôle échoue sur un compose cassé, puis revenir**

Run :
```bash
python3 - <<'EOF'
p = 'compose.template.yaml'
s = open(p).read()
s = s.replace('      - "3100"\n', '      - "3100"\n    ports:\n      - "3100:3100"\n', 1)
open(p, 'w').write(s)
EOF
python3 scripts/render.py >/dev/null && python3 scripts/check.py --only ports
```

Attendu : `check: [FAIL] ports` avec « loki: declares ports », code 1.

Run :
```bash
python3 - <<'EOF'
p = 'compose.template.yaml'
s = open(p).read()
s = s.replace('    ports:\n      - "3100:3100"\n', '', 1)
open(p, 'w').write(s)
EOF
python3 scripts/render.py >/dev/null && python3 scripts/check.py --only ports,render
```

Attendu : `check: [PASS] ports` et `check: [PASS] render` : le gabarit est revenu à l'identique.

- [ ] **Étape 7 : commit**

```bash
git add compose.template.yaml .env.example scripts/check.py docker-compose.yaml compose.dev.yaml tests/test_check.py tests/test_render.py
git commit -m "feat(compose): add compose template for storage services and static checks"
```


### Task 8: Banc natif `harness/stack.py`

Le banc lit le gabarit (via `render.render_text(..., strip_content=True)`) et lance chaque service
avec le binaire officiel, les **mêmes** arguments et variables, sur sa propre adresse
`127.0.10.N`. Règles de traduction : cible de volume → chemin local (fichiers de `./config`,
`.harness/data/<volume>`, `.harness/tmpfs/<service>`), remplacement limité à des segments de
chemin entiers (`http://loki:3100` n'est jamais réécrit), puis `0.0.0.0` → adresse du service.
Trois pièges constatés pendant la préparation du plan, et traités ici : (1) un processus lancé
en arrière-plan meurt avec le shell appelant, d'où `start_new_session=True` ; (2) `pkill -f` tue
le shell qui l'appelle, d'où un suivi strict par PID dans le fichier d'état de `.harness/` ;
(3) une application qui écoute sur `0.0.0.0:<port>` (ici un serveur Next.js sur 3000) intercepte
silencieusement les requêtes destinées au banc, d'où un contrôle préalable par `bind` avec
`SO_REUSEADDR` (les connexions `TIME_WAIT` ne gênent pas, un écouteur si). Les noms des services
sont ajoutés à `/etc/hosts` dans un bloc balisé, retiré par `down`. Loki et Tempo peuvent mettre
jusqu'à ~20 s à être prêts (délai de 15 s de l'ingester).

**Files:**

- Create: `harness/stack.py`
- Test: `tests/test_harness.py`

**Interfaces:**

- Consumes : `scripts/render.py` : `render_text`, `load_versions`, `RenderError`.
- Consumes : `compose.template.yaml` (tâche 7), `harness/harness.env` (tâche 4), `.bin/` (tâche 1), `sudo` sans mot de passe.
- Produces : `harness/stack.py` : `SERVICE_IPS` (`loki` 127.0.10.2, `tempo` .3, `prometheus` .4, `alloy` .5, `alloy-gateway` .6, `node-exporter` .7, `config-guard` .8, `grafana-setup` .9), `ROOT`, `BIN`, `HARNESS` (= `.harness`), `STATE`, `HOSTS`, `HOSTS_BEGIN`, `HarnessError`, `load_compose()`, `load_env_values(sets)` (`harness.env`, puis `.harness/runtime.env` écrit par la tâche 15, puis `--set`), `interpolate(value, env)`, `image_repository(image)`, `mounts(name, service, config_dir)`, `rewriter(mapping, ip)`, `build(name, service, env_values, config_dir) -> {'args','env','ip'}`, `hosts_block()`, `without_block(text)`, `preflight(pairs)`, `http_ok(url)`, `load_state()`, `save_state(state)`, `alive(pid)`, `spawn(name, spec) -> pid`, `run_oneshot(name, spec) -> code`, `terminate(pid)`, `tail(name, lines)`.
- Produces : CLI : `python3 harness/stack.py up [--set K=V]… [--config-dir DIR] [--only a,b]` | `down [--purge]` | `status` | `stop S` | `start S [--set K=V]` | `oneshot config-guard|grafana-setup [--set K=V]` | `logs S [-n N]`. Journaux : `.harness/logs/<service>.log`. `down` supprime aussi `.harness/edge.json` et `.harness/runtime.env`.

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_harness.py` :

```python
import socket
import sys
import unittest
import urllib.request

from support import ROOT, require_harness, run

sys.path.insert(0, str(ROOT / "harness"))
import stack  # noqa: E402

STACK = [sys.executable, str(ROOT / "harness" / "stack.py")]


class InterpolationTest(unittest.TestCase):
    def test_compose_rules(self):
        env = {"SET": "v", "EMPTY": ""}
        cases = {
            "${SET}": "v",
            "${UNSET:-d}": "d",
            "${EMPTY:-d}": "d",
            "${EMPTY-d}": "",
            "$$1 ${SET}": "$1 v",
            "--flag=${EMPTY:-}": "--flag=",
            "$SET": "v",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(stack.interpolate(value, env), expected)

    def test_required_variable(self):
        with self.assertRaisesRegex(stack.HarnessError, "IP_HASH_SALT"):
            stack.interpolate("${IP_HASH_SALT:?IP_HASH_SALT is required}", {"IP_HASH_SALT": ""})


class RewriteTest(unittest.TestCase):
    def test_whole_path_segments_only(self):
        rewrite = stack.rewriter([("/loki", "/data/loki"), ("/guard", "/repo/config"), ("/etc/loki/loki.yaml", "/repo/config/loki/loki.yaml")], "127.0.10.2")
        self.assertEqual(rewrite("-config.file=/etc/loki/loki.yaml"), "-config.file=/repo/config/loki/loki.yaml")
        self.assertEqual(rewrite("/loki"), "/data/loki")
        self.assertEqual(rewrite("/loki/chunks"), "/data/loki/chunks")
        self.assertEqual(rewrite("http://loki:3100"), "http://loki:3100")
        self.assertEqual(rewrite("/lokix"), "/lokix")
        self.assertEqual(rewrite("/guard/a=1;/guard/b=2"), "/repo/config/a=1;/repo/config/b=2")
        self.assertEqual(rewrite("--web.listen-address=0.0.0.0:9090"), "--web.listen-address=127.0.10.2:9090")

    def test_image_repository(self):
        self.assertEqual(stack.image_repository("grafana/loki:3.7.8"), "grafana/loki")
        self.assertEqual(stack.image_repository("quay.io/prometheus/node-exporter:v1.12.1"), "quay.io/prometheus/node-exporter")

    def test_hosts_block_round_trip(self):
        original = "127.0.0.1 localhost\n"
        with_block = original + stack.hosts_block()
        self.assertIn("127.0.10.2 loki", with_block)
        self.assertEqual(stack.without_block(with_block), original)


class StorageTrioTest(unittest.TestCase):
    """The storage trio starts from compose.template.yaml and answers readiness by service name."""

    def setUp(self):
        require_harness(self)
        run([*STACK, "down"], timeout=120)

    def tearDown(self):
        run([*STACK, "down"], timeout=120)

    def test_trio_starts_and_stops(self):
        result = run([*STACK, "up", "--only", "loki,tempo,prometheus"], timeout=400)
        self.assertEqual(result.returncode, 0, result.stdout)
        for url in ("http://loki:3100/ready", "http://tempo:3200/ready", "http://prometheus:9090/-/ready"):
            with self.subTest(url=url), urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        self.assertEqual(run([*STACK, "down"], timeout=120).returncode, 0)
        self.assertNotIn(stack.HOSTS_BEGIN, stack.HOSTS.read_text(encoding="utf-8"))
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(("127.0.10.2", 3100)), 0)

    def test_busy_address_is_reported(self):
        with socket.socket() as blocker:
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            blocker.bind(("127.0.10.2", 3100))
            blocker.listen(1)
            result = run([*STACK, "up", "--only", "loki"], timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("127.0.10.2:3100", result.stdout)
        self.assertIn("already in use", result.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer le test — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_harness.py -v`

Attendu : ERROR « No module named 'stack' ».

- [ ] **Étape 3 : implémentation minimale**

Fichier complet `harness/stack.py` :

```python
#!/usr/bin/env python3
"""Native test bench without Docker (spec 12): run the stack of compose.template.yaml as processes.

Every service runs the official binary (installed by tools/fetch-binaries.sh) with the SAME
command arguments and environment as in the compose, on its own loopback IP. Volume targets are
mapped to local paths (./config files, .harness/data/<volume>, .harness/tmpfs/<service>), and
"0.0.0.0" is replaced by the service IP. /etc/hosts maps the service names to those IPs (sudo),
so the configs keep their in-stack names (loki:3100, tempo:4317...).

Usage:
  python3 harness/stack.py up [--set KEY=VALUE]... [--config-dir DIR] [--only a,b]
  python3 harness/stack.py down [--purge]
  python3 harness/stack.py status
  python3 harness/stack.py stop SERVICE | start SERVICE
  python3 harness/stack.py oneshot SERVICE [--set KEY=VALUE]...
  python3 harness/stack.py logs SERVICE [-n LINES]
"""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import render  # noqa: E402

HARNESS = ROOT / ".harness"
STATE = HARNESS / "state.json"
BIN = Path(os.environ.get("GC_BIN_DIR", str(ROOT / ".bin")))
HOSTS = Path("/etc/hosts")
HOSTS_BEGIN = "# BEGIN grafana-coolify harness"
HOSTS_END = "# END grafana-coolify harness"
SERVICE_IPS = {
    "loki": "127.0.10.2",
    "tempo": "127.0.10.3",
    "prometheus": "127.0.10.4",
    "alloy": "127.0.10.5",
    "alloy-gateway": "127.0.10.6",
    "node-exporter": "127.0.10.7",
    "config-guard": "127.0.10.8",
    "grafana-setup": "127.0.10.9",
}
# Image repository -> binary in .bin/ (the image ENTRYPOINT). Other images run `command:` as is.
BINARIES = {
    "grafana/alloy": "alloy",
    "grafana/loki": "loki",
    "grafana/tempo": "tempo",
    "prom/prometheus": "prometheus",
    "quay.io/prometheus/node-exporter": "node_exporter",
}
READY = {
    "loki": "http://{ip}:3100/ready",
    "tempo": "http://{ip}:3200/ready",
    "prometheus": "http://{ip}:9090/-/ready",
    "alloy": "http://{ip}:12345/-/ready",
    "alloy-gateway": "http://{ip}:12345/-/ready",
    "node-exporter": "http://{ip}:9100/metrics",
}
# Internal gRPC ports pinned to 127.0.0.1 in the Loki and Tempo configs.
EXTRA_PORTS = {"loki": [("127.0.0.1", 9095)], "tempo": [("127.0.0.1", 9096)]}
ONESHOTS = ("config-guard", "grafana-setup")
START_ORDER = ("loki", "tempo", "prometheus", "node-exporter", "alloy", "alloy-gateway")
VAR_RE = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class HarnessError(Exception):
    pass


# ------------------------------------------------------------------ compose model
def load_compose():
    template = (ROOT / "compose.template.yaml").read_text(encoding="utf-8")
    versions = render.load_versions(ROOT / "tools" / "versions.env")
    return yaml.safe_load(render.render_text(template, ROOT, versions, strip_content=True))


def load_env_values(sets):
    values = {}
    for raw in (ROOT / "harness" / "harness.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    # Written by harness/edge.py (e.g. the Grafana service account token of the test Grafana).
    runtime = HARNESS / "runtime.env"
    if runtime.exists():
        for raw in runtime.read_text(encoding="utf-8").splitlines():
            if "=" in raw:
                key, value = raw.split("=", 1)
                values[key] = value
    for item in sets:
        key, _, value = item.partition("=")
        values[key] = value
    return values


def interpolate(value, env):
    """Compose-style interpolation: $$, $VAR, ${VAR}, ${VAR:-default}, ${VAR-default}, ${VAR:?error}."""

    def replace(match):
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(4)
        op, arg = match.group(2), match.group(3)
        current = env.get(name)
        if op in (":-", "-"):
            empty = current is None or (op == ":-" and current == "")
            return arg if empty else current
        if op in (":?", "?"):
            if current is None or (op == ":?" and current == ""):
                raise HarnessError(f"required variable {name}: {arg}")
            return current
        return current or ""

    return VAR_RE.sub(replace, str(value))


def image_repository(image):
    name, _, tag = image.rpartition(":")
    return name if name and "/" not in tag else image


def mounts(name, service, config_dir):
    """[(container_target, local_path)] for every volume and tmpfs of a service."""
    result = []
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            source, target = volume.split(":")[:2]
            spec = {"type": "volume" if not source.startswith((".", "/")) else "bind", "source": source, "target": target}
        else:
            spec = volume
        source, target = spec["source"], spec["target"]
        if spec.get("type") == "bind" and source.startswith("./config"):
            local = Path(config_dir) / source[len("./config"):].lstrip("/")
        elif spec.get("type") == "bind":
            local = Path(source)
        else:
            local = HARNESS / "data" / source
            local.mkdir(parents=True, exist_ok=True)
        result.append((target, str(local)))
    for entry in service.get("tmpfs", []) or []:
        target = entry.split(":")[0]
        local = HARNESS / "tmpfs" / name / target.strip("/").replace("/", "_")
        local.mkdir(parents=True, exist_ok=True)
        result.append((target, str(local)))
    return result


def rewriter(mapping, ip):
    """Replace container paths (whole path segments only) and 0.0.0.0 in a command/env value."""
    targets = sorted((t for t, _ in mapping), key=len, reverse=True)
    local = dict(mapping)
    pattern = re.compile(r"(?<![\w/.:-])(" + "|".join(re.escape(t) for t in targets) + r")(?=$|[/;,\s'\"])") if targets else None

    def rewrite(value):
        if pattern:
            value = pattern.sub(lambda m: local[m.group(1)], value)
        return value.replace("0.0.0.0", ip)

    return rewrite


def build(name, service, env_values, config_dir):
    ip = SERVICE_IPS[name]
    rewrite = rewriter(mounts(name, service, config_dir), ip)
    command = service.get("command") or []
    if isinstance(command, str):
        raise HarnessError(f"{name}: use the list form of command:")
    args = [rewrite(interpolate(arg, env_values)) for arg in command]
    repository = image_repository(service["image"])
    if repository in BINARIES:
        binary = BIN / BINARIES[repository]
        if not binary.exists():
            raise HarnessError(f"{binary} missing: run tools/fetch-binaries.sh")
        args = [str(binary), *args]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(HARNESS / "work" / name), "LANG": "C.UTF-8"}
    for key, value in (service.get("environment") or {}).items():
        if value is not None:
            env[key] = rewrite(interpolate(value, env_values))
    return {"args": args, "env": env, "ip": ip}


# ------------------------------------------------------------------ host plumbing
def hosts_block():
    lines = [HOSTS_BEGIN] + [f"{ip} {name}" for name, ip in SERVICE_IPS.items()] + [HOSTS_END]
    return "\n".join(lines) + "\n"


def without_block(text):
    return re.sub(rf"(?ms)^{re.escape(HOSTS_BEGIN)}\n.*?^{re.escape(HOSTS_END)}\n?", "", text)


def write_hosts(text):
    subprocess.run(["sudo", "-n", "tee", str(HOSTS)], input=text, text=True, stdout=subprocess.DEVNULL, check=True)


def install_hosts():
    current = HOSTS.read_text(encoding="utf-8")
    wanted = without_block(current).rstrip("\n") + "\n" + hosts_block()
    if wanted != current:
        write_hosts(wanted)


def remove_hosts():
    current = HOSTS.read_text(encoding="utf-8")
    if HOSTS_BEGIN in current:
        write_hosts(without_block(current))


def ports_of(name, service):
    ports = [(SERVICE_IPS[name], int(p)) for p in service.get("expose", []) or []]
    return ports + EXTRA_PORTS.get(name, [])


def preflight(pairs):
    """Fail loudly when an address is taken (also by a 0.0.0.0 listener of another program)."""
    busy = []
    for ip, port in pairs:
        with socket.socket() as sock:
            # Same option as the Go servers: TIME_WAIT leftovers are fine, a listener is not.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((ip, port))
                sock.listen(1)
            except OSError as exc:
                busy.append(f"{ip}:{port} ({exc.strerror})")
    if busy:
        raise HarnessError("addresses already in use: " + ", ".join(busy))


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ------------------------------------------------------------------ processes
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"processes": {}, "sets": [], "config_dir": str(ROOT / "config")}


def save_state(state):
    HARNESS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            return stat.read().split(") ")[1].split()[0] != "Z"
    except OSError:
        return False


def log_path(name):
    return HARNESS / "logs" / f"{name}.log"


def tail(name, lines=30):
    path = log_path(name)
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) if path.exists() else ""


def spawn(name, spec):
    (HARNESS / "logs").mkdir(parents=True, exist_ok=True)
    workdir = HARNESS / "work" / name
    workdir.mkdir(parents=True, exist_ok=True)
    with open(log_path(name), "ab") as log:
        proc = subprocess.Popen(
            spec["args"], env=spec["env"], cwd=workdir, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True
        )
    return proc.pid


def run_oneshot(name, spec, timeout=300):
    (HARNESS / "logs").mkdir(parents=True, exist_ok=True)
    with open(log_path(name), "ab") as log:
        result = subprocess.run(
            spec["args"], env=spec["env"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, check=False
        )
    return result.returncode


def wait_ready(name, pid, timeout=180):
    url = READY[name].format(ip=SERVICE_IPS[name])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            raise HarnessError(f"{name} exited during startup:\n{tail(name)}")
        if http_ok(url):
            return
        time.sleep(1)
    raise HarnessError(f"{name} not ready after {timeout}s ({url}):\n{tail(name)}")


def terminate(pid, grace=15):
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and alive(pid):
        time.sleep(0.2)
    if alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


# ------------------------------------------------------------------ commands
def cmd_up(args):
    state = load_state()
    if any(alive(p["pid"]) for p in state["processes"].values()):
        raise HarnessError("harness already running: python3 harness/stack.py down first")
    compose = load_compose()["services"]
    env_values = load_env_values(args.set)
    config_dir = str(Path(args.config_dir).resolve()) if args.config_dir else str(ROOT / "config")
    names = [n for n in START_ORDER if n in compose]
    if args.only:
        names = [n for n in names if n in args.only.split(",")]
    specs = {name: build(name, compose[name], env_values, config_dir) for name in ["config-guard", *names]}
    preflight([pair for name in names for pair in ports_of(name, compose[name])])
    install_hosts()
    state = {"processes": {}, "sets": args.set, "config_dir": config_dir}
    save_state(state)
    code = run_oneshot("config-guard", specs["config-guard"])
    print(f"stack: config-guard exited with {code}")
    if code != 0:
        raise HarnessError(f"config-guard failed, no service started:\n{tail('config-guard')}")
    for name in names:
        pid = spawn(name, specs[name])
        state["processes"][name] = {"pid": pid, "ip": SERVICE_IPS[name]}
        save_state(state)
    for name in names:
        wait_ready(name, state["processes"][name]["pid"])
        print(f"stack: {name} ready on {SERVICE_IPS[name]}")
    return 0


def cmd_down(args):
    state = load_state()
    for name, proc in state["processes"].items():
        terminate(proc["pid"])
        print(f"stack: stopped {name}")
    remove_hosts()
    if args.purge and HARNESS.exists():
        shutil.rmtree(HARNESS)
        return 0
    # STATE plus the files harness/edge.py writes for the edge it started (now stopped).
    for path in (STATE, HARNESS / "edge.json", HARNESS / "runtime.env"):
        if path.exists():
            path.unlink()
    return 0


def cmd_stop(args):
    state = load_state()
    proc = state["processes"].get(args.service)
    if not proc:
        raise HarnessError(f"{args.service} is not managed by the harness")
    terminate(proc["pid"])
    print(f"stack: stopped {args.service}")
    return 0


def cmd_start(args):
    state = load_state()
    compose = load_compose()["services"]
    spec = build(args.service, compose[args.service], load_env_values(state["sets"] + args.set), state["config_dir"])
    if args.service in state["processes"] and alive(state["processes"][args.service]["pid"]):
        raise HarnessError(f"{args.service} is already running")
    pid = spawn(args.service, spec)
    state["processes"][args.service] = {"pid": pid, "ip": SERVICE_IPS[args.service]}
    save_state(state)
    wait_ready(args.service, pid)
    print(f"stack: {args.service} ready on {SERVICE_IPS[args.service]}")
    return 0


def cmd_oneshot(args):
    if args.service not in ONESHOTS:
        raise HarnessError(f"{args.service} is not a one-shot service")
    state = load_state()
    compose = load_compose()["services"]
    spec = build(args.service, compose[args.service], load_env_values(state["sets"] + args.set), state["config_dir"])
    code = run_oneshot(args.service, spec)
    print(tail(args.service, 20))
    print(f"stack: {args.service} exited with {code}")
    return code


def cmd_status(_args):
    state = load_state()
    for name, proc in state["processes"].items():
        running = alive(proc["pid"])
        ready = running and name in READY and http_ok(READY[name].format(ip=proc["ip"]))
        print(f"{name:15} pid={proc['pid']:<8} {'running' if running else 'stopped'}{' ready' if ready else ''}")
    return 0


def cmd_logs(args):
    print(tail(args.service, args.n))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify native harness")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    up.add_argument("--config-dir", help="use this directory instead of ./config (robustness tests)")
    up.add_argument("--only", help="comma-separated subset of services")
    down = sub.add_parser("down")
    down.add_argument("--purge", action="store_true", help="also delete .harness (data, logs)")
    for name in ("stop", "start", "oneshot"):
        command = sub.add_parser(name)
        command.add_argument("service")
        command.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    sub.add_parser("status")
    logs = sub.add_parser("logs")
    logs.add_argument("service")
    logs.add_argument("-n", type=int, default=50)
    args = parser.parse_args(argv)
    handlers = {"up": cmd_up, "down": cmd_down, "stop": cmd_stop, "start": cmd_start, "oneshot": cmd_oneshot, "status": cmd_status, "logs": cmd_logs}
    try:
        return handlers[args.command](args)
    except (HarnessError, render.RenderError, subprocess.CalledProcessError) as exc:
        print(f"stack: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

Run : `chmod +x harness/stack.py`

Attendu : aucune sortie.

- [ ] **Étape 4 : lancer les tests — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_harness.py -v`

Attendu : `Ran 7 tests` … `OK (skipped=2)` : les deux tests du banc demandent `GC_HARNESS=1`.

Run : `GC_HARNESS=1 python3 -m unittest discover -s tests -p test_harness.py -v`

Attendu : `Ran 7 tests` … `OK` (≈ 1 min 30) : le trio démarre depuis le gabarit et répond par son nom de service, `down` retire le bloc `/etc/hosts`, une adresse occupée est signalée « 127.0.10.2:3100 (Address already in use) ».

Run : `python3 harness/stack.py up && python3 harness/stack.py status && tail -n 1 .harness/logs/config-guard.log && python3 harness/stack.py down`

Attendu : `stack: config-guard exited with 0`, `loki`, `tempo`, `prometheus`, `node-exporter` « running ready », puis `config-guard: all checks passed`.

Run : `.bin/ruff check .`

Attendu : « All checks passed! ».

- [ ] **Étape 5 : commit**

```bash
git add harness/stack.py tests/test_harness.py
git commit -m "test(harness): add native harness running compose services as processes"
```


### Task 9: Chemin OTLP : `alloy-gateway`, pipeline de base d'`alloy` et premiers contrôles de bout en bout

`alloy-gateway` relaie l'OTLP/HTTP vers `alloy:4317` sans traitement et **sans file** :
`sending_queue` et `retry_on_failure` désactivés, pour que l'émetteur reçoive l'erreur si `alloy`
est indisponible (§ 9.2). `alloy` reçoit l'OTLP, passe par `memory_limiter`, raccourcit
l'environnement (`deployment.environment.name` ou l'ancien `deployment.environment` → `env`,
original supprimé), rejette ce qui n'a pas `project` ou `env`, groupe, puis exporte vers Tempo,
Loki (`/otlp`) et Prometheus (`/api/v1/otlp`) avec une file persistante
(`otelcol.storage.file`, `max_elapsed_time = "1h"` pour survivre à une indisponibilité longue).
Les deux instances tournent en `--stability.level=public-preview` : `alloy validate` refuse tout
composant de niveau inférieur. Les compteurs de rejet réels portent le suffixe `_total`
(`otelcol_processor_filter_logs_filtered_total`…). Les envois de test utilisent OTLP/HTTP JSON
en bibliothèque standard : `telemetrygen` n'a pas de binaire publié dans
`opentelemetry-collector-releases` v0.161.0 (vérifié) et `go` n'est pas disponible.

**Files:**

- Create: `scripts/gclib.py`
- Create: `scripts/smoke.py`
- Create: `config/alloy-gateway/config.alloy`
- Create: `config/alloy/config.alloy`
- Modify: `compose.template.yaml`
- Modify: `.env.example`
- Modify: `docker-compose.yaml, compose.dev.yaml (régénérés)`

**Interfaces:**

- Consumes : `harness/stack.py` (tâche 8) : `up`, `down`, `stop`, `start` ; noms `alloy`, `alloy-gateway`, `loki`, `tempo`, `prometheus` résolus par `/etc/hosts`.
- Consumes : `scripts/render.py`, `scripts/check.py` (tâches 2 et 7).
- Produces : `scripts/gclib.py` (stdlib) : `settings()` (valeurs de `harness.env`, puis `.harness/edge.json`, puis variables `GC_*` : `GC_OTLP_URL`, `GC_GATEWAY_URL`, `GC_GATEWAY_HOST`, `GC_GATEWAY_USER`, `GC_GATEWAY_PASSWORD`, `GC_FARO_URL`, `GC_LOKI_URL`, `GC_TEMPO_URL`, `GC_PROM_URL`, `GC_ALLOY_METRICS_URL`), `Response`, `http(method, url, body=None, headers=None, host=None, auth=None, timeout=15)`, `get_json`, `wait_for(fetch, what, timeout, interval)`, `run_id`, `new_trace_id`, `new_span_id`, `attrs`, `otlp_logs`, `otlp_traces`, `span`, `otlp_sum`, `send_otlp`, `faro_payload`, `faro_log`, `send_faro`, `loki_entries(loki_url, query) -> [(labels, line, metadata)]` (en-tête `categorize-labels`), `tempo_trace` (None si absente : Tempo répond 200 et une trace vide), `otlp_attr_map`, `trace_resources_and_spans`, `prom_query`, `metric_value`, `scrape`, `duration_seconds`, `size_bytes`, `yaml_section_value`.
- Produces : `scripts/smoke.py` : registre `SECTIONS` rempli par `@section("nom")`, classe `Ctx` (`run`, `project`, `resource()`, `gateway()`, `faro_app()`, `logs()`, `wait_logs()`, `wait_trace()`, `wait_prom()`, `ip_hash(ip)`), `expect(cond, msg)`, `INDEXED`, marqueur d'insertion `# --- end of sections ---`, CLI `python3 scripts/smoke.py [--only a,b] [--list]`. Sections de cette tâche : `otlp-names`, `reject`.
- Produces : `config/alloy/config.alloy` : variables `BIND_ADDR`, `ALLOY_QUEUE_DIR` ; ports 4317, 4318 (`BIND_ADDR`), 12345 (flag `--server.http.listen-addr`).
- Produces : Gabarit : services `alloy` (utilisateur `473:473`, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, volume `alloy-data:/var/lib/alloy`, label `coolify.traefik.middlewares=gc-faro-ratelimit@file,gc-faro-cors@file,gc-faro-body@file`, `SERVICE_FQDN_ALLOY_12347`) et `alloy-gateway` (tmpfs `/var/lib/alloy:mode=1777`, label `coolify.traefik.middlewares=gc-otlp-auth@file`, `SERVICE_FQDN_ALLOY_GATEWAY_4318`).

- [ ] **Étape 1 : écrire les contrôles de bout en bout qui échouent**

Fichier complet `scripts/gclib.py` :

```python
"""Shared helpers for scripts/smoke.py and scripts/security.py (standard library only).

Endpoints default to the native harness (service names resolved by /etc/hosts) and can be
overridden with GC_* environment variables to run against a real deployment.
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env_file(path):
    values = {}
    if Path(path).exists():
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def settings():
    """Stack values: harness/harness.env, then .harness/edge.json, then GC_* overrides."""
    values = env_file(ROOT / "harness" / "harness.env")
    edge_path = ROOT / ".harness" / "edge.json"
    edge = json.loads(edge_path.read_text(encoding="utf-8")) if edge_path.exists() else {}
    defaults = {
        "GC_OTLP_URL": "http://alloy:4318",
        "GC_GATEWAY_URL": edge.get("traefik_url", "http://alloy-gateway:4318"),
        "GC_GATEWAY_HOST": edge.get("hosts", {}).get("alloy-gateway", ""),
        "GC_GATEWAY_USER": edge.get("user", ""),
        "GC_GATEWAY_PASSWORD": edge.get("password", ""),
        "GC_FARO_URL": "http://alloy:12347",
        "GC_LOKI_URL": "http://loki:3100",
        "GC_TEMPO_URL": "http://tempo:3200",
        "GC_PROM_URL": "http://prometheus:9090",
        "GC_ALLOY_METRICS_URL": "http://alloy:12345",
    }
    for key, value in defaults.items():
        values[key] = os.environ.get(key, value)
    for key in list(values):
        if key in os.environ:
            values[key] = os.environ[key]
    values["edge"] = edge
    return values


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body)

    def header_values(self, name):
        return self.headers.get_all(name) or []


def http(method, url, body=None, headers=None, host=None, auth=None, timeout=15):
    """HTTP request that never raises on status codes. `host` overrides the Host header."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None and not (headers and "Content-Type" in headers):
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    if host:
        req.add_header("Host", host)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.headers, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.headers, exc.read())


def get_json(url, params=None, headers=None):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    resp = http("GET", full, headers=headers)
    if resp.status != 200:
        raise AssertionError(f"GET {full}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp.json()


def wait_for(fetch, what, timeout=60, interval=2):
    """Call fetch() until it returns a truthy value; AssertionError after timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fetch()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timeout after {timeout}s waiting for {what} (last: {last!r})")


def run_id():
    return os.urandom(4).hex()


def new_trace_id():
    return os.urandom(16).hex()


def new_span_id():
    return os.urandom(8).hex()


def now_ns():
    return time.time_ns()


# ------------------------------------------------------------------ OTLP/HTTP JSON builders
def attrs(values):
    out = []
    for key, value in values.items():
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def otlp_logs(resource, body, attributes=None, trace_id=None, span_id=None, severity="INFO"):
    record = {"timeUnixNano": str(now_ns()), "severityText": severity, "body": {"stringValue": body}, "attributes": attrs(attributes or {})}
    if trace_id:
        record["traceId"] = trace_id
        record["spanId"] = span_id or new_span_id()
    return {"resourceLogs": [{"resource": {"attributes": attrs(resource)}, "scopeLogs": [{"logRecords": [record]}]}]}


def otlp_traces(resource, spans):
    return {"resourceSpans": [{"resource": {"attributes": attrs(resource)}, "scopeSpans": [{"spans": spans}]}]}


def span(trace_id, name, attributes=None, span_id=None, kind=2, events=None):
    end = now_ns()
    return {
        "traceId": trace_id,
        "spanId": span_id or new_span_id(),
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(end - 5_000_000),
        "endTimeUnixNano": str(end),
        "attributes": attrs(attributes or {}),
        "events": events or [],
    }


def otlp_sum(resource, name, value, attributes=None):
    point = {"asInt": str(value), "timeUnixNano": str(now_ns()), "startTimeUnixNano": str(now_ns() - 10**9), "attributes": attrs(attributes or {})}
    metric = {"name": name, "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [point]}}
    return {"resourceMetrics": [{"resource": {"attributes": attrs(resource)}, "scopeMetrics": [{"metrics": [metric]}]}]}


def send_otlp(base_url, signal, payload, host=None, auth=None):
    resp = http("POST", f"{base_url}/v1/{signal}", payload, host=host, auth=auth)
    if resp.status // 100 != 2:
        raise AssertionError(f"OTLP {signal} to {base_url}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp


# ------------------------------------------------------------------ Faro
def faro_payload(app, page_url, logs=None, traces=None, session_attributes=None):
    meta = {"app": app, "session": {"id": "gc-" + run_id(), "attributes": session_attributes or {}}, "page": {"url": page_url}}
    payload = {"meta": meta, "logs": logs or [], "events": [], "measurements": [], "exceptions": []}
    if traces:
        payload["traces"] = traces
    return payload


def faro_log(message, level="info", trace_id=None, span_id=None, context=None):
    entry = {"message": message, "level": level, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "context": context or {}}
    if trace_id:
        entry["trace"] = {"trace_id": trace_id, "span_id": span_id or new_span_id()}
    return entry


def send_faro(base_url, payload, api_key, host=None, headers=None):
    all_headers = {"x-api-key": api_key, **(headers or {})}
    resp = http("POST", f"{base_url}/collect", payload, headers=all_headers, host=host)
    if resp.status // 100 != 2:
        raise AssertionError(f"Faro to {base_url}: HTTP {resp.status}: {resp.body[:300]!r}")
    return resp


# ------------------------------------------------------------------ stores
def loki_entries(loki_url, query, since_s=900):
    """[(indexed_labels, line, structured_metadata)] with labels and metadata kept apart."""
    params = {"query": query, "start": str(now_ns() - since_s * 10**9), "limit": "500", "direction": "forward"}
    data = get_json(loki_url + "/loki/api/v1/query_range", params, headers={"X-Loki-Response-Encoding-Flags": "categorize-labels"})
    entries = []
    for stream in data["data"]["result"]:
        for value in stream["values"]:
            meta = value[2].get("structuredMetadata", {}) if len(value) > 2 else {}
            entries.append((stream["stream"], value[1], meta))
    return entries


def tempo_trace(tempo_url, trace_id):
    """Tempo v2 trace, or None when absent (Tempo answers 404 or an empty trace)."""
    resp = http("GET", f"{tempo_url}/api/v2/traces/{trace_id}")
    if resp.status == 404:
        return None
    if resp.status != 200:
        raise AssertionError(f"Tempo trace {trace_id}: HTTP {resp.status}: {resp.body[:200]!r}")
    trace = resp.json().get("trace") or {}
    return trace if trace.get("resourceSpans") else None


def otlp_attr_map(attributes):
    out = {}
    for item in attributes or []:
        value = item.get("value", {})
        out[item["key"]] = next(iter(value.values()), None) if value else None
    return out


def trace_resources_and_spans(trace):
    """[(resource_attrs, [span_attrs...])] of a Tempo v2 trace."""
    result = []
    for resource_spans in (trace or {}).get("resourceSpans", []):
        resource = otlp_attr_map(resource_spans.get("resource", {}).get("attributes"))
        spans = [otlp_attr_map(s.get("attributes")) for scope in resource_spans.get("scopeSpans", []) for s in scope.get("spans", [])]
        result.append((resource, spans))
    return result


def prom_query(prom_url, expr):
    return get_json(prom_url + "/api/v1/query", {"query": expr})["data"]["result"]


def metric_value(metrics_text, name, labels=None):
    """Sum of the samples of `name` in a Prometheus text exposition matching `labels`."""
    total = 0.0
    pattern = re.compile(r"^" + re.escape(name) + r"(\{(?P<labels>[^}]*)\})? (?P<value>\S+)$")
    for line in metrics_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        found = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', match.group("labels") or ""))
        if all(found.get(k) == v for k, v in (labels or {}).items()):
            total += float(match.group("value"))
    return total


def scrape(url):
    resp = http("GET", url.rstrip("/") + "/metrics")
    if resp.status != 200:
        raise AssertionError(f"scrape {url}: HTTP {resp.status}")
    return resp.body.decode("utf-8", "replace")


DURATION_RE = re.compile(r"(\d+)(ms|y|w|d|h|m|s)")
DURATION_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def duration_seconds(text):
    """Parse Go/Prometheus durations: 168h, 1w, 30d, 720h0m0s, 90d."""
    parts = DURATION_RE.findall(text or "")
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(n) * DURATION_UNITS[u] for n, u in parts)


SIZE_RE = re.compile(r"^(\d+)([KMGTPE]?)(i?)B$")


def size_bytes(text):
    """Prometheus byte sizes are base 2 whatever the spelling: 100GB == 100GiB."""
    match = SIZE_RE.match(text or "")
    if not match:
        raise ValueError(f"not a size: {text!r}")
    return int(match.group(1)) * 1024 ** "_KMGTPE".index(match.group(2) or "_")


def yaml_section_value(text, section, key):
    """Value of `key` inside the top-level `section:` block of a YAML dump (first occurrence)."""
    match = re.search(rf"(?ms)^{re.escape(section)}:\n(?P<body>(?:^[ \-].*\n?)*)", text)
    if not match:
        return None
    found = re.search(rf"(?m)^\s+-?\s*{re.escape(key)}: ?(.*)$", match.group("body"))
    return found.group(1).strip().strip("'\"") if found else None
```

Fichier complet `scripts/smoke.py` :

```python
#!/usr/bin/env python3
"""End-to-end checks of spec 12.3 against the running stack (standard library only).

Usage: python3 scripts/smoke.py [--only section,section] [--list]
Defaults target the native harness; GC_* variables (see scripts/gclib.py) target a deployment.
"""

import argparse
import hashlib
import sys
import time
import traceback

import gclib as g

SECTIONS = {}


def section(name):
    def register(func):
        SECTIONS[name] = func
        return func

    return register


class Ctx:
    """Values shared by the sections of one run."""

    def __init__(self):
        self.s = g.settings()
        self.run = g.run_id()
        self.project = f"smoke-{self.run}"

    def resource(self, service, env_attr="deployment.environment.name", env="prod", **extra):
        values = {"project": self.project, env_attr: env, "service.name": f"{service}-{self.run}"}
        values.update(extra)
        return values

    def gateway(self):
        s = self.s
        auth = (s["GC_GATEWAY_USER"], s["GC_GATEWAY_PASSWORD"]) if s["GC_GATEWAY_USER"] else None
        return s["GC_GATEWAY_URL"], (s["GC_GATEWAY_HOST"] or None), auth

    def faro_app(self, name="web", environment="clientenv", namespace=None):
        return {"name": f"{name}-{self.run}", "namespace": namespace or self.project, "environment": environment, "version": "1.0.0"}

    def logs(self, query, since_s=900):
        return g.loki_entries(self.s["GC_LOKI_URL"], query, since_s)

    def wait_logs(self, query, count=1, timeout=60):
        return g.wait_for(lambda: (lambda e: e if len(e) >= count else None)(self.logs(query)), f"Loki {query}", timeout)

    def wait_trace(self, trace_id, timeout=60):
        return g.wait_for(lambda: g.tempo_trace(self.s["GC_TEMPO_URL"], trace_id), f"Tempo trace {trace_id}", timeout)

    def wait_prom(self, expr, timeout=90):
        return g.wait_for(lambda: g.prom_query(self.s["GC_PROM_URL"], expr), f"Prometheus {expr}", timeout, interval=5)

    def ip_hash(self, ip):
        return hashlib.sha256((self.s["IP_HASH_SALT"] + ip).encode()).hexdigest()


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


INDEXED = {"project", "env", "service_name"}


# ================================================================== sections
@section("otlp-names")
def otlp_names(c):
    """12.3.1 + 12.3.8: OTLP log/trace/metric via the internal path and alloy-gateway."""
    gateway_url, gateway_host, gateway_auth = c.gateway()
    paths = {"internal": (c.s["GC_OTLP_URL"], None, None), "gateway": (gateway_url, gateway_host, gateway_auth)}
    for path, (url, host, auth) in paths.items():
        trace_id = g.new_trace_id()
        resource = c.resource(f"api-{path}", tenant="acme")
        resource["service.instance.id"] = "instance-1"
        g.send_otlp(url, "logs", g.otlp_logs(resource, f"hello from {path}", trace_id=trace_id, severity="ERROR"), host, auth)
        g.send_otlp(url, "traces", g.otlp_traces(resource, [g.span(trace_id, "GET /x", {"http.route": "/x"})]), host, auth)
        g.send_otlp(url, "metrics", g.otlp_sum(resource, f"smoke_{c.run}_{path}_requests", 3), host, auth)
        service = resource["service.name"]

        labels, line, meta = c.wait_logs(f'{{service_name="{service}"}}')[0]
        expect(set(labels) == INDEXED, f"{path}: Loki indexed labels {sorted(labels)} != {sorted(INDEXED)}")
        expect(labels == {"project": c.project, "env": "prod", "service_name": service}, f"{path}: labels {labels}")
        expect(meta.get("tenant") == "acme", f"{path}: tenant metadata {meta}")
        expect(meta.get("trace_id") == trace_id, f"{path}: trace_id metadata {meta}")
        expect(meta.get("detected_level") == "error", f"{path}: detected_level {meta}")

        resource_attrs, _spans = g.trace_resources_and_spans(c.wait_trace(trace_id))[0]
        expect(resource_attrs.get("env") == "prod" and resource_attrs.get("project") == c.project, f"{path}: Tempo resource {resource_attrs}")
        expect("deployment.environment.name" not in resource_attrs, f"{path}: original env attribute kept in Tempo")

        series = c.wait_prom(f'smoke_{c.run}_{path}_requests_total{{job="{service}"}}')
        metric = series[0]["metric"]
        expect((metric.get("project"), metric.get("env"), metric.get("tenant")) == (c.project, "prod", "acme"), f"{path}: metric labels {metric}")
    series = g.get_json(c.s["GC_LOKI_URL"] + "/loki/api/v1/series", {"match[]": f'{{project="{c.project}"}}', "start": str(g.now_ns() - 900 * 10**9)})["data"]
    extra = {key for labels in series for key in labels} - INDEXED
    expect(not extra, f"12.3.8: unexpected Loki indexed labels {sorted(extra)}")


@section("reject")
def reject(c):
    """12.3.5: data without project (or env) is dropped and counted."""
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    names = {
        "logs": "otelcol_processor_filter_logs_filtered_total",
        "traces": "otelcol_processor_filter_spans_filtered_total",
        "metrics": "otelcol_processor_filter_datapoints_filtered_total",
    }
    before = {signal: g.metric_value(g.scrape(alloy), metric) for signal, metric in names.items()}
    resource = {"deployment.environment.name": "prod", "service.name": f"noproject-{c.run}"}
    trace_id = g.new_trace_id()
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, "no project"))
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "orphan")]))
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, f"smoke_{c.run}_orphan", 1))
    no_env = {"project": c.project, "service.name": f"noenv-{c.run}"}
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(no_env, "no env"))

    def increased():
        text = g.scrape(alloy)
        now = {signal: g.metric_value(text, metric) for signal, metric in names.items()}
        return now if now["logs"] >= before["logs"] + 2 and all(now[k] > before[k] for k in names) else None

    g.wait_for(increased, "filter counters", timeout=30)
    time.sleep(5)
    expect(not c.logs(f'{{service_name="noproject-{c.run}"}}'), "log without project reached Loki")
    expect(not c.logs(f'{{service_name="noenv-{c.run}"}}'), "log without env reached Loki")
    expect(g.tempo_trace(c.s["GC_TEMPO_URL"], trace_id) is None, "trace without project reached Tempo")
    expect(not g.prom_query(c.s["GC_PROM_URL"], f"smoke_{c.run}_orphan_total"), "metric without project reached Prometheus")


# --- end of sections ---


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify end-to-end checks (spec 12.3)")
    parser.add_argument("--only", help="comma-separated sections")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(SECTIONS))
        return 0
    names = args.only.split(",") if args.only else list(SECTIONS)
    unknown = [n for n in names if n not in SECTIONS]
    if unknown:
        print(f"smoke: unknown sections {unknown}; known: {list(SECTIONS)}", file=sys.stderr)
        return 2
    ctx = Ctx()
    failed = 0
    for name in names:
        started = time.monotonic()
        try:
            SECTIONS[name](ctx)
            print(f"smoke: [PASS] {name} ({time.monotonic() - started:.1f}s)")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failed += 1
            print(f"smoke: [FAIL] {name}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
    print(f"smoke: {len(names) - failed}/{len(names)} sections passed (run {ctx.run})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

Run : `chmod +x scripts/smoke.py`

Attendu : aucune sortie.

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 harness/stack.py down && python3 harness/stack.py up && python3 scripts/smoke.py`

Attendu : `smoke: [FAIL] otlp-names: … Connection refused` et `smoke: [FAIL] reject: …`, `0/2 sections passed`, code 1 (aucun Alloy ne tourne).

- [ ] **Étape 3 : écrire les configurations Alloy**

Fichier complet `config/alloy-gateway/config.alloy` :

```alloy
// grafana-coolify - alloy-gateway: public OTLP/HTTP relay, no processing (spec 5.2).
// Authentication is done by Traefik (gc-otlp-auth@file) before traffic reaches this port.
logging {
  level  = "info"
  format = "logfmt"
}

otelcol.receiver.otlp "gateway" {
  http {
    endpoint = sys.env("BIND_ADDR") + ":4318"
  }

  output {
    logs    = [otelcol.exporter.otlp.alloy.input]
    metrics = [otelcol.exporter.otlp.alloy.input]
    traces  = [otelcol.exporter.otlp.alloy.input]
  }
}

// No queue: when alloy is down the sender gets an error and its SDK retries (spec 9.2).
otelcol.exporter.otlp "alloy" {
  client {
    endpoint = "alloy:4317"

    tls {
      insecure = true
    }
  }

  sending_queue {
    enabled = false
  }

  retry_on_failure {
    enabled = false
  }
}
```

Fichier complet `config/alloy/config.alloy` :

```alloy
// grafana-coolify - main Alloy pipeline (spec 7.1).
// In: OTLP on 4317/4318 (internal, and relayed by alloy-gateway) and Faro on 12347 (public).
// Out: traces -> Tempo, logs -> Loki, metrics -> Prometheus.
// Every runtime value comes from sys.env(); Coolify writes this file byte for byte.

logging {
  level  = "info"
  format = "logfmt"
}

// Persistent sending queue of the three OTLP exporters (spec 9.2).
otelcol.storage.file "queue" {
  directory = sys.env("ALLOY_QUEUE_DIR")
}

// ------------------------------------------------------------------ OTLP path
otelcol.receiver.otlp "default" {
  grpc {
    endpoint = sys.env("BIND_ADDR") + ":4317"
  }

  http {
    endpoint = sys.env("BIND_ADDR") + ":4318"
  }

  output {
    logs    = [otelcol.processor.memory_limiter.default.input]
    metrics = [otelcol.processor.memory_limiter.default.input]
    traces  = [otelcol.processor.memory_limiter.default.input]
  }
}

otelcol.processor.memory_limiter "default" {
  check_interval         = "1s"
  limit_percentage       = 80
  spike_limit_percentage = 20

  output {
    logs    = [otelcol.processor.transform.default.input]
    metrics = [otelcol.processor.transform.default.input]
    traces  = [otelcol.processor.transform.default.input]
  }
}

// Short env: deployment.environment.name (or legacy deployment.environment) -> env,
// original attribute deleted (spec 6.1). Masking is added in the same transform (task 10).
otelcol.processor.transform "default" {
  error_mode = "ignore"

  trace_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }
  log_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }
  metric_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }

  output {
    logs    = [otelcol.processor.filter.default.input]
    metrics = [otelcol.processor.filter.default.input]
    traces  = [otelcol.processor.filter.default.input]
  }
}

// Drop everything without project or env (spec 6.3). Counters:
// otelcol_processor_filter_{spans,logs,datapoints}_filtered_total.
otelcol.processor.filter "default" {
  error_mode = "ignore"

  traces {
    span = [
      `resource.attributes["project"] == nil or resource.attributes["env"] == nil`,
    ]
  }

  logs {
    log_record = [
      `resource.attributes["project"] == nil or resource.attributes["env"] == nil`,
    ]
  }

  metrics {
    datapoint = [
      `resource.attributes["project"] == nil or resource.attributes["env"] == nil`,
    ]
  }

  output {
    logs    = [otelcol.processor.batch.default.input]
    metrics = [otelcol.processor.batch.default.input]
    traces  = [otelcol.processor.batch.default.input]
  }
}

otelcol.processor.batch "default" {
  output {
    logs    = [otelcol.exporter.otlphttp.loki.input]
    metrics = [otelcol.exporter.otlphttp.prometheus.input]
    traces  = [otelcol.exporter.otlp.tempo.input]
  }
}

otelcol.exporter.otlp "tempo" {
  client {
    endpoint = "tempo:4317"

    tls {
      insecure = true
    }
  }

  sending_queue {
    enabled = true
    storage = otelcol.storage.file.queue.handler
  }

  retry_on_failure {
    max_elapsed_time = "1h"
  }
}

otelcol.exporter.otlphttp "loki" {
  client {
    endpoint = "http://loki:3100/otlp"
  }

  sending_queue {
    enabled = true
    storage = otelcol.storage.file.queue.handler
  }

  retry_on_failure {
    max_elapsed_time = "1h"
  }
}

otelcol.exporter.otlphttp "prometheus" {
  client {
    endpoint = "http://prometheus:9090/api/v1/otlp"
  }

  sending_queue {
    enabled = true
    storage = otelcol.storage.file.queue.handler
  }

  retry_on_failure {
    max_elapsed_time = "1h"
  }
}
```

Si `alloy validate` refuse un bloc avec Alloy v1.20.0, corriger d'après <https://grafana.com/docs/alloy/v1.20/reference/components/> jusqu'à ce que `check.py --only validators` passe.

- [ ] **Étape 4 : ajouter les services au gabarit et leurs variables**

Dans `compose.template.yaml`, repérer le texte exact suivant (unique dans le fichier) — c'est-à-dire après le service `node-exporter` :

```yaml
volumes:
  loki-data:
```

et insérer juste **avant** lui :

```yaml
  alloy:
    image: grafana/alloy:@@ALLOY_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    user: "473:473"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    command:
      - run
      - /etc/alloy/config.alloy
      - --server.http.listen-addr=0.0.0.0:12345
      - --storage.path=/var/lib/alloy/data
      - --stability.level=public-preview
    environment:
      SERVICE_FQDN_ALLOY_12347:
      BIND_ADDR: 0.0.0.0
      ALLOY_QUEUE_DIR: /var/lib/alloy/queue
      IP_HASH_SALT: ${IP_HASH_SALT:?IP_HASH_SALT is required}
      FARO_API_KEY: ${FARO_API_KEY:-}
      FARO_RATE: ${FARO_RATE:-100}
      FARO_BURST: ${FARO_BURST:-200}
      FARO_MAX_PAYLOAD: ${FARO_MAX_PAYLOAD:-5MiB}
      HOST_MAP: ${HOST_MAP:-}
      RESERVED_SUBDOMAINS: ${RESERVED_SUBDOMAINS:-}
      TENANT_HOST_REGEX: ${TENANT_HOST_REGEX:-}
    volumes:
      - type: bind
        source: ./config/alloy/config.alloy
        target: /etc/alloy/config.alloy
        content: "@@CONTENT@@"
        read_only: true
      - alloy-data:/var/lib/alloy
    labels:
      - coolify.traefik.middlewares=gc-faro-ratelimit@file,gc-faro-cors@file,gc-faro-body@file
    expose:
      - "4317"
      - "4318"
      - "12345"
      - "12347"
    mem_limit: 768m
    cpus: 1.0

  alloy-gateway:
    image: grafana/alloy:@@ALLOY_VERSION@@
    restart: unless-stopped
    depends_on:
      config-guard:
        condition: service_completed_successfully
    user: "473:473"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    command:
      - run
      - /etc/alloy/config.alloy
      - --server.http.listen-addr=0.0.0.0:12345
      - --storage.path=/var/lib/alloy/data
      - --stability.level=public-preview
    environment:
      SERVICE_FQDN_ALLOY_GATEWAY_4318:
      BIND_ADDR: 0.0.0.0
    volumes:
      - type: bind
        source: ./config/alloy-gateway/config.alloy
        target: /etc/alloy/config.alloy
        content: "@@CONTENT@@"
        read_only: true
    tmpfs:
      - /var/lib/alloy:mode=1777
    labels:
      - coolify.traefik.middlewares=gc-otlp-auth@file
    expose:
      - "4318"
      - "12345"
    mem_limit: 256m
    cpus: 0.5

```

Dans `compose.template.yaml`, remplacer exactement (liste `volumes:` de fin de fichier) :

```yaml
  prometheus-data:
```

par :

```yaml
  prometheus-data:
  alloy-data:
```

Ajouter à la fin de `.env.example` — le bloc commence par une ligne vide :

```dotenv

# --- Domaines publics (générés par Coolify, à laisser vides) ---
SERVICE_FQDN_ALLOY_12347=
SERVICE_FQDN_ALLOY_GATEWAY_4318=

# --- Faro (clients : navigateur, desktop, mobile) ---
# Clé d'application Faro. Vide : le point Faro refuse toute requête.
FARO_API_KEY=
# Limites côté Alloy (globales au récepteur). Défauts : 100 / 200 / 5MiB.
FARO_RATE=100
FARO_BURST=200
FARO_MAX_PAYLOAD=5MiB
```

Run : `python3 scripts/render.py && python3 scripts/check.py`

Attendu : `render: wrote docker-compose.yaml` et `compose.dev.yaml`, puis huit `[PASS]` (dont `validators` pour les deux configs Alloy).

- [ ] **Étape 5 : lancer — succès attendu**

Run : `python3 harness/stack.py down && python3 harness/stack.py up && python3 scripts/smoke.py`

Attendu : `alloy` et `alloy-gateway` « ready », puis `smoke: [PASS] otlp-names` et `smoke: [PASS] reject`, `2/2 sections passed`.

Run : `python3 -m unittest discover -s tests -v`

Attendu : tous les tests `OK`.

- [ ] **Étape 6 : commit**

```bash
git add scripts/gclib.py scripts/smoke.py config/alloy-gateway/config.alloy config/alloy/config.alloy compose.template.yaml .env.example docker-compose.yaml compose.dev.yaml
git commit -m "feat(alloy): add otlp pipeline, gateway relay and smoke checks"
```


### Task 10: Masquage des données personnelles (chemin OTLP)

Le transform `default` reçoit le masquage du § 8.1, en OTTL stable uniquement
(`otelcol.processor.redaction` est expérimental, donc écarté). Mécanismes vérifiés sur Alloy
v1.20.0 (OTTL v0.161.0) : les chaînes brutes Alloy entre accents graves évitent un double
échappement ; `replace_all_patterns(..., "<sel>${0}", SHA256)` produit exactement
`sha256(IP_HASH_SALT + ip)` (= `SHA256(Concat([sel, ip], ""))`) ; OTTL n'ayant aucune fonction
stable qui parcourt les clés, les valeurs des clés sensibles sont masquées par
`set(cache["sens"], attrs)` → `keep_matching_keys` → `replace_all_patterns` → `merge_maps`.
Les cartes ne sont masquées que dans le texte libre (corps, `message`, `exception.message`,
`exception.stacktrace`) : un epoch en millisecondes sous `*_ms`, `*timestamp*` ou `*_id`
reste intact. La regex IPv6 exige soit huit groupes, soit `::` entouré de groupes : une heure
`01:30:29` ou un appel `App\User::find` ne sont pas hachés (point de vigilance n° 1).

**Files:**

- Modify: `scripts/smoke.py`
- Modify: `config/alloy/config.alloy`
- Modify: `docker-compose.yaml, compose.dev.yaml (régénérés)`

**Interfaces:**

- Consumes : `scripts/gclib.py`, `scripts/smoke.py` (tâche 9) : `section`, `Ctx.ip_hash`, `expect`.
- Consumes : Banc de la tâche 8, démarré par la tâche 9.
- Produces : Section `masking` de `scripts/smoke.py`. Transform `otelcol.processor.transform "default"` complet (réutilisé par les traces Faro, tâche 12).

- [ ] **Étape 1 : écrire le contrôle qui échoue**

Dans `scripts/smoke.py`, repérer le texte exact suivant (unique dans le fichier) :

```python
# --- end of sections ---
```

et insérer juste **avant** lui :

```python
@section("masking")
def masking(c):
    """12.3.4 (OTLP path): emails, Bearer, secrets, cards in free text, IPs hashed; no false positives."""
    epoch_ms = "1790559058622"
    body = (
        f"run {c.run} mail bob@example.com from 203.0.113.9 and 2001:db8::7 "
        f"card 4111 1111 1111 1111 at {epoch_ms} time 01:30:29 App\\User::find header Bearer abc.def-ghi password=hunter2x"
    )
    attributes = {
        "created_ms": epoch_ms,
        "event.timestamp": epoch_ms,
        "user_id": "4111111111111111",
        "message": f"attr message {epoch_ms}",
        "http.request.header.authorization": "Basic dXNlcjpwYXNz",
        "db.password": "hunter2",
        "client.address": "198.51.100.23",
    }
    resource = c.resource("masking")
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, body, attributes))
    _labels, line, meta = c.wait_logs(f'{{service_name="{resource["service.name"]}"}}')[0]
    for secret in ("bob@example.com", "203.0.113.9", "2001:db8::7", "4111 1111 1111 1111", "abc.def-ghi", "hunter2x", epoch_ms):
        expect(secret not in line, f"{secret!r} survived in the log body: {line}")
    for marker in ("[email]", "[card]", "Bearer [redacted]", "password=[redacted]", c.ip_hash("203.0.113.9"), c.ip_hash("2001:db8::7")):
        expect(marker in line, f"{marker!r} missing from the log body: {line}")
    for kept in ("01:30:29", "App\\User::find"):
        expect(kept in line, f"false positive: {kept!r} was altered: {line}")
    expect(meta.get("created_ms") == epoch_ms, f"epoch under *_ms was masked: {meta.get('created_ms')}")
    expect(meta.get("event_timestamp") == epoch_ms, f"epoch under *timestamp* was masked: {meta.get('event_timestamp')}")
    expect(meta.get("user_id") == "4111111111111111", f"*_id value was masked: {meta.get('user_id')}")
    expect(meta.get("message") == "attr message [card]", f"epoch in message attribute not masked: {meta.get('message')}")
    expect(meta.get("http_request_header_authorization") == "[redacted]", f"authorization: {meta}")
    expect(meta.get("db_password") == "[redacted]", f"password: {meta}")
    expect(meta.get("client_address") == c.ip_hash("198.51.100.23"), f"client.address: {meta}")


```

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 harness/stack.py status | grep -q 'alloy .*ready' || python3 harness/stack.py down && python3 harness/stack.py up; python3 scripts/smoke.py --only masking`

Attendu : `smoke: [FAIL] masking: 'bob@example.com' survived in the log body: …`, code 1.

- [ ] **Étape 3 : remplacer le transform par la version avec masquage**

Dans `config/alloy/config.alloy`, remplacer exactement (tout le bloc, commentaire d'en-tête compris) :

```alloy
// Short env: deployment.environment.name (or legacy deployment.environment) -> env,
// original attribute deleted (spec 6.1). Masking is added in the same transform (task 10).
otelcol.processor.transform "default" {
  error_mode = "ignore"

  trace_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }
  log_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }
  metric_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
    ]
  }

  output {
    logs    = [otelcol.processor.filter.default.input]
    metrics = [otelcol.processor.filter.default.input]
    traces  = [otelcol.processor.filter.default.input]
  }
}

```

par :

```alloy
// 1. Short env: deployment.environment.name (or legacy deployment.environment) -> env,
//    original attribute deleted (spec 6.1).
// 2. Masking safety net (spec 8.1): secret keys and Bearer tokens -> [redacted], emails ->
//    [email], card numbers -> [card] in free text only (body, message, exception.*),
//    IPv4/IPv6 -> SHA256(IP_HASH_SALT + ip). Regexes are RE2; backticks are Alloy raw strings.
otelcol.processor.transform "default" {
  error_mode = "ignore"

  trace_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
      `set(cache["sens"], resource.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(resource.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(resource.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
    ]
  }
  trace_statements {
    context    = "span"
    statements = [
      `set(cache["sens"], span.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(span.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(span.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(span.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(span.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(span.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
      `replace_pattern(span.attributes["exception.message"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where span.attributes["exception.message"] != nil`,
    ]
  }
  trace_statements {
    context    = "spanevent"
    statements = [
      `set(cache["sens"], spanevent.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(spanevent.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(spanevent.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(spanevent.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(spanevent.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(spanevent.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
      `replace_pattern(spanevent.attributes["exception.message"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where spanevent.attributes["exception.message"] != nil`,
      `replace_pattern(spanevent.attributes["exception.stacktrace"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where spanevent.attributes["exception.stacktrace"] != nil`,
    ]
  }
  log_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
      `set(cache["sens"], resource.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(resource.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(resource.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
    ]
  }
  log_statements {
    context    = "log"
    statements = [
      `replace_pattern(log.body, "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_pattern(log.body, "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_pattern(log.body, "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_pattern(log.body, "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]")`,
      `replace_pattern(log.body, "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
      `set(cache["sens"], log.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(log.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(log.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(log.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(log.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(log.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
      `replace_pattern(log.attributes["message"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where log.attributes["message"] != nil`,
      `replace_pattern(log.attributes["exception.message"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where log.attributes["exception.message"] != nil`,
      `replace_pattern(log.attributes["exception.stacktrace"], "\\b(?:\\d[ -]?){12,18}\\d\\b", "[card]") where log.attributes["exception.stacktrace"] != nil`,
    ]
  }
  metric_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
      `set(cache["sens"], resource.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(resource.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(resource.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(resource.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
    ]
  }
  metric_statements {
    context    = "datapoint"
    statements = [
      `set(cache["sens"], datapoint.attributes)`,
      `keep_matching_keys(cache["sens"], "(?i).*(authorization|cookie|password|token|secret).*")`,
      `replace_all_patterns(cache["sens"], "value", "^[\\s\\S]*$", "[redacted]")`,
      `merge_maps(datapoint.attributes, cache["sens"], "upsert")`,
      `replace_all_patterns(datapoint.attributes, "value", "(?i)\\bbearer\\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]")`,
      `replace_all_patterns(datapoint.attributes, "value", "(?i)\\b(authorization|cookie|password|token|secret)(\\x22?\\s*[:=]\\s*\\x22?)[^\\s\\x22&,;]+", "${1}${2}[redacted]")`,
      `replace_all_patterns(datapoint.attributes, "value", "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[email]")`,
      `replace_all_patterns(datapoint.attributes, "value", "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)", "` + sys.env("IP_HASH_SALT") + `${0}", SHA256)`,
    ]
  }

  output {
    logs    = [otelcol.processor.filter.default.input]
    metrics = [otelcol.processor.filter.default.input]
    traces  = [otelcol.processor.filter.default.input]
  }
}

```

Run : `python3 scripts/render.py && python3 scripts/check.py --only render,size,validators,secrets`

Attendu : quatre `[PASS]`.

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 harness/stack.py stop alloy && python3 harness/stack.py start alloy && python3 scripts/smoke.py`

Attendu : `otlp-names`, `reject`, `masking` : `3/3 sections passed`.

- [ ] **Étape 5 : commit**

```bash
git add scripts/smoke.py config/alloy/config.alloy docker-compose.yaml compose.dev.yaml
git commit -m "feat(alloy): mask personal data in the otlp pipeline"
```


### Task 11: Logs Faro : réception, déduction depuis l'hôte, masquage

`faro.receiver` : clé `api_key` (si `FARO_API_KEY` est vide, repli sur une clé impossible à
deviner dérivée du sel secret : un point public ne doit jamais s'ouvrir par oubli — point de
vigilance n° 2), `rate_limiting`, `max_allowed_payload_size`, `cors_allowed_origins = []` (le
CORS est fait par Traefik). Les logs passent par `loki.process`, qui applique l'algorithme
normatif du § 6.4. Mécanisme retenu pour des règles venues de l'environnement : `HOST_MAP` et
`RESERVED_SUBDOMAINS` sont copiés dans la carte extraite par `stage.template` (gabarit
= `"," + sys.env(...)`, jamais vide), puis parcourus par des gabarits Go avec les fonctions sprig
`splitList` et `has` ; `TENANT_HOST_REGEX` est enveloppé dans un groupe nommé
`gc_host_match` (vide : classe `[^\s\S]` qui ne correspond à rien). Les valeurs absentes
passent toujours par `{{ with … }}` : sans cela Go écrit `<no value>` et la ligne sans `project`
n'est pas rejetée (constaté). Masquage Faro : Bearer, clés sensibles et emails sur toute la
ligne ; cartes et IP **seulement** dans `message`, `value` et `stacktrace`, sinon la version
navigateur `128.0.0.0` serait hachée comme une IP ; chaque IP est hachée par
`{{ Sha2Hash "<sel>" $ip }}` dans une boucle `regexFindAll`, même empreinte que l'OTTL. Pas de
WAL pour `loki.write` (expérimental).

**Files:**

- Modify: `scripts/smoke.py`
- Modify: `config/alloy/config.alloy`
- Modify: `docker-compose.yaml, compose.dev.yaml (régénérés)`
- Test: `tests/test_faro_closed.py`

**Interfaces:**

- Consumes : `scripts/gclib.py` : `faro_payload`, `faro_log`, `send_faro`, `loki_entries`, `metric_value`, `scrape`.
- Consumes : Variables du banc : `FARO_API_KEY`, `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX`, `IP_HASH_SALT`.
- Produces : Port Faro 12347 sur `BIND_ADDR`, chemin `/collect`, en-tête `x-api-key`.
- Produces : Correspondances : `app_namespace` → `project`, `app_name` → `service_name`, `app_environment` → `env` (si l'hôte ne décide pas), attribut de session `tenant` (logfmt `session_attr_tenant`) → `tenant`, `traceID` → `trace_id`, `level` → `detected_level`.
- Produces : Compteur `loki_process_dropped_lines_total{reason="missing_project"|"missing_env"}`.
- Produces : Sections `faro-hosts` (constante `HOST_CASES`), `faro-names`, `faro-reject`, `ip-parity` ; test `tests/test_faro_closed.py` (`GC_HARNESS=1`, banc démarré).

- [ ] **Étape 1 : écrire les contrôles qui échouent**

Dans `scripts/smoke.py`, repérer le texte exact suivant (unique dans le fichier) :

```python
# --- end of sections ---
```

et insérer juste **avant** lui :

```python
# 12.3.2: the reference table of spec 6.4 (harness values of HOST_MAP, RESERVED_SUBDOMAINS,
# TENANT_HOST_REGEX). None = attribute absent.
HOST_CASES = [
    ("example.me", "guest-front", "prod", None),
    ("www.example.me", "client", "prod", None),
    ("api-dev.example.me", "client", "preprod", None),
    ("acme.example.me", "client", "prod", "acme"),
    ("acme-dev.example.app", "client", "preprod", "acme"),
    ("inconnu.autre.org", "client", "clientenv", "clienttenant"),
    ("ACME.Example.ME", "client", "prod", "acme"),
]


@section("faro-hosts")
def faro_hosts(c):
    """12.3.2: env/tenant/service deduced from the page host (spec 6.4), client values otherwise."""
    for index, (host, service, env, tenant) in enumerate(HOST_CASES):
        token = f"host-{index}-{c.run}"
        payload = g.faro_payload(c.faro_app(), f"https://{host}/path?q=1", logs=[g.faro_log(token)], session_attributes={"tenant": "clienttenant"})
        g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
        labels, _line, meta = c.wait_logs(f'{{project="{c.project}"}} |= "{token}"')[0]
        expected_service = f"web-{c.run}" if service == "client" else service
        expect(labels.get("service_name") == expected_service, f"{host}: service_name {labels.get('service_name')} != {expected_service}")
        expect(labels.get("env") == env, f"{host}: env {labels.get('env')} != {env}")
        expect(meta.get("tenant") == tenant, f"{host}: tenant {meta.get('tenant')} != {tenant}")


@section("faro-names")
def faro_names(c):
    """12.3.1 + 12.3.9 (logs): Faro logs use the Loki names of the OTLP path; app.* mapping."""
    trace_id = g.new_trace_id()
    token = f"names-{c.run}"
    payload = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(token, "warn", trace_id)])
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    labels, _line, meta = c.wait_logs(f'{{project="{c.project}"}} |= "{token}"')[0]
    expect(set(labels) == INDEXED, f"Faro indexed labels {sorted(labels)}")
    expect(labels == {"project": c.project, "env": "prod", "service_name": f"web-{c.run}"}, f"Faro labels {labels}")
    expect(meta.get("trace_id") == trace_id, f"trace_id not normalised from traceID: {meta}")
    expect(meta.get("detected_level") == "warn", f"detected_level: {meta}")


@section("faro-reject")
def faro_reject(c):
    """12.3.5 (Faro path): lines without project or env are dropped with a reason."""
    alloy = c.s["GC_ALLOY_METRICS_URL"]
    metric = "loki_process_dropped_lines_total"
    before = {r: g.metric_value(g.scrape(alloy), metric, {"reason": r}) for r in ("missing_project", "missing_env")}
    no_project = g.faro_payload({"name": f"web-{c.run}", "environment": "prod"}, "https://inconnu.autre.org/", logs=[g.faro_log(f"np-{c.run}")])
    no_env = g.faro_payload({"name": f"web-{c.run}", "namespace": c.project}, "https://inconnu.autre.org/", logs=[g.faro_log(f"ne-{c.run}")])
    g.send_faro(c.s["GC_FARO_URL"], no_project, c.s["FARO_API_KEY"])
    g.send_faro(c.s["GC_FARO_URL"], no_env, c.s["FARO_API_KEY"])

    def counted():
        text = g.scrape(alloy)
        return all(g.metric_value(text, metric, {"reason": r}) > before[r] for r in before)

    g.wait_for(counted, "loki_process_dropped_lines_total increments", timeout=30)
    expect(not c.logs(f'{{service_name="web-{c.run}"}} |= "np-{c.run}"'), "Faro line without project stored")
    expect(not c.logs(f'{{project="{c.project}"}} |= "ne-{c.run}"'), "Faro line without env stored")


@section("ip-parity")
def ip_parity(c):
    """12.3.4: the same IP gives the same digest in a Faro log and in an OTLP log."""
    ip = "192.0.2.77"
    token = f"parity-{c.run}"
    faro = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(f"{token} from {ip} card 4111 1111 1111 1111")])
    g.send_faro(c.s["GC_FARO_URL"], faro, c.s["FARO_API_KEY"])
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(c.resource("parity"), f"{token} from {ip}"))
    lines = [line for _l, line, _m in c.wait_logs(f'{{project="{c.project}"}} |= "{token}"', count=2)]
    digest = c.ip_hash(ip)
    for line in lines:
        expect(ip not in line and digest in line, f"IP not hashed as sha256(salt+ip): {line}")
    faro_line = next(line for line in lines if "kind=log" in line)
    expect("[card]" in faro_line and "4111" not in faro_line, f"card not masked in Faro message: {faro_line}")


```

Fichier complet `tests/test_faro_closed.py` :

```python
import sys
import unittest

from support import ROOT, require_harness, run

sys.path.insert(0, str(ROOT / "scripts"))
import gclib as g  # noqa: E402

STACK = [sys.executable, str(ROOT / "harness" / "stack.py")]


def stack(*args):
    result = run([*STACK, *args], timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"stack.py {' '.join(args)} failed:\n{result.stdout}")


class EmptyFaroKeyTest(unittest.TestCase):
    """Review focus: an empty FARO_API_KEY must keep the public Faro endpoint closed.

    Needs a running harness: python3 harness/stack.py up.
    """

    def setUp(self):
        require_harness(self)

    def test_empty_key_rejects_every_request(self):
        payload = g.faro_payload({"name": "web", "namespace": "closed", "environment": "prod"}, "https://x.org/", logs=[g.faro_log("x")])
        stack("stop", "alloy")
        try:
            stack("start", "alloy", "--set", "FARO_API_KEY=")
            self.assertEqual(g.http("POST", "http://alloy:12347/collect", payload).status, 401)
            self.assertEqual(g.http("POST", "http://alloy:12347/collect", payload, headers={"x-api-key": ""}).status, 401)
            self.assertEqual(g.http("POST", "http://alloy:12347/collect", payload, headers={"x-api-key": "-faro-disabled"}).status, 401)
        finally:
            stack("stop", "alloy")
            stack("start", "alloy")
        self.assertEqual(g.http("POST", "http://alloy:12347/collect", payload, headers={"x-api-key": g.settings()["FARO_API_KEY"]}).status, 202)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 harness/stack.py status | grep -q 'alloy .*ready' || python3 harness/stack.py down && python3 harness/stack.py up; python3 scripts/smoke.py --only faro-hosts,faro-names,faro-reject,ip-parity`

Attendu : quatre `[FAIL]` (« Connection refused » sur `alloy:12347`), code 1.

- [ ] **Étape 3 : ajouter le chemin Faro à la fin de `config/alloy/config.alloy`**

Ajouter à la fin de `config/alloy/config.alloy` — le bloc commence par une ligne vide ; les traces Faro vont provisoirement au transform `default` :

```alloy

// ------------------------------------------------------------------ Faro path
faro.receiver "default" {
  server {
    listen_address = sys.env("BIND_ADDR")
    listen_port    = 12347
    // An empty FARO_API_KEY must never open the endpoint: fall back to an unguessable key
    // derived from the mandatory, secret IP hash salt.
    api_key = coalesce(sys.env("FARO_API_KEY"), sys.env("IP_HASH_SALT") + "-faro-disabled")
    // CORS is answered by Traefik (gc-faro-cors@file): a second header would break browsers.
    cors_allowed_origins     = []
    max_allowed_payload_size = coalesce(sys.env("FARO_MAX_PAYLOAD"), "5MiB")

    rate_limiting {
      enabled    = true
      rate       = coalesce(sys.env("FARO_RATE"), "100")
      burst_size = coalesce(sys.env("FARO_BURST"), "200")
    }
  }

  output {
    logs   = [loki.process.faro.receiver]
    traces = [otelcol.processor.transform.default.input]
  }
}

// Faro logs (logfmt lines): deduce env/tenant from the page host (normative algorithm,
// spec 6.4), map app_* to the contract, emit exactly the Loki names of the OTLP path,
// drop lines without project/env, then mask.
loki.process "faro" {
  forward_to = [loki.write.loki.receiver]

  stage.logfmt {
    mapping = {
      "gc_page_url"        = "page_url",
      "gc_app_name"        = "app_name",
      "gc_app_namespace"   = "app_namespace",
      "gc_app_environment" = "app_environment",
      "gc_client_tenant"   = "session_attr_tenant",
      "gc_level"           = "level",
      "trace_id"           = "traceID",
    }
  }

  // host := lowercase host of page_url
  stage.regex {
    source     = "gc_page_url"
    expression = `^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?(?P<gc_host>[^:/?#]+)`
  }

  stage.template {
    source   = "gc_host"
    template = "{{ ToLower .Value }}"
  }

  // Rules come from the environment. A leading comma keeps the template non-empty.
  stage.template {
    source   = "gc_host_map"
    template = "," + sys.env("HOST_MAP")
  }

  stage.template {
    source   = "gc_reserved"
    template = "," + sys.env("RESERVED_SUBDOMAINS")
  }

  // HOST_MAP lookup (exact match, has priority): "service:env" or empty.
  stage.template {
    source   = "gc_hm_hit"
    template = `{{ $h := .gc_host }}{{ $r := "" }}{{ if $h }}{{ range $e := splitList "," .gc_host_map }}{{ $kv := splitList "=" $e }}{{ if and (eq (len $kv) 2) (eq (index $kv 0) $h) }}{{ $r = index $kv 1 }}{{ end }}{{ end }}{{ end }}{{ $r }}`
  }

  stage.regex {
    source     = "gc_hm_hit"
    expression = `^(?P<gc_hm_service>[^:]+):(?P<gc_hm_env>.+)$`
  }

  // TENANT_HOST_REGEX (named groups sub and dev). Empty variable: a class that never matches.
  stage.regex {
    source     = "gc_host"
    expression = "(?P<gc_host_match>" + coalesce(sys.env("TENANT_HOST_REGEX"), "[^\\s\\S]") + ")"
  }

  stage.template {
    source   = "env"
    template = `{{ if .gc_hm_env }}{{ .gc_hm_env }}{{ else if .gc_host_match }}{{ if .dev }}preprod{{ else }}prod{{ end }}{{ else }}{{ with .gc_app_environment }}{{ . }}{{ end }}{{ end }}`
  }

  stage.template {
    source   = "tenant"
    template = `{{ if .gc_hm_env }}{{ else if .gc_host_match }}{{ if not (has .sub (splitList "," .gc_reserved)) }}{{ .sub }}{{ end }}{{ else }}{{ with .gc_client_tenant }}{{ . }}{{ end }}{{ end }}`
  }

  stage.template {
    source   = "service_name"
    template = `{{ if .gc_hm_service }}{{ .gc_hm_service }}{{ else }}{{ with .gc_app_name }}{{ . }}{{ end }}{{ end }}`
  }

  stage.template {
    source   = "project"
    template = "{{ with .gc_app_namespace }}{{ . }}{{ end }}"
  }

  stage.template {
    source   = "detected_level"
    template = "{{ with .gc_level }}{{ . }}{{ end }}"
  }

  stage.labels {
    values = {
      project      = "",
      env          = "",
      service_name = "",
    }
  }

  stage.structured_metadata {
    values = {
      tenant         = "",
      trace_id       = "",
      detected_level = "",
    }
  }

  // Counter: loki_process_dropped_lines_total{reason="missing_project"|"missing_env"}.
  stage.drop {
    source              = "project"
    expression          = "^$"
    drop_counter_reason = "missing_project"
  }

  stage.drop {
    source              = "env"
    expression          = "^$"
    drop_counter_reason = "missing_env"
  }

  // Masking (spec 8.1) on the whole line: Bearer tokens, secret key=value pairs, emails.
  stage.replace {
    expression = `(?i)\bbearer\s+([A-Za-z0-9._~+/=-]+)`
    replace    = "[redacted]"
  }

  stage.replace {
    expression = `(?i)\b(?:authorization|cookie|password|token|secret)(?:\\?")?\s*[:=]\s*(?:\\?")?([^\s"\\&,;]+)`
    replace    = "[redacted]"
  }

  stage.replace {
    expression = `([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})`
    replace    = "[email]"
  }

  // Free text only (message, value, stacktrace): card numbers, then every IPv4/IPv6 hashed as
  // sha256(salt + ip) - Sha2Hash(salt, input), salt first, same digest as the OTTL SHA256.
  stage.replace {
    expression = `(?:^|\s)(?:message|value|stacktrace)=("(?:[^"\\]|\\.)*"|\S*)`
    replace    = `{{ $v := regexReplaceAll "\\b(?:\\d[ -]?){12,18}\\d\\b" .Value "[card]" }}{{ range $ip := regexFindAll "(?:\\b(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}\\b|\\b(?:[0-9A-Fa-f]{1,4}:){1,6}(?::[0-9A-Fa-f]{1,4}){1,6}\\b|\\b(?:(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\.){3}(?:25[0-5]|2[0-4]\\d|1?\\d?\\d)\\b)" $v -1 }}{{ $v = replace $ip (Sha2Hash "` + sys.env("IP_HASH_SALT") + `" $ip) $v }}{{ end }}{{ $v }}`
  }
}

// No WAL for Faro logs: loki.write's WAL is experimental; a limited loss is accepted (spec 9.2).
loki.write "loki" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
```

Run : `python3 scripts/render.py && python3 scripts/check.py --only render,size,validators,secrets`

Attendu : quatre `[PASS]`.

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 harness/stack.py stop alloy && python3 harness/stack.py start alloy && python3 scripts/smoke.py`

Attendu : `7/7 sections passed` : les sept hôtes de `HOST_CASES` (dont `ACME.Example.ME`, en majuscules) donnent exactement le tableau du § 6.4.

Run : `GC_HARNESS=1 python3 -m unittest discover -s tests -p test_faro_closed.py -v`

Attendu : `Ran 1 test` … `OK` : clé vide → 401 même sans en-tête, avec `x-api-key` vide ou `-faro-disabled`.

- [ ] **Étape 5 : commit**

```bash
git add scripts/smoke.py tests/test_faro_closed.py config/alloy/config.alloy docker-compose.yaml compose.dev.yaml
git commit -m "feat(alloy): add faro logs pipeline with host-based env and tenant"
```


### Task 12: Traces Faro : validation de `tenant` et `env`

`faro.receiver` transmet les traces du SDK telles quelles (vérifié : ni `app.*` ni URL de page).
Un transform `faro` les met au contrat avant le transform partagé : `project` depuis
`service.namespace` (que le SDK Faro Web renseigne avec `app.namespace` ; spike S3) ou
`app.namespace`, `env` depuis `deployment.environment(.name)`, puis suppression de `env` et
`tenant` s'ils ne respectent pas `^[a-z0-9-]+$` ou s'ils sont un sous-domaine réservé. La regex
des réservés est construite par `string.replace(sys.env("RESERVED_SUBDOMAINS"), ",", "|")`.

**Files:**

- Modify: `scripts/smoke.py`
- Modify: `config/alloy/config.alloy`
- Modify: `docker-compose.yaml, compose.dev.yaml (régénérés)`

**Interfaces:**

- Consumes : Transform `default` (tâche 10), `faro.receiver` (tâche 11), `gclib.otlp_traces`, `gclib.span`, `gclib.trace_resources_and_spans`.
- Produces : Transform `otelcol.processor.transform "faro"` ; section `faro-traces`.

- [ ] **Étape 1 : écrire le contrôle qui échoue**

Dans `scripts/smoke.py`, repérer le texte exact suivant (unique dans le fichier) :

```python
# --- end of sections ---
```

et insérer juste **avant** lui :

```python
@section("faro-traces")
def faro_traces(c):
    """12.3.3 + 12.3.9 (traces): tenant/env validation, legacy deployment.environment -> env."""
    trace_id = g.new_trace_id()
    resource = {"service.name": f"web-{c.run}", "service.namespace": c.project, "deployment.environment": "prod", "tenant": "ACME"}
    spans = [
        g.span(trace_id, "valid", {"tenant": "acme"}, kind=3),
        g.span(trace_id, "reserved", {"tenant": "www"}, kind=3),
        g.span(trace_id, "badformat", {"tenant": "Acme Corp"}, kind=3),
    ]
    payload = g.faro_payload(c.faro_app(environment="prod"), "https://acme.example.me/", traces=g.otlp_traces(resource, spans))
    g.send_faro(c.s["GC_FARO_URL"], payload, c.s["FARO_API_KEY"])
    resource_attrs, _ = g.trace_resources_and_spans(c.wait_trace(trace_id))[0]
    expect(resource_attrs.get("project") == c.project, f"project not mapped from service.namespace: {resource_attrs}")
    expect(resource_attrs.get("env") == "prod", f"env not moved from deployment.environment: {resource_attrs}")
    expect("deployment.environment" not in resource_attrs, f"legacy attribute kept: {resource_attrs}")
    expect("tenant" not in resource_attrs, f"malformed resource tenant kept: {resource_attrs}")
    trace = g.tempo_trace(c.s["GC_TEMPO_URL"], trace_id)
    by_name = {s["name"]: g.otlp_attr_map(s.get("attributes")) for rs in trace["resourceSpans"] for sc in rs["scopeSpans"] for s in sc["spans"]}
    expect(by_name["valid"].get("tenant") == "acme", f"valid tenant removed: {by_name['valid']}")
    expect("tenant" not in by_name["reserved"], f"reserved tenant kept: {by_name['reserved']}")
    expect("tenant" not in by_name["badformat"], f"malformed tenant kept: {by_name['badformat']}")


```

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 harness/stack.py status | grep -q 'alloy .*ready' || python3 harness/stack.py down && python3 harness/stack.py up; python3 scripts/smoke.py --only faro-traces`

Attendu : `smoke: [FAIL] faro-traces: timeout … waiting for Tempo trace …` : sans `project`, la trace est rejetée par le filtre.

- [ ] **Étape 3 : insérer le transform `faro` et y brancher le récepteur**

Dans `config/alloy/config.alloy`, remplacer exactement (le bloc `faro.receiver` entier devient le récepteur suivi du transform) :

```alloy
faro.receiver "default" {
  server {
    listen_address = sys.env("BIND_ADDR")
    listen_port    = 12347
    // An empty FARO_API_KEY must never open the endpoint: fall back to an unguessable key
    // derived from the mandatory, secret IP hash salt.
    api_key = coalesce(sys.env("FARO_API_KEY"), sys.env("IP_HASH_SALT") + "-faro-disabled")
    // CORS is answered by Traefik (gc-faro-cors@file): a second header would break browsers.
    cors_allowed_origins     = []
    max_allowed_payload_size = coalesce(sys.env("FARO_MAX_PAYLOAD"), "5MiB")

    rate_limiting {
      enabled    = true
      rate       = coalesce(sys.env("FARO_RATE"), "100")
      burst_size = coalesce(sys.env("FARO_BURST"), "200")
    }
  }

  output {
    logs   = [loki.process.faro.receiver]
    traces = [otelcol.processor.transform.default.input]
  }
}

```

par :

```alloy
faro.receiver "default" {
  server {
    listen_address = sys.env("BIND_ADDR")
    listen_port    = 12347
    // An empty FARO_API_KEY must never open the endpoint: fall back to an unguessable key
    // derived from the mandatory, secret IP hash salt.
    api_key = coalesce(sys.env("FARO_API_KEY"), sys.env("IP_HASH_SALT") + "-faro-disabled")
    // CORS is answered by Traefik (gc-faro-cors@file): a second header would break browsers.
    cors_allowed_origins     = []
    max_allowed_payload_size = coalesce(sys.env("FARO_MAX_PAYLOAD"), "5MiB")

    rate_limiting {
      enabled    = true
      rate       = coalesce(sys.env("FARO_RATE"), "100")
      burst_size = coalesce(sys.env("FARO_BURST"), "200")
    }
  }

  output {
    logs   = [loki.process.faro.receiver]
    traces = [otelcol.processor.transform.faro.input]
  }
}

// Faro traces carry no page URL: validate instead of deducing (spec 6.5). project comes from
// the Faro SDK resource (service.namespace = app.namespace); client tenant/env must match
// [a-z0-9-]+ and must not be a reserved subdomain, otherwise they are deleted.
// Masking and the project/env filter then happen in the shared "default" transform.
otelcol.processor.transform "faro" {
  error_mode = "ignore"

  trace_statements {
    context    = "resource"
    statements = [
      `set(resource.attributes["project"], resource.attributes["service.namespace"]) where resource.attributes["project"] == nil and resource.attributes["service.namespace"] != nil`,
      `set(resource.attributes["project"], resource.attributes["app.namespace"]) where resource.attributes["project"] == nil and resource.attributes["app.namespace"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment.name"]) where resource.attributes["deployment.environment.name"] != nil`,
      `set(resource.attributes["env"], resource.attributes["deployment.environment"]) where resource.attributes["deployment.environment.name"] == nil and resource.attributes["deployment.environment"] != nil`,
      `delete_key(resource.attributes, "deployment.environment.name")`,
      `delete_key(resource.attributes, "deployment.environment")`,
      `delete_key(resource.attributes, "env") where resource.attributes["env"] != nil and (not IsMatch(resource.attributes["env"], "^[a-z0-9-]+$") or IsMatch(resource.attributes["env"], "^(` + string.replace(sys.env("RESERVED_SUBDOMAINS"), ",", "|") + `)$"))`,
      `delete_key(resource.attributes, "tenant") where resource.attributes["tenant"] != nil and (not IsMatch(resource.attributes["tenant"], "^[a-z0-9-]+$") or IsMatch(resource.attributes["tenant"], "^(` + string.replace(sys.env("RESERVED_SUBDOMAINS"), ",", "|") + `)$"))`,
    ]
  }

  trace_statements {
    context    = "span"
    statements = [
      `delete_key(span.attributes, "tenant") where span.attributes["tenant"] != nil and (not IsMatch(span.attributes["tenant"], "^[a-z0-9-]+$") or IsMatch(span.attributes["tenant"], "^(` + string.replace(sys.env("RESERVED_SUBDOMAINS"), ",", "|") + `)$"))`,
    ]
  }

  output {
    traces = [otelcol.processor.transform.default.input]
  }
}

```

Run : `python3 scripts/render.py && python3 scripts/check.py --only render,size,validators,secrets`

Attendu : quatre `[PASS]`.

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 harness/stack.py stop alloy && python3 harness/stack.py start alloy && python3 scripts/smoke.py`

Attendu : `8/8 sections passed`.

- [ ] **Étape 5 : commit**

```bash
git add scripts/smoke.py config/alloy/config.alloy docker-compose.yaml compose.dev.yaml
git commit -m "feat(alloy): validate faro traces tenant and env"
```


### Task 13: Métriques : span-metrics, métriques OTLP et rétention effective

Ces comportements sont configurés par les tâches 5 et 6 : les contrôles passent donc dès leur
écriture. Pour respecter la règle du § 12 (« chaque test est prouvé capable d'échouer »), chaque
contrôle est rejoué une fois contre une configuration volontairement cassée, puis la
configuration est restaurée par `git checkout`. Faits vérifiés : Tempo cherche une dimension
dans les attributs de span puis de ressource (les deux cas donnent `tenant`) ; Prometheus 3
suffixe les compteurs en `_total` et remplit `job` avec `service.name` ; `/config` de Loki
normalise les durées (`168h` → `1w`, `720h` → `30d`) et Prometheus les tailles (`100GB` →
`100GiB`, unités en base 2) : les comparaisons passent par `duration_seconds` et `size_bytes`.

**Files:**

- Modify: `scripts/smoke.py`

**Interfaces:**

- Consumes : `config/tempo/tempo.yaml`, `config/prometheus/prometheus.yml` (tâches 5, 6) ; `gclib.prom_query`, `gclib.duration_seconds`, `gclib.size_bytes`, `gclib.yaml_section_value`.
- Produces : Sections `spanmetrics`, `otlp-metrics`, `retention` (placées avant le marqueur de fin ; la tâche 16 insère `correlation` avant `retention`).

- [ ] **Étape 1 : écrire les contrôles**

Dans `scripts/smoke.py`, repérer le texte exact suivant (unique dans le fichier) :

```python
# --- end of sections ---
```

et insérer juste **avant** lui :

```python
@section("spanmetrics")
def spanmetrics(c):
    """12.3.6: span-metrics carry tenant whether it is a resource or a span attribute."""
    cases = {"res": ({"tenant": "t-res"}, {}), "span": ({}, {"tenant": "t-span"})}
    for name, (resource_extra, span_attrs) in cases.items():
        trace_id = g.new_trace_id()
        resource = c.resource(f"sm-{name}", **resource_extra)
        attributes = {"http.route": "/{tenant}/assets/{id}", **span_attrs}
        g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "GET /{tenant}/assets/{id}", attributes)]))
    for name, tenant in (("res", "t-res"), ("span", "t-span")):
        expr = f'traces_spanmetrics_calls_total{{service="sm-{name}-{c.run}", tenant="{tenant}"}}'
        metric = c.wait_prom(expr, timeout=150)[0]["metric"]
        expect(metric.get("project") == c.project and metric.get("env") == "prod", f"span-metrics labels {metric}")
        expect(metric.get("http_route") == "/{tenant}/assets/{id}", f"http.route dimension {metric}")


@section("otlp-metrics")
def otlp_metrics(c):
    """12.3.7: project, env, tenant are labels of the metric (not only target_info); service is job."""
    resource = c.resource("metrics", tenant="acme")
    name = f"smoke_{c.run}_orders"
    g.send_otlp(c.s["GC_OTLP_URL"], "metrics", g.otlp_sum(resource, name, 7))
    metric = c.wait_prom(f"{name}_total")[0]["metric"]
    expect(metric.get("job") == resource["service.name"], f"job != service.name: {metric}")
    expect((metric.get("project"), metric.get("env"), metric.get("tenant")) == (c.project, "prod", "acme"), f"labels {metric}")
    expect("service_name" not in metric, f"service.name promoted as a duplicate label: {metric}")


@section("retention")
def retention(c):
    """12.3.11: effective retention of Loki, Tempo and Prometheus matches the variables."""
    s = c.s
    loki = g.http("GET", s["GC_LOKI_URL"] + "/config").body.decode()
    limits = loki[loki.index("\nlimits_config:") :]
    default = g.yaml_section_value(limits.lstrip("\n"), "limits_config", "retention_period")
    expect(g.duration_seconds(default) == g.duration_seconds(s["LOKI_RETENTION_DEFAULT"]), f"Loki default retention {default}")
    stream_period = g.yaml_section_value(limits.lstrip("\n"), "limits_config", "period")
    expect(g.duration_seconds(stream_period) == g.duration_seconds(s["LOKI_RETENTION_PROD"]), f"Loki prod retention {stream_period}")
    expect("selector: '{env=\"prod\"}'" in limits, "Loki retention_stream selector for env=prod missing")
    tempo = g.http("GET", s["GC_TEMPO_URL"] + "/status/config").body.decode()
    block = g.yaml_section_value(tempo, "compactor", "block_retention")
    expect(g.duration_seconds(block) == g.duration_seconds(s["TEMPO_RETENTION"]), f"Tempo block_retention {block}")
    flags = g.get_json(s["GC_PROM_URL"] + "/api/v1/status/flags")["data"]
    expect(flags["storage.tsdb.retention.time"] == s["PROM_RETENTION_TIME"], f"Prometheus retention.time {flags['storage.tsdb.retention.time']}")
    size = flags["storage.tsdb.retention.size"]
    expect(g.size_bytes(size) == g.size_bytes(s["PROM_RETENTION_SIZE"]), f"Prometheus retention.size {size}")


```

- [ ] **Étape 2 : lancer — succès attendu d'emblée**

Run : `python3 harness/stack.py status | grep -q 'alloy .*ready' || python3 harness/stack.py down && python3 harness/stack.py up; python3 scripts/smoke.py --only spanmetrics,otlp-metrics,retention`

Attendu : `3/3 sections passed` (span-metrics : jusqu'à ~2 min, le temps du flush du metrics-generator).

- [ ] **Étape 3 : prouver que `spanmetrics` peut échouer**

Run : `sed -i '/^        - tenant$/d' config/tempo/tempo.yaml && python3 harness/stack.py stop tempo && python3 harness/stack.py start tempo && python3 scripts/smoke.py --only spanmetrics`

Attendu : `[FAIL] spanmetrics: timeout … traces_spanmetrics_calls_total{…tenant=…}`, code 1.

Run : `git checkout -- config/tempo/tempo.yaml && python3 harness/stack.py stop tempo && python3 harness/stack.py start tempo`

Attendu : `stack: tempo ready on 127.0.10.3`.

- [ ] **Étape 4 : prouver que `otlp-metrics` peut échouer**

Run : `sed -i '/^    - tenant$/d' config/prometheus/prometheus.yml && python3 harness/stack.py stop prometheus && python3 harness/stack.py start prometheus && python3 scripts/smoke.py --only otlp-metrics`

Attendu : `[FAIL] otlp-metrics: labels {…}` (pas de `tenant`), code 1.

Run : `git checkout -- config/prometheus/prometheus.yml && python3 harness/stack.py stop prometheus && python3 harness/stack.py start prometheus`

Attendu : `stack: prometheus ready on 127.0.10.4`.

- [ ] **Étape 5 : prouver que `retention` peut échouer**

Run : `LOKI_RETENTION_PROD=721h python3 scripts/smoke.py --only retention`

Attendu : `[FAIL] retention: Loki prod retention 30d`, code 1.

Run : `python3 scripts/smoke.py`

Attendu : `11/11 sections passed`.

- [ ] **Étape 6 : commit**

```bash
git add scripts/smoke.py
git commit -m "test(smoke): check span-metrics, otlp metric labels and retention"
```


### Task 14: Robustesse (§ 12.5)

Tests du banc, gérés de bout en bout (ils arrêtent le banc au début et à la fin) : Loki arrêté
pendant un envoi, redémarrage d'`alloy` avec des données en file (rejouées depuis
`otelcol.storage.file`), envoi massif (8 × 40 lots de 500 logs) sous l'enveloppe mémoire
d'`alloy` (`mem_limit` 768 Mio, mesurée par `VmRSS`), `PROM_ENABLE_FEATURES` vide (vérifié :
Prometheus 3.15 accepte `--enable-feature=` vide, aucun défaut de repli n'est nécessaire),
`config-guard` qui échoue et bloque tout démarrage (fichier devenu dossier, contenu altéré,
fichier vide), sur une copie de `config/` passée par `--config-dir`. Le comportement testé existe
déjà : le test passe d'emblée, puis la preuve d'échec retire la file persistante et constate la
perte.

**Files:**

- Test: `tests/test_robustness.py`

**Interfaces:**

- Consumes : `harness/stack.py` : `up --config-dir`, `stop`, `start`, `status`, `down` ; `scripts/gclib.py`.
- Produces : `tests/test_robustness.py` (`GC_HARNESS=1`), méthodes `test_1_…` à `test_5_…` exécutées dans l'ordre des noms.

- [ ] **Étape 1 : écrire les tests**

Fichier complet `tests/test_robustness.py` :

```python
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import ROOT, require_harness, run

sys.path.insert(0, str(ROOT / "scripts"))
import gclib as g  # noqa: E402

STACK = [sys.executable, str(ROOT / "harness" / "stack.py")]
ALLOY_MEM_LIMIT = 768 * 1024 * 1024


def stack(*args, timeout=400):
    result = run([*STACK, *args], timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(f"stack.py {' '.join(args)} failed:\n{result.stdout}")
    return result


def pid_of(name):
    return json.loads((ROOT / ".harness" / "state.json").read_text(encoding="utf-8"))["processes"][name]["pid"]


def rss_bytes(pid):
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return 0


class RobustnessTest(unittest.TestCase):
    """Spec 12.5, on the native harness. Methods run in name order; the last one stops the stack."""

    @classmethod
    def setUpClass(cls):
        require_harness(cls)
        run([*STACK, "down"], timeout=120)
        stack("up")
        cls.settings = g.settings()

    @classmethod
    def tearDownClass(cls):
        run([*STACK, "down"], timeout=120)

    def send_log(self, service):
        resource = {"project": "robust", "deployment.environment.name": "prod", "service.name": service}
        g.send_otlp("http://alloy:4318", "logs", g.otlp_logs(resource, f"durable {service}"))

    def wait_log(self, service, timeout=180):
        g.wait_for(lambda: g.loki_entries("http://loki:3100", f'{{service_name="{service}"}}'), f"log {service} in Loki", timeout, interval=3)

    def test_1_loki_outage_is_absorbed(self):
        service = f"outage-{g.run_id()}"
        stack("stop", "loki")
        self.send_log(service)
        time.sleep(5)
        stack("start", "loki")
        self.wait_log(service)

    def test_2_alloy_restart_replays_the_persistent_queue(self):
        service = f"replay-{g.run_id()}"
        stack("stop", "loki")
        self.send_log(service)
        time.sleep(5)
        stack("stop", "alloy")
        stack("start", "alloy")
        stack("start", "loki")
        self.wait_log(service)

    def test_3_massive_send_stays_in_the_memory_envelope(self):
        pid = pid_of("alloy")
        peak = [rss_bytes(pid)]
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                peak[0] = max(peak[0], rss_bytes(pid))
                time.sleep(0.2)

        def burst(worker):
            resource = {"project": "robust", "deployment.environment.name": "prod", "service.name": f"burst-{worker}"}
            record = g.otlp_logs(resource, "x" * 512)["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
            payload = {"resourceLogs": [{"resource": {"attributes": g.attrs(resource)}, "scopeLogs": [{"logRecords": [record] * 500}]}]}
            for _ in range(40):
                g.http("POST", "http://alloy:4318/v1/logs", payload, timeout=30)

        watcher = threading.Thread(target=watch)
        watcher.start()
        workers = [threading.Thread(target=burst, args=(i,)) for i in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        stop.set()
        watcher.join()
        self.assertLess(peak[0], ALLOY_MEM_LIMIT, f"alloy RSS peaked at {peak[0] / 2**20:.0f} MiB")
        status = stack("status").stdout
        for name in ("loki", "tempo", "prometheus", "alloy", "alloy-gateway", "node-exporter"):
            self.assertRegex(status, rf"{name}\s+pid=\d+\s+running ready")

    def test_4_empty_prometheus_features_start_normally(self):
        flags = g.get_json("http://prometheus:9090/api/v1/status/flags")["data"]
        self.assertEqual(flags["enable-feature"], "")
        self.assertEqual(g.http("GET", "http://prometheus:9090/-/ready").status, 200)

    def test_5_config_guard_failure_blocks_every_service(self):
        stack("down")
        for breakage, message in (("directory", "is a directory"), ("altered", "differs from source"), ("empty", "empty")):
            with self.subTest(breakage=breakage), tempfile.TemporaryDirectory() as tmp:
                config = Path(tmp) / "config"
                shutil.copytree(ROOT / "config", config, ignore=shutil.ignore_patterns("__pycache__"))
                target = config / "loki" / "loki.yaml"
                if breakage == "directory":
                    target.unlink()
                    target.mkdir()
                elif breakage == "altered":
                    target.write_text(target.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
                else:
                    target.write_text("", encoding="utf-8")
                result = run([*STACK, "up", "--config-dir", str(config)], timeout=120)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stdout)
                self.assertIn("no service started", result.stdout)
                with socket.socket() as sock:
                    self.assertNotEqual(sock.connect_ex(("127.0.10.2", 3100)), 0, "loki started despite config-guard")
                run([*STACK, "down"], timeout=120)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_robustness.py -v`

Attendu : `OK (skipped=1)` sans `GC_HARNESS` (la classe entière est sautée).

Run : `GC_HARNESS=1 python3 -m unittest discover -s tests -p test_robustness.py -v`

Attendu : `Ran 5 tests` … `OK` (≈ 2 min).

- [ ] **Étape 3 : prouver que le test de rejeu peut échouer, puis restaurer**

Run : `sed -i '/storage = otelcol.storage.file.queue.handler/d' config/alloy/config.alloy && GC_HARNESS=1 python3 -m unittest discover -s tests -p test_robustness.py -k test_2 -v`

Attendu : FAIL « timeout after 180s waiting for log replay-… in Loki » : sans file sur disque, le redémarrage d'`alloy` perd les données.

Run : `git checkout -- config/alloy/config.alloy && git status --short`

Attendu : aucune sortie : la configuration est restaurée.

- [ ] **Étape 4 : commit**

```bash
git add tests/test_robustness.py
git commit -m "test(robustness): check outages, queue replay, memory and config-guard"
```


### Task 15: Traefik : modèle de middlewares, bordure du banc et `security.py`

Le modèle `traefik/grafana-coolify.yaml.example` définit `gc-otlp-auth` (avec
`removeHeader: true` : les identifiants ne sont pas relayés), `gc-faro-cors`,
`gc-faro-ratelimit`, `gc-faro-body`. Décision du plan (écart assumé avec l'ordre écrit au § 5.4) :
le label d'`alloy` place `gc-faro-ratelimit@file` **en premier**, car le middleware CORS de
Traefik répond lui-même aux requêtes `OPTIONS` ; placé avant, il les soustrairait à la limite de
débit, contrairement au § 5.3 (vérifié : 409 réponses 429 sur 600 préalables concurrentes).
`harness/edge.py` lance un Traefik HTTP qui reproduit les routeurs que Coolify dériverait des
labels `coolify.traefik.middlewares` et des variables `SERVICE_FQDN_*`, plus un routeur
`other.gc.test` (« un autre domaine public du serveur »), et un Grafana 13.2.2 (admin/admin,
anonyme désactivé, port 3300 : 3000 est souvent pris) avec un compte de service Admin.
`security.py` couvre les huit points du § 12.4. Pour la taille maximale, il envoie l'en-tête
`Expect: 100-continue` sur une socket brute, comme `curl` : Traefik répond 413 avant le corps,
alors qu'`urllib` enverrait tout le corps et recevrait un « connection reset » (constaté).

**Files:**

- Create: `traefik/grafana-coolify.yaml.example`
- Create: `harness/edge.py`
- Create: `scripts/security.py`

**Interfaces:**

- Consumes : `harness/stack.py` : `SERVICE_IPS`, `HARNESS`, `BIN`, `load_compose`, `load_state`, `save_state`, `spawn`, `alive`, `terminate`, `preflight`, `http_ok`, `HarnessError`.
- Consumes : `scripts/check.py` : `compose_json()` (contrôle 8 en mode banc) ; `scripts/gclib.py` : `settings`, `http`.
- Produces : `harness/edge.py` : Traefik sur `127.0.10.100:8080` (routeurs `Host(alloy.gc.test)`, `Host(alloy-gateway.gc.test)`, `Host(other.gc.test)`), Grafana sur `127.0.10.101:3300`, utilisateurs `proj-a` / `harness-pass-a1` et `proj-b` / `harness-pass-b2` (révocable), origine valide `https://acme.example.me`. Écrit `.harness/edge.json` (`traefik_url`, `traefik_ip`, `hosts`, `user`, `password`, `revocable_user`, `revocable_password`, `origin_ok`, `grafana_url`, `grafana_token`) et `.harness/runtime.env` (`GRAFANA_URL`, `GRAFANA_SA_TOKEN`), tous deux en 0600. CLI `python3 harness/edge.py up | down | revoke USER | restore`.
- Produces : `scripts/security.py` : `python3 scripts/security.py [--remote] [--only 1,…,8]` ; en mode distant, variables `GC_PUBLIC_IP`, `GC_FARO_PUBLIC_URL`, `GC_GATEWAY_PUBLIC_URL`, `GC_OTHER_PUBLIC_URL`, `GC_GATEWAY_USER`, `GC_GATEWAY_PASSWORD`, `GC_REVOKED_USER`, `GC_REVOKED_PASSWORD`, `GC_ORIGIN_OK`, `FARO_API_KEY`, `GC_ALLOY_CONTAINER`, `GC_GATEWAY_CONTAINER`.

- [ ] **Étape 1 : écrire les contrôles qui échouent**

Fichier complet `scripts/security.py` :

```python
#!/usr/bin/env python3
"""Security checks of spec 12.4 (standard library only).

Harness mode (default): the public side is Traefik on the harness edge (harness/edge.py up).
"Unreachable from outside" means: no listener on the Traefik-facing address and no route.
Remote mode (--remote): against a Coolify deployment; set
  GC_PUBLIC_IP, GC_FARO_PUBLIC_URL, GC_GATEWAY_PUBLIC_URL, GC_OTHER_PUBLIC_URL,
  GC_GATEWAY_USER, GC_GATEWAY_PASSWORD, GC_REVOKED_USER, GC_REVOKED_PASSWORD, GC_ORIGIN_OK,
  FARO_API_KEY, and for item 8 GC_ALLOY_CONTAINER, GC_GATEWAY_CONTAINER (run on the server).

Usage: python3 scripts/security.py [--remote] [--only 1,2,...]
"""

import argparse
import concurrent.futures
import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.parse
from pathlib import Path

import gclib as g

ROOT = Path(__file__).resolve().parent.parent
INTERNAL_PORTS = [3100, 3200, 9090, 9100, 4317, 4318, 9095, 9096, 12345, 12347]
INTERNAL_NAMES = ["loki", "tempo", "prometheus", "node-exporter"]
EVIL_ORIGINS = ["https://evil-example.me", "https://x.example.me.attacker.com"]
HARDENED = ("alloy", "alloy-gateway")


class Target:
    def __init__(self, remote):
        s = g.settings()
        self.remote = remote
        self.faro_key = s["FARO_API_KEY"]
        if remote:
            env = os.environ
            self.public_ip = env["GC_PUBLIC_IP"]
            self.faro = (env["GC_FARO_PUBLIC_URL"].rstrip("/"), None)
            self.gateway = (env["GC_GATEWAY_PUBLIC_URL"].rstrip("/"), None)
            self.other = (env["GC_OTHER_PUBLIC_URL"].rstrip("/"), None)
            self.user = (env["GC_GATEWAY_USER"], env["GC_GATEWAY_PASSWORD"])
            self.revoked = (env["GC_REVOKED_USER"], env["GC_REVOKED_PASSWORD"])
            self.origin_ok = env["GC_ORIGIN_OK"]
        else:
            edge = s["edge"]
            if not edge:
                raise SystemExit("security: start the edge first: python3 harness/edge.py up")
            url = edge["traefik_url"]
            self.public_ip = edge["traefik_ip"]
            self.faro = (url, edge["hosts"]["alloy"])
            self.gateway = (url, edge["hosts"]["alloy-gateway"])
            self.other = (url, edge["hosts"]["other"])
            self.user = (edge["user"], edge["password"])
            self.revoked = (edge["revocable_user"], edge["revocable_password"])
            self.origin_ok = edge["origin_ok"]

    def faro_request(self, method, body=None, headers=None):
        url, host = self.faro
        return g.http(method, url + "/collect", body, headers=headers, host=host)

    def gateway_post(self, auth=None, headers=None):
        url, host = self.gateway
        return g.http("POST", url + "/v1/logs", {"resourceLogs": []}, headers=headers, host=host, auth=auth)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def preflight_headers(origin):
    return {"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-api-key"}


def item1_unreachable(t):
    """1. Internal services and ports unreachable from outside."""
    for port in INTERNAL_PORTS:
        with socket.socket() as sock:
            sock.settimeout(2)
            reachable = sock.connect_ex((t.public_ip, port)) == 0
        expect(not reachable, f"port {port} answers on the public address {t.public_ip}")
    if not t.remote:
        url, _host = t.faro
        for name in INTERNAL_NAMES + [f"{n}.gc.test" for n in INTERNAL_NAMES]:
            status = g.http("GET", url + "/ready", host=name).status
            expect(status == 404, f"Traefik routes Host {name} (HTTP {status})")


def item2_gateway_auth(t):
    """2. OTLP gateway: 401 without credentials, 401 once revoked (hot reload), 2xx when valid."""
    expect(t.gateway_post().status == 401, "gateway without credentials is not 401")
    expect(t.gateway_post(auth=(t.user[0], "wrong-password")).status == 401, "gateway with a wrong password is not 401")
    expect(t.gateway_post(auth=t.user).status // 100 == 2, "gateway rejects valid credentials")
    if t.remote:
        expect(t.gateway_post(auth=t.revoked).status == 401, "revoked credentials still accepted")
        return
    expect(t.gateway_post(auth=t.revoked).status // 100 == 2, "revocable user should work before revocation")
    subprocess.run([sys.executable, str(ROOT / "harness" / "edge.py"), "revoke", t.revoked[0]], check=True)
    try:
        expect(t.gateway_post(auth=t.revoked).status == 401, "revoked credentials still accepted")
        expect(t.gateway_post(auth=t.user).status // 100 == 2, "revocation broke the other project")
    finally:
        subprocess.run([sys.executable, str(ROOT / "harness" / "edge.py"), "restore"], check=True)


def item3_cors(t):
    """3. Faro CORS: valid preflight headers, nothing for foreign origins, a single ACAO."""
    resp = t.faro_request("OPTIONS", headers=preflight_headers(t.origin_ok))
    expect(resp.status == 200, f"valid preflight: HTTP {resp.status}")
    expect(resp.header_values("Access-Control-Allow-Origin") == [t.origin_ok], f"ACAO {resp.header_values('Access-Control-Allow-Origin')}")
    methods = {m.strip() for m in resp.headers.get("Access-Control-Allow-Methods", "").split(",")}
    expect(methods == {"POST", "OPTIONS"}, f"methods {methods}")
    allowed = {h.strip().lower() for h in resp.headers.get("Access-Control-Allow-Headers", "").split(",")}
    expect(allowed == {"content-type", "x-api-key", "x-faro-session-id"}, f"headers {allowed}")
    expect(resp.headers.get("Access-Control-Max-Age") == "600", f"max-age {resp.headers.get('Access-Control-Max-Age')}")
    for origin in EVIL_ORIGINS:
        evil = t.faro_request("OPTIONS", headers=preflight_headers(origin))
        expect(not evil.header_values("Access-Control-Allow-Origin"), f"CORS header returned for {origin}")
    post = t.faro_request("POST", {"meta": {}}, headers={"Origin": t.origin_ok, "x-api-key": t.faro_key})
    expect(len(post.header_values("Access-Control-Allow-Origin")) == 1, f"ACAO count {post.header_values('Access-Control-Allow-Origin')}")
    expect("Origin" in ",".join(post.header_values("Vary")), f"Vary {post.header_values('Vary')}")


def item4_faro_key(t):
    """4. Faro without key or with a wrong key: rejected."""
    expect(t.faro_request("POST", {"meta": {}}).status == 401, "Faro without key accepted")
    expect(t.faro_request("POST", {"meta": {}}, headers={"x-api-key": "wrong-key"}).status == 401, "Faro with a wrong key accepted")


def item5_rate_limit(t):
    """5. A burst beyond the limit gets 429 from Traefik (preflights never reach Alloy)."""

    def one(_):
        return t.faro_request("OPTIONS", headers=preflight_headers(t.origin_ok)).status

    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as pool:
        statuses = list(pool.map(one, range(600)))
    expect(429 in statuses, f"no 429 in a burst of 600 requests: {sorted(set(statuses))}")


def read_status(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return int(data.split(b" ", 2)[1]) if data.startswith(b"HTTP/") else 0


def oversized_post(t, size):
    """POST with `Expect: 100-continue`, like curl: Traefik answers 413 before the body is sent."""
    url, host = t.faro
    parts = urllib.parse.urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    raw = socket.create_connection((parts.hostname, port), timeout=15)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname) if parts.scheme == "https" else raw
    with sock:
        head = (
            f"POST /collect HTTP/1.1\r\nHost: {host or parts.netloc}\r\nContent-Type: application/json\r\n"
            f"x-api-key: {t.faro_key}\r\nContent-Length: {size}\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n"
        )
        sock.sendall(head.encode())
        status = read_status(sock)
        if status == 100:
            try:
                sock.sendall(b" " * size)
            except OSError:
                pass
            status = read_status(sock)
        return status


def item6_body_size(t):
    """6. A body beyond the maximum size: 413."""
    status = oversized_post(t, 6 * 1024 * 1024)
    expect(status == 413, f"6 MiB body: HTTP {status}")


def item7_middleware_leak(t):
    """7. Coolify #9886: middlewares must not leak to other routers."""
    faro = t.faro_request("OPTIONS", headers=preflight_headers(t.origin_ok))
    expect(faro.status != 401 and "Basic" not in faro.headers.get("WWW-Authenticate", ""), "alloy asks for Basic Auth")
    gateway = t.gateway_post(auth=t.user, headers={"Origin": t.origin_ok})
    expect(not gateway.header_values("Access-Control-Allow-Origin"), "alloy-gateway returns a CORS header")
    url, host = t.other
    other = g.http("GET", url + "/api/health", headers={"Origin": t.origin_ok}, host=host)
    expect(other.status != 401 and "Basic" not in other.headers.get("WWW-Authenticate", ""), f"other domain asks for auth (HTTP {other.status})")
    expect(not other.header_values("Access-Control-Allow-Origin"), "other domain returns the package CORS header")


def hardening_errors(name, user, read_only, cap_drop, security_opt, host_mounts):
    errors = []
    if not user or user.split(":")[0] in ("", "0", "root"):
        errors.append(f"{name}: runs as root (user={user!r})")
    if not read_only:
        errors.append(f"{name}: root filesystem is writable")
    if "ALL" not in [c.upper() for c in cap_drop or []]:
        errors.append(f"{name}: cap_drop ALL missing")
    if not any(o.replace("=", ":") == "no-new-privileges:true" for o in security_opt or []):
        errors.append(f"{name}: no-new-privileges missing")
    for source, target, writable in host_mounts:
        errors.append(f"{name}: host mount {source} -> {target}{' (rw)' if writable else ''}")
    return errors


def item8_hardening(t):
    """8. alloy and alloy-gateway: no host mount except their read-only config, non-root, read-only FS."""
    errors = []
    if t.remote:
        for name, env in (("alloy", "GC_ALLOY_CONTAINER"), ("alloy-gateway", "GC_GATEWAY_CONTAINER")):
            info = json.loads(subprocess.run(["docker", "inspect", os.environ[env]], capture_output=True, text=True, check=True).stdout)[0]
            mounts = [
                (m["Source"], m["Destination"], m.get("RW", True))
                for m in info["Mounts"]
                if m["Type"] == "bind" and not (m["Destination"] == "/etc/alloy/config.alloy" and not m.get("RW", True))
            ]
            host = info["HostConfig"]
            errors += hardening_errors(name, info["Config"]["User"], host["ReadonlyRootfs"], host["CapDrop"], host["SecurityOpt"], mounts)
    else:
        sys.path.insert(0, str(ROOT / "scripts"))
        import check  # noqa: PLC0415 - reuse the docker compose config helper

        services = check.compose_json()["services"]
        for name in HARDENED:
            svc = services[name]
            mounts = [
                (v["source"], v["target"], not v.get("read_only", False))
                for v in svc.get("volumes", [])
                if v["type"] == "bind" and not (v["target"] == "/etc/alloy/config.alloy" and v.get("read_only"))
            ]
            errors += hardening_errors(name, svc.get("user"), svc.get("read_only"), svc.get("cap_drop"), svc.get("security_opt"), mounts)
    expect(not errors, "; ".join(errors))


ITEMS = {
    "1": item1_unreachable,
    "2": item2_gateway_auth,
    "3": item3_cors,
    "4": item4_faro_key,
    "5": item5_rate_limit,
    "6": item6_body_size,
    "7": item7_middleware_leak,
    "8": item8_hardening,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify security checks (spec 12.4)")
    parser.add_argument("--remote", action="store_true", help="target a Coolify deployment (see module docstring)")
    parser.add_argument("--only", help="comma-separated item numbers")
    args = parser.parse_args(argv)
    target = Target(args.remote)
    names = args.only.split(",") if args.only else list(ITEMS)
    failed = 0
    for name in names:
        func = ITEMS[name]
        try:
            func(target)
            print(f"security: [PASS] {func.__doc__.splitlines()[0]}")
        except (AssertionError, KeyError, OSError, subprocess.CalledProcessError) as exc:
            failed += 1
            print(f"security: [FAIL] {func.__doc__.splitlines()[0]} -> {exc}")
    print(f"security: {len(names) - failed}/{len(names)} items passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

Run : `chmod +x scripts/security.py`

Attendu : aucune sortie.

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 harness/stack.py down && python3 harness/stack.py up && python3 scripts/security.py`

Attendu : « security: start the edge first: python3 harness/edge.py up », code 1.

- [ ] **Étape 3 : écrire le modèle Traefik et la bordure du banc**

Fichier complet `traefik/grafana-coolify.yaml.example` :

```yaml
# grafana-coolify - Traefik middlewares (dynamic configuration, spec 5.4).
# Paste into Coolify -> Servers -> Proxy -> Dynamic Configurations (one file per server).
# Replace before use:
#   - the htpasswd lines: one per project, generated with `openssl passwd -apr1`, written between
#     double quotes, with `$` NOT doubled;
#   - the regular expression of the origins allowed to send Faro data.
# Revoking a project = deleting its line: Traefik reloads this file without any redeployment.
# The services reference these middlewares through the coolify.traefik.middlewares labels of
# compose.template.yaml (alloy-gateway: gc-otlp-auth; alloy: the three gc-faro-* middlewares).
http:
  middlewares:
    gc-otlp-auth:
      basicAuth:
        # Do not forward the credentials to alloy-gateway.
        removeHeader: true
        users:
          - "example-project:$apr1$CHANGEME$0000000000000000000000"
    gc-faro-cors:
      headers:
        accessControlAllowOriginListRegex:
          - "^https://([a-z0-9-]+\\.)?example\\.com$"
        accessControlAllowMethods:
          - POST
          - OPTIONS
        accessControlAllowHeaders:
          - Content-Type
          - x-api-key
          - x-faro-session-id
        accessControlMaxAge: 600
        addVaryHeader: true
    gc-faro-ratelimit:
      # Per source IP. An agency behind one NAT shares this quota: keep it generous.
      # Listed first on alloy so that CORS preflight requests are counted too (spec 5.3).
      rateLimit:
        average: 50
        burst: 100
        period: 1s
    gc-faro-body:
      buffering:
        # Same value as FARO_MAX_PAYLOAD (5MiB).
        maxRequestBodyBytes: 5242880
```

Fichier complet `harness/edge.py` :

```python
#!/usr/bin/env python3
"""Harness edge: Traefik emulating the routers Coolify generates, plus a test Grafana.

- Traefik (HTTP only, 127.0.10.100:8080) loads a file-provider config built from
  traefik/grafana-coolify.yaml.example (test htpasswd lines and origin regex) and one router per
  service carrying a `coolify.traefik.middlewares` label and a SERVICE_FQDN_<SERVICE>_<PORT>
  variable: Host(<service>.gc.test) -> service IP:PORT with the label's middlewares.
  A router Host(other.gc.test) -> Grafana stands for "another public domain of the server".
- Grafana OSS (127.0.10.101:3300, admin/admin, anonymous access disabled); a service account
  with the Admin role is created and its token written to .harness/runtime.env and edge.json.

Usage: python3 harness/edge.py up | down | revoke USER | restore
Needs `python3 harness/stack.py up` first (service IPs, /etc/hosts).
"""

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import yaml

import stack

ROOT = stack.ROOT
EDGE_DIR = stack.HARNESS / "edge"
DYNAMIC = EDGE_DIR / "dynamic.yml"
EDGE_JSON = stack.HARNESS / "edge.json"
RUNTIME_ENV = stack.HARNESS / "runtime.env"
TRAEFIK_IP, TRAEFIK_PORT = "127.0.10.100", 8080
GRAFANA_IP, GRAFANA_PORT = "127.0.10.101", 3300
DOMAIN = "gc.test"
USERS = {"proj-a": "harness-pass-a1", "proj-b": "harness-pass-b2"}
REVOCABLE_USER = "proj-b"
ORIGIN_REGEX = r"^https://([a-z0-9-]+\.)?example\.(me|app)$"
LABEL = "coolify.traefik.middlewares="


def htpasswd_line(user, password):
    salt = hashlib.sha256(user.encode()).hexdigest()[:8]
    result = subprocess.run(["openssl", "passwd", "-apr1", "-salt", salt, password], capture_output=True, text=True, check=True)
    return f"{user}:{result.stdout.strip()}"


def public_routes(services):
    """[(service, port, [middlewares])] for services Coolify would expose publicly."""
    routes = []
    for name, service in services.items():
        labels = service.get("labels") or []
        middlewares = [m for label in labels if label.startswith(LABEL) for m in label[len(LABEL) :].split(",")]
        prefix = "SERVICE_FQDN_" + name.upper().replace("-", "_") + "_"
        ports = [key[len(prefix) :] for key in (service.get("environment") or {}) if key.startswith(prefix)]
        for port in ports:
            routes.append((name, int(port), middlewares))
    return routes


def dynamic_config(services, users):
    example = yaml.safe_load((ROOT / "traefik" / "grafana-coolify.yaml.example").read_text(encoding="utf-8"))
    middlewares = example["http"]["middlewares"]
    middlewares["gc-otlp-auth"]["basicAuth"]["users"] = [htpasswd_line(u, USERS[u]) for u in users]
    middlewares["gc-faro-cors"]["headers"]["accessControlAllowOriginListRegex"] = [ORIGIN_REGEX]
    routers, backends = {}, {}
    for name, port, used in public_routes(services):
        # Coolify references the middlewares with their provider suffix (@file); in the harness
        # they live in the same file provider, so the suffix is kept as-is.
        routers[name] = {"rule": f"Host(`{name}.{DOMAIN}`)", "entryPoints": ["web"], "service": name, "middlewares": used}
        backends[name] = {"loadBalancer": {"servers": [{"url": f"http://{stack.SERVICE_IPS[name]}:{port}"}]}}
    routers["other"] = {"rule": f"Host(`other.{DOMAIN}`)", "entryPoints": ["web"], "service": "other"}
    backends["other"] = {"loadBalancer": {"servers": [{"url": f"http://{GRAFANA_IP}:{GRAFANA_PORT}"}]}}
    return {"http": {"middlewares": middlewares, "routers": routers, "services": backends}}


def write_dynamic(users):
    EDGE_DIR.mkdir(parents=True, exist_ok=True)
    services = stack.load_compose()["services"]
    DYNAMIC.write_text(yaml.safe_dump(dynamic_config(services, users), sort_keys=True), encoding="utf-8")


def wait(check, what, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise stack.HarnessError(f"timeout waiting for {what}")


def gateway_status(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}/v1/logs",
        data=b'{"resourceLogs":[]}',
        headers={"Host": f"alloy-gateway.{DOMAIN}", "Content-Type": "application/json", "Authorization": "Basic " + token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def grafana_api(method, path, body=None):
    auth = base64.b64encode(b"admin:admin").decode()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://{GRAFANA_IP}:{GRAFANA_PORT}{path}", data=data, method=method)
    req.add_header("Authorization", "Basic " + auth)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or b"null")


def service_account_token():
    found = grafana_api("GET", "/api/serviceaccounts/search?query=gc-setup")["serviceAccounts"]
    account = found[0] if found else grafana_api("POST", "/api/serviceaccounts", {"name": "gc-setup", "role": "Admin"})
    token = grafana_api("POST", f"/api/serviceaccounts/{account['id']}/tokens", {"name": f"harness-{int(time.time())}"})
    return token["key"]


def cmd_up(_args):
    state = stack.load_state()
    if not any(stack.alive(p["pid"]) for p in state["processes"].values()):
        raise stack.HarnessError("start the stack first: python3 harness/stack.py up")
    stack.preflight([(TRAEFIK_IP, TRAEFIK_PORT), (GRAFANA_IP, GRAFANA_PORT)])
    write_dynamic(list(USERS))
    grafana_home = stack.BIN / "grafana"
    grafana_data = stack.HARNESS / "grafana"
    specs = {
        "traefik": {
            "args": [
                str(stack.BIN / "traefik"),
                f"--entrypoints.web.address={TRAEFIK_IP}:{TRAEFIK_PORT}",
                f"--providers.file.filename={DYNAMIC}",
                "--providers.file.watch=true",
                "--ping=true",
                "--ping.entrypoint=web",
                "--log.level=INFO",
                "--accesslog=true",
            ],
            "env": {"PATH": "/usr/bin:/bin"},
        },
        "grafana": {
            "args": [str(grafana_home / "bin" / "grafana"), "server", "--homepath", str(grafana_home)],
            "env": {
                "PATH": "/usr/bin:/bin",
                "GF_SERVER_HTTP_ADDR": GRAFANA_IP,
                "GF_SERVER_HTTP_PORT": str(GRAFANA_PORT),
                "GF_PATHS_DATA": str(grafana_data / "data"),
                "GF_PATHS_LOGS": str(grafana_data / "logs"),
                "GF_PATHS_PLUGINS": str(grafana_data / "plugins"),
                "GF_SECURITY_ADMIN_USER": "admin",
                "GF_SECURITY_ADMIN_PASSWORD": "admin",
                "GF_AUTH_ANONYMOUS_ENABLED": "false",
                "GF_ANALYTICS_REPORTING_ENABLED": "false",
                "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
            },
        },
    }
    for name, spec in specs.items():
        pid = stack.spawn(name, spec)
        state["processes"][name] = {"pid": pid, "ip": TRAEFIK_IP if name == "traefik" else GRAFANA_IP}
        stack.save_state(state)
    wait(lambda: stack.http_ok(f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}/ping"), "Traefik /ping")
    wait(lambda: gateway_status(REVOCABLE_USER, USERS[REVOCABLE_USER]) == 200, "Traefik routes to alloy-gateway")
    wait(lambda: stack.http_ok(f"http://{GRAFANA_IP}:{GRAFANA_PORT}/api/health"), "Grafana /api/health", timeout=180)
    token = service_account_token()
    grafana_url = f"http://{GRAFANA_IP}:{GRAFANA_PORT}"
    RUNTIME_ENV.write_text(f"GRAFANA_URL={grafana_url}\nGRAFANA_SA_TOKEN={token}\n", encoding="utf-8")
    RUNTIME_ENV.chmod(0o600)
    edge = {
        "traefik_url": f"http://{TRAEFIK_IP}:{TRAEFIK_PORT}",
        "traefik_ip": TRAEFIK_IP,
        "hosts": {"alloy": f"alloy.{DOMAIN}", "alloy-gateway": f"alloy-gateway.{DOMAIN}", "other": f"other.{DOMAIN}"},
        "user": "proj-a",
        "password": USERS["proj-a"],
        "revocable_user": REVOCABLE_USER,
        "revocable_password": USERS[REVOCABLE_USER],
        "origin_ok": "https://acme.example.me",
        "grafana_url": grafana_url,
        "grafana_token": token,
    }
    EDGE_JSON.write_text(json.dumps(edge, indent=2), encoding="utf-8")
    EDGE_JSON.chmod(0o600)
    print(f"edge: Traefik on {TRAEFIK_IP}:{TRAEFIK_PORT}, Grafana on {GRAFANA_IP}:{GRAFANA_PORT}")
    return 0


def cmd_down(_args):
    state = stack.load_state()
    for name in ("traefik", "grafana"):
        proc = state["processes"].pop(name, None)
        if proc:
            stack.terminate(proc["pid"])
            print(f"edge: stopped {name}")
    stack.save_state(state)
    for path in (EDGE_JSON, RUNTIME_ENV):
        if path.exists():
            path.unlink()
    return 0


def cmd_revoke(args):
    write_dynamic([u for u in USERS if u != args.user])
    wait(lambda: gateway_status(args.user, USERS[args.user]) == 401, f"Traefik reload without {args.user}", timeout=30)
    print(f"edge: {args.user} revoked (hot reload)")
    return 0


def cmd_restore(_args):
    write_dynamic(list(USERS))
    wait(lambda: gateway_status(REVOCABLE_USER, USERS[REVOCABLE_USER]) == 200, "Traefik reload with every user", timeout=30)
    print("edge: every user restored")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="grafana-coolify harness edge (Traefik + Grafana)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up")
    sub.add_parser("down")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("user", choices=sorted(USERS))
    sub.add_parser("restore")
    args = parser.parse_args(argv)
    handlers = {"up": cmd_up, "down": cmd_down, "revoke": cmd_revoke, "restore": cmd_restore}
    try:
        return handlers[args.command](args)
    except (stack.HarnessError, subprocess.CalledProcessError, OSError) as exc:
        print(f"edge: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

Run : `chmod +x harness/edge.py && .bin/ruff check .`

Attendu : « All checks passed! ».

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 harness/edge.py up && python3 scripts/security.py`

Attendu : « edge: Traefik on 127.0.10.100:8080, Grafana on 127.0.10.101:3300 », « edge: proj-b revoked (hot reload) », « edge: every user restored », puis `security: 8/8 items passed`.

Run : `python3 scripts/smoke.py --only otlp-names`

Attendu : `[PASS] otlp-names` : le chemin passerelle passe maintenant par Traefik avec l'identifiant `proj-a`.

- [ ] **Étape 5 : prouver qu'un contrôle échoue sur un middleware cassé, puis restaurer**

Run : `sed -i 's/accessControlMaxAge: 600/accessControlMaxAge: 60/' traefik/grafana-coolify.yaml.example && python3 harness/edge.py restore && for i in $(seq 30); do python3 scripts/security.py --only 3 >/dev/null || break; sleep 1; done; python3 scripts/security.py --only 3`

Attendu : `security: [FAIL] 3. Faro CORS … -> max-age 60`, code 1 (la boucle attend que Traefik ait rechargé le fichier).

Run : `sed -i 's/accessControlMaxAge: 60$/accessControlMaxAge: 600/' traefik/grafana-coolify.yaml.example && python3 harness/edge.py restore && for i in $(seq 30); do python3 scripts/security.py --only 3 >/dev/null && break; sleep 1; done; python3 scripts/security.py --only 3`

Attendu : `security: [PASS] 3. …` : le modèle est revenu à `accessControlMaxAge: 600`.

- [ ] **Étape 6 : commit**

```bash
git add traefik/grafana-coolify.yaml.example harness/edge.py scripts/security.py
git commit -m "feat(traefik): add middleware template, harness edge and security checks"
```


### Task 16: `grafana-setup` : sources de données, corrélations et dossiers

Script en bibliothèque standard pour `python:3.13-alpine`. Garde de version sur `/api/health`
(arrêt propre sous 12.0). Sources Loki, Tempo et Prometheus à UID fixes (`gc-loki`, `gc-tempo`,
`gc-prometheus`) : `GET /api/datasources/uid/:uid`, puis `POST /api/datasources` si 404, `PUT`
seulement si le contenu diffère. Point de vigilance n° 4, constaté sur Grafana 13.2.2 : Grafana
rend **par défaut** la première source créée d'une organisation ; comparer `isDefault` ferait
croire à un changement au second passage. Le script n'envoie donc jamais `isDefault` à la
création, l'exclut de la comparaison et reporte la valeur existante lors d'un `PUT`.
Corrélations du § 7.3 : champ dérivé `trace_id` (`matcherType: label`, qui lit aussi les
métadonnées structurées), `tracesToLogsV2` avec la requête
`{project=~".+"} | trace_id="${__trace.traceId}"` (la même constante sert au contrôle
`correlation`), `tracesToMetrics` sur les span-metrics. Dossiers `gc-<projet>` pour `PROJECTS`
(noms `[a-z0-9-]+`, doublons ignorés). Le token n'apparaît jamais dans la sortie : chaque message
d'erreur est expurgé.

**Files:**

- Create: `config/grafana-setup/setup.py`
- Modify: `compose.template.yaml`
- Modify: `.env.example`
- Modify: `scripts/smoke.py`
- Modify: `docker-compose.yaml, compose.dev.yaml (régénérés)`
- Test: `tests/test_grafana_setup.py`

**Interfaces:**

- Consumes : `harness/edge.py` (tâche 15) : Grafana de test, `.harness/runtime.env`, `.harness/edge.json` ; `harness/stack.py oneshot grafana-setup`.
- Produces : `config/grafana-setup/setup.py` : `SetupError`, `Grafana(url, token)` avec `request(method, path, body=None) -> (status, payload)` et `expect(...)`, `MIN_MAJOR = 12`, `LOKI_UID`, `TEMPO_UID`, `PROMETHEUS_UID`, `TRACE_TO_LOGS_QUERY`, `COMPARED_FIELDS`, `wait_healthy`, `check_version`, `parse_projects`, `datasources(loki_url, tempo_url, prometheus_url)`, `ensure_datasource(api, desired) -> 'created'|'updated'|'unchanged'`, `ensure_folder(api, project)`, `run(env)`, `main()`.
- Produces : Variables lues : `GRAFANA_URL`, `GRAFANA_SA_TOKEN`, `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL`, `PROJECTS`.
- Produces : Service `grafana-setup` du gabarit (`restart: "no"`, variables du plan B déjà déclarées pour que `.env.example` reste exhaustif) ; section `correlation`.

- [ ] **Étape 1 : écrire les tests qui échouent**

Fichier complet `tests/test_grafana_setup.py` :

```python
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import ROOT, require_harness, run

SETUP = ROOT / "config" / "grafana-setup" / "setup.py"
TOKEN = "glsa_testTokenValue_0123456789abcdef"
sys.path.insert(0, str(SETUP.parent))
import setup  # noqa: E402


class FakeGrafana(BaseHTTPRequestHandler):
    """Minimal in-memory Grafana API: health, datasources by UID, folders by UID."""

    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length)) if length else None

    def handle_any(self, method):
        state = self.state
        state["requests"].append((method, self.path))
        if self.path == "/api/health":
            return self.reply(200, {"database": "ok", "version": state["version"]})
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.reply(401, {"message": "invalid API key"})
        if state.get("fail_with_token_echo"):
            return self.reply(500, {"message": "internal error for " + self.headers["Authorization"]})
        if self.path.startswith("/api/datasources/uid/"):
            uid = self.path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["datasources"][uid]) if uid in state["datasources"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["datasources"][uid] = self.body()
                return self.reply(200, {"message": "updated"})
        if self.path == "/api/datasources" and method == "POST":
            data = self.body()
            data.setdefault("isDefault", not state["datasources"])
            state["datasources"][data["uid"]] = data
            return self.reply(200, {"message": "created"})
        if self.path.startswith("/api/folders/"):
            uid = self.path.rsplit("/", 1)[1]
            if method == "GET":
                return self.reply(200, state["folders"][uid]) if uid in state["folders"] else self.reply(404, {"message": "not found"})
            if method == "PUT":
                state["folders"][uid].update(title=self.body()["title"])
                return self.reply(200, state["folders"][uid])
        if self.path == "/api/folders" and method == "POST":
            data = self.body()
            state["folders"][data["uid"]] = data
            return self.reply(200, data)
        return self.reply(404, {"message": "unknown route"})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")


class GrafanaSetupUnitTest(unittest.TestCase):
    def setUp(self):
        self.state = {"version": "13.2.2", "datasources": {}, "folders": {}, "requests": []}
        handler = type("Handler", (FakeGrafana,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {
            "PATH": os.environ["PATH"],
            "GRAFANA_URL": f"http://127.0.0.1:{self.server.server_port}",
            "GRAFANA_SA_TOKEN": TOKEN,
            "LOKI_INTERNAL_URL": "http://loki-abc:3100",
            "TEMPO_INTERNAL_URL": "http://tempo-abc:3200",
            "PROMETHEUS_INTERNAL_URL": "http://prometheus-abc:9090",
            "PROJECTS": "in-immo, other-project,in-immo",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def setup_run(self, **overrides):
        return run([sys.executable, SETUP], env=dict(self.env, **overrides), timeout=60)

    def test_first_run_creates_everything(self):
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sorted(self.state["datasources"]), ["gc-loki", "gc-prometheus", "gc-tempo"])
        self.assertEqual(sorted(self.state["folders"]), ["gc-in-immo", "gc-other-project"])
        self.assertEqual(self.state["datasources"]["gc-loki"]["url"], "http://loki-abc:3100")

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.setup_run().returncode, 0)
        snapshot = json.dumps([self.state["datasources"], self.state["folders"]], sort_keys=True)
        self.state["requests"].clear()
        result = self.setup_run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.dumps([self.state["datasources"], self.state["folders"]], sort_keys=True), snapshot)
        writes = [r for r in self.state["requests"] if r[0] in ("POST", "PUT")]
        self.assertEqual(writes, [])
        self.assertEqual(result.stdout.count(": unchanged"), 5)

    def test_changed_url_is_updated_and_default_flag_kept(self):
        self.assertEqual(self.setup_run().returncode, 0)
        self.assertTrue(self.state["datasources"]["gc-loki"]["isDefault"])
        result = self.setup_run(LOKI_INTERNAL_URL="http://loki-new:3100")
        self.assertIn("datasource gc-loki: updated", result.stdout)
        self.assertEqual(self.state["datasources"]["gc-loki"]["url"], "http://loki-new:3100")
        self.assertTrue(self.state["datasources"]["gc-loki"]["isDefault"])

    def test_correlations(self):
        self.assertEqual(self.setup_run().returncode, 0)
        loki = self.state["datasources"]["gc-loki"]["jsonData"]["derivedFields"][0]
        self.assertEqual((loki["matcherType"], loki["matcherRegex"], loki["datasourceUid"]), ("label", "trace_id", "gc-tempo"))
        tempo = self.state["datasources"]["gc-tempo"]["jsonData"]
        self.assertEqual(tempo["tracesToLogsV2"]["datasourceUid"], "gc-loki")
        self.assertEqual(tempo["tracesToLogsV2"]["query"], '{project=~".+"} | trace_id="${__trace.traceId}"')
        self.assertEqual(tempo["tracesToMetrics"]["datasourceUid"], "gc-prometheus")

    def test_old_grafana_stops_cleanly(self):
        self.state["version"] = "11.6.3"
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Grafana 11.6.3 is not supported", result.stdout)
        self.assertEqual([r for r in self.state["requests"] if r[1] != "/api/health"], [])

    def test_token_never_printed(self):
        self.state["fail_with_token_echo"] = True
        result = self.setup_run()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(TOKEN, result.stdout)
        self.assertIn("***", result.stdout)

    def test_invalid_project_name(self):
        result = self.setup_run(PROJECTS="ok,Not_Valid")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid project name", result.stdout)

    def test_missing_variable(self):
        result = self.setup_run(TEMPO_INTERNAL_URL="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("TEMPO_INTERNAL_URL is required", result.stdout)

    def test_parse_projects(self):
        self.assertEqual(setup.parse_projects(" a, b ,a,,"), ["a", "b"])
        self.assertEqual(setup.parse_projects(""), [])


class GrafanaSetupHarnessTest(unittest.TestCase):
    """Against the real test Grafana started by `harness/stack.py up --with-edge`."""

    def setUp(self):
        require_harness(self)
        edge = json.loads((ROOT / ".harness" / "edge.json").read_text(encoding="utf-8"))
        self.env = dict(
            os.environ,
            GRAFANA_URL=edge["grafana_url"],
            GRAFANA_SA_TOKEN=edge["grafana_token"],
            LOKI_INTERNAL_URL="http://loki:3100",
            TEMPO_INTERNAL_URL="http://tempo:3200",
            PROMETHEUS_INTERNAL_URL="http://prometheus:9090",
            PROJECTS="demo,other-project",
        )
        self.api = setup.Grafana(edge["grafana_url"], edge["grafana_token"])

    def snapshot(self):
        datasources = {}
        for uid in (setup.LOKI_UID, setup.TEMPO_UID, setup.PROMETHEUS_UID):
            status, payload = self.api.request("GET", f"/api/datasources/uid/{uid}")
            self.assertEqual(status, 200)
            datasources[uid] = {k: payload[k] for k in ("uid", "name", "type", "url", "jsonData", "isDefault")}
        status, folders = self.api.request("GET", "/api/folders")
        self.assertEqual(status, 200)
        return datasources, sorted(f["uid"] for f in folders)

    def test_idempotent_against_real_grafana(self):
        first = run([sys.executable, SETUP], env=self.env, timeout=120)
        self.assertEqual(first.returncode, 0, first.stdout)
        before = self.snapshot()
        second = run([sys.executable, SETUP], env=self.env, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertNotIn(": created", second.stdout)
        self.assertNotIn(": updated", second.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("gc-demo", before[1])
        self.assertNotIn(self.env["GRAFANA_SA_TOKEN"], first.stdout + second.stdout)


if __name__ == "__main__":
    unittest.main()
```

Dans `scripts/smoke.py`, repérer le texte exact suivant (unique dans le fichier) :

```python
@section("retention")
```

et insérer juste **avant** lui :

```python
@section("correlation")
def correlation(c):
    """12.3.10: from a log of each path, trace_id finds the trace; from the trace, the logs."""
    sys.path.insert(0, str(g.ROOT / "config" / "grafana-setup"))
    import setup  # noqa: PLC0415 - the query Grafana is provisioned with

    trace_id = g.new_trace_id()
    resource = c.resource("corr")
    g.send_otlp(c.s["GC_OTLP_URL"], "traces", g.otlp_traces(resource, [g.span(trace_id, "server")]))
    g.send_otlp(c.s["GC_OTLP_URL"], "logs", g.otlp_logs(resource, f"otlp-corr-{c.run}", trace_id=trace_id))
    faro = g.faro_payload(c.faro_app(environment="prod"), "https://inconnu.autre.org/", logs=[g.faro_log(f"faro-corr-{c.run}", trace_id=trace_id)])
    g.send_faro(c.s["GC_FARO_URL"], faro, c.s["FARO_API_KEY"])
    for token in (f"otlp-corr-{c.run}", f"faro-corr-{c.run}"):
        _labels, _line, meta = c.wait_logs(f'{{project="{c.project}"}} |= "{token}"')[0]
        expect(meta.get("trace_id") == trace_id, f"{token}: trace_id metadata {meta}")
        expect(c.wait_trace(meta["trace_id"]), f"{token}: trace not found from the log")
    query = setup.TRACE_TO_LOGS_QUERY.replace("${__trace.traceId}", trace_id)
    lines = [line for _l, line, _m in c.wait_logs(query, count=2)]
    expect(any(f"otlp-corr-{c.run}" in line for line in lines), f"trace -> logs misses the OTLP log: {lines}")
    expect(any(f"faro-corr-{c.run}" in line for line in lines), f"trace -> logs misses the Faro log: {lines}")


```

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_grafana_setup.py -v`

Attendu : ERROR « No module named 'setup' ».

Run : `python3 harness/stack.py status | grep -q 'alloy .*ready' || python3 harness/stack.py down && python3 harness/stack.py up; python3 scripts/smoke.py --only correlation`

Attendu : `[FAIL] correlation: No module named 'setup'`, code 1.

- [ ] **Étape 3 : écrire le script et l'ajouter au gabarit**

Fichier complet `config/grafana-setup/setup.py` :

```python
#!/usr/bin/env python3
"""grafana-setup (plan A): idempotently provision datasources and project folders in Grafana.

Standard library only (runs in python:3.13-alpine). Spec 10.1-10.2:
- version guard: stop cleanly if Grafana < 12.0 (read from /api/health);
- datasources with fixed UIDs: GET /api/datasources/uid/:uid -> PUT when present and different,
  POST /api/datasources otherwise; correlations of spec 7.3;
- folders gc-<project> for every project of PROJECTS;
- the service account token is never written to the output.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

MIN_MAJOR = 12
LOKI_UID = "gc-loki"
TEMPO_UID = "gc-tempo"
PROMETHEUS_UID = "gc-prometheus"
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Trace -> logs: every log of the trace, whatever its path (OTLP or Faro), via trace_id metadata.
TRACE_TO_LOGS_QUERY = '{project=~".+"} | trace_id="${__trace.traceId}"'
# Fields compared to decide whether an existing datasource must be updated. isDefault is left to
# the operator (Grafana makes the first datasource of an organisation the default one).
COMPARED_FIELDS = ("name", "type", "access", "url", "basicAuth", "jsonData")


class SetupError(Exception):
    """A clean, explained stop (exit code 1)."""


class Grafana:
    def __init__(self, url, token, timeout=15):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method, path, body=None):
        """Return (status, parsed JSON or None). Never raises on HTTP error statuses."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, parse_json(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, parse_json(exc.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise SetupError(f"{method} {path}: Grafana unreachable ({reason(exc)})") from None

    def expect(self, method, path, body=None, ok=(200,)):
        status, payload = self.request(method, path, body)
        if status not in ok:
            raise SetupError(f"{method} {path}: HTTP {status}: {message_of(payload)}")
        return payload


def parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:200]


def message_of(payload):
    return payload.get("message") if isinstance(payload, dict) else payload


def reason(exc):
    return getattr(exc, "reason", None) or exc.__class__.__name__


def wait_healthy(api, timeout=120, interval=3):
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, payload = api.request("GET", "/api/health")
            if status == 200 and isinstance(payload, dict):
                return payload
        except SetupError:
            if time.monotonic() >= deadline:
                raise
        if time.monotonic() >= deadline:
            raise SetupError(f"Grafana not healthy after {timeout}s")
        time.sleep(interval)


def check_version(health):
    version = str(health.get("version", ""))
    match = re.match(r"^v?(\d+)\.", version)
    if not match:
        raise SetupError(f"cannot read the Grafana version from /api/health: {version!r}")
    if int(match.group(1)) < MIN_MAJOR:
        raise SetupError(f"Grafana {version} is not supported: version {MIN_MAJOR}.0 or later is required")
    return version


def parse_projects(value):
    projects = []
    for item in (value or "").split(","):
        name = item.strip()
        if not name:
            continue
        if not PROJECT_RE.match(name):
            raise SetupError(f"invalid project name in PROJECTS: {name!r} (expected [a-z0-9-]+)")
        if name not in projects:
            projects.append(name)
    return projects


def datasources(loki_url, tempo_url, prometheus_url):
    """Desired datasources, with the correlations of spec 7.3."""
    return [
        {
            "uid": LOKI_UID,
            "name": "Loki (grafana-coolify)",
            "type": "loki",
            "access": "proxy",
            "url": loki_url,
            "basicAuth": False,
            "jsonData": {
                "derivedFields": [
                    {
                        "name": "trace_id",
                        "matcherType": "label",
                        "matcherRegex": "trace_id",
                        "url": "${__value.raw}",
                        "urlDisplayLabel": "Trace",
                        "datasourceUid": TEMPO_UID,
                    }
                ]
            },
        },
        {
            "uid": TEMPO_UID,
            "name": "Tempo (grafana-coolify)",
            "type": "tempo",
            "access": "proxy",
            "url": tempo_url,
            "basicAuth": False,
            "jsonData": {
                "tracesToLogsV2": {
                    "datasourceUid": LOKI_UID,
                    "spanStartTimeShift": "-5m",
                    "spanEndTimeShift": "5m",
                    "filterByTraceID": True,
                    "filterBySpanID": False,
                    "customQuery": True,
                    "query": TRACE_TO_LOGS_QUERY,
                },
                "tracesToMetrics": {
                    "datasourceUid": PROMETHEUS_UID,
                    "spanStartTimeShift": "-5m",
                    "spanEndTimeShift": "5m",
                    "tags": [{"key": "service.name", "value": "service"}],
                    "queries": [
                        {"name": "Request rate", "query": "sum(rate(traces_spanmetrics_calls_total{$__tags}[5m]))"},
                        {
                            "name": "p95 latency",
                            "query": "histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket{$__tags}[5m])) by (le))",
                        },
                    ],
                },
                "serviceMap": {"datasourceUid": PROMETHEUS_UID},
                "nodeGraph": {"enabled": True},
                "lokiSearch": {"datasourceUid": LOKI_UID},
            },
        },
        {
            "uid": PROMETHEUS_UID,
            "name": "Prometheus (grafana-coolify)",
            "type": "prometheus",
            "access": "proxy",
            "url": prometheus_url,
            "basicAuth": False,
            "jsonData": {
                "httpMethod": "POST",
                "exemplarTraceIdDestinations": [{"name": "trace_id", "datasourceUid": TEMPO_UID}],
            },
        },
    ]


def ensure_datasource(api, desired):
    path = f"/api/datasources/uid/{desired['uid']}"
    status, current = api.request("GET", path)
    if status == 404:
        api.expect("POST", "/api/datasources", desired)
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise SetupError(f"GET {path}: HTTP {status}: {message_of(current)}")
    if all(current.get(field) == desired[field] for field in COMPARED_FIELDS):
        return "unchanged"
    api.expect("PUT", path, dict(desired, isDefault=bool(current.get("isDefault"))))
    return "updated"


def ensure_folder(api, project):
    uid = f"gc-{project}"
    status, current = api.request("GET", f"/api/folders/{uid}")
    if status == 404:
        api.expect("POST", "/api/folders", {"uid": uid, "title": project})
        return "created"
    if status != 200 or not isinstance(current, dict):
        raise SetupError(f"GET /api/folders/{uid}: HTTP {status}: {message_of(current)}")
    if current.get("title") == project:
        return "unchanged"
    api.expect("PUT", f"/api/folders/{uid}", {"title": project, "overwrite": True})
    return "updated"


def required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise SetupError(f"{name} is required")
    return value


def run(env, out=print, health_timeout=120):
    api = Grafana(required(env, "GRAFANA_URL"), required(env, "GRAFANA_SA_TOKEN"))
    desired = datasources(
        required(env, "LOKI_INTERNAL_URL"),
        required(env, "TEMPO_INTERNAL_URL"),
        required(env, "PROMETHEUS_INTERNAL_URL"),
    )
    projects = parse_projects(env.get("PROJECTS", ""))
    version = check_version(wait_healthy(api, timeout=health_timeout))
    out(f"grafana-setup: Grafana {version}")
    for datasource in desired:
        out(f"grafana-setup: datasource {datasource['uid']}: {ensure_datasource(api, datasource)}")
    for project in projects:
        out(f"grafana-setup: folder gc-{project}: {ensure_folder(api, project)}")
    out("grafana-setup: done")


def main():
    token = os.environ.get("GRAFANA_SA_TOKEN", "")
    try:
        run(os.environ)
    except SetupError as exc:
        message = str(exc)
        if token:
            message = message.replace(token, "***")
        print(f"grafana-setup: ERROR: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Dans `compose.template.yaml`, repérer le texte exact suivant (unique dans le fichier) — c'est-à-dire après le service `alloy-gateway` :

```yaml
volumes:
  loki-data:
```

et insérer juste **avant** lui :

```yaml
  grafana-setup:
    image: @@PYTHON_IMAGE@@
    restart: "no"
    depends_on:
      config-guard:
        condition: service_completed_successfully
    command:
      - python3
      - /opt/grafana-setup/setup.py
    environment:
      GRAFANA_URL: ${GRAFANA_URL:?GRAFANA_URL is required}
      GRAFANA_SA_TOKEN: ${GRAFANA_SA_TOKEN:?GRAFANA_SA_TOKEN is required}
      LOKI_INTERNAL_URL: ${LOKI_INTERNAL_URL:?LOKI_INTERNAL_URL is required}
      TEMPO_INTERNAL_URL: ${TEMPO_INTERNAL_URL:?TEMPO_INTERNAL_URL is required}
      PROMETHEUS_INTERNAL_URL: ${PROMETHEUS_INTERNAL_URL:?PROMETHEUS_INTERNAL_URL is required}
      PROJECTS: ${PROJECTS:-}
      # Plan B (dashboards and alerts) reads these; declared now so .env.example stays exhaustive.
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}
      TELEGRAM_CHAT_ID: ${TELEGRAM_CHAT_ID:-}
      ALERT_EMAILS: ${ALERT_EMAILS:-}
      ALERT_ERROR_RATE: ${ALERT_ERROR_RATE:-0.05}
      ALERT_P95_MS: ${ALERT_P95_MS:-1500}
      ALERT_SILENCE_MIN: ${ALERT_SILENCE_MIN:-15}
      ALERT_DISK_PCT: ${ALERT_DISK_PCT:-80}
      CARDINALITY_ALERT_THRESHOLD: ${CARDINALITY_ALERT_THRESHOLD:-200000}
    volumes:
      - type: bind
        source: ./config/grafana-setup/setup.py
        target: /opt/grafana-setup/setup.py
        content: "@@CONTENT@@"
        read_only: true
    mem_limit: 128m
    cpus: 0.2

```

Ajouter à la fin de `.env.example` — le bloc commence par une ligne vide :

```dotenv

# --- grafana-setup (obligatoire) ---
# Noms réels des services sur le réseau coolify (voir README, « Noms internes »).
LOKI_INTERNAL_URL=
TEMPO_INTERNAL_URL=
PROMETHEUS_INTERNAL_URL=
# URL publique de Grafana et token d'un compte de service au rôle Admin.
GRAFANA_URL=
GRAFANA_SA_TOKEN=
# Projets pour lesquels créer un dossier Grafana gc-<projet> (ex. in-immo,autre-projet).
PROJECTS=

# --- Notifications et seuils (plan B) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ALERT_EMAILS=
ALERT_ERROR_RATE=0.05
ALERT_P95_MS=1500
ALERT_SILENCE_MIN=15
ALERT_DISK_PCT=80
CARDINALITY_ALERT_THRESHOLD=200000
```

Run : `python3 scripts/render.py && python3 scripts/check.py`

Attendu : huit `[PASS]` (le contrôle `secrets` accepte `setup.py`).

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_grafana_setup.py -v`

Attendu : `Ran 10 tests` … `OK (skipped=1)` : faux Grafana en mémoire (création, second passage sans écriture, `isDefault` conservé, Grafana 11.6.3 refusé, token expurgé).

Run : `python3 harness/stack.py down && python3 harness/stack.py up && python3 harness/edge.py up && python3 harness/stack.py oneshot grafana-setup && GC_HARNESS=1 python3 -m unittest discover -s tests -p test_grafana_setup.py -v`

Attendu : `datasource gc-loki: created` … `folder gc-other-project: created`, `stack: grafana-setup exited with 0`, puis `Ran 10 tests` … `OK` contre le vrai Grafana.

Run : `python3 scripts/smoke.py`

Attendu : `12/12 sections passed`.

- [ ] **Étape 5 : commit**

```bash
git add config/grafana-setup/setup.py tests/test_grafana_setup.py scripts/smoke.py compose.template.yaml .env.example docker-compose.yaml compose.dev.yaml
git commit -m "feat(grafana-setup): provision datasources, correlations and folders"
```


### Task 17: Documentation de déploiement (README en français)

README pas à pas conforme au § 14 : ressource Application Git, variables (dont « Is Literal? »
pour `TENANT_HOST_REGEX`), configuration dynamique Traefik, réseau prédéfini et noms internes,
compte de service et rotation du token, conteneurs « exited » normaux, vérification par
`security.py --remote`, sonde externe, contrat des attributs, masquage et ses limites, banc
natif et banc Docker. Le banc Docker (`compose.dev.yaml`) n'a pas pu être exercé dans cet
environnement (aucun conteneur ne peut démarrer) : le README le dit explicitement. Un test
garde les points que l'opérateur ne doit pas manquer.

**Files:**

- Create: `README.md`
- Test: `tests/test_docs.py`

**Interfaces:**

- Consumes : Noms, variables et commandes des tâches 1 à 16.
- Produces : `README.md` (français) ; `tests/test_docs.py`.

- [ ] **Étape 1 : écrire le test qui échoue**

Fichier complet `tests/test_docs.py` :

```python
import unittest

from support import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")


class ReadmeTest(unittest.TestCase):
    """The French README covers the deployment of spec 14 and the operator pitfalls."""

    def test_deployment_steps(self):
        for number in range(1, 9):
            self.assertIn(f"\n### {number}. ", README)

    def test_operator_pitfalls_are_documented(self):
        for text in (
            "Dynamic Configurations",
            "Connect To Predefined Network",
            "Is Literal?",
            "exited",
            "openssl passwd -apr1",
            "Service accounts",
            "sonde",
            "docs/spikes.md",
            "#9886",
        ):
            with self.subTest(text=text):
                self.assertIn(text, README)

    def test_mandatory_variables_are_explained(self):
        for name in ("IP_HASH_SALT", "FARO_API_KEY", "LOKI_INTERNAL_URL", "TEMPO_INTERNAL_URL", "PROMETHEUS_INTERNAL_URL", "GRAFANA_URL", "GRAFANA_SA_TOKEN"):
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", README)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Étape 2 : lancer — échec attendu**

Run : `python3 -m unittest discover -s tests -p test_docs.py -v`

Attendu : ERROR « FileNotFoundError … README.md ».

- [ ] **Étape 3 : écrire le README**

Fichier complet `README.md` :

````markdown
# grafana-coolify

Package Docker Compose, déployable sur [Coolify](https://coolify.io), qui ajoute à un service
Coolify **Grafana** existant tout ce qu'il faut pour recevoir, masquer, stocker et relier logs,
traces et métriques :

| Service | Rôle | Exposition |
|---|---|---|
| `alloy` | Collecteur : OTLP interne (4317/4318) et Faro (12347), masquage, étiquetage, filtrage, routage | Faro public via Traefik |
| `alloy-gateway` | Relais OTLP/HTTP authentifié (Basic Auth par projet) vers `alloy` | OTLP public via Traefik |
| `loki` | Logs (rétention prod 30 j, reste 7 j) | interne |
| `tempo` | Traces (7 j) et métriques dérivées (span-metrics, carte des services) | interne |
| `prometheus` | Métriques (90 j), récepteurs OTLP et remote write, scrape de la stack | interne |
| `node-exporter` | Métriques de l'hôte (montages en lecture seule) | interne |
| `config-guard` | Ponctuel : vérifie les fichiers de config et les variables avant tout démarrage | — |
| `grafana-setup` | Ponctuel : crée les sources de données et les dossiers dans Grafana | — |

Grafana n'est **pas** dans le package : c'est le service Coolify « Grafana » (variante
PostgreSQL recommandée), inchangé. Toutes les images sont officielles et épinglées
(`tools/versions.env`). La conception complète est dans
[`docs/superpowers/specs/2026-09-27-grafana-coolify-design.md`](docs/superpowers/specs/2026-09-27-grafana-coolify-design.md).

## Prérequis

- Un serveur Coolify avec le proxy Traefik, et le service Coolify **Grafana 12.0 ou plus récent**.
- Environ **6 Go de RAM** libres (somme des `mem_limit` : ~5,7 Gio) et de la place disque pour
  90 jours de métriques.
- Les **spikes de `docs/spikes.md`** joués une fois sur le serveur cible (version de Coolify,
  portée des middlewares, fichiers `content:`, noms internes).

## Déploiement pas à pas

### 1. Créer la ressource

Coolify → **New Resource** → **Application** → depuis ce dépôt Git (public), build pack
**Docker Compose**, fichier `docker-compose.yaml` (le fichier **généré**, jamais
`compose.template.yaml`).

Les mises à jour se font par `git push` puis **Redeploy**.

### 2. Renseigner les variables

Copier `.env.example` dans l'onglet **Environment Variables**, puis remplir :

| Variable | Valeur |
|---|---|
| `IP_HASH_SALT` | Obligatoire. Au moins 16 caractères `[A-Za-z0-9]`, par exemple `openssl rand -hex 24`. |
| `FARO_API_KEY` | Clé des SDK Faro (navigateur, desktop, mobile). **Vide = point Faro fermé.** |
| `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` | Règles de déduction depuis l'hôte (voir plus bas). |
| `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL` | Noms réels sur le réseau `coolify` (étape 4). |
| `GRAFANA_URL`, `GRAFANA_SA_TOKEN` | URL **publique** de Grafana et token du compte de service (étape 5). |
| `PROJECTS` | Projets pour lesquels créer un dossier Grafana `gc-<projet>`, séparés par des virgules. |

- **`TENANT_HOST_REGEX` contient des `$`** : cocher **« Is Literal? »** sur cette variable, sinon
  Coolify tente de l'interpréter.
- Les domaines publics `SERVICE_FQDN_ALLOY_12347` (Faro) et `SERVICE_FQDN_ALLOY_GATEWAY_4318`
  (OTLP) sont générés par Coolify : renseigner le domaine de chaque service dans l'onglet de la
  ressource, par exemple `https://faro.example.com:12347` et `https://otlp.example.com:4318`.
- `config-guard` refuse de démarrer la stack si `IP_HASH_SALT`, `HOST_MAP`,
  `RESERVED_SUBDOMAINS` ou `TENANT_HOST_REGEX` ont un format invalide : le message d'erreur est
  dans les logs de `config-guard`.

### 3. Configurer les middlewares Traefik

Les protections des points publics ne sont **pas** dans le dépôt (aucun hash de mot de passe
publié ; rechargement à chaud) :

1. Coolify → **Servers** → le serveur → **Proxy** → **Dynamic Configurations** → nouveau
   fichier `grafana-coolify.yaml`.
2. Coller `traefik/grafana-coolify.yaml.example`.
3. Remplacer la ligne htpasswd d'exemple par **une ligne par projet** émetteur :

   ```bash
   printf '%s:%s\n' mon-projet "$(openssl passwd -apr1 'mot-de-passe-long')"
   ```

   Chaque ligne est écrite **entre guillemets doubles**, avec des `$` **non doublés**.
4. Remplacer la regex d'origines (`accessControlAllowOriginListRegex`) par celle de vos domaines,
   par exemple `^https://([a-z0-9-]+\.)?example\.(me|app)$`.

Révoquer un projet : supprimer sa ligne. Traefik recharge le fichier sans redéploiement.

Le rattachement est déjà fait par les labels du compose :
`alloy-gateway` → `gc-otlp-auth@file` ; `alloy` → `gc-faro-ratelimit@file`,
`gc-faro-cors@file`, `gc-faro-body@file`.

> **Limite par IP et NAT** : une agence dont tous les postes sortent par une seule IP partage le
> quota `gc-faro-ratelimit` (50 req/s, rafale 100). Augmenter `average` et `burst` si besoin.

### 4. Réseau et noms internes

1. Onglet de la ressource → activer **Connect To Predefined Network** : le package rejoint le
   réseau `coolify`, partagé avec Grafana et les applications du serveur.
2. Après un premier déploiement, relever les noms réels des conteneurs : Coolify les suffixe
   (`loki-<uuid>`). Sur le serveur :

   ```bash
   docker ps --format '{{.Names}}' | grep -E '^(loki|tempo|prometheus|alloy)-'
   ```

3. Renseigner `LOKI_INTERNAL_URL=http://loki-<uuid>:3100`,
   `TEMPO_INTERNAL_URL=http://tempo-<uuid>:3200`,
   `PROMETHEUS_INTERNAL_URL=http://prometheus-<uuid>:9090`, et documenter pour les applications
   du même hôte `ALLOY_INTERNAL_URL=http://alloy-<uuid>:4318`.

**Hypothèse de sécurité** : tout conteneur du réseau `coolify` est de confiance (Loki, Tempo,
Prometheus et les ports internes d'Alloy n'ont pas d'authentification). Aucun service interne ne
publie de port (`scripts/check.py` le vérifie).

### 5. Compte de service Grafana

Dans Grafana : **Administration** → **Users and access** → **Service accounts** → créer
`grafana-coolify` avec le rôle **Admin**, puis **Add service account token** (expiration
conseillée : **90 jours**). Copier le token dans `GRAFANA_SA_TOKEN`.

Rotation : créer un nouveau token, remplacer `GRAFANA_SA_TOKEN`, redéployer, puis supprimer
l'ancien token. Le token n'est jamais écrit dans les logs de `grafana-setup`.

### 6. Déployer

Ordre de démarrage : `config-guard` (vérifie chaque fichier de config par empreinte SHA-256,
puis les variables), puis les services, puis `grafana-setup`.

Coolify affiche ensuite **deux conteneurs « exited »** : `config-guard` et `grafana-setup`.
C'est normal : ce sont des tâches ponctuelles (`restart: "no"`). Un `config-guard` en erreur
signale une régression de Coolify sur les fichiers `content:` : aucun service ne démarre.

`grafana-setup` est relançable sans effet de bord : il crée les sources Loki, Tempo et Prometheus
(UID fixes `gc-loki`, `gc-tempo`, `gc-prometheus`, corrélations log ↔ trace ↔ métriques) et les
dossiers `gc-<projet>`.

### 7. Vérifier le déploiement

Depuis un poste de l'opérateur ou le serveur :

```bash
tools/fetch-binaries.sh                   # uniquement pour check.py et le banc local
GC_PUBLIC_IP=203.0.113.10 \
GC_FARO_PUBLIC_URL=https://faro.example.com GC_GATEWAY_PUBLIC_URL=https://otlp.example.com \
GC_OTHER_PUBLIC_URL=https://grafana.example.com GC_ORIGIN_OK=https://app.example.com \
GC_GATEWAY_USER=mon-projet GC_GATEWAY_PASSWORD=... GC_REVOKED_USER=ancien GC_REVOKED_PASSWORD=... \
FARO_API_KEY=... GC_ALLOY_CONTAINER=alloy-<uuid> GC_GATEWAY_CONTAINER=alloy-gateway-<uuid> \
python3 scripts/security.py --remote
```

`scripts/smoke.py` interroge Loki, Tempo et Prometheus, qui ne sont joignables que depuis le
réseau `coolify` : le lancer depuis un conteneur rattaché à ce réseau, avec les variables
`GC_OTLP_URL`, `GC_FARO_URL`, `GC_LOKI_URL`, `GC_TEMPO_URL`, `GC_PROM_URL`,
`GC_ALLOY_METRICS_URL` pointant vers les noms réels, et les mêmes valeurs de `HOST_MAP`,
`RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` que le banc (`harness/harness.env`) sur un
**déploiement de recette**.

Rejouer `security.py --remote` (contrôle 7, fuite de middlewares, bug Coolify #9886) **après
chaque mise à jour de Coolify**.

### 8. Sonde externe

Configurer une sonde **hors du serveur** (Uptime Kuma sur une autre machine, service SaaS…) sur
les URL publiques Faro et OTLP : c'est la seule alerte qui survit à une panne du serveur.

## Envoyer des données

| Émetteur | Adresse | Protection |
|---|---|---|
| Serveur du **même hôte** | `ALLOY_INTERNAL_URL` (OTLP/HTTP 4318, ou gRPC 4317) | réseau `coolify` |
| Serveur **distant** | `https://<domaine OTLP>` | `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64 projet:mot-de-passe>` |
| Navigateur, desktop, mobile | `https://<domaine Faro>/collect` | CORS, `FARO_API_KEY`, débit, taille |

Attributs obligatoires (ressource OpenTelemetry) : `project`, `deployment.environment.name`
(`prod` ou `preprod`), `service.name`. `tenant` en ressource ou en attribut de span selon le
projet. Toute donnée sans `project` ou sans environnement est rejetée (compteurs
`otelcol_processor_filter_*_filtered_total` et
`loki_process_dropped_lines_total{reason=...}`).

Faro : `app.namespace` → `project`, `app.name` → `service_name`, `app.environment` → `env` ;
le tenant déclaré par le client passe par l'attribut de session `tenant`. Pour les logs Faro,
`env` et `tenant` sont **déduits de l'hôte de la page** :

| Variable | Exemple |
|---|---|
| `HOST_MAP` | `example.me=guest-front:prod` (hôte exact → service et env) |
| `RESERVED_SUBDOMAINS` | `www,api` (jamais des tenants) |
| `TENANT_HOST_REGEX` | `^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me\|app)$` |

Noms à utiliser dans les requêtes Grafana :

| Donnée | Loki | Tempo | Prometheus (OTLP) | Span-metrics |
|---|---|---|---|---|
| projet | `project` | `resource.project` | `project` | `project` |
| environnement | `env` | `resource.env` | `env` | `env` |
| service | `service_name` | `resource.service.name` | `job` | `service` |
| tenant | `tenant` (métadonnée) | `tenant` | `tenant` | `tenant` |
| niveau | `detected_level` (métadonnée) | — | — | — |

## Masquage (filet de sécurité)

`alloy` remplace, avant tout stockage : les emails par `[email]`, les jetons `Bearer` et les
valeurs des clés `authorization`, `cookie`, `password`, `token`, `secret` par `[redacted]`, les
numéros de carte par `[card]` (**seulement** dans le texte libre : corps, `message`,
`exception.*`), et chaque adresse IP par `sha256(IP_HASH_SALT + ip)`, identique sur les chemins
OTLP et Faro.

Limites connues : un numéro de version à quatre nombres (`1.2.3.4`) dans un attribut OTLP est
pris pour une IP ; dans les logs Faro, seules les IP du texte libre (`message`, `value`,
`stacktrace`) sont hachées, pas celles d'une URL.

## Exemplars (expérimental)

Désactivés par défaut. Pour les activer : `PROM_ENABLE_FEATURES=exemplar-storage` **et**
`ENABLE_EXEMPLARS=true`.

## Développement

Conventions : documentation en français ; code, commentaires, commits et branches en anglais.

```bash
tools/fetch-binaries.sh                        # binaires officiels vérifiés (SHA-256) dans .bin/
python3 scripts/render.py                      # régénère docker-compose.yaml et compose.dev.yaml
python3 scripts/check.py                       # contrôles statiques (spec § 12.1)
python3 -m unittest discover -s tests -v       # tests unitaires
```

Ne jamais modifier `docker-compose.yaml` à la main : modifier `compose.template.yaml` ou
`config/`, puis relancer `render.py` (`check.py` refuse un fichier périmé et un compose de plus de
120 Kio encodé en base64).

### Banc natif sans Docker (`harness/`)

Il lance les binaires officiels avec les mêmes commandes, variables et fichiers que le compose,
chaque service sur sa propre adresse `127.0.10.N`, et ajoute temporairement les noms des services
dans `/etc/hosts` (`sudo` requis).

```bash
python3 harness/stack.py up                     # config-guard puis la stack
python3 harness/edge.py up                      # Traefik (routes Coolify simulées) + Grafana de test
python3 harness/stack.py oneshot grafana-setup  # provisionne le Grafana de test
python3 scripts/smoke.py                        # bout en bout (spec § 12.3)
python3 scripts/security.py                     # sécurité (spec § 12.4)
GC_HARNESS=1 python3 -m unittest discover -s tests -v   # + banc, robustesse, Grafana réel
python3 harness/stack.py down                   # arrête tout, retire le bloc /etc/hosts
```

Logs et données du banc : `.harness/` (non versionné).

### Banc Docker (`compose.dev.yaml`)

`compose.dev.yaml` est généré par `render.py` : il monte directement `config/` et ajoute un
Grafana de test sur `127.0.0.1:3000` (admin/admin). Il n'est **jamais** déployé.

```bash
cp .env.example .env    # puis remplir les variables obligatoires
docker compose -f compose.dev.yaml up -d
```

> Ce banc n'a pas pu être exécuté dans l'environnement de développement du plan A (Docker n'y
> peut pas créer de conteneurs) : seule sa syntaxe est validée par `check.py`. Le banc de
> référence est `harness/`.

## Licence

MIT — voir [LICENSE](LICENSE).
````

- [ ] **Étape 4 : lancer — succès attendu**

Run : `python3 -m unittest discover -s tests -p test_docs.py -v`

Attendu : `Ran 3 tests` … `OK`.

- [ ] **Étape 5 : commit**

```bash
git add README.md tests/test_docs.py
git commit -m "docs: add french deployment guide"
```


### Task 18: Vérification finale complète

Rejoue tout, dans l'ordre, sur un banc repartant de zéro. Aucun fichier ne doit changer : si
une commande échoue, corriger dans la tâche concernée (en suivant ses étapes) plutôt que de
modifier les tests.

**Files:**


**Interfaces:**

- Consumes : Tout ce qui précède.
- Produces : Un état vérifié de la branche `feat/plan-a-ingestion`.

- [ ] **Étape 1 : contrôles statiques et tests unitaires**

Run : `python3 harness/stack.py down --purge && python3 scripts/render.py --check && python3 scripts/check.py`

Attendu : `render: docker-compose.yaml base64 size 77… bytes (budget 122880)` puis huit `[PASS]`.

Run : `python3 -m unittest discover -s tests -v`

Attendu : tous `OK`, `skipped` uniquement pour les tests `GC_HARNESS`.

- [ ] **Étape 2 : tests du banc qui gèrent leur propre pile**

Run : `GC_HARNESS=1 python3 -m unittest discover -s tests -p test_harness.py -v && GC_HARNESS=1 python3 -m unittest discover -s tests -p test_robustness.py -v`

Attendu : les deux suites `OK`.

- [ ] **Étape 3 : banc complet, bout en bout et sécurité**

Run : `python3 harness/stack.py down && python3 harness/stack.py up && python3 harness/edge.py up && python3 harness/stack.py oneshot grafana-setup && GC_HARNESS=1 python3 -m unittest discover -s tests -p 'test_[fg]*.py' -v && python3 scripts/smoke.py && python3 scripts/security.py`

Attendu : `grafana-setup exited with 0`, tests `test_faro_closed` et `test_grafana_setup` `OK`, `smoke: 12/12 sections passed`, `security: 8/8 items passed`.

Run : `python3 harness/stack.py down && git status --short && test "$(git log --format='%an <%ae>' main..HEAD | sort -u)" = 'Henoc Djabia <henoc35@gmail.com>' && ! git log --format=%B main..HEAD | grep -qi 'co-authored-by' && echo 'authorship OK'`

Attendu : `git status --short` vide (seul le plan, non suivi, peut apparaître en `??`), puis « authorship OK » : tous les commits sont de Henoc Djabia et aucun ne porte de ligne `Co-Authored-By`.


### Task 19: Checklist des spikes opérateur (non exécutable par un agent)

Les spikes du § 16 exigent une vraie instance Coolify. L'agent écrit la checklist ; son
**exécution** revient à l'opérateur. S0 à S4 sont ceux de la spec ; S5 ajoute les points que
les décisions du plan font reposer sur Coolify (utilisateur 473 et volumes, tmpfs,
`read_only` des montages `content:`, montage du dossier `./config` par `config-guard`,
variable littérale).

**Files:**

- Create: `docs/spikes.md`

**Interfaces:**

- Consumes : README (tâche 17), `scripts/security.py --remote` (tâche 15).
- Produces : `docs/spikes.md` (français) : S0 à S5 avec commandes exactes, résultats attendus et tableau de résultats.

- [ ] **Étape 1 : écrire la checklist**

Fichier complet `docs/spikes.md` :

````markdown
# Spikes Coolify — checklist opérateur (plan A)

Ces vérifications demandent une **vraie instance Coolify** : elles ne peuvent pas être jouées par
un agent ni par le banc natif. Les jouer **une fois** sur le serveur cible avant la mise en
production, puis à chaque mise à jour de Coolify pour S2 et S4. Noter le résultat (date, version
de Coolify, OK/KO, remarque) dans le tableau final et ouvrir une issue pour tout KO.

Préparation commune :

- Un **déploiement de recette** du package (ressource Application depuis ce dépôt, build pack
  Docker Compose), variables remplies d'après `.env.example`, **Connect To Predefined Network**
  activé.
- Un accès SSH au serveur, `docker` disponible.
- Dans les commandes, remplacer `<app>` par l'UUID de la ressource Coolify et `<uuid>` par le
  suffixe des conteneurs.

## S0 — Version de Coolify et limite de la ligne de commande (spec O3, § 4.3)

```bash
docker exec coolify php artisan --version
docker inspect coolify --format '{{.Config.Image}}'
```

Attendu : la version est notée dans le tableau. Si elle est **> 4.3.19**, relire
`ApplicationDeploymentJob.php` de cette version : le compose y est-il toujours transmis en base64
dans la ligne de commande SSH ? Le budget de 120 Kio (`scripts/check.py`, contrôle `size`) reste
la règle tant que ce n'est pas démontré autrement.

## S1 — Noms internes vus depuis Grafana (spec § 3.3)

```bash
docker ps --format '{{.Names}}' | grep -E '^(loki|tempo|prometheus|alloy)-'
GRAFANA=$(docker ps --format '{{.Names}}' | grep -E '^grafana-' | head -n1)
for url in http://loki-<uuid>:3100/ready http://tempo-<uuid>:3200/ready http://prometheus-<uuid>:9090/-/ready; do
  docker exec "$GRAFANA" wget -qO- "$url"; echo " <- $url"
done
docker exec "$GRAFANA" wget -qO- http://loki:3100/ready || echo "alias nu non résolu (attendu possible)"
```

Attendu : les trois URL suffixées répondent `ready` / `Prometheus Server is Ready.`. Ces URL
sont les valeurs de `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL`. Noter si
l'alias nu `loki` résout (il ne doit pas être utilisé, il peut entrer en collision).

## S2 — Portée de `coolify.traefik.middlewares` et références `@file` (spec § 5.4)

1. Installer la configuration dynamique (`traefik/grafana-coolify.yaml.example` rempli).
2. Lire les labels générés :

   ```bash
   docker inspect alloy-<uuid> --format '{{json .Config.Labels}}' | tr ',' '\n' | grep -i middlewares
   docker inspect alloy-gateway-<uuid> --format '{{json .Config.Labels}}' | tr ',' '\n' | grep -i middlewares
   ```

   Attendu : chaque routeur d'`alloy` porte exactement
   `gc-faro-ratelimit@file,gc-faro-cors@file,gc-faro-body@file` ; chaque routeur
   d'`alloy-gateway` porte exactement `gc-otlp-auth@file` ; aucun autre routeur du serveur ne
   porte ces middlewares.
3. Vérifier la résolution `@file` dans le tableau de bord ou les logs du proxy :

   ```bash
   docker logs coolify-proxy 2>&1 | grep -iE 'middleware .* does not exist|gc-(otlp|faro)' | tail
   ```

   Attendu : aucune erreur « middleware does not exist ».
4. Lancer `python3 scripts/security.py --remote` (voir README, étape 7). Attendu : 8/8, et en
   particulier le contrôle 7 (fuite de middlewares, bug coollabsio/coolify #9886).

## S3 — Attributs d'un span Faro reçu (spec § 6.5)

Depuis une page instrumentée avec le SDK Faro Web et `@grafana/faro-web-tracing` (configurée avec
`app.namespace`, `app.name`, `app.environment`), déclencher un `fetch`, puis :

```bash
docker exec alloy-<uuid> wget -qO- 'http://tempo-<uuid>:3200/api/search?tags=service.name%3D<app.name>&limit=1'
docker exec alloy-<uuid> wget -qO- 'http://tempo-<uuid>:3200/api/v2/traces/<traceID>'
```

Attendu, sur la ressource du span stocké : `project` (issu de `service.namespace`), `env` (issu de
`deployment.environment` ou `deployment.environment.name`), **pas** d'attribut
`deployment.environment*`, `tenant` présent seulement s'il respecte `[a-z0-9-]+` et n'est pas
réservé. Noter la liste complète des attributs reçus : si le SDK n'envoie pas
`service.namespace`, ouvrir une issue (le mapping de `config/alloy/config.alloy`, transform
`faro`, est à ajuster).

## S4 — Fichiers `content:` écrits octet pour octet (spec § 4.2, § 12.2)

```bash
cd /data/coolify/applications/<app>
sha256sum config/loki/loki.yaml config/tempo/tempo.yaml config/prometheus/prometheus.yml \
  config/alloy/config.alloy config/alloy-gateway/config.alloy config/grafana-setup/setup.py \
  config/config-guard/guard.sh
find config -type d
docker logs config-guard-<uuid>
```

Attendu :

- chaque empreinte est égale à celle du fichier du dépôt au même commit
  (`git rev-parse HEAD` puis `sha256sum config/...` en local) ;
- aucun des chemins ci-dessus n'est un dossier ;
- `config-guard` affiche `config-guard: all checks passed`.

Vérifier aussi dans l'interface Coolify (onglet **Environment Variables**) si les `${…}` des
contenus (`${BIND_ADDR}`, `${__trace.traceId}`…) apparaissent comme de fausses variables : gêne
purement visuelle, à noter.

## S5 — Montages et droits propres au plan A

Ces points découlent de choix du plan A que le banc natif ne peut pas exercer :

```bash
docker inspect alloy-<uuid> --format '{{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} {{json .Mounts}}'
docker exec alloy-<uuid> sh -c 'ls -ld /var/lib/alloy /var/lib/alloy/queue && touch /var/lib/alloy/queue/.w && echo writable'
docker exec alloy-gateway-<uuid> sh -c 'touch /var/lib/alloy/.w && echo writable'
docker inspect config-guard-<uuid> --format '{{json .Mounts}}'
docker inspect loki-<uuid> --format '{{json .Mounts}}'
```

Attendu :

- `alloy` et `alloy-gateway` tournent en `473:473`, système de fichiers en lecture seule ;
  `/var/lib/alloy` (volume `alloy-data`, tmpfs pour la passerelle) est inscriptible ;
- le montage `/guard` de `config-guard` est le dossier `config` de l'application, en lecture
  seule, et contient les fichiers écrits par Coolify pour les autres services ;
- les fichiers de config sont montés en lecture seule (`"RW":false`) : Coolify a conservé
  `read_only: true` sur les volumes `content:` ;
- `TENANT_HOST_REGEX`, marquée « Is Literal? », arrive intacte :
  `docker exec alloy-<uuid> printenv TENANT_HOST_REGEX` affiche la regex avec ses `$`.

## Résultats

| Spike | Date | Version Coolify | Résultat | Remarque |
|---|---|---|---|---|
| S0 | | | | |
| S1 | | | | |
| S2 | | | | |
| S3 | | | | |
| S4 | | | | |
| S5 | | | | |
````

- [ ] **Étape 2 : vérifier les liens depuis le README**

Run : `grep -c 'docs/spikes.md' README.md`

Attendu : `2` ou plus.

- [ ] **Étape 3 : commit**

```bash
git add docs/spikes.md
git commit -m "docs: add coolify operator spike checklist"
```

- [ ] **Étape 4 : OPÉRATEUR UNIQUEMENT — jouer les spikes**

**Ne pas exécuter par un agent.** Sur le serveur Coolify cible, jouer S0 à S5 de `docs/spikes.md` dans l'ordre, remplir le tableau « Résultats », puis committer le fichier complété (`git add docs/spikes.md && git commit -m "docs: record coolify spike results"`). Tout résultat KO bloque la mise en production et ouvre une correction du plan A.


---

## Auto-revue du plan

**Couverture de la spec.**

| Spec | Tâche(s) |
|---|---|
| § 3 architecture, § 3.1 unités et interfaces, ports | 7, 9, 11, 16 (gabarit), 8 (banc) |
| § 3.2 aucun `ports:` | 7 (`check.py`, contrôle `ports`) |
| § 3.3 noms internes en variables | 16 (`*_INTERNAL_URL`), 17 (README), 19 (S1) |
| § 4.1–4.2 `content:` octet pour octet, variables résolues par les outils | 2, 7 (`RepositoryRenderTest`), 19 (S4) |
| § 4.3 budget base64 | 2 (`base64_size`), 7 (`check.py size`), 18 |
| § 4.4 `config-guard` | 3, 7, 14 (`test_5`) |
| § 5.1–5.2 points d'entrée, Basic Auth par projet, révocation à chaud | 9, 15 (`security.py` 1, 2) |
| § 5.3 CORS, clé, débit, taille | 11 (Alloy), 15 (Traefik, `security.py` 3–6) |
| § 5.4 middlewares `@file` et fuite #9886 | 15 (`security.py` 7), 19 (S2) |
| § 6.1–6.2 contrat et noms effectifs | 4, 6, 9 (`otlp-names`), 11 (`faro-names`), 13 |
| § 6.3 rejet et compteurs | 9 (`reject`), 11 (`faro-reject`) |
| § 6.4 déduction depuis l'hôte (6 cas) | 11 (`faro-hosts`) |
| § 6.5 traces Faro | 12 (`faro-traces`), 19 (S3) |
| § 7.1 pipelines | 9, 10, 11, 12 |
| § 7.2 metrics-generator | 5, 13 (`spanmetrics`) |
| § 7.3 corrélations, exemplars | 16 (`correlation`, sources), 5 et 7 (`ENABLE_EXEMPLARS`, `PROM_ENABLE_FEATURES`) |
| § 7.4 scrape interne | 6, 8 |
| § 8.1 masquage et empreinte identique | 10 (`masking`), 11 (`ip-parity`) |
| § 8.2 cardinalité (routes normalisées, plafond) | 5 (`max_active_series`), 13 (`http.route`) |
| § 9.1 rétention | 4, 5, 7, 13 (`retention`) |
| § 9.2 durabilité | 9 (file persistante), 14 (`test_1`, `test_2`) |
| § 9.4 ressources et durcissement | 7, 9 (gabarit), 14 (`test_3`), 15 (`security.py` 8) |
| § 10.1–10.2 `grafana-setup` | 16 |
| § 11 variables | 7, 9, 16 (`.env.example`, contrôle `env`) |
| § 12.1 statique | 7 (8 contrôles) |
| § 12.2 unitaires | 2, 16 ; recette : 19 (S4) |
| § 12.3 bout en bout (1 à 11) | 9–13, 16 |
| § 12.4 sécurité (1 à 8) | 15 |
| § 12.5 robustesse (1 à 5) | 14 |
| § 14 déploiement | 17 |
| § 16 spikes | 19 |

Aucune exigence du périmètre A n'est restée sans tâche. Seule limite assumée : le banc Docker `compose.dev.yaml` est généré et validé syntaxiquement, mais ne peut pas être exécuté ici.

**Recherche de trous.** Aucune occurrence de « TBD », « TODO », « à compléter » ni de renvoi du type « comme la tâche N » : chaque étape donne le fichier complet ou le texte exact à insérer ou remplacer, avec son point d'ancrage.

**Cohérence des noms.** Ports (3100, 3200, 4317, 4318, 9090, 9100, 12345, 12347 ; gRPC internes 9095 et 9096 sur `127.0.0.1`), adresses du banc (`127.0.10.2` à `.9`, Traefik `.100:8080`, Grafana `.101:3300`), UID (`gc-loki`, `gc-tempo`, `gc-prometheus`, dossiers `gc-<projet>`, utilisateur 473), middlewares (`gc-otlp-auth`, `gc-faro-cors`, `gc-faro-ratelimit`, `gc-faro-body`) et variables (`BIND_ADDR`, `LOKI_DATA_DIR`, `TEMPO_DATA_DIR`, `ALLOY_QUEUE_DIR`, et celles du § 11) sont identiques d'une tâche à l'autre. Le contrôle `env` de `check.py` et `RepositoryRenderTest` le vérifient mécaniquement.

**Décisions du plan absentes de la spec** (à valider par la relecture) :

1. `config-guard` monte le dossier `./config` (en lecture seule) au lieu d'une seconde copie `content:` de chaque fichier : sinon le compose dépasse le budget (139,9 Ko contre 77,6 Ko en base64), et il vérifie ainsi les fichiers réellement montés par les services (spike S5).
2. `config-guard` valide aussi `IP_HASH_SALT`, `HOST_MAP`, `RESERVED_SUBDOMAINS` et `TENANT_HOST_REGEX`.
3. `FARO_API_KEY` vide ferme le point Faro (clé de repli dérivée du sel) au lieu de l'ouvrir.
4. Label d'`alloy` : `gc-faro-ratelimit@file` avant `gc-faro-cors@file`, pour que les requêtes préalables comptent dans la limite (§ 5.3), au prix de l'ordre écrit au § 5.4.
5. Le tenant client des logs Faro passe par l'attribut de session `tenant` (`session_attr_tenant`).
6. Dans les logs Faro, cartes et IP ne sont masquées que dans `message`, `value` et `stacktrace` (une version navigateur à quatre nombres serait sinon hachée).
7. Le gRPC interne de Loki (9095) et de Tempo (9096) écoute sur `127.0.0.1` (anneaux mono-binaire).
8. `grafana-setup` ne gère pas `isDefault` (laissé à l'opérateur).
9. Les variables du plan B sont déjà transmises à `grafana-setup` pour que `.env.example` corresponde exactement au gabarit.
10. `telemetrygen` est remplacé par des envois OTLP/HTTP JSON en bibliothèque standard (pas de binaire publié en v0.161.0, pas de `go`).
11. Les montages `content:` sont en `read_only: true`, et la file d'Alloy réessaie jusqu'à une heure (`max_elapsed_time = "1h"`).
