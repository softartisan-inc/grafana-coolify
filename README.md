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
| `grafana-setup` | Ponctuel : crée dans Grafana les sources de données, les dossiers, les tableaux de bord et les alertes | — |

Grafana n'est **pas** dans le package : c'est le service Coolify « Grafana » (variante
PostgreSQL recommandée), inchangé. Toutes les images sont officielles et épinglées
(`tools/versions.env`). La conception complète est dans
[`docs/superpowers/specs/2026-09-27-grafana-coolify-design.md`](docs/superpowers/specs/2026-09-27-grafana-coolify-design.md).

## Documentation

Le guide de l'opérateur, en pages courtes, est dans [`docs/README.md`](docs/README.md) :
installation pas à pas, ajout d'un projet, alertes, rotation des secrets, durcissement du
serveur, et un [index de dépannage](docs/depannage/README.md) qui part du message d'erreur exact
(tous les incidents du premier déploiement réel, avec leur correctif).

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
| `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL` | Alias stables sur le réseau `coolify` : `http://gc-loki:3100`, `http://gc-tempo:3200`, `http://gc-prometheus:9090` (étape 4). |
| `GRAFANA_URL`, `GRAFANA_SA_TOKEN` | Adresse **interne** de Grafana, `http://grafana-<uuid>:3000` (étape 4), et jeton du compte de service (étape 5). |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERT_EMAILS` | Notifications (voir « Tableaux de bord et alertes ») : bot Telegram pour `critical` + `prod`, email pour le reste. |
| `ALERT_ERROR_RATE`, `ALERT_P95_MS`, `ALERT_SILENCE_MIN`, `ALERT_DISK_PCT`, `CARDINALITY_ALERT_THRESHOLD` | Seuils des alertes ; défauts de `.env.example`. |
| `HOST_ENV` | `prod` (défaut) ou `preprod` : environnement que sert l'hôte, label `env` des alertes d'hôte (« Disque »). Vérifié par `config-guard`. |
| `GRAFANA_SETUP_MIRROR_URL` | Facultatif : source de remplacement des tableaux et alertes de `grafana-setup` (fork, miroir). |

- **`TENANT_HOST_REGEX` contient des `$`** : cocher **« Is Literal? »** sur cette variable, sinon
  Coolify tente de l'interpréter.
- Les domaines publics `SERVICE_FQDN_ALLOY_12347` (Faro) et `SERVICE_FQDN_ALLOY_GATEWAY_4318`
  (OTLP) sont générés par Coolify : renseigner le domaine de chaque service dans l'onglet de la
  ressource, **sans port** (`https://faro.example.com`, `https://otlp.example.com`), et mettre
  dans le champ **Internal port** de ce domaine le port du conteneur : `12347` pour `alloy`,
  `4318` pour `alloy-gateway` (vérifié sur Coolify v4.3.23). **Save** en confirmant
  l'avertissement sur le port s'il s'affiche (sinon Coolify restaure l'ancienne valeur sans le
  dire), puis **Redeploy** de la ressource entière (un Restart ne régénère pas les
  labels Traefik). Le port routé par défaut est le premier de `expose` (12347 et 4318 dans ce
  compose), mais un **Internal port** déjà enregistré l'emporte. Versions plus anciennes de
  Coolify (sans champ **Internal port**) : saisir le port dans l'URL,
  `https://faro.example.com:12347` et `https://otlp.example.com:4318`. Symptôme d'un mauvais
  port (`POST /collect` → `500 Internal Server Error`) et vérification :
  [dépannage](docs/depannage/deploiement-coolify.md#faro--500-internal-server-error-sur-collect).
  **Ne pas ajouter soi-même** de variable `SERVICE_FQDN_*` dans **Environment Variables** :
  Coolify les crée quand un domaine est renseigné (et n'en crée aucune pour un service sans
  domaine, ce qui garde le point Faro fermé).
- **Point Faro fermé** : `alloy` écoute toujours Faro, la clé reste donc obligatoire. Pour ne
  pas exposer Faro, ne donner **aucun domaine** au service `alloy` dans Coolify : sans domaine,
  Traefik n'a aucun routeur vers le port 12347.
- **Domaines générés d'office (serveur unique avec domaine wildcard)** : au premier déploiement,
  Coolify **remplit lui-même** le champ **Domains** de `alloy` et de `alloy-gateway`
  (`http://alloy-<uuid>.<wildcard>`, `http://alloy-gateway-<uuid>.<wildcard>`), ce qui ouvre
  Faro et OTLP sur Internet sans que vous l'ayez demandé. Après le premier déploiement, ouvrir
  chacun de ces deux services dans la ressource, **vider le champ Domains** de `alloy-gateway`
  (tant qu'aucun serveur distant n'envoie d'OTLP) et de `alloy` (tant qu'aucune application
  n'est instrumentée avec Faro), enregistrer et redéployer. Pour ouvrir Faro plus tard, saisir
  `https://faro.example.com` avec **Internal port** `12347` (OTLP : `https://otlp.example.com`,
  port `4318`) ; ce port désigne le conteneur, le public reste sur 443. Sur une version de
  Coolify sans ce champ, le port s'écrit dans l'URL (`https://faro.example.com:12347`).
- `config-guard` refuse de démarrer la stack si `IP_HASH_SALT`, `FARO_API_KEY`, `PROJECTS`,
  `FARO_SERVICES`, `HOST_MAP`, `RESERVED_SUBDOMAINS` ou `TENANT_HOST_REGEX` ont un format invalide :
  le message d'erreur est dans les logs de `config-guard`.
- `GRAFANA_URL`, `GRAFANA_SA_TOKEN`, `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL` et
  `PROMETHEUS_INTERNAL_URL` ne servent qu'à `grafana-setup`, qui les vérifie lui-même (URL
  `http://` ou `https://`, jeton non vide) : vides ou invalides, seul `grafana-setup` s'arrête
  (code 1, message clair dans ses logs) ; Loki, Tempo, Prometheus et Alloy démarrent quand même.
- Aucun de ces contrôles n'est confié à la syntaxe `${VAR:?message}` de Compose : Coolify ne
  refuse pas le déploiement, il donne à la variable le texte du message pour valeur
  (`GRAFANA_URL=GRAFANA_URL is required`).
- `GF_SMTP_*` (email des alertes) **ne se renseignent pas ici** : ces variables vont sur le
  **service Grafana** (voir « Prérequis : SMTP de Grafana »).

> **Logs de déploiement Coolify = secrets en clair.** Les logs de déploiement contiennent une
> ligne `[CMD] … base64 …` : c'est le fichier `.env` **complet** de la ressource, encodé en base64
> (donc lisible par quiconque), avec `GRAFANA_SA_TOKEN`, `FARO_API_KEY`, `IP_HASH_SALT`,
> `TELEGRAM_BOT_TOKEN`… Ne **jamais** copier ces logs dans un ticket, une discussion ou un
> assistant. Si c'est arrivé : faire tourner tous les secrets concernés (nouveau jeton Grafana,
> nouvelle clé Faro, nouveau token Telegram via @BotFather, nouveau sel) puis redéployer.

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
   **entre guillemets simples** :

   ```yaml
   accessControlAllowOriginListRegex:
     - '^https://([a-z0-9-]+\.)?example\.(me|app)$'
   ```

   Entre guillemets doubles, chaque barre oblique inverse doit être doublée (`\\.`) : un `\.`
   seul y est une séquence d'échappement YAML invalide et Traefik rejette tout le fichier. Les
   guillemets simples gardent la regex telle quelle.

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

