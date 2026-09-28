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

Les mises à jour se font par `git push` puis **Redeploy** (voir « Modifier une configuration »).

### 2. Renseigner les variables

Copier `.env.example` dans l'onglet **Environment Variables**, puis remplir :

| Variable | Valeur |
|---|---|
| `IP_HASH_SALT` | Obligatoire. Au moins 16 caractères `[A-Za-z0-9]`, par exemple `openssl rand -hex 24`. |
| `FARO_API_KEY` | Obligatoire. Clé des SDK Faro (navigateur, poste de travail, mobile), au moins 16 caractères : `openssl rand -hex 24`. |
| `PROJECTS` | Projets autorisés, séparés par des virgules **sans espace** (`in-immo,autre-projet`) : logs Faro d'un autre projet rejetés, un dossier Grafana `gc-<projet>` par projet. |
| `FARO_SERVICES` | Services Faro autorisés (`app.name`), mêmes règles d'écriture que `PROJECTS` (`web-app,desktop`). Les services de `HOST_MAP` sont toujours acceptés ; tout autre nom choisi par le client est rejeté. **Fermé par défaut** : vide, seuls les services de `HOST_MAP` passent. |
| `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` | Règles de déduction depuis l'hôte (voir plus bas). |
| `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL` | Noms réels sur le réseau `coolify` (étape 4). |
| `GRAFANA_URL`, `GRAFANA_SA_TOKEN` | URL **publique** de Grafana et jeton du compte de service (étape 5). |

- **`TENANT_HOST_REGEX` contient des `$`** : cocher **« Is Literal? »** sur cette variable, sinon
  Coolify tente de l'interpréter.
- Les domaines publics `SERVICE_FQDN_ALLOY_12347` (Faro) et `SERVICE_FQDN_ALLOY_GATEWAY_4318`
  (OTLP) sont générés par Coolify : renseigner le domaine de chaque service dans l'onglet de la
  ressource, par exemple `https://faro.example.com:12347` et `https://otlp.example.com:4318`.
- **Point Faro fermé** : `alloy` écoute toujours Faro, la clé reste donc obligatoire. Pour ne
  pas exposer Faro, ne donner **aucun domaine** au service `alloy` dans Coolify : sans domaine,
  Traefik n'a aucun routeur vers le port 12347.
- `config-guard` refuse de démarrer la stack si `IP_HASH_SALT`, `FARO_API_KEY`, `PROJECTS`,
  `FARO_SERVICES`, `HOST_MAP`, `RESERVED_SUBDOMAINS` ou `TENANT_HOST_REGEX` ont un format invalide : le message
  d'erreur est dans les logs de `config-guard`.

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
`alloy-gateway` → `gc-otlp-auth@file` ; `alloy` → `gc-faro-cors@file`,
`gc-faro-ratelimit@file`, `gc-faro-body@file`, dans cet ordre : le CORS d'abord, pour que
toute réponse, un 429 compris, porte les en-têtes CORS que le navigateur exige pour la lire.
Traefik répond lui-même aux requêtes préalables `OPTIONS`, avant la limite de débit.

> **Limite par IP et NAT** : une agence dont tous les postes sortent par une seule IP partage le
> quota `gc-faro-ratelimit` (50 req/s, rafale 100). Augmenter `average` et `burst` si besoin.

> **Derrière un autre proxy (Cloudflare…)** : toutes les requêtes arrivent de l'IP du proxy et
> partageraient un seul quota. Décommenter `sourceCriterion.ipStrategy.depth: 1` dans
> `gc-faro-ratelimit` (l'IP du client est celle que le proxy ajoute en dernier à
> `X-Forwarded-For`) et déclarer les plages d'IP du proxy dans
> `forwardedHeaders.trustedIPs` du point d'entrée Traefik, sans quoi Traefik ignore cet en-tête.

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
conseillée : **90 jours**). Copier le jeton dans `GRAFANA_SA_TOKEN`.

