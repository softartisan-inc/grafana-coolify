# grafana-coolify — Spécification de conception

- **Date :** 2026-09-27 · **Révision :** 6 (après l'exécution du plan A : décisions reportées en § 6, § 8.1, § 9.1, § 11)
- **Historique :** r5 (2026-09-28), après lecture de `applicationParser()` de Coolify : fichiers
  `content:` adressés par leur contenu (§ 4.2), `FARO_API_KEY` obligatoire (§ 5.3, § 11), ordre des
  middlewares justifié (§ 5.4), rejets élargis et liste `PROJECTS` (§ 6.3), tenant client validé
  (§ 6.4), limiteur mémoire des traces Faro (§ 7.1), exclusions du hachage d'IP (§ 8.1), montages
  `:ro` en syntaxe courte (§ 9.4, § 12.4).
- **Statut :** prête pour le plan, en attente de validation
- **Auteur :** Henoc Djabia
- **Dépôt :** `grafana-coolify` (public, licence MIT)

---

## 1. Objet

`grafana-coolify` est un **package Docker Compose déployable sur Coolify**. Il ajoute à un service
Coolify **Grafana** existant tout ce qui lui manque pour recevoir, stocker et relier logs, traces et
métriques :

- **Grafana Alloy**, le collecteur ;
- **Loki**, pour les logs ;
- **Tempo**, pour les traces ;
- **Prometheus**, pour les métriques ;
- **node-exporter**, pour les métriques de l'hôte ;
- et deux conteneurs ponctuels : `config-guard` et `grafana-setup`.

Le package ne réinvente rien de ce que Coolify fournit déjà :

- **Grafana n'est pas dans le package.** Il reste le service Coolify « Grafana » (variante
  PostgreSQL recommandée — <https://coolify.io/docs/services/grafana>), inchangé.
- **Images publiées par l'éditeur du logiciel**, ou images officielles Docker, toutes à une version
  épinglée :
  - `grafana/alloy` (≥ 1.12), `grafana/loki` (3.x), `grafana/tempo` (2.x) ;
  - `prom/prometheus` (3.x), `quay.io/prometheus/node-exporter` ;
  - `python:3.13-alpine` et `alpine` (images officielles Docker).
- Aucune image maison, aucun registre privé, aucune CI de build.

### 1.1 Contexte d'origine

Le premier consommateur est **IN IMMO** : un SaaS multi-tenant de 7 projets, en prod et en préprod,
sur un seul serveur Coolify (48 Go RAM, 500 Go NVMe). Le package est **générique** : aucun domaine,
aucune règle et aucun secret propres à IN IMMO n'y figurent. Tout passe par les variables
d'environnement Coolify, et `check.py` refuse tout secret écrit en dur (§ 12.1).

### 1.2 Vocabulaire

- **`env`** : la valeur d'étiquette `prod` ou `preprod` portée par les données.
- **Déploiement de recette** : une instance Coolify du package qui sert aux tests (§ 12). Ce n'est
  pas la même chose que `env=preprod`.

### 1.3 Hors périmètre

| Sujet | Où il est traité |
|---|---|
| Installation et configuration SMTP de Grafana | Service Coolify Grafana (prérequis) |
| Instrumentation des applications (SDK, code) | Sous-projet 2 (par projet consommateur) |
| Retrait de Nightwatch, log-viewer, Sentry dans IN IMMO | Sous-projet 3 |
| Tableaux de bord métier propres à un projet | Projet consommateur (dossier Grafana réservé) |
| Métriques par conteneur (cAdvisor) | Non prévu : il exige le socket Docker, soit un accès root. Coolify fournit déjà son propre suivi des ressources |
| Haute disponibilité, stockage objet S3 | Non prévu (mono-serveur, stockage disque local) |

### 1.4 Découpage en deux plans

| Plan | Contenu | Livre |
|---|---|---|
| **Plan A — ingestion** | § 3 à § 9, `config-guard`, sources de données de `grafana-setup`, tests § 12 | Une stack qui reçoit, masque, stocke et relie, sécurisée et testée |
| **Plan B — exploitation** | Tableaux de bord et alertes de `grafana-setup` (§ 10.3, § 10.4) | Le contenu visible et les notifications |

---

## 2. Décisions

| # | Décision | Justification |
|---|---|---|
| D1 | Prometheus retenu, **Mimir écarté** | Mimir = stockage long terme distribué, surdimensionné pour un seul serveur. |
| D2 | **Pas d'Alertmanager séparé** | L'alerting intégré de Grafana couvre les règles, le routage et les notifications. |
| D3 | **Alloy** comme collecteur, en **deux instances** (§ 3) | Coolify rattache les middlewares Traefik à *tous* les routeurs d'un service. Deux points publics aux protections différentes exigent donc deux services. |
| D4 | **Sentry remplacé** par la stack (côté IN IMMO) | Une seule interface ; la perte de visibilité sur les crashs natifs est assumée. |
| D5 | Stack **partagée** (étiquette `project`) **et redéployable** sur un autre serveur | Même compose, seules les variables changent. |
| D6 | Étiquette **`tenant` partout**, métriques comprises | Filtrer par client est prioritaire ; garde-fous de cardinalité (§ 8.2). |
| D7 | **Données personnelles masquées** avant stockage | À la source (apps) et dans Alloy (filet de sécurité). |
| D8 | Rétention : logs **prod 30 j / autres 7 j**, traces **7 j**, métriques **90 j** | Loki gère la rétention par flux ; Tempo et Prometheus ont une durée unique. |
| D9 | Alertes `critical` + `prod` → **Telegram** ; le reste → **email** | Destinataires : l'administratrice et l'équipe. |
| D10 | Configs **générées dans un compose unique** (blocs `content:`) | Les montages de fichiers depuis Git redeviennent régulièrement des dossiers sous Coolify (§ 4). |
| D11 | Ressource Coolify de type **Application depuis le dépôt Git**, compose ≤ **120 Kio encodé en base64** | Mises à jour par `git push` puis redéploiement : les fichiers `content:` portent l'empreinte de leur contenu, sans quoi Coolify garderait l'ancien (§ 4.2). Coolify transmet le compose en base64 dans la ligne de commande SSH, limitée à 128 Kio (§ 4.3). |
| D12 | Middlewares Traefik (Basic Auth, CORS, débit, taille) en **configuration dynamique Traefik** (`@file`), hors du dépôt | Aucun hash de mot de passe dans un dépôt public ; révocation à chaud, sans redéployer ; un label ne peut pas lire une variable `${VAR}`, que Coolify neutralise (§ 5.4). |

---

## 3. Architecture

```
Coolify (un serveur) — réseau partagé « coolify »
├── Grafana + PostgreSQL           ← service Coolify existant, non modifié
└── grafana-coolify                ← ce package (Application Git, Docker Compose)
    ├── alloy          PUBLIC (Faro) : pipeline complet — réception OTLP interne + Faro,
    │                  masquage, étiquetage, filtrage, routage
    ├── alloy-gateway  PUBLIC (OTLP) : reçoit l'OTLP des serveurs distants, Basic Auth
    │                  par projet (Traefik), relaie tel quel vers alloy:4317
    ├── loki           stockage des logs                        (interne)
    ├── tempo          stockage des traces + metrics-generator  (interne)
    ├── prometheus     stockage des métriques + scrape interne  (interne)
    ├── node-exporter  métriques de l'hôte, montages en lecture seule (interne)
    ├── config-guard   ponctuel : vérifie les fichiers de config avant tout démarrage
    └── grafana-setup  ponctuel : configure Grafana via son API HTTP
```

### 3.1 Rôle de chaque unité

| Unité | Fait | Dépend de | Interface |
|---|---|---|---|
| `alloy` | Reçoit, masque, étiquette, filtre, route | Loki, Tempo, Prometheus | OTLP 4317/4318 (interne), Faro 12347 (public), `/metrics` 12345 (interne) |
| `alloy-gateway` | Relaie l'OTLP authentifié, sans traitement | `alloy` | OTLP 4318 (public), `/metrics` 12345 (interne) |
| `loki` | Stocke et sert les logs | disque | HTTP 3100 |
| `tempo` | Stocke les traces ; dérive des métriques | disque, Prometheus | OTLP 4317 (interne), HTTP 3200 |
| `prometheus` | Stocke les métriques ; scrape la stack | disque | HTTP 9090 (OTLP, remote write, PromQL) |
| `node-exporter` | Expose CPU, RAM, disque, réseau de l'hôte | `/proc`, `/sys`, `/` en `ro` | HTTP 9100 (interne) |
| `config-guard` | Échoue si un fichier de config est absent, vide, un dossier, ou d'empreinte SHA-256 différente de la source | — | code de sortie |
| `grafana-setup` | Crée sources, dossiers, tableaux, alertes | Grafana (API + token) | variables d'environnement |

### 3.2 Modèle de confiance réseau

- L'option Coolify **« Connect To Predefined Network »** place le package sur le réseau `coolify`,
  partagé par **toutes** les ressources du serveur.
- Loki, Tempo, Prometheus et les ports internes d'Alloy n'ont **pas d'authentification**.
- **Hypothèse de sécurité :** le serveur a un seul opérateur, et **tout conteneur du réseau
  `coolify` est de confiance**. Un voisin compromis pourrait lire les logs (masqués) et envoyer des
  données.
- **Règle vérifiée par `check.py` :** aucun service interne ne déclare `ports:`, seulement `expose:`.
- **Évolution possible** (§ 15) : un réseau interne dédié, avec Grafana rattaché à la main.

### 3.3 Noms internes

- **À l'intérieur du package**, les services se joignent par leur **nom de service** (`loki`,
  `alloy`…). La documentation Coolify le garantit dans un même stack. Les configs internes (§ 7.4,
  pipelines Alloy) l'utilisent.