Coolify suffixe le nom de chaque conteneur à **chaque déploiement** (`loki-<uuid>-<horodatage>`) :
ce nom change à chaque redéploiement, et `loki-<uuid>` ne résout **pas** sur le réseau `coolify`.
Le compose donne donc lui-même à `loki`, `tempo`, `prometheus` et `alloy` un **alias stable et
unique** sur ce réseau : `gc-loki`, `gc-tempo`, `gc-prometheus`, `gc-alloy`.

Le réseau externe s'appelle `coolify` : c'est le réseau de la destination par défaut de Coolify.
Si la destination de la ressource porte un autre nom, adapter `networks.coolify.name` dans
`compose.template.yaml`, puis régénérer `docker-compose.yaml` (`python3 scripts/render.py`).

1. **Ce package** : laisser **Connect To Predefined Network** **désactivé** sur la ressource. Le
   compose déclare le réseau externe `coolify` et y rattache les quatre services avec leur alias,
   ainsi que `grafana-setup`, sans alias, pour joindre `GRAFANA_URL` ; option désactivée, Coolify
   n'y met aucun autre service. Option activée, Coolify remplace l'entrée `coolify` de chaque
   service par la sienne, sans alias : les noms `gc-*` ne résolvent plus (voir `docs/spikes.md`,
   S1).
2. **Le service Coolify « Grafana »** : il n'est que sur son propre réseau. Dans son onglet,
   activer **Connect To Predefined Network**, puis le redémarrer : sans cela, il ne joint ni Loki,
   ni Tempo, ni Prometheus.
3. Renseigner dans ce package :

   ```
   LOKI_INTERNAL_URL=http://gc-loki:3100
   TEMPO_INTERNAL_URL=http://gc-tempo:3200
   PROMETHEUS_INTERNAL_URL=http://gc-prometheus:9090
   ```

   et documenter pour les applications du même hôte `ALLOY_INTERNAL_URL=http://gc-alloy:4318`.
   Tant que ces trois URL sont vides, seul `grafana-setup` échoue
   (`LOKI_INTERNAL_URL is required`) : les autres services tournent.
4. `GRAFANA_URL` est l'adresse **interne** du conteneur Grafana, `http://grafana-<uuid>:3000` :
   l'URL publique échoue (`Grafana not healthy after 120s`), car un conteneur du serveur ne
   rejoint pas sa propre adresse publique à travers le proxy. Relever le nom sur le serveur :

   ```bash
   docker ps --format '{{.Names}}' | grep -E '^grafana-[a-z0-9]+$'
   ```

   Le service Grafana en un clic est une ressource **Service** de Coolify : son conteneur s'appelle
   `grafana-<uuid>`, sans horodatage, et garde ce nom d'un déploiement à l'autre.