Rotation : créer un nouveau jeton, remplacer `GRAFANA_SA_TOKEN`, redéployer, puis supprimer
l'ancien jeton. Le jeton n'est jamais écrit dans les logs de `grafana-setup`.

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
GC_REVOKED_WAS_VALID=1 FARO_API_KEY=... \
GC_ALLOY_CONTAINER=alloy-<uuid> GC_GATEWAY_CONTAINER=alloy-gateway-<uuid> \
GC_NODE_EXPORTER_CONTAINER=node-exporter-<uuid> python3 scripts/security.py --remote
```

Chaque contrôle négatif a son témoin positif, qui dépend de ces variables :

- **Contrôle 1** (ports internes fermés) : le port du point d'entrée public est celui de
  `GC_FARO_PUBLIC_URL` (le port explicite de l'URL, sinon 443 en `https` et 80 en `http`). Il doit répondre sur
  `GC_PUBLIC_IP` avant que les ports internes soient déclarés fermés : une IP fausse ferait
  sinon passer le contrôle à tort.
- **Contrôle 2** (révocation) : `GC_REVOKED_USER` doit être un compte dont la ligne htpasswd a
  **fonctionné**, puis a été **retirée** de la configuration dynamique. Ne poser
  `GC_REVOKED_WAS_VALID=1` qu'après ces deux étapes : un utilisateur qui n'a jamais existé reçoit
  lui aussi un 401 et ne prouve rien ; sans cette variable, le contrôle 2 échoue toujours.
- **Contrôle 3** (CORS) : envoie une vraie requête Faro (app `gc-security`) au point public.
  Elle traverse le Traefik et l'`alloy` de **production** (elle est comptée dans les métriques du
  récepteur Faro), mais la charge ne contient ni log, ni événement, ni mesure, ni exception :
  **rien n'est écrit dans Loki**.
- **Contrôle 7** : `GC_OTHER_PUBLIC_URL` doit être un autre service routé par Traefik qui répond
  2xx sur `/api/health` (Grafana convient).
- **Contrôle 8** (durcissement) : `GC_ALLOY_CONTAINER`, `GC_GATEWAY_CONTAINER` et
  `GC_NODE_EXPORTER_CONTAINER` sont les noms réels des conteneurs ; le script appelle
  `docker inspect` et `docker exec` : le lancer **sur le serveur**.

Sur le banc local, les contrôles 1, 7 et 8 sont structurels (« structural on bench ») : seul
`--remote` les rend probants.

`scripts/smoke.py` interroge Loki, Tempo et Prometheus, qui ne sont joignables que depuis le
réseau `coolify` : le lancer depuis un conteneur rattaché à ce réseau, avec les variables
`GC_OTLP_URL`, `GC_FARO_URL`, `GC_LOKI_URL`, `GC_TEMPO_URL`, `GC_PROM_URL`,
`GC_ALLOY_METRICS_URL` pointant vers les noms réels, `GC_GATEWAY_URL`, `GC_GATEWAY_HOST`,
`GC_GATEWAY_USER`, `GC_GATEWAY_PASSWORD` pour la passerelle OTLP (leurs valeurs par défaut
viennent de `.harness/edge.json`, absent sur un serveur), et les mêmes valeurs de `HOST_MAP`,
`RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` que le banc (`harness/harness.env`) sur un
**déploiement de recette**.

Rejouer `security.py --remote` (contrôle 7, fuite de middlewares, bug Coolify #9886) **après
chaque mise à jour de Coolify**.

### 8. Sonde externe

Configurer une sonde **hors du serveur** (Uptime Kuma sur une autre machine, service SaaS…) sur
les URL publiques Faro et OTLP : c'est la seule alerte qui survit à une panne du serveur.

## Exploitation

### Modifier une configuration

Modifier le fichier dans `config/`, relancer `python3 scripts/render.py`, valider le commit, **`git push`
puis Redeploy**. Aucune autre action : ni fichier à retoucher sur le serveur, ni stockage à vider
dans Coolify.

Pourquoi : Coolify range chaque fichier `content:` par chemin de montage et, une fois ce fichier
créé, réutilise le contenu enregistré au lieu de celui du compose. `render.py` ajoute donc
l'empreinte du contenu au nom de chaque fichier (`loki.yaml` devient `loki.<8 hex>.yaml`, dans la
source, la cible et les commandes) : un contenu modifié porte un nom neuf, que Coolify écrit, et
`config-guard` le vérifie. Les anciens fichiers restent sur l'hôte, dans
`/data/coolify/applications/<app>/config/`, sans être montés : ils sont inoffensifs et peuvent
être supprimés à la main.

### Taille du volume `alloy-data`

La file d'envoi persistante d'`alloy` (volume `alloy-data`) absorbe les pannes de Loki, Tempo ou
Prometheus jusqu'à une heure. Loki arrêté, par exemple : `alloy` écrit les lots dans sa file
sur disque (`alloy-data`) et les réessaie pendant **environ une heure** ; au-delà, les lots
encore en échec sont **abandonnés** (perdus). Pendant une longue panne la file grossit : au plus
1000 lots de 2048 éléments par exportateur (Loki, Tempo, Prometheus), soit de l'ordre de quelques
Gio. File pleine, `alloy` refuse les nouvelles données et les émetteurs reçoivent une erreur.
Dimensionner le volume `alloy-data` (l'espace libre du disque qui le porte) en conséquence et
surveiller l'espace disque de l'hôte pendant une panne prolongée ; la file se vide d'elle-même au
retour des stockages. Pour que Loki absorbe ce rattrapage sans répondre 429, `config/loki/loki.yaml`
fixe `ingestion_rate_mb: 16` et `ingestion_burst_size_mb: 32` (défauts de Loki : 4 et 6) : une
file de l'ordre de 2 Gio de logs se vide en quelques minutes, bien avant la fin de la fenêtre de
réessai d'une heure.

## Envoyer des données

| Émetteur | Adresse | Protection |
|---|---|---|
| Serveur du **même hôte** | `ALLOY_INTERNAL_URL` (OTLP/HTTP 4318, ou gRPC 4317) | réseau `coolify` |
| Serveur **distant** | `https://<domaine OTLP>` | `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64 projet:mot-de-passe>` |
| Navigateur, poste de travail, mobile | `https://<domaine Faro>/collect` | CORS, `FARO_API_KEY`, débit, taille |