- **Depuis l'extérieur du package** (Grafana, applications du même hôte), il en va autrement. Sur
  le réseau prédéfini, Coolify suffixe les conteneurs (`loki-<uuid>`) : l'alias nu n'est pas
  garanti, et il peut entrer en collision avec une autre ressource. Ces URL sont donc des
  **variables** :
  - `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL`, utilisées par
    `grafana-setup` pour créer les sources de données ;
  - `ALLOY_INTERNAL_URL`, documentée pour les applications.
- Le README explique comment lire les noms réels dans Coolify. La valeur attendue de `GRAFANA_URL`
  est l'**URL publique** de Grafana.

**Premier point du plan A, par un spike :** vérifier la résolution des noms depuis le conteneur
Grafana, sur une vraie instance.

---

## 4. Livraison des configurations

### 4.1 Une source de vérité, deux formes

- `config/` contient les **fichiers sources lisibles** : `alloy/config.alloy`,
  `alloy-gateway/config.alloy`, `loki/loki.yaml`, `tempo/tempo.yaml`,
  `prometheus/prometheus.yml`, `grafana-setup/…`.
- `scripts/render.py` **génère** `docker-compose.yaml` à partir de `compose.template.yaml`, en
  insérant chaque fichier dans un bloc `content:` de volume `bind`. Le fichier généré commence par
  « généré — ne pas modifier » et **est versionné** : c'est lui que Coolify déploie.
- `compose.dev.yaml` (local uniquement) monte directement `config/` et ajoute un **Grafana de test**.
  Il n'est jamais déployé.

### 4.2 Contenu inséré tel quel

- Le code de Coolify (`parsers.php`, `LocalFileVolume::saveStorageOnServer`) montre que le bloc
  `content:` **ne passe jamais par Docker Compose**. Coolify le retire du compose, puis l'écrit sur
  l'hôte **octet pour octet**, sans substitution.
- Règle : **`render.py` insère chaque fichier sans aucune transformation**, et en particulier sans
  échapper les `$`. `$__rate_interval`, `${DS_X}` ou `$1` arrivent intacts.
- Les variables d'environnement sont résolues **par l'outil lui-même**, à l'exécution :
  - Alloy : `sys.env("VAR")` ;
  - Loki et Tempo : `${VAR}` avec le flag `-config.expand-env=true` ;
  - Prometheus : arguments `command:` dans `compose.template.yaml`, la seule partie que Compose
    interpole ;
  - `grafana-setup` : variables d'environnement lues par le script.
- **Chemins adressés par le contenu.** `applicationParser()` enregistre chaque fichier `content:`
  comme un stockage de l'application **indexé par son chemin de montage**
  (`LocalFileVolume::updateOrCreate(['mount_path' => $target, …])`) et, une fois ce stockage créé,
  **réutilise son contenu en ignorant celui du compose**. Sans parade, deux services montant le
  même chemin s'écrasent, et une config modifiée par `git push` n'atteint jamais l'hôte.
  `render.py` insère donc les 8 premiers caractères hexadécimaux du SHA-256 du fichier dans la
  source **et** la cible (`loki.yaml` → `loki.<sha8>.yaml`) et réécrit les références à la cible
  (`command:`) ; `check.py` refuse deux cibles identiques dans le compose. Modifier une config =
  `git push` puis redéploiement ; les anciens fichiers restent sur l'hôte, non montés et
  inoffensifs.
- **Spike 4** : vérifier le fichier écrit sur l'hôte, puis une config modifiée et redéployée (nouveau
  nom utilisé, `config-guard` qui passe, sort des stockages périmés), et si l'interface Coolify
  affiche les `${…}` des contenus comme de fausses variables (gêne purement visuelle).

### 4.3 Budget de taille

Coolify (≤ 4.3.19, vérifié dans `sentry-coolify/NOTES.md`) transmet le compose rendu en base64 dans la
ligne de commande SSH : au-delà d'environ 128 Ko, le déploiement échoue avec « Argument list too
long ».

- La limite du noyau (`MAX_ARG_STRLEN`, 128 Kio) porte sur la chaîne **encodée en base64**, qui
  pèse environ 4/3 du fichier brut.
- `check.py` mesure donc la **taille base64** de `docker-compose.yaml` et **échoue au-delà de
  120 Kio**, ce qui laisse de la marge pour le reste de la commande. Cela représente environ 90 Ko
  bruts.
- Les tableaux de bord (plan B) sont les plus lourds. S'ils font dépasser le budget, ils sortent du
  compose : `grafana-setup` les télécharge depuis ce dépôt public, à un **commit épinglé**, et
  vérifie leur empreinte SHA-256.

### 4.4 `config-guard`

- Image `alpine` (officielle). `restart: "no"`.
- Il monte en lecture seule le **dossier `./config`** de l'application (`./config:/guard:ro`), là où
  Coolify écrit les fichiers `content:` : il contrôle donc exactement les fichiers que les services
  montent, sans en porter une seconde copie (budget de taille, § 4.3).