> **Repli : noms nus.** Les noms de service nus (`http://loki:3100`, `http://tempo:3200`,
> `http://prometheus:9090`, `http://alloy:4318`) résolvent aussi sur le réseau `coolify`, mais
> toute autre ressource du serveur qui a un service du même nom (un autre `loki`, un autre
> `prometheus`…) entre en collision : le nom désigne alors plusieurs conteneurs, et Docker répond
> par l'un ou l'autre. Ne s'en servir qu'en dépannage. Les alias `gc-*` n'évitent pas la collision
> entre **deux déploiements de ce package** sur le même serveur : ils partageraient aussi
> `gc-loki`.

> **Alternative écartée : « Consistent Container Names ».** Cette option de Coolify donne aux
> conteneurs un nom stable, `loki-<uuid>`. Les alias lui sont préférés : courts, indépendants de
> l'UUID de la ressource, ils restent identiques d'un serveur à l'autre et après une recréation de
> la ressource, sans modifier les variables.

**Migration d'un déploiement existant** (option activée sur le package, noms nus ou
`loki-<uuid>`) :

1. Noter les valeurs actuelles de `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`,
   `PROMETHEUS_INTERNAL_URL` et `GRAFANA_URL` : ce sont les valeurs de retour arrière.
2. Vérifier que le service Coolify « Grafana » est sur le réseau `coolify` :

   ```bash
   GRAFANA=$(docker ps --format '{{.Names}}' | grep -E '^grafana-[a-z0-9]+$')
   docker inspect "$GRAFANA" --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}'
   ```

3. Mettre à jour le dépôt, puis **désactiver** **Connect To Predefined Network** sur ce package,
   sans toucher aux variables.

   > **Ne jamais désactiver l'option tant que le compose déployé ne déclare pas lui-même le réseau
   > `coolify`** : avec l'ancien compose et l'option désactivée, aucun service n'est sur `coolify`,
   > les sources Grafana et `grafana-setup` tombent.
4. Redéployer, puis vérifier que les alias de `loki` contiennent `gc-loki` et `loki`, et que les
   deux noms répondent depuis Grafana :

   ```bash
   docker inspect "$(docker ps --format '{{.Names}}' | grep -E '^loki-')" \
     --format '{{json .NetworkSettings.Networks.coolify.Aliases}}'
   docker exec "$GRAFANA" wget -qO- http://gc-loki:3100/ready
   docker exec "$GRAFANA" wget -qO- http://loki:3100/ready
   ```

5. Passer les trois `*_INTERNAL_URL` aux valeurs `gc-*` ci-dessus (et `GRAFANA_URL` à l'adresse
   interne), redéployer, puis tester les sources de données dans Grafana (**Connections** →
   **Data sources** → **Save & test**).
6. Passer `ALLOY_INTERNAL_URL` des applications à `http://gc-alloy:4318`, une application à la
   fois, en vérifiant l'arrivée de ses données avant de passer à la suivante.

Retour arrière : remettre les valeurs notées à l'étape 1 et redéployer. Les noms nus résolvent
dans les deux états de l'option, car Compose ajoute toujours le nom du service aux alias du
conteneur.