Attributs obligatoires (ressource OpenTelemetry) : `project`, `deployment.environment.name`
(`prod` ou `preprod`), `service.name`. `tenant` en ressource ou en attribut de span selon le
projet. Toute donnée sans `project`, sans environnement (une chaîne vide compte comme absente) ou
dont l'environnement n'est ni `prod` ni `preprod` est rejetée (compteurs
`otelcol_processor_filter_*_filtered_total`).

Faro : `app.namespace` → `project`, `app.name` → `service_name`, `app.environment` → `env` ;
le tenant déclaré par le client passe par l'attribut de session `tenant`. Un log Faro est rejeté,
avec son motif dans `loki_process_dropped_lines_total{reason=...}`, si son projet manque
(`missing_project`), est mal formé (`invalid_project`, attendu `[a-z0-9][a-z0-9-]{0,63}`) ou absent de
`PROJECTS` (`unknown_project`), si son environnement manque (`missing_env`) ou n'est ni `prod` ni
`preprod` (`invalid_env`), si son nom de service est mal formé (`invalid_service`), ou s'il n'est
ni dans `FARO_SERVICES` ni un service de `HOST_MAP` (`unknown_service`). Une trace Faro dont le
projet est absent de `PROJECTS` (liste renseignée) ou dont le service n'est pas autorisé est
supprimée (`otelcol_processor_filter_spans_filtered_total{component_id="otelcol.processor.filter.faro"}`).
Un tenant client hors de `[a-z0-9-]+` ou réservé est retiré. Pour les logs Faro, `env` et `tenant` sont
**déduits de l'hôte de la page** :

| Variable | Exemple |
|---|---|
| `HOST_MAP` | `example.me=guest-front:prod` (hôte exact → service et env) |
| `RESERVED_SUBDOMAINS` | `www,api` (jamais des tenants) |
| `TENANT_HOST_REGEX` | `^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me\|app)$` |

> **Risque résiduel connu** : le point Faro public ne crée plus d'identités (flux Loki, séries
> Tempo) : le service vient de `HOST_MAP` ou de `FARO_SERVICES`, jamais librement du client.
> Garder `PROJECTS` renseigné : une liste vide accepte tout projet au bon format, donc autant de
> flux que de projets inventés. Reste qu'un détenteur de la clé peut remplir les flux autorisés au
> rythme de `FARO_RATE`. Loki plafonne le tout à `max_global_streams_per_user: 10000` flux
> (`config/loki/loki.yaml`, deux fois le défaut de Loki), budget partagé avec le chemin OTLP.

Noms à utiliser dans les requêtes Grafana :

| Donnée | Loki | Tempo | Prometheus (OTLP) | Span-metrics |
|---|---|---|---|---|
| projet | `project` | `resource.project` | `project` | `project` |
| environnement | `env` | `resource.env` | `env` | `env` |
| service | `service_name` | `resource.service.name` | `job` | `service` |
| tenant | `tenant` (métadonnée) | `tenant` | `tenant` | `tenant` |
| niveau | `detected_level` (métadonnée) | — | — | — |