- Il échoue si un chemin est absent, vide ou **un dossier**, ou si son empreinte SHA-256 diffère de
  celle que `render.py` a calculée sur la source (injectée dans l'environnement de `config-guard`).
- Tous les autres services en dépendent (`depends_on: condition: service_completed_successfully`).

Une régression Coolify produit ainsi un échec clair au déploiement, au lieu d'un service qui démarre
avec une config vide. Le README précise que Coolify affichera deux conteneurs « exited »
(`config-guard`, `grafana-setup`) : c'est normal.

---

## 5. Points d'entrée et sécurité

### 5.1 Deux familles d'émetteurs

| Émetteur | Chemin | Protection |
|---|---|---|
| Serveur sur le **même hôte** (Laravel, Next.js serveur, workers) | `ALLOY_INTERNAL_URL` (port 4318) sur le réseau `coolify`, nom réel § 3.3 | Réseau de confiance (§ 3.2) |
| Serveur sur un **autre hôte** | `https://<fqdn>` → Traefik → `alloy-gateway:4318` → `alloy:4317` | **Basic Auth par projet**, HTTPS |
| **Client** (navigateur, desktop, mobile) | `https://<fqdn>/collect` → Traefik → `alloy:12347` | CORS (regex), clé d'app, limite de débit, taille max |

- **Jamais publics :** Loki, Tempo, Prometheus, node-exporter, les ports 4317, 4318 et 12345
  d'`alloy`, le port 12345 d'`alloy-gateway`.
- Domaines publics : variables magiques de Coolify, **`SERVICE_FQDN_ALLOY_12347`** et
  **`SERVICE_FQDN_ALLOY_GATEWAY_4318`**. Coolify dérive le nom du service en majuscules et remplace
  `-` par `_`.

### 5.2 Serveurs distants — un identifiant par projet

- Middleware Traefik **`basicAuth`** (`gc-otlp-auth@file`) sur `alloy-gateway` uniquement, avec un
  utilisateur par projet. Les lignes htpasswd sont dans la configuration dynamique de Traefik (§ 5.4),
  **jamais dans le dépôt ni dans un label**.
- Révoquer un projet = retirer sa ligne dans la configuration dynamique. Traefik la recharge **à
  chaud**, sans redéploiement.
- Les SDK OpenTelemetry envoient `Authorization: Basic …` via `OTEL_EXPORTER_OTLP_HEADERS`.
- Pourquoi Traefik et non `otelcol.auth.bearer` d'Alloy : ce dernier n'accepte **qu'un seul
  token**, donc pas de révocation par projet.
- `alloy-gateway` ne fait **aucun traitement** : le masquage et le filtrage ont lieu dans `alloy`,
  une seule fois, quel que soit le chemin.

### 5.3 Clients — rien n'y est secret

Tout ce qui est embarqué dans un bundle est lisible. Le point Faro est **en écriture seule** : un abus
ne permet pas de lire les données, seulement d'envoyer du bruit.

| Mesure | Où | Réglage |
|---|---|---|
| CORS | Traefik `headers` (`gc-faro-cors@file`) : `accessControlAllowOriginListRegex`, `accessControlAllowMethods=POST,OPTIONS`, `accessControlAllowHeaders=Content-Type,x-api-key,x-faro-session-id`, `accessControlMaxAge=600`, `addVaryHeader=true` | configuration dynamique (§ 5.4) |
| CORS côté Alloy | `faro.receiver` `cors_allowed_origins` **laissé vide**, pour éviter un double en-tête `Access-Control-Allow-Origin`, que les navigateurs refusent | — |
| Clé d'application | `faro.receiver` `server.api_key`, **obligatoire** (≥ 16 caractères, vérifiée par `config-guard` : une clé vide désactiverait le contrôle) | `FARO_API_KEY` |
| Limite de débit | Traefik `rateLimit` (`gc-faro-ratelimit@file`, par IP source) **et** `faro.receiver` `rate_limiting` (global au récepteur) | configuration dynamique ; `FARO_RATE`, `FARO_BURST` côté Alloy |
| Taille maximale | Traefik `buffering.maxRequestBodyBytes` (`gc-faro-body@file`) **et** `faro.receiver` `max_allowed_payload_size` | configuration dynamique ; `FARO_MAX_PAYLOAD` côté Alloy (ex. `5MiB`) |

- Le CORS est fait **dans Traefik** : `faro.receiver` n'accepte qu'une liste exacte ou `*`. Or les
  sous-domaines tenant sont créés à la volée, donc une liste serait toujours en retard.
- La **requête préalable `OPTIONS`** est traitée par Traefik (`gc-faro-cors`, premier middleware,
  § 5.4). Elle n'exige pas la clé et ne compte pas dans la limite de débit : elle n'atteint jamais
  `alloy`.
- **Point Faro fermé** : `alloy` écoute toujours Faro, la clé reste obligatoire ; ne pas exposer
  Faro = ne donner aucun domaine à `alloy` (`SERVICE_FQDN_ALLOY_12347` non renseigné).
- **Limite par IP et NAT :** une agence dont tous les postes sortent par une seule IP partage le même
  quota. Les valeurs par défaut doivent le supporter, et le README le signale.

### 5.4 Labels Traefik sous Coolify

- **Les middlewares ne sont pas déclarés en labels**, pour trois raisons :
  - les hash htpasswd seraient **publiés** avec le dépôt ;
  - Coolify double les `$` des labels (option « Escape special characters in labels », active par
    défaut). Un hash ou une regex écrits en dur restent valides, car Compose ramène `$$` à `$`, mais
    une **référence `${VAR}`** devient un texte littéral : impossible d'injecter ces valeurs par
    variable ;
  - modifier un label impose un redéploiement, alors que la configuration dynamique se recharge à
    chaud.
- Ils vivent donc dans la **configuration dynamique de Traefik** : Coolify → Servers → Proxy →
  Dynamic Configurations, un fichier par serveur, **hors du dépôt**.
  - Le dépôt fournit un modèle, `traefik/grafana-coolify.yaml.example`, qui définit les
    middlewares `gc-otlp-auth`, `gc-faro-cors`, `gc-faro-ratelimit` et `gc-faro-body`.
  - L'opérateur le copie, puis remplit les lignes htpasswd (entre guillemets YAML, `$` non doublés)
    et la regex d'origines.
- Rattachement par le raccourci supporté :
  - `alloy-gateway` : `coolify.traefik.middlewares=gc-otlp-auth@file`
  - `alloy` : `coolify.traefik.middlewares=gc-faro-cors@file,gc-faro-ratelimit@file,gc-faro-body@file`,
    dans cet ordre : le CORS d'abord, pour que toute réponse, un 429 compris, porte les en-têtes
    CORS sans lesquels le navigateur masque le 429 au SDK.
- Ce raccourci s'applique à **tous les routeurs du service**. C'est la raison de la séparation
  `alloy` / `alloy-gateway` (D3) : un service = un domaine = un jeu de middlewares.
- **Spike 2** : vérifier la portée du raccourci, et la résolution des références `@file`.
- **Risque connu :** coollabsio/coolify #9886, des middlewares de service qui fuient vers d'autres
  routeurs. Contrôle au § 12.4.7, à rejouer **après chaque mise à jour de Coolify**.

---

## 6. Modèle d'étiquetage

### 6.1 Attributs posés par les applications (contrat)

| Attribut OpenTelemetry | Niveau | Obligatoire | Valeur |
|---|---|---|---|
| `project` | ressource | oui | `in-immo`, … |
| `deployment.environment.name` | ressource | oui | `prod` \| `preprod` |
| `service.name` | ressource | oui | `tenant-api`, `tenant-front`, … |
| `tenant` | ressource ou span | selon le projet | identifiant du tenant, sans suffixe `-dev` |
| `target_tenant` | span / log | selon le projet | tenant visé par une action d'administration |

- Alloy **déplace** l'environnement vers un attribut court **`env`** (ressource) avant l'export : il
  copie la valeur, puis **supprime** l'attribut d'origine. Il accepte `deployment.environment.name`
  (convention actuelle) et `deployment.environment` (ancienne convention, que Faro utilise encore
  pour ses traces). Les apps suivent la convention OpenTelemetry, et les requêtes restent courtes.
- **Correspondance pour Faro** : `app.namespace` → `project`, `app.name` → `service.name`,
  `app.environment` → `env`. Le sous-projet 2 configure le SDK Faro en conséquence. Pour les logs
  Faro, la déduction depuis l'hôte (§ 6.4) prime sur `app.environment`.

### 6.2 Noms effectifs par stockage (ce qu'on écrit dans les requêtes)

| Donnée | Loki (OTLP et Faro) | Tempo | Prometheus (OTLP) | Prometheus (span-metrics Tempo) |
|---|---|---|---|---|
| projet | `project` (label indexé) | `resource.project` | `project` | `project` |
| environnement | `env` (label indexé) | `resource.env` | `env` | `env` |
| service | `service_name` (label indexé) | `resource.service.name` | `job` | `service` |
| tenant | `tenant` (métadonnée structurée) | `tenant` (ressource ou span) | `tenant` | `tenant` |
| niveau | `detected_level` (métadonnée structurée) | — | — | — |
| trace | `trace_id` (métadonnée structurée) | natif | exemplar (optionnel) | exemplar (optionnel) |

Mécanismes :

- **Loki, chemin OTLP** : `limits_config.otlp_config` avec
  **`resource_attributes.ignore_defaults: true`**. Sinon Loki ajoute ses propres labels par défaut,
  dont `service_instance_id`, qui fait exploser la cardinalité. Seuls `project`, `env` et
  `service.name` deviennent des `index_label` ; `tenant` va en `structured_metadata`. Seuls les **attributs de ressource** peuvent
  devenir des labels indexés. Le niveau reste donc une métadonnée (`detected_level`) : on filtre
  avec `| detected_level="error"`.
- **Loki, chemin Faro** (`loki.process`) : il émet **exactement les mêmes noms** (`project`, `env`,
  `service_name` en labels ; `tenant`, `trace_id`, `detected_level` en métadonnées structurées).
- **Prometheus, chemin OTLP** : il faut le flag `--web.enable-otlp-receiver` (Prometheus 3.x), et
  `otlp.promote_resource_attributes: [project, env, tenant]` dans `prometheus.yml`. Sans ce réglage,
  ces attributs finissent dans `target_info`. Le traducteur OTLP convertit **toujours**
  `service.name` en `job` : le service se filtre donc sur `job`, et `service.name` n'est pas promu
  (ce serait un doublon). Plus `--storage.tsdb.out-of-order-time-window=30m`.
- **Span-metrics Tempo** : `dimensions: [project, env, tenant, http.route]`. Tempo cherche chaque dimension
  dans les attributs de span, puis de ressource ; les deux niveaux sont testés (§ 12.3.6).

### 6.3 Rejet

- Toute donnée sans `project` ou sans `env` (une chaîne vide compte comme absente), ou dont `env`
  n'est ni `prod` ni `preprod`, est **supprimée** par `alloy`.
- Logs Faro : `project` doit aussi respecter `[a-z0-9-]{1,64}` et, si `PROJECTS` est renseigné, en
  faire partie ; `service_name` doit respecter `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
- Métriques observables :
  - chemin OTLP : `otelcol_processor_filter_spans_filtered`,
    `otelcol_processor_filter_logs_filtered`, `otelcol_processor_filter_datapoints_filtered`
    (sans motif) ;
  - chemin Faro : `loki_process_dropped_lines_total{reason=…}` avec `missing_project`,
    `invalid_project`, `unknown_project`, `missing_env`, `invalid_env`, `invalid_service`, `unknown_service`
    (`stage.drop` avec `drop_counter_reason`).
- Prometheus scrape ces métriques (§ 7.4).

### 6.4 Déduction depuis l'hôte (logs Faro)

`faro.receiver` place l'URL de la page (`page_url`) dans la ligne logfmt des **logs**. Pour ces logs,
`alloy` **déduit** `tenant` et `env` de l'hôte au lieu de faire confiance au client. L'algorithme est
normatif :

```
host := hôte de page_url, en minuscules
si host ∈ HOST_MAP :                      # correspondance exacte, prioritaire
    service_name := HOST_MAP[host].service   # écrase la valeur du client
    env          := HOST_MAP[host].env
    tenant       := (aucun)
sinon si host correspond à TENANT_HOST_REGEX (groupes nommés `sub` et `dev`) :
    env := "preprod" si `dev` est présent, sinon "prod"
    si sub ∈ RESERVED_SUBDOMAINS :
        tenant := (aucun)                    # le suffixe -dev fixe quand même l'env
    sinon :
        tenant := sub                        # suffixe -dev déjà retiré par la regex
sinon :
    env, tenant := valeurs fournies par le client (puis contrôle § 6.3 ;
                   tenant retiré s'il ne respecte pas [a-z0-9-]+ ou s'il est réservé, comme au § 6.5)
```

Cas de référence, tous testés au § 12.3.2 (avec `TENANT_HOST_REGEX` =
`^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$`, `HOST_MAP` = `example.me=guest-front:prod`,
`RESERVED_SUBDOMAINS` = `www,api`) :

| Hôte | `service_name` | `env` | `tenant` |
|---|---|---|---|
| `example.me` | `guest-front` | `prod` | — |
| `www.example.me` | (client) | `prod` | — |
| `api-dev.example.me` | (client) | `preprod` | — |
| `acme.example.me` | (client) | `prod` | `acme` |
| `acme-dev.example.app` | (client) | `preprod` | `acme` |
| `inconnu.autre.org` | (client) | (client) | (client) |

Toutes les règles sont des variables. Un projet sans sous-domaines laisse `TENANT_HOST_REGEX` vide.

### 6.5 Traces Faro, serveurs, desktop, mobile

- **Traces Faro** : `faro.receiver` transmet les traces **sans** l'URL de la page. Pour elles,
  `alloy` **valide** au lieu de déduire. `tenant` et `env` fournis par le client doivent respecter
  le format (`[a-z0-9-]+`) et ne pas être un sous-domaine réservé ; sinon ils sont retirés.
  - Le **span serveur** correspondant, résolu par le backend, porte le tenant qui fait foi.
  - **Spike du plan A :** relever les attributs réellement présents sur un span Faro reçu, et
    confirmer cette approche.
- **Serveurs, desktop, mobile** : pas d'hôte de page. Ils posent eux-mêmes `tenant` (le tenant
  résolu, ou celui de la session) et `env` (config de déploiement ou de build).

---

## 7. Trajet des données

### 7.1 Pipelines d'`alloy`

```
alloy-gateway ──OTLP──┐
otelcol.receiver.otlp ◄┘─► memory_limiter ─► transform (env court, masquage) ─► filter (project/env) ─► batch
                                                                                  ├─ traces  ─► Tempo      (OTLP)
                                                                                  ├─ logs    ─► Loki       (/otlp/v1/logs)
                                                                                  └─ metrics ─► Prometheus (/api/v1/otlp/v1/metrics)
faro.receiver ─┬─ traces ─► memory_limiter ─► transform (env court, validation tenant/env, masquage) ─► filter ─► batch ─► Tempo
               └─ logs   ─► loki.process (déduction hôte § 6.4, validation, stage.drop, stage.replace) ─► loki.write ─► Loki
```

- `faro.receiver` ne sort les logs que vers des récepteurs Loki : ses logs suivent donc le chemin
  `loki.process` / `loki.write`, distinct du chemin OTLP.

### 7.2 Métriques dérivées des traces

Sous PHP-FPM, chaque requête est un processus éphémère : un compteur en mémoire meurt avec elle. Au
lieu d'une librairie de métriques PHP adossée à Redis ou APCu, le **metrics-generator de Tempo**
calcule débit, erreurs, latences et carte des services.

- Il utilise les processeurs `span-metrics` et `service-graphs`, activés dans
  `overrides.defaults.metrics_generator.processors`.
- Il écrit dans Prometheus par `storage.remote_write` vers `/api/v1/write`, avec
  `--web.enable-remote-write-receiver`.
- Dimensions : `project`, `env`, `tenant`, et la route normalisée `http.route`.
- **Plafond :** `overrides.defaults.metrics_generator.max_active_series` = `TEMPO_MAX_ACTIVE_SERIES`.
- **Contrainte d'ordre :** si un échantillonnage des traces est un jour ajouté dans Alloy, les
  span-metrics migrent dans Alloy (`otelcol.connector.spanmetrics`, `servicegraph`), **en amont**
  de l'échantillonneur. Sinon les métriques deviennent fausses.

### 7.3 Corrélations (sources de données créées par `grafana-setup`)

- **Log → trace** : champ dérivé sur la métadonnée structurée `trace_id`. Le chemin Faro la
  normalise lui aussi en `trace_id` (depuis `traceID` en logfmt) : un seul champ dérivé couvre donc
  les deux chemins.
- **Trace → logs** : `tracesToLogsV2`, filtré sur `trace_id`.
- **Trace → métriques** : `tracesToMetrics`.
- **Exemplars** (point de graphique → trace) : **désactivés par défaut**. Ils exigent le flag
  expérimental de Prometheus, et s'activent par :
  - `PROM_ENABLE_FEATURES=exemplar-storage`, injecté dans `command:` ;
  - `ENABLE_EXEMPLARS=true`, que Tempo lit via `-config.expand-env` pour `send_exemplars`.

### 7.4 Scrape interne de Prometheus

Prometheus scrape :

- lui-même ;
- `loki:3100/metrics` et `tempo:3200/metrics` ;
- `alloy:12345/metrics` (point de métriques natif d'Alloy) ;
- `alloy-gateway:12345/metrics` ;
- `node-exporter:9100`.

C'est la source des alertes de santé, de rejet et de cardinalité.

---

## 8. Masquage et cardinalité

### 8.1 Masquage dans `alloy` (filet de sécurité)

Le masquage principal se fait **à la source**, dans chaque app : liste de champs autorisés, requêtes
SQL sans valeurs, jamais de corps de requête. Il relève des sous-projets d'instrumentation. `alloy`
rattrape ce qui a échappé :

| Donnée | Motif | Remplacement |
|---|---|---|
| Email | adresse RFC 5322 simplifiée | `[email]` |
| Secrets (texte libre) | `Bearer <jeton>`, `Basic`/`Digest <jeton>`, `*_token=`, `*_secret=`, `"refresh_token":"…"` | `[redacted]` |
| Secrets (attributs) | clés dont un mot vaut `authorization`, `cookie`, `password`, `token`, `secret`… (borné : `input_tokens`, `tokenizer` sont conservés), à tout niveau après aplatissement (`user.credentials.password`) | **attribut supprimé** |
| Numéro de carte | `\b(?:\d[ -]?){12,18}\d\b`, appliqué **seulement** au texte libre (`body`, `message`, `exception.*`), jamais aux clés `*_id`, `*timestamp*`, `*_ms` | `[card]` |
| Adresse IP (v4 et v6) | adresse complète, dans toutes les valeurs (URL comprises) **sauf** les clés techniques où un nombre pointé est une version : chemin OTLP, clés contenant `version` ou `user_agent` ; logs Faro, `browser_*`, `sdk_*`, `app_version`, `*_id`, `*_ms`, `*timestamp*`, `*version*`, `user_agent*` | SHA-256 de la chaîne **`IP_HASH_SALT` suivi de l'IP**, en hexadécimal complet |

Portée : sur le chemin OTLP, toutes les cartes d'attributs (ressource, **portée
d'instrumentation** `scope.attributes`, span, événement, log, point de métrique) et le corps de log
structuré. Les règles s'appliquent à une **copie aplatie** (`flatten`, stable) de chaque carte
d'attributs : cartes imbriquées et tableaux y deviennent des clés pointées (`user.email`, `tags.0`),
masquées élément par élément ; la copie ne remplace l'original que si une règle l'a modifiée
(comparaison `pcommon.Map.Equal`), si bien qu'un enregistrement propre garde sa forme. Le corps de
log structuré est toujours aplati ; un corps en tableau devient son texte JSON. `error.message` est
du texte libre au même titre que `exception.message`. `replace_all_patterns` ne lit que les chaînes et parcourir un tableau
exigerait les lambdas OTTL (alpha, porte `ottl.functions.enableLambda`) : `flatten` est la seule voie
stable. Contrepartie acceptée : sur un enregistrement **masqué**, un attribut tableau ou carte change de
forme dans les stockages (`k` → `k.0`, `k.1`) ; coût CPU mesuré sur le banc : +45 % pour `alloy`. Reste hors d'atteinte : le motif de carte bancaire sur les clés de texte libre
imbriquées, et les valeurs binaires.

Composants :

- Chemin OTLP et traces Faro : `otelcol.processor.transform`, en OTTL, stable : `flatten`,
  `replace_all_patterns`, et `SHA256(Concat([sel, ip], ""))` pour les IP.
- Logs Faro : `loki.process` `stage.replace`, stable, avec la fonction de gabarit **`Sha2Hash`**.
  Sa signature est `Sha2Hash(salt, input)` et elle calcule `sha256(salt + input)`. Il faut donc
  écrire **`{{ Sha2Hash "<sel>" .Value }}`**, sel en premier. L'ordre inverse produirait
  `sha256(ip + sel)` et casserait la correspondance avec l'OTTL. À ne pas confondre avec `Hash`, qui
  fait du SHA3-256.
- Même algorithme, même sel et même ordre sur les deux chemins : **une même IP produit la même
  empreinte** partout (testé au § 12.3.4).

**Écarté :** `otelcol.processor.redaction`. Il est **expérimental** et imposerait
`stability.level = "experimental"` à toute l'instance, ce qui autoriserait aussi tous les autres
composants expérimentaux.

### 8.2 Garde-fous de cardinalité (`tenant` dans les métriques)

- `tenant` n'apparaît que sur les **métriques clés** (span-metrics : requêtes, erreurs, latence).
- Routes **normalisées** (`http.route`, ex. `/{tenant}/assets/{id}`), jamais l'URL brute.
- Plafond de séries du metrics-generator (§ 7.2).
- Alerte sur `prometheus_tsdb_head_series` au-delà de `CARDINALITY_ALERT_THRESHOLD` (plan B).

---

## 9. Stockage, rétention, durabilité, ressources

### 9.1 Rétention

| Composant | Rétention | Réglage |
|---|---|---|
| Loki | `env="prod"` 30 j ; **tout autre `env`** 7 j | `retention_period` = 7 j (défaut) + `retention_stream` `{env="prod"}` = 30 j ; `compactor.retention_enabled: true` ; `compactor.delete_request_store: filesystem` ; plafonds explicites `max_global_streams_per_user: 10000`, `ingestion_rate_mb: 16`, `ingestion_burst_size_mb: 32`, et par flux `per_stream_rate_limit: 8MB`, `per_stream_rate_limit_burst: 24MB` pour des enregistrements de 1 à 4 Kio (le point Faro public ne peut pas épuiser le budget des flux ; une file Alloy se vide plus vite, même concentrée sur un seul flux, qui ne prend jamais plus de la moitié du débit du tenant) ; schéma **tsdb v13** avec `index.period: 24h` (requis pour les métadonnées structurées et la rétention) |
| Tempo | 7 j | `compactor.compaction.block_retention` (clé de Tempo 2.x ; à revérifier avant un passage en 3.x) |
| Prometheus | 90 j | `--storage.tsdb.retention.time` + `--storage.tsdb.retention.size` en garde-fou |

- Le flux prod est la **règle explicite**, et tout le reste retombe sur la durée courte. Une valeur
  `env` imprévue ne peut donc pas être gardée 30 jours par erreur.
- Variables : `LOKI_RETENTION_PROD`, `LOKI_RETENTION_DEFAULT`, `TEMPO_RETENTION`,
  `PROM_RETENTION_TIME`, `PROM_RETENTION_SIZE`.

### 9.2 Durabilité côté `alloy`

- **Traces, logs OTLP et métriques** : file d'envoi persistante sur disque (`sending_queue` +
  `otelcol.storage.file`, présent depuis Alloy 1.9). `alloy` tourne avec
  `stability.level = "public-preview"`, pas plus haut. Le niveau de stabilité réel du composant est
  vérifié pour la version épinglée par `alloy validate` (§ 12.1).
- **Logs Faro** (`loki.write`) : **pas de WAL**, car il est encore expérimental. Une perte limitée
  est acceptée si Loki redémarre pendant un envoi.
- `alloy-gateway` ne stocke rien : si `alloy` est indisponible, il renvoie une erreur, et le SDK de
  l'émetteur réessaie.

### 9.3 Sauvegardes

Les volumes Loki, Tempo et Prometheus ne sont **pas** sauvegardés : la télémétrie se renouvelle.
Seul le PostgreSQL de Grafana l'est, par les backups Coolify, hors package.

### 9.4 Ressources et durcissement

- Chaque conteneur a une limite `mem_limit` et `cpus`. Enveloppe visée : **~6 Go de RAM** au total,
  pour qu'un pic d'ingestion ne prive jamais les applications voisines.
- `alloy` et `alloy-gateway` :
  - **aucun montage de fichier de l'hôte** hors de leur propre fichier de config ;
  - `user:` non-root, `read_only: true`. Seuls restent inscriptibles le volume de file d'envoi
    d'`alloy` et le répertoire `--storage.path` des deux services (un volume pour `alloy`, un `tmpfs`
    pour `alloy-gateway`) ;
  - `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`.
- `node-exporter` est le **seul** service à monter des chemins système de l'hôte (`config-guard` ne
  monte que `./config`, en lecture seule) : `/proc`, `/sys` et `/` en **lecture
  seule**, en syntaxe courte `:ro` (Coolify reconstruit les montages `bind` en `source:cible` et ne
  garde que le mode d'une syntaxe courte : un `read_only: true` de syntaxe longue disparaît). Il
  n'est pas exposé publiquement.
- Les fichiers `content:` exigent la syntaxe longue : Coolify les monte donc sans `read_only`, et
  c'est l'utilisateur non-root des services qui ne peut pas les écrire.

---

## 10. `grafana-setup`

### 10.1 Exécution

- Image officielle **`python:3.13-alpine`** (version épinglée), script en bibliothèque standard uniquement (`urllib`,
  `json`, `hashlib`). Pas de `curl` ni de `jq`.
- Lancé au déploiement, relançable sans effet de bord. `restart: "no"`.
- Authentification : **token de compte de service** Grafana (`GRAFANA_SA_TOKEN`) :
  - rôle **Admin d'organisation**, nécessaire pour les sources de données et le provisioning ;
  - expiration conseillée : 90 jours, rotation décrite au README ;
  - le token n'est **jamais** écrit dans les logs.
- **Garde de version :** le script lit `/api/health` et s'arrête avec un message clair si Grafana a
  une version inférieure à **12.0**.

### 10.2 Sources de données et dossiers (plan A)

| Objet | Méthode idempotente |
|---|---|
| Sources Loki, Tempo, Prometheus (UID fixes, corrélations § 7.3) | `GET /api/datasources/uid/:uid` → `PUT /api/datasources/uid/:uid` si présente, sinon `POST /api/datasources` avec l'UID dans le corps |
| Dossiers par projet (`PROJECTS`) | `GET /api/folders/:uid` → création si absent ; UID = `gc-<projet>` |

### 10.3 Tableaux de bord (plan B)

Liste fermée :

1. **Vue projet** : débit, taux d'erreur et p95 par service, avec filtres `project`, `env`,
   `service`, `tenant`.
2. **Erreurs et latence par service** : détail par route, et logs d'erreur liés.
3. **Frontend** : erreurs JS, Web Vitals, sessions (données Faro).
4. **Hôte** : CPU, RAM, disque, réseau (`node-exporter`).
5. **Santé du pipeline** : ingestion, rejets (§ 6.3), file d'envoi, santé de Loki, Tempo et
   Prometheus.
6. **Cardinalité** : séries par `project`, `service`, `tenant`.

Chaque dossier de projet reçoit des **liens** vers ces tableaux, avec `var-project` déjà réglé.

La variable de tableau « service » se traduit selon la source (§ 6.2) : `job` pour les métriques
OTLP, `service` pour les span-metrics, `service_name` pour Loki.

Import : `POST /api/dashboards/db` avec `overwrite: true` et un UID fixe. L'idempotence se mesure sur
le **contenu** (JSON normalisé), pas sur `version`, que Grafana incrémente à chaque écriture.

### 10.4 Alertes (plan B)

- API legacy `/api/v1/provisioning/*` : **dépréciée**, mais fonctionnelle en Grafana 12.x. Sa
  remplaçante est en alpha ou bêta.
- Tous les appels passent par **une fonction par type d'objet**, pour que la bascule future ne
  touche qu'elles.
- En-tête **`X-Disable-Provenance: true`** : les objets restent modifiables dans l'interface.

| Objet | Méthode idempotente |
|---|---|
| Points de contact Telegram et email | `GET` liste → `PUT /contact-points/:uid` si présent, sinon `POST` |
| Politique de notification | `PUT /policies` (objet unique) |
| Règles d'alerte | `GET /alert-rules/:uid` → `PUT` si présente, `POST` si le `GET` renvoie 404 |

**Règles de base** (seuils en variables, § 11) :

| Règle | Condition | `severity` |
|---|---|---|
| Taux d'erreur | > `ALERT_ERROR_RATE` sur 5 min, par service | `critical` |
| Latence | p95 > `ALERT_P95_MS` sur 10 min | `warning` |
| Service muet | aucune donnée depuis `ALERT_SILENCE_MIN` min | `critical` |
| Disque | usage > `ALERT_DISK_PCT` % | `critical` |
| Cardinalité | séries > `CARDINALITY_ALERT_THRESHOLD` | `warning` |
| Rejets | données rejetées > 0 sur 15 min | `warning` |

**Routage :** `severity=critical` **et** `env=prod` → Telegram ; tout le reste → email.

**Prérequis manuels** (documentés, non automatisés) :
- SMTP configuré sur le service Grafana ;
- un bot Telegram créé, et le `chat_id` du groupe connu.

---

## 11. Variables d'environnement

- `.env.example` fait foi. `check.py` vérifie que **chaque** `${VAR}` de `compose.template.yaml`
  y figure, et inversement, à l'exception des variables purement documentaires (`ALLOY_INTERNAL_URL`),
  listées dans `check.py`. Les blocs `content:` sont **exclus** de ce contrôle : leurs `${…}` sont
  résolus par les outils (§ 4.2), ou sont des variables de tableaux de bord.
- Les réglages Traefik (htpasswd, regex d'origines, débit et taille côté Traefik) ne sont **pas**
  des variables. Ils sont dans la configuration dynamique (§ 5.4), décrite par
  `traefik/grafana-coolify.yaml.example`.

| Variable | Oblig. | Défaut | Rôle |
|---|---|---|---|
| `SERVICE_FQDN_ALLOY_12347`, `SERVICE_FQDN_ALLOY_GATEWAY_4318` | selon usage | — | Domaines publics (Coolify) |
| `FARO_API_KEY` | oui | — | Clé d'application Faro, ≥ 16 caractères (`openssl rand -hex 24`) |
| `FARO_RATE`, `FARO_BURST`, `FARO_MAX_PAYLOAD` | non | 100 / 200 / 5MiB | Limites côté Alloy (NAT pris en compte) |
| `ALLOY_INTERNAL_URL` | non (doc) | — | Adresse interne d'`alloy` pour les apps du même hôte (§ 3.3) |
| `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` | non | vide | Déduction depuis l'hôte (§ 6.4) |
| `IP_HASH_SALT` | oui | — | Sel de l'empreinte des IP |
| `LOKI_RETENTION_PROD`, `LOKI_RETENTION_DEFAULT` | non | 720h / 168h | Rétention logs |
| `TEMPO_RETENTION` | non | 168h | Rétention traces |
| `TEMPO_MAX_ACTIVE_SERIES` | non | 100000 | Plafond du metrics-generator |
| `PROM_RETENTION_TIME`, `PROM_RETENTION_SIZE` | non | 90d / 100GB | Rétention métriques |
| `PROM_ENABLE_FEATURES`, `ENABLE_EXEMPLARS` | non | vide / false | Exemplars (expérimental) |
| `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL` | oui | — | Noms réels sur le réseau `coolify` (§ 3.3) |
| `GRAFANA_URL`, `GRAFANA_SA_TOKEN` | oui | — | Accès API pour `grafana-setup` |
| `PROJECTS` | non | vide | Projets autorisés pour les logs **et les traces** Faro (vide : tout projet bien formé) et dossiers Grafana à créer |
| `FARO_SERVICES` | non | vide | Services Faro autorisés en plus de ceux de `HOST_MAP` ; **fermé par défaut** : un `service_name` client absent des deux listes est rejeté (`unknown_service`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERT_EMAILS` | plan B | — | Notifications |
| `ALERT_ERROR_RATE`, `ALERT_P95_MS`, `ALERT_SILENCE_MIN`, `ALERT_DISK_PCT` | non | 0.05 / 1500 / 15 / 80 | Seuils d'alerte |
| `CARDINALITY_ALERT_THRESHOLD` | non | 200000 | Seuil de séries |

---

## 12. Tests et vérification

- Tous les tests tournent **en local** **et** sur un **déploiement de recette** Coolify.
- En local, deux bancs :
  - `compose.dev.yaml`, pour une machine avec Docker ;
  - `harness/`, un **banc natif sans Docker**. Il lance les binaires officiels (téléchargés et
    vérifiés par `tools/fetch-binaries.sh` aux versions de `tools/versions.env`) avec les **mêmes
    commandes, variables et fichiers** que ceux lus dans `compose.template.yaml`, chaque service
    sur sa propre adresse de boucle locale. C'est le banc de la CI et des environnements où Docker
    ne peut pas créer de conteneurs.
- Chaque test est **prouvé capable d'échouer** : chaque assertion est jouée une fois contre une
  config volontairement cassée.

### 12.1 Statique — `scripts/check.py`

1. `docker-compose.yaml` à jour : rendu puis `diff` avec la version versionnée.
2. Taille encodée en base64 ≤ 120 Kio (§ 4.3).
3. `docker compose config` sur une variante du compose où `content:` est retiré et `source:` rendu
   fictif. C'est ainsi que le compose *déployé* est validé.
4. Aucun `ports:` sur les services internes (§ 3.2).
5. `.env.example` et les `${VAR}` du compose se correspondent exactement (§ 11).
6. Aucun secret en dur dans `config/` : motifs `token|password|secret|salt|key` avec une valeur
   littérale non vide.
7. Validateurs officiels : `alloy validate` (les deux configs), `loki -verify-config`,
   `tempo -config.verify`, `promtool check config`.
8. `ruff` sur les scripts Python ; `shellcheck` sur les scripts shell (`config-guard`, outils).

### 12.2 Unitaires

- **`render.py`** :
  - chaque bloc `content:`, relu par un parseur YAML, est **identique octet pour octet** au fichier de
    `config/`. Le test utilise un fichier contenant `$__rate_interval`, `${DS_X}`, `$$`, `$1`, des
    tabulations, des lignes vides en fin de fichier et des caractères non ASCII ;
  - la sortie est déterministe ;
  - l'indentation YAML est correcte.
- **Sur le déploiement de recette** (spike 4, puis à chaque version) : chaque fichier écrit par
  Coolify sur l'hôte est identique octet pour octet à sa source dans `config/` (comparaison
  d'empreintes SHA-256 par `config-guard`, qui reçoit les empreintes attendues).
- **`grafana-setup`** (contre le Grafana de test) :
  - 1er passage : tout est créé ;
  - 2e passage : aucun changement de contenu, mêmes UID, même nombre d'objets ;
  - version de Grafana < 12 : arrêt propre ;
  - token jamais présent dans la sortie.

### 12.3 Bout en bout — `scripts/smoke.py`

Envois OTLP/HTTP JSON écrits en Python standard (`scripts/gclib.py` : attributs de ressource et de
span, en-têtes), `telemetrygen` n'ayant pas de binaire publié pour la version visée, plus des
charges Faro envoyées de la même façon. Puis interrogation de Loki, Tempo et Prometheus.

1. Log, trace et métrique arrivés avec les **noms effectifs** du § 6.2, par les chemins interne et
   `alloy-gateway` ; log et trace par le chemin Faro, qui n'a pas de chemin métriques (ses mesures
   arrivent comme des logs).
2. Les 6 cas du tableau § 6.4 donnent exactement le résultat attendu.
3. Trace Faro avec un `tenant` au mauvais format ou réservé : l'attribut est retiré (§ 6.5).
4. Masquage :
   - email, `Bearer`, numéro de carte : masqués ;
   - un **epoch en millisecondes** placé sous une clé exclue (`*timestamp*`, `*_ms`) n'est **pas**
     masqué en `[card]` ; le même nombre dans `message` l'est (comportement attendu, documenté) ;
   - la même IP donne la **même empreinte** dans un log Faro et dans un log OTLP.
5. Envoi sans `project` : absent du stockage, compteurs du § 6.3 incrémentés.
6. Span-metrics dans Prometheus avec `tenant`, que `tenant` soit un attribut de **ressource** ou de
   **span**.
7. Métriques applicatives OTLP : `project`, `env` et `tenant` présents comme labels, et non dans
   `target_info` ; le service est porté par `job`.
8. Loki : **aucun** label indexé autre que `project`, `env`, `service_name` (en particulier ni
   `deployment_environment_name`, ni `service_instance_id`).
9. Faro : `app.namespace` donne `project`, `app.name` donne `service_name`. Une trace Faro portant
   `deployment.environment` (ancienne convention) ressort avec `env`, et sans l'attribut d'origine.
10. **Corrélations** : depuis un log de chaque chemin, la requête `trace_id` retrouve la trace dans
   Tempo ; depuis la trace, la requête de logs retrouve le log.
11. Rétention effective, lue dans `/config` de Loki, dans la config de Tempo et dans les flags de
   Prometheus : conforme aux variables.

### 12.4 Sécurité — `scripts/security.py`

1. Loki, Tempo, Prometheus, node-exporter, les ports 4317, 4318, 12345 d'`alloy` et 12345
   d'`alloy-gateway` : **injoignables** depuis l'extérieur.
2. OTLP public (`alloy-gateway`) :
   - sans identifiant : 401 ;
   - identifiant retiré de la configuration dynamique : 401, sans redéploiement ;
   - identifiant valide : 2xx.
3. Faro :
   - requête préalable `OPTIONS` depuis une origine valide : 200 avec les en-têtes du § 5.3 ;
   - depuis `evil-example.me` ou `x.example.me.attacker.com` : pas d'en-tête CORS ;
   - un seul en-tête `Access-Control-Allow-Origin` sur une réponse valide.
4. Faro sans clé ou avec une mauvaise clé : rejet.
5. Rafale de `POST` au-delà de la limite : 429, renvoyé par Traefik, avec
   `Access-Control-Allow-Origin`.
6. Charge au-delà de la taille maximale : 413.
7. **Fuite de middlewares** (Coolify #9886) :
   - `alloy` ne demande pas de Basic Auth ;
   - `alloy-gateway` ne renvoie pas d'en-tête CORS ;
   - un autre domaine public du serveur ne renvoie ni 401, ni en-tête CORS du package.
8. `alloy` et `alloy-gateway` : aucun montage de l'hôte hors de leur fichier de config, que leur
   utilisateur non-root ne peut pas écrire, système de fichiers en lecture seule (`docker inspect`,
   `docker exec`) ; montages de `node-exporter` en lecture seule.

### 12.5 Robustesse

1. **Arrêt de Loki** pendant un envoi OTLP : les données sont livrées après son redémarrage.
2. **Redémarrage d'`alloy`** pendant un envoi : la file persistante est rejouée, sans perte.
3. Envoi massif : l'enveloppe mémoire est respectée, et les autres conteneurs restent sains.
4. Chemin de config transformé en dossier, ou contenu altéré (régression Coolify simulée) :
   `config-guard` échoue, et aucun service ne démarre.
5. `PROM_ENABLE_FEATURES` vide : Prometheus démarre normalement. S'il refuse une valeur vide, le
   défaut devient une fonctionnalité sans effet, documentée au plan.

### 12.6 Exploitation (plan B)

1. Chaque règle d'alerte se déclenche sur une donnée synthétique.
2. Routage : `GET /api/v1/provisioning/policies` conforme ; une notification de test arrive sur
   Telegram (`critical` + `prod`) et par email (le reste).
3. Les 6 tableaux de bord se chargent sans erreur de requête sur des données synthétiques.

---

## 13. Structure du dépôt

```
grafana-coolify/
├── docker-compose.yaml        # GÉNÉRÉ — déployé par Coolify
├── compose.template.yaml      # modèle (services, labels, limites)
├── compose.dev.yaml           # local : montages directs + Grafana de test
├── config/
│   ├── alloy/config.alloy
│   ├── alloy-gateway/config.alloy
│   ├── loki/loki.yaml
│   ├── tempo/tempo.yaml
│   ├── prometheus/prometheus.yml
│   └── grafana-setup/         # setup.py, dashboards/*.json, alerts/*.json
├── traefik/grafana-coolify.yaml.example   # middlewares @file (modèle, sans secret)
├── scripts/                   # render.py, check.py, smoke.py, security.py (Python, stdlib)
├── harness/                   # banc natif sans Docker (§ 12)
├── tools/                     # fetch-binaries.sh, versions.env
├── docs/superpowers/specs/
├── .env.example
├── README.md                  # déploiement pas à pas (FR)
└── LICENSE                    # MIT
```

Conventions : documentation et spec en **français** ; code, commentaires, noms de variables,
messages de commit et branches en **anglais**.

---

## 14. Déploiement (résumé du README)

1. Coolify : créer une nouvelle ressource **Application** depuis ce dépôt Git, en build pack
   **Docker Compose**.
2. Renseigner les variables (§ 11).
3. Coolify → Servers → Proxy → **Dynamic Configurations** : coller
   `traefik/grafana-coolify.yaml.example`, puis remplir les lignes htpasswd et la regex d'origines.
4. Activer **Connect To Predefined Network**, puis relever les noms réels des services (§ 3.3).
5. Dans Grafana : créer un compte de service (rôle **Admin**), puis copier son token dans
   `GRAFANA_SA_TOKEN`.
6. Déployer : `config-guard` démarre en premier, puis les services, puis `grafana-setup`.
7. Lancer `smoke.py` et `security.py` contre le déploiement.
8. Configurer une **sonde externe** (hors serveur) sur les URL publiques. C'est la seule alerte qui
   survit à une panne du serveur.

---

## 15. Risques

| Risque | Gravité | Mitigation |
|---|---|---|
| Stack sur le même serveur que les apps : une panne du serveur coupe les alertes | Élevée | Sonde externe (§ 14.8) |
| Voisin compromis sur le réseau `coolify` : lecture des logs, injection de données | Moyenne | Hypothèse énoncée (§ 3.2) ; évolution possible : réseau interne dédié |
| Régression Coolify sur les montages de fichiers | Moyenne | Blocs `content:` + `config-guard` |
| Compose au-delà de la limite de la ligne de commande | Moyenne | Budget de 120 Kio encodés en base64, vérifié ; tableaux de bord téléchargés au besoin (§ 4.3) |
| Configuration dynamique Traefik hors dépôt : oubliée lors d'un redéploiement sur un autre serveur | Moyenne | `security.py` échoue tant que les middlewares `@file` sont absents ; étape 3 du README |
| Fuite de middlewares Traefik (Coolify #9886) | Moyenne | Test § 12.4.7 après chaque mise à jour de Coolify |
| API d'alerting Grafana dépréciée | Moyenne | Appels isolés, bascule localisée |
| Explosion de cardinalité via `tenant` | Moyenne | Garde-fous § 8.2 |
| `tenant` des traces Faro déclaré par le client | Faible | Validation (§ 6.5) ; le span serveur fait foi |
| Limite de débit partagée derrière un NAT d'agence | Faible | Défauts généreux, documentés |
| Composants Alloy en public preview (`otelcol.storage.file`) | Faible | `stability.level` limité à `public-preview`, version épinglée |
| Clé Faro publique | Faible | Écriture seule, limites de débit et de taille |
| Changement de clé de rétention Tempo en 3.x | Faible | Versions épinglées ; vérification avant toute montée de version |

**Risque reporté au sous-projet 2 (instrumentation) :**
- Le SDK Faro pour React Native est **expérimental** et exige le workflow natif (pas Expo Go).
- Electron n'est pas une cible officielle de Faro.
- `tenant-app` et `immo-desktop` commenceront donc par un **spike** avant tout engagement. C'est aussi
  ce qui pèse le plus sur le retrait de Sentry (décision D4).

---

## 16. Points ouverts

| # | Point | Recommandation |
|---|---|---|
| O1 | Domaines exacts de central-front, de guest-front en préprod, et de l'API centrale en préprod (`central-dev-api` ou `admin-api.staging` ?) | À fournir au déploiement : ce sont des valeurs de `HOST_MAP`, pas du code |
| O2 | Titulaire de la licence MIT | « SoftArtisan », comme `sentry-coolify` |
| O3 | Version de Coolify du serveur (pour la limite de la ligne de commande et le bug #9886) | À relever au spike du plan A |
| O4 | Publication du dépôt sur GitHub | Seulement après validation de la spec et du plan, sur accord explicite |

### Spikes du plan A (à faire en premier, sur une vraie instance Coolify)

1. Résolution des noms internes depuis Grafana (§ 3.3).
2. Portée réelle de `coolify.traefik.middlewares` sur deux services, et résolution des références
   `@file` de la configuration dynamique (§ 5.4).
3. Attributs présents sur un span Faro reçu (§ 6.5).
4. Version de Coolify ; fichiers `content:` écrits octet pour octet sur l'hôte dans une ressource
   Application Git, puis une config modifiée, poussée et redéployée (nouveau fichier à empreinte
   utilisé, sort des stockages périmés) ; affichage des `${…}` dans l'interface (§ 4.2).