**Hypothèse de sécurité** : tout conteneur du réseau `coolify` est de confiance (Loki, Tempo,
Prometheus et les ports internes d'Alloy n'ont pas d'authentification). Aucun service interne ne
publie de port (`scripts/check.py` le vérifie).

### 5. Compte de service Grafana

Dans Grafana : **Administration** → **Users and access** → **Service accounts** → créer
`grafana-coolify` avec le rôle **Admin**, puis **Add service account token** (expiration
conseillée : **1 an**). Copier le jeton dans `GRAFANA_SA_TOKEN`. Le jeton ne sert qu'au
déploiement (`grafana-setup`) : une expiration courte n'apporte guère de sécurité mais fait
échouer un redéploiement à l'improviste. **Créer un rappel d'agenda** un mois avant l'échéance
pour la rotation.

Rotation : créer un nouveau jeton, remplacer `GRAFANA_SA_TOKEN`, redéployer, puis supprimer
l'ancien jeton. Le jeton n'est jamais écrit dans les logs de `grafana-setup`.

### 6. Déployer

Ordre de démarrage : `config-guard` (vérifie chaque fichier de config par empreinte SHA-256,
puis les variables), puis les services, puis `grafana-setup`.

Coolify affiche ensuite **deux conteneurs « exited »** : `config-guard` et `grafana-setup`.
C'est normal : ce sont des tâches ponctuelles (`restart: "no"`). Un `config-guard` en erreur
signale une régression de Coolify sur les fichiers `content:` : aucun service ne démarre.

`grafana-setup` est relançable sans effet de bord : il crée les sources Loki, Tempo et Prometheus
(UID fixes `gc-loki`, `gc-tempo`, `gc-prometheus`, corrélations log ↔ trace ↔ métriques), les
dossiers `gc-<projet>`, puis les tableaux de bord et les alertes (voir « Tableaux de bord et
alertes »). Ces derniers sont téléchargés au tag épinglé et vérifiés par empreinte SHA-256 : pour
eux seuls, le conteneur doit pouvoir joindre `raw.githubusercontent.com` (ou le miroir). Point de
contrôle du déploiement : la dernière ligne des logs de `grafana-setup` est
`grafana-setup: done`. `grafana-setup: datasources and folders: done` suivi d'une erreur signifie
que les sources et les dossiers sont en place, mais pas les tableaux ni les alertes.

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
- **Contrôle 5** (limite de débit) : envoie une rafale d'environ **600 requêtes** POST au point
  Faro de **production**. Pendant quelques secondes, les vrais clients derrière la même IP que le
  poste de l'opérateur (même agence, même NAT) reçoivent eux aussi des 429 : le lancer hors des
  heures d'usage. **Point Faro fermé** (aucun domaine pour `alloy`) : les contrôles 3 à 6 n'ont
  pas de cible, lancer `security.py --remote --only 1,2,7,8`.
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
`GC_ALLOY_METRICS_URL` pointant vers les alias `gc-*` (`http://gc-loki:3100`, `http://gc-alloy:4318`…), `GC_GATEWAY_URL`, `GC_GATEWAY_HOST`,
`GC_GATEWAY_USER`, `GC_GATEWAY_PASSWORD` pour la passerelle OTLP (leurs valeurs par défaut
viennent de `.harness/edge.json`, absent sur un serveur), et, exportées elles aussi, les valeurs
du déploiement pour `IP_HASH_SALT` (empreintes des IP), `FARO_API_KEY`, `PROJECTS` et
`FARO_SERVICES` (projet et service des envois Faro), ainsi que les mêmes valeurs de `HOST_MAP`,
`RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` que le banc (`harness/harness.env`) : à défaut,
`smoke.py` prend celles du banc et échoue. Uniquement sur un **déploiement de recette**.

Rejouer `security.py --remote` (contrôle 7, fuite de middlewares, bug Coolify #9886) **après
chaque mise à jour de Coolify**.

Enfin, vérifier les notifications avec `scripts/notify_test.py` (voir « Tester les
notifications »).

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

Le rattrapage tient souvent dans **un seul flux** (le service qui a écrit pendant la panne), or
Loki limite aussi chaque flux (`project`, `env`, `service_name`), à 3 Mio/s (rafale 15 Mio) par
défaut. Hypothèse de taille : **1 à 4 Kio par enregistrement de log**, soit des lots de 2 à 8 Mio
(2048 enregistrements) et une file pleine de 2 à 8 Gio. À 3 Mio/s, 8 Gio mettraient ~45 min à
passer, trop près de la fenêtre d'une heure. `config/loki/loki.yaml` fixe donc
`per_stream_rate_limit: 8MB` et `per_stream_rate_limit_burst: 24MB` : la **moitié** du débit du
tenant, soit ~4 min pour 2 Gio et ~17 min pour 8 Gio sur un seul flux, sans qu'un flux (rattrapage,
flux Faro bruyant) puisse prendre tout le budget des autres. Loki lit les tailles en unités
binaires (`24MB` = 24 Mio ; les `*_mb` aussi) : la rafale contient trois des plus gros lots
(3 × 8 Mio à 4 Kio par enregistrement ; un envoi plus gros que la rafale ne passerait jamais), les
10 envois concurrents à 1 Kio par enregistrement (~20 Mio), et reste sous celle du tenant
(32 Mio). Au-delà de ces hypothèses (enregistrements plus gros, plus de 8 Gio en attente sur un
seul flux), des lots peuvent encore être abandonnés : raccourcir la panne ou relever ces
plafonds ensemble.

## Tableaux de bord et alertes

`grafana-setup` crée, à chaque déploiement et sans effet de bord :

- le dossier **`grafana-coolify`** (UID `gc-grafana-coolify`), qui contient les six tableaux de
  bord et les règles d'alerte ;
- les six tableaux de bord (liste fermée) : **Vue projet** (`gc-project` : débit, taux d'erreur
  et p95 par service, filtres `project`, `env`, `service`, `tenant`), **Erreurs et latence par
  service** (`gc-service` : détail par route, logs d'erreur liés à leur trace), **Frontend**
  (`gc-frontend` : erreurs JS, Web Vitals, sessions Faro), **Hôte** (`gc-host` : CPU, mémoire,
  disque, réseau), **Santé du pipeline** (`gc-pipeline` : ingestion, rejets, file d'envoi, santé
  de Loki, Tempo et Prometheus) et **Cardinalité** (`gc-cardinality` : séries par projet, service
  et tenant) ;
- dans chaque dossier de projet `gc-<projet>`, un tableau **`grafana-coolify — <projet>`** dont
  les liens ouvrent ces tableaux avec `var-project` déjà réglé ;
- les points de contact **`gc-telegram`** et **`gc-email`**, la politique de notification et les
  six règles d'alerte.

La variable « service » des tableaux vise le même nom partout ; seul le label change selon la
source : `service` pour les span-metrics, `job` pour les métriques OTLP, `service_name` pour Loki.

Les tableaux, les points de contact, la politique et les règles restent **modifiables dans
Grafana** (`X-Disable-Provenance: true`), mais `grafana-setup` remet leur contenu à l'identique au
déploiement suivant dès qu'il diffère. Pour garder une modification : la copier dans un autre
tableau (**Save as**), ou l'apporter au dépôt (voir « Modifier un tableau de bord ou une
alerte »). Seule exception : les routes ajoutées à la main dans la politique de notification sont
conservées, après la route Telegram, et les réglages ajoutés à la main à la route Telegram
elle-même (`continue`, `group_wait`, `mute_time_intervals`…) aussi : seuls son récepteur et ses
matchers sont gérés. `grafana-setup` reconnaît sa route Telegram à son seul récepteur
`gc-telegram` : une route ajoutée à la main doit viser **son propre point de contact**, jamais
`gc-telegram`, sinon elle est remplacée au déploiement suivant.

### Alertes

| Règle | Condition | `severity` | Variable |
|---|---|---|---|
| Taux d'erreur | part des requêtes (spans serveur) en erreur sur 5 min, par service, au-dessus du seuil pendant 2 min | `critical` | `ALERT_ERROR_RATE` (0.05 = 5 %) |
| Latence p95 | p95 sur 10 min au-dessus du seuil pendant 2 min, par service | `warning` | `ALERT_P95_MS` |
| Service muet | service qui a émis des spans dans la dernière heure (4 × le délai au-delà de 15 min), mais aucun depuis le délai | `critical` | `ALERT_SILENCE_MIN` |
| Disque | usage d'un système de fichiers de l'hôte au-dessus du seuil pendant 2 min | `critical` (`env` = `HOST_ENV`) | `ALERT_DISK_PCT`, `HOST_ENV` |
| Cardinalité | séries actives de Prometheus au-dessus du seuil pendant 2 min | `warning` | `CARDINALITY_ALERT_THRESHOLD` |
| Rejets | au moins une donnée rejetée (§ 6.3 de la spec) sur 15 min | `warning` | — |

**Routage** : `severity=critical` **et** `env=prod` → Telegram ; tout le reste → email. Le disque
porte `env` = `HOST_ENV` (`prod` par défaut : l'hôte sert la production ; `preprod` sur un serveur
de recette, dont le disque part alors par email). Une règle dont la requête échoue passe en état
**Error** (notifiée) ; l'absence de données n'est pas une alerte.

- `ALERT_EMAILS` vide et Telegram vide : les règles sont créées et évaluées, mais **ne
  notifient personne** : la politique par défaut de Grafana envoie tout au récepteur intégré
  `empty`, sans intégration (`notifications: skipped (ALERT_EMAILS is empty: the rules notify
  nobody)` dans les logs). Si un déploiement précédent avait configuré `gc-email`, la politique
  n'est plus touchée et continue de l'utiliser.
- Telegram exige `ALERT_EMAILS` : tout ce qui n'est pas `critical` + `prod` part par email.
- Retirer le token Telegram retire la route Telegram de la politique ; le point de contact
  `gc-telegram` reste dans Grafana, inutilisé, et peut être supprimé à la main.
- **API d'alerting** : `grafana-setup` utilise l'API legacy `/api/v1/provisioning/*`, dépréciée
  mais servie par Grafana 12 et 13 (vérifiée sur Grafana 13.2.2). Si une version future la
  retire, `grafana-setup` s'arrête avec « legacy alerting provisioning API unavailable » : les
  sources et les tableaux sont déjà en place, seules les alertes manquent. Le test
  `test_legacy_provisioning_api_is_served` du banc échoue avant toute montée de version de
  Grafana qui la retirerait (`GRAFANA_VERSION` de `tools/versions.env`).

### Avant la production : spike S6 (bloquant)

Le banc prouve le routage et la délivrance vers un faux SMTP et un faux proxy, pas l'arrivée d'un
vrai message. Le spike **S6** de [`docs/spikes.md`](docs/spikes.md) (vrai bot Telegram, vrai SMTP,
`scripts/notify_test.py` sur la recette) est un **prérequis bloquant** de la mise en production :
tant qu'il n'est pas OK, aucune alerte n'est réputée arriver.

### Prérequis : bot Telegram

1. Dans Telegram, écrire à **@BotFather** : `/newbot`, choisir un nom, puis copier le token
   (`123456789:AA…`) dans `TELEGRAM_BOT_TOKEN`.
2. Créer le groupe des alertes (administratrice et équipe) et y **ajouter le bot**.
3. Écrire un message dans le groupe, puis lire son identifiant (`read -rs` demande le token sans
   l'afficher ni l'écrire dans l'historique du shell) :

   ```bash
   read -rs TELEGRAM_BOT_TOKEN
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | python3 -m json.tool | grep -A3 '"chat"'
   ```

   L'identifiant d'un groupe est négatif (`-100…` pour un supergroupe) : le copier dans
   `TELEGRAM_CHAT_ID`.
4. Redéployer, puis lancer `scripts/notify_test.py` (plus bas).

Le token n'est jamais écrit dans les logs de `grafana-setup`. Rotation : `/revoke` auprès de
@BotFather, nouveau token dans `TELEGRAM_BOT_TOKEN`, redéployer.

### Prérequis : SMTP de Grafana

L'email passe par le SMTP **du service Coolify Grafana** (hors package). Dans ses
**Environment Variables** :

| Variable | Exemple |
|---|---|
| `GF_SMTP_ENABLED` | `true` |
| `GF_SMTP_HOST` | `smtp.example.com:587` |
| `GF_SMTP_USER`, `GF_SMTP_PASSWORD` | identifiants du compte d'envoi |
| `GF_SMTP_FROM_ADDRESS`, `GF_SMTP_FROM_NAME` | `grafana@example.com`, `Grafana` |

Redémarrer Grafana, puis renseigner `ALERT_EMAILS` (adresses séparées par des virgules) dans ce
package et redéployer.

### Tester les notifications

```bash
GRAFANA_URL=https://grafana.example.com GRAFANA_SA_TOKEN=glsa_... python3 scripts/notify_test.py
```

Depuis un poste, `GRAFANA_URL` est l'URL **publique** de Grafana (dans le package, c'est
l'adresse interne : étape 4).

Le script demande à Grafana d'envoyer une notification de test par `gc-telegram` et par
`gc-email`, avec les réglages enregistrés (le token du bot ne quitte pas Grafana). Attendu :
`notify-test: gc-telegram: sent` et `notify-test: gc-email: sent`, puis le message dans le groupe
Telegram et dans les boîtes des destinataires. Grafana 13 a retiré l'ancien point de test
(`receivers/test`, HTTP 410) : le script utilise l'API `notifications.alerting.grafana.app`
(v1beta1). Faute de script, le bouton **Test** de chaque point de contact (**Alerting** →
**Contact points**) fait la même chose.

### Source des tableaux et des alertes

Le compose contient `config/grafana-setup/setup.py` (sources de données et dossiers), mais pas les
tableaux ni le code des alertes : ils dépasseraient le budget de 120 Kio encodé en base64. Une fois
les sources et les dossiers en place, `setup.py` télécharge `dashboards.py`, `alerting.py` et
`dashboards/*.json` depuis **ce dépôt, au tag épinglé** (`GRAFANA_SETUP_TAG` de
`tools/versions.env`, par exemple `grafana-setup-content-v1`), sur `raw.githubusercontent.com`,
vérifie l'empreinte SHA-256 de chacun (liste `GRAFANA_SETUP_FILES` du compose, 2 Mio au plus par
fichier) et ne les exécute que si toutes correspondent.

Le compose ne contient pas non plus les commentaires : `render.py` retire des fichiers YAML et
Alloy qu'il y insère les lignes de commentaire et les lignes vides, et des scripts shell (`.sh`)
et Python (`.py`) les seules lignes de commentaire : le shebang (`#!`) et les lignes vides restent.
Pour Python, les commentaires sont repérés avec `tokenize`, si bien qu'une ligne commençant par
`#` dans une chaîne reste ; pour le shell, `check.py` refuse un heredoc ou une chaîne entre
guillemets sur plusieurs lignes dans un script inséré. Les empreintes (noms de fichiers,
`CONFIG_GUARD_EXPECTED`, empreinte de `guard.sh` dans la commande de `config-guard`) sont calculées
après ce retrait. Les commentaires restent dans `config/`, mais n'apparaissent ni dans le compose
ni dans les fichiers que Coolify écrit sur le serveur ; `compose.dev.yaml` monte les fichiers du
dépôt tels quels.

- Le conteneur `grafana-setup` doit pouvoir joindre `https://raw.githubusercontent.com`, pour les
  tableaux et les alertes seulement : sans accès, les sources et les dossiers sont quand même
  créés, puis `grafana-setup` s'arrête en erreur avec un message explicite.
- Un **fork**, ou un serveur sans accès à GitHub, renseigne `GRAFANA_SETUP_MIRROR_URL` : toute
  URL `https://`, `http://` ou `file:` qui sert les mêmes fichiers sous le même chemin
  (`<url>/config/grafana-setup/alerting.py`). Les empreintes restent vérifiées : un miroir ne peut
  pas changer le code exécuté.

### Modifier un tableau de bord ou une alerte

1. Tableaux : modifier `scripts/build_dashboards.py`, puis `python3 scripts/build_dashboards.py`
   (les JSON de `config/grafana-setup/dashboards/` sont générés : ne jamais les modifier à la
   main). Alertes : modifier `config/grafana-setup/alerting.py`.
2. **Monter le suffixe du tag** dans `tools/versions.env` (`GRAFANA_SETUP_TAG=grafana-setup-content-v2`),
   puis `python3 scripts/render.py` (nouvelles empreintes et nouvelle URL dans le compose). Le
   suffixe monte à **chaque** modification d'un fichier téléchargé (`dashboards.py`,
   `alerting.py`, `dashboards/*.json`) ; une modification de `setup.py` seul, inséré dans le
   compose, n'en demande pas. Tant que le nouveau tag n'est pas poussé, `grafana-setup` échoue
   (`HTTP 404` au téléchargement) ; un fichier modifié sans monter le suffixe échoue à la
   vérification SHA-256 (`SHA-256 … differs from the pinned …: nothing was run`). Dans les deux
   cas, les sources et les dossiers sont en place, pas les tableaux ni les alertes.