## Masquage (filet de sécurité)

`alloy` applique, avant tout stockage :

- **Clés sensibles supprimées** : sur le chemin OTLP, tout attribut (ressource, span,
  événement, log, point de métrique) dont la clé contient `authorization`, `cookie`, `password`,
  `token` ou `secret` est **supprimé**, quelle que soit sa valeur (pas de marqueur
  `[redacted]`). Effet de bord : cela retire aussi des dimensions de métriques légitimes, par
  exemple les compteurs de jetons `gen_ai.*token*`.
- **Secrets dans le texte libre** remplacés par `[redacted]` : jetons `Bearer`, identifiants
  `Basic` / `Digest`, valeurs `*_token=…`, `*_secret=…` et JSON `"refresh_token":"…"` (toute clé
  contenant `authorization`, `cookie`, `password`, `token` ou `secret`).
- **Emails** remplacés par `[email]`.
- **Numéros de carte** remplacés par `[card]`, **seulement** dans le texte libre (corps,
  `message`, `exception.*`) : tout nombre de 13 à 19 chiffres y est pris pour une carte, un
  horodatage en millisecondes dans un message compris.
- **Adresses IP** remplacées par `sha256(IP_HASH_SALT + ip)`, identique sur les chemins OTLP et
  Faro.

Corps de log structurés (cartes) : seul le **premier niveau** est masqué ; les cartes et
tableaux imbriqués passent **inchangés**. Préférer des corps texte ou plats pour tout ce qui peut
contenir des données personnelles.

Logs Faro : mêmes règles. En plus, `trace_id` n'est conservé que s'il fait 32 caractères
hexadécimaux minuscules, et `detected_level` seulement s'il appartient aux niveaux connus de
Loki ; le niveau Faro `log` n'en fait pas partie, Loki déduit alors le niveau lui-même.

Les IP sont hachées dans toutes les valeurs, URL comprises (`page_url`, `context_*`,
`event_data_*`), sauf dans les clés techniques où un nombre pointé est une version : sur le
chemin OTLP, les clés contenant `version` ou `user_agent` ; dans les logs Faro, `browser_*`,
`sdk_*`, `app_version`, `*_id`, `*_ms`, `*timestamp*`, `*version*`, `user_agent*`. Limite
connue : ailleurs, un numéro de version à quatre nombres (`1.2.3.4`) est pris pour une IP.

## Exemplars (expérimental)

Désactivés par défaut. Pour les activer : `PROM_ENABLE_FEATURES=exemplar-storage` **et**
`ENABLE_EXEMPLARS=true`.

`ENABLE_EXEMPLARS` et `TEMPO_MAX_ACTIVE_SERIES` ont une valeur de repli dans le compose
(`${VAR:-défaut}`). Ne pas les vider dans Coolify : les supprimer, ou remettre la valeur de
`.env.example`. Que Coolify conserve ces replis est vérifié par le spike S5 de
[`docs/spikes.md`](docs/spikes.md).

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

Autres commandes :

- `python3 harness/stack.py status` : état des services ; `python3 harness/stack.py logs <service>` :
  leurs logs ;
- `python3 harness/stack.py stop <service>` / `start <service>` : arrêter ou relancer un service
  (simulation de panne) ;
- `python3 harness/stack.py oneshot <service>` : rejouer une tâche ponctuelle (`config-guard`,
  `grafana-setup`) ;
- `python3 harness/edge.py down` : arrêter seulement Traefik et le Grafana de test ;
- `python3 harness/edge.py revoke <utilisateur>` (`proj-a` ou `proj-b`) / `restore` : retirer une
  ligne htpasswd de la configuration dynamique du Traefik de test, puis la remettre (révocation à
  chaud).

Les tests lancés avec `GC_HARNESS=1` **recyclent le banc** : ils arrêtent et relancent la stack
(et le bord) à leur guise ; ne pas compter sur un banc démarré à la main pendant ces tests.

Logs et données du banc : `.harness/` (non versionné). `.harness/coolify/` y joue le dossier où
Coolify écrit les fichiers `content:`, sous leur nom à empreinte. Faute de cgroup, le banc donne
lui-même à `alloy` et `prometheus` le `GOMEMLIMIT` (90 % du `mem_limit`) que ces binaires tirent
de la limite de leur conteneur ; le `memory_limiter` d'Alloy, lui, ne se déclenche qu'en conteneur.

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