3. Committer, puis créer le tag sur le **dernier commit** de la branche et le pousser avec elle :

   ```bash
   git tag -a grafana-setup-content-v2 -m "grafana-setup content v2"
   python3 scripts/check.py --only bundle
   git push origin <branche> grafana-setup-content-v2
   ```

   Le contrôle `bundle` vérifie que le tag existe et contient exactement les fichiers de travail
   (dans un clone superficiel sans le tag, il est sauté avec un message). Tant qu'un tag n'est pas
   poussé, il peut être déplacé (`git tag -f -a …`) ; une fois poussé, **jamais** : monter le
   suffixe.
4. Ouvrir la PR ; n'importe quelle méthode de fusion convient (le tag garde le commit accessible,
   même après un « squash »). Après la fusion, **Redeploy**.

Alertes : une route ajoutée à la main dans la politique de notification doit viser son propre
point de contact, pas `gc-telegram` (voir plus haut).

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
Un tenant client hors de `[a-z0-9-]+` ou réservé est retiré. Un client hors navigateur (desktop :
`page_url` en `app://<service>/…`) n'a pas d'hôte reconnu : son `env` et son `tenant` sont pris du
client et validés ([client desktop](docs/ajouter-un-projet.md#un-client-desktop-hors-navigateur)).
Pour les logs Faro, `env` et `tenant` sont **déduits de l'hôte de la page** :

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

- **Cartes et tableaux aplatis pour le masquage** : sur le chemin OTLP, les règles ci-dessous
  s'appliquent à une **copie aplatie** (fonction OTTL stable `flatten`) de chaque carte
  d'attributs (ressource, portée d'instrumentation `scope.attributes`, span, événement, log, point
  de métrique) : les cartes imbriquées et les tableaux y deviennent des clés pointées
  (`{"user": {"email": …}}` → `user.email`, `"tags": ["a", "b"]` → `tags.0`, `tags.1`), que les
  règles atteignent à tous les niveaux. La copie ne remplace l'original **que si une règle l'a
  modifiée** : un enregistrement sans rien de sensible garde sa forme d'origine. Le corps de log
  structuré, lui, est toujours aplati. Les valeurs numériques gardent leur type. Un corps de log
  en tableau devient son texte JSON, puis suit les règles du texte libre.
- **Clés sensibles supprimées** : sur le chemin OTLP, tout attribut (ressource, portée, span,
  événement, log, point de métrique) dont la clé est sensible est **supprimé**, quelle que soit
  sa valeur (pas de marqueur `[redacted]`) ; dans les logs Faro, la paire `clé=valeur` entière
  disparaît. Une clé est sensible quand l'un des mots `authorization`, `cookie(s)`,
  `password(s)`, `passwd`, `token`, `secret(s)`, `api_key` / `api-key` / `apikey` (casse
  indifférente) la **termine** ou est suivi de `.`, `_`, `-` ou d'une majuscule :
  `http.request.header.authorization`, `access_token`, `client_secret`, `x-api-token`,
  `accessToken` sont supprimées ; `gen_ai.usage.input_tokens`, `tokenizer`, `secretary` sont
  gardées. Effet de bord accepté : `token_type` ou `gen_ai.token.type` sont supprimées aussi.
- **Secrets dans le texte libre** remplacés par `[redacted]` : jetons `Bearer`, identifiants
  `Basic` / `Digest`, valeurs `access_token=…`, `client_secret=…`, `api_key=…` et JSON
  `"refresh_token":"…"` (même règle de clé sensible).
- **Emails** remplacés par `[email]`.
- **Numéros de carte** remplacés par `[card]`, **seulement** dans le texte libre (corps,
  `message`, `exception.*`, `error.message`) : tout nombre de 13 à 19 chiffres y est pris pour une carte, un
  horodatage en millisecondes dans un message compris.
- **Adresses IP** remplacées par `sha256(IP_HASH_SALT + ip)`, identique sur les chemins OTLP et
  Faro.

Pourquoi aplatir : `replace_all_patterns` ne réécrit que les chaînes, et parcourir un tableau
élément par élément exige les lambdas OTTL, **alpha** (porte `ottl.functions.enableLambda`) et
exclues par `stability.level = "public-preview"`. `flatten` est la seule fonction stable qui
descend dans les cartes et les tableaux ; le masquage se fait donc élément par élément plutôt que
par suppression de l'attribut entier.

> **Risque résiduel connu** (masquage) :
> - **Forme des données, seulement sur les enregistrements masqués** : quand une règle modifie une
>   carte d'attributs, toute la carte est réécrite aplatie ; un attribut tableau ou carte y change
>   alors de forme dans Tempo, Loki et Prometheus. Exemple TraceQL : un span propre garde
>   `{ span.http.request.header.accept = "text/html" }` (tableau inchangé), mais si un autre
>   attribut du même span contient un email, l'en-tête devient
>   `{ span.http.request.header.accept.0 = "text/html" }`. Idem pour `process.command_args`
>   (ressource posée par les SDK) : intact tant que la ressource ne contient rien de sensible,
>   sinon `process.command_args.0`, `.1`… dans Tempo et dans `target_info` de Prometheus
>   (`process_command_args_0`…). Une requête qui doit tout voir interroge les deux formes.
> - **Corps de log structurés** : toujours aplatis (`{"user": {"email": …}}` est stocké
>   `{"user.email": "[email]"}`). Dans Loki, `| json` sans argument donne le même nom qu'avant
>   (`user_email`) ; les tableaux sont désormais indexés (`tags_0`). Mais une expression de chemin
>   ne trouve plus la valeur : `| json role="user.role"` lit `user` puis `role`, absents, et
>   renvoie une chaîne vide ; désigner la clé littérale entre crochets,
>   ``| json role=`["user.role"]` `` (ou `| json role="[\"user.role\"]"`), vérifié sur le banc.
> - **Collision** : une clé pointée littérale égale à un chemin imbriqué (`"a.b"` et
>   `{"a": {"b": …}}` dans la même carte) ne garde qu'une des deux valeurs, masquée dans les deux cas.
> - **Cartes bancaires** : le motif ne s'applique qu'aux clés de texte libre (`message`,
>   `exception.message`, `exception.stacktrace`, `error.message`), à ce nom pointé exact ;
>   `details.reason` imbriqué n'est pas examiné pour `[card]` (les emails, secrets et IP, eux, le
>   sont).
> - **Exemptions d'IP** : une clé imbriquée hérite du nom de son parent ; tout ce qui se trouve
>   sous une clé contenant `version` ou `user_agent` n'est pas haché.
> - **Coût** : chaque carte d'attributs est copiée, aplatie, masquée puis comparée, sur chaque
>   span, log et point. Mesuré sur le banc (60 000 logs et spans, 1 sur 10 avec une donnée
>   sensible) : ~11,5 s de CPU pour `alloy` avant ce masquage, ~17 s après (+48 %), soit
>   ~0,1 ms de CPU de plus par élément ; prévoir environ 0,1 cœur de plus par 1000 éléments/s.
> - **Octets** : les valeurs binaires (`bytesValue`) ne sont pas examinées.

Logs Faro : mêmes règles. Ce sont des lignes logfmt plates (le récepteur Faro aplatit déjà
`context_*`, `event_data_*`) : rien d'imbriqué à aplatir. Les traces Faro passent par la même
transformation que le chemin OTLP (aplatissement et portée compris). En plus, `trace_id` n'est conservé que s'il fait 32 caractères
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

Une variable vidée dans Coolify vaut une variable absente : elle prend la valeur de
`.env.example`. Coolify transmet la valeur vide telle quelle et le repli `${VAR:-défaut}` du
compose ne s'applique pas (spike S5 de [`docs/spikes.md`](docs/spikes.md)) ; le défaut est donc
appliqué là où la valeur est lue : par Tempo et Loki dans leur configuration, par
`config/prometheus/start.sh` pour Prometheus, par Alloy et par `grafana-setup`. Une valeur
renseignée mais invalide ou nulle (`TEMPO_MAX_ACTIVE_SERIES=0` : plafond illimité ;
`TEMPO_RETENTION=0h` : traces supprimées aussitôt) est refusée par `config-guard`.

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
dans `/etc/hosts` (`sudo` requis). Les variables y sont interpolées comme le fait Coolify : une
variable vide (`--set TEMPO_RETENTION=`) arrive vide, le repli `${VAR:-défaut}` du compose ne
s'applique pas.

```bash
python3 harness/stack.py up                     # config-guard puis la stack
python3 harness/edge.py up                      # Traefik (routes Coolify simulées) + Grafana de test
python3 harness/stack.py oneshot grafana-setup  # provisionne le Grafana de test
python3 scripts/smoke.py                        # bout en bout (spec § 12.3)
python3 scripts/synth.py                        # données synthétiques pour tableaux et alertes (§ 12.6)
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
- `python3 harness/edge.py down` : arrêter seulement Traefik, le Grafana de test et le puits de
  notifications ;
- `python3 harness/edge.py revoke <utilisateur>` (`proj-a` ou `proj-b`) / `restore` : retirer une
  ligne htpasswd de la configuration dynamique du Traefik de test, puis la remettre (révocation à
  chaud).

Les tests lancés avec `GC_HARNESS=1` **recyclent le banc** : ils arrêtent et relancent la stack
(et le bord) à leur guise ; ne pas compter sur un banc démarré à la main pendant ces tests.

Le Grafana de test envoie ses emails au puits `harness/sink.py` (faux serveur SMTP) et sa
sortie HTTPS par son faux proxy, qui enregistre la tentative vers `api.telegram.org` puis la
refuse, et relaie tout autre hôte (Grafana 13 installe au premier démarrage ses plugins
Prometheus, Loki et Tempo depuis grafana.com). Tout est consigné dans
`.harness/sink/messages.jsonl`. `harness/harness.env` pointe `GRAFANA_SETUP_MIRROR_URL` sur
`file:.` : le banc exécute les fichiers de travail, vérifiés contre les empreintes du compose.
`tests/test_exploitation.py` (§ 12.6) dure environ huit minutes.

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
