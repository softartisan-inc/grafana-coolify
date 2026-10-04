# Spikes Coolify — liste de contrôle de l'opérateur (plan A)

Ces vérifications demandent une **vraie instance Coolify** : elles ne peuvent pas être jouées par
un agent ni par le banc natif. Les jouer **une fois** sur le serveur cible avant la mise en
production, puis à chaque mise à jour de Coolify pour S2 et S4. Noter le résultat (date, version
de Coolify, OK/KO, remarque) dans le tableau final et ouvrir un ticket pour tout KO.

Préparation commune :

- Un **déploiement de recette** du package (ressource Application depuis ce dépôt, build pack
  Docker Compose), variables remplies d'après `.env.example`, **Connect To Predefined Network**
  **désactivé** sur le package (le compose rejoint lui-même le réseau `coolify`, voir S1) et
  **activé** sur le service Coolify « Grafana ».
- Un accès SSH au serveur, `docker` disponible.
- Dans les commandes, remplacer `<app>` par l'UUID de la ressource Coolify et `<uuid>` par le
  suffixe **complet** des conteneurs (`<uuid>-<horodatage>` pour le package, relevé par
  `docker ps`). `config-guard` et `grafana-setup` sont des tâches ponctuelles : une fois finies
  (`Exited (0)`), `docker ps` ne les liste plus ; les trouver avec `docker ps -a`
  (`docker ps -a --filter name=config-guard --format '{{.Names}} {{.Status}}'`).

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
GRAFANA=$(docker ps --format '{{.Names}}' | grep -E '^grafana-[a-z0-9]+$'); echo "$GRAFANA"
docker inspect "$GRAFANA" --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}'
for url in http://gc-loki:3100/ready http://gc-tempo:3200/ready http://gc-prometheus:9090/-/ready; do
  docker exec "$GRAFANA" wget -qO- "$url"; echo " <- $url"
done
docker inspect "$(docker ps --format '{{.Names}}' | grep -E '^loki-')" \
  --format '{{json .NetworkSettings.Networks.coolify.Aliases}}'
```

Attendu : Grafana est sur le réseau `coolify` ; les trois URL `gc-*` répondent `ready` /
`Prometheus Server is Ready.` ; les alias de `loki` sur `coolify` contiennent `gc-loki`. Ces URL
sont les valeurs de `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL`.

**Observé au premier déploiement réel** (avant les alias `gc-*`, option activée sur le package) :

- les conteneurs portent un suffixe par déploiement, `loki-<uuid>-<horodatage>` ; `loki-<uuid>`
  ne résout **pas** sur `coolify` ;
- le nom de service nu (`loki`, `tempo`, `prometheus`, `alloy`) **résout** sur `coolify` : stable,
  mais il entre en collision avec tout service du même nom d'une autre ressource ;
- le service Coolify « Grafana » n'était que sur son propre réseau : il faut y activer **Connect
  To Predefined Network** ;
- `GRAFANA_URL` public : `grafana-setup` échoue avec `Grafana not healthy after 120s` (le
  conteneur ne rejoint pas l'adresse publique du serveur à travers le proxy). L'adresse interne
  `http://grafana-<uuid>:3000` fonctionne.

**Pourquoi l'option reste désactivée sur le package.** Pour une ressource Application, le parseur
de Coolify (`applicationParser`, `bootstrap/helpers/parsers.php`, commit
`16a8c79`) ajoute, option activée, le réseau de destination à chaque service par
`$networks_temp->put($network, null)` **après** les réseaux écrits dans le compose : l'entrée
`coolify: {aliases: [gc-loki]}` est remplacée par `coolify: null` et les alias disparaissent.
Option désactivée, les entrées du compose sont gardées telles quelles, avec leurs alias. Le même
parseur (l. 1543-1560) ajoute bien chaque réseau de premier niveau déclaré (sauf `default`), ici
`coolify`, aux services qui ne le listent pas, mais avec la valeur `null`, que la boucle suivante
(l. 1590-1603) écarte : elle ne garde qu'une chaîne ou un tableau. Seul le réseau `<uuid>` de la
ressource est ajouté à tous les services (l. 1605-1607). Option désactivée, un service n'est donc
sur `coolify` que s'il le déclare : `grafana-setup` le déclare lui-même, sans alias, pour joindre
`GRAFANA_URL` (`grafana-<uuid>`) ; `config-guard`, `node-exporter` et `alloy-gateway` n'y sont
pas (Traefik joint `alloy-gateway` par le réseau `<uuid>`, auquel Coolify connecte
`coolify-proxy`). Vérifié en exécutant ces lignes sur le compose rendu : option désactivée,
`grafana-setup` → `{default, coolify, <uuid>}` et un service sans `networks` → `{<uuid>}`. Le
service « Grafana », lui, est une ressource Service : l'option y lance `docker network connect`
après le démarrage, sans conflit.

**Alternative écartée : « Consistent Container Names ».** L'option donne aux conteneurs un nom
stable, `loki-<uuid>`, qui résoudrait sur `coolify`. Les alias `gc-*` lui sont préférés : courts,
indépendants de l'UUID de la ressource, ils restent identiques d'un serveur à l'autre et après
une recréation de la ressource.

## S2 — Portée de `coolify.traefik.middlewares` et références `@file` (spec § 5.4)

1. Installer la configuration dynamique (`traefik/grafana-coolify.yaml.example` rempli).
2. Lire les labels générés :

   ```bash
   docker inspect alloy-<uuid> --format '{{json .Config.Labels}}' | tr ',' '\n' | grep -i middlewares
   docker inspect alloy-gateway-<uuid> --format '{{json .Config.Labels}}' | tr ',' '\n' | grep -i middlewares
   ```

   Attendu : chaque routeur d'`alloy` porte exactement
   `gc-faro-cors@file,gc-faro-ratelimit@file,gc-faro-body@file`, dans cet ordre ; chaque routeur
   d'`alloy-gateway` porte exactement `gc-otlp-auth@file` ; aucun autre routeur du serveur ne
   porte ces middlewares.
3. Vérifier la résolution `@file` dans le tableau de bord ou les logs du proxy :

   ```bash
   docker logs coolify-proxy 2>&1 | grep -iE 'middleware .* does not exist|gc-(otlp|faro)' | tail
   ```

   Attendu : aucune erreur « middleware does not exist ».
4. Lancer `python3 scripts/security.py --remote` (voir README, étape 7) **sur le serveur**.
   Attendu : 8/8, et en particulier le contrôle 7 (fuite de middlewares, bug
   coollabsio/coolify #9886). Rappels :

   - le contrôle 1 teste d'abord le port de `GC_FARO_PUBLIC_URL` (le port explicite de l'URL,
     sinon 443 en `https` et 80 en `http`) sur
     `GC_PUBLIC_IP` ;
   - pour le contrôle 2, créer une ligne htpasswd de test, vérifier qu'elle fonctionne :

     ```bash
     curl -s -o /dev/null -w '%{http_code}\n' -u ancien:'mot-de-passe' -X POST \
       -H 'Content-Type: application/json' --data '{"resourceLogs":[]}' \
       https://<domaine OTLP>/v1/logs
     ```

     Attendu : un code autre que `401` (normalement `200`). Puis **retirer** la ligne, vérifier
     que la même commande renvoie `401`, et seulement alors lancer le script avec
     `GC_REVOKED_USER`, `GC_REVOKED_PASSWORD` et `GC_REVOKED_WAS_VALID=1` (sans cette variable,
     le contrôle 2 échoue toujours) ;
   - le contrôle 3 envoie une vraie requête Faro (app `gc-security`) : elle traverse le Traefik
     et l'`alloy` de production (comptée dans les métriques du récepteur Faro), mais sa charge est
     vide et **rien n'est écrit dans Loki** ;
   - le contrôle 8 lit `GC_ALLOY_CONTAINER`, `GC_GATEWAY_CONTAINER` et
     `GC_NODE_EXPORTER_CONTAINER` (noms réels, `docker ps`).

## S3 — Attributs d'un span Faro reçu (spec § 6.5)

Depuis une page instrumentée avec le SDK Faro Web et `@grafana/faro-web-tracing` (configurée avec
`app.namespace`, `app.name`, `app.environment`), déclencher un `fetch`, puis :

```bash
docker exec alloy-<uuid> wget -qO- 'http://gc-tempo:3200/api/search?tags=service.name%3D<app.name>&limit=1'
docker exec alloy-<uuid> wget -qO- 'http://gc-tempo:3200/api/v2/traces/<traceID>'
```

Attendu, sur la ressource du span stocké : `project` (issu de `service.namespace`), `env` (issu de
`deployment.environment` ou `deployment.environment.name`), **pas** d'attribut
`deployment.environment*`, `tenant` présent seulement s'il respecte `[a-z0-9-]+` et n'est pas
réservé. Noter la liste complète des attributs reçus : si le SDK n'envoie pas
`service.namespace`, ouvrir un ticket (le mapping de `config/alloy/config.alloy`, transform
`faro`, est à ajuster).

## S4 — Fichiers `content:` écrits octet pour octet, puis modifiés (spec § 4.2, § 12.2)

Les fichiers portent l'empreinte de leur contenu (`loki.yaml` → `loki.<8 hex>.yaml`) : les noms
exacts du commit déployé sont dans `docker-compose.yaml`
(`grep -E 'source: ./config/' docker-compose.yaml`).

1. Premier déploiement :

   ```bash
   cd /data/coolify/applications/<app>
   find config -type f | sort
   find config -type f -exec sha256sum {} +
   find config -mindepth 2 -type d
   docker logs config-guard-<uuid>
   ```

   Attendu :

   - un fichier par volume `content:`, sous le nom à empreinte du compose ; les 8 premiers
     caractères de chaque SHA-256 sont ceux du nom, et l'empreinte complète est celle du fichier
     du dépôt au même commit (`sha256sum config/...` en local) ;
   - aucun de ces chemins n'est un dossier (`find -mindepth 2 -type d` ne liste rien) ;
   - `config-guard` affiche `config-guard: all checks passed`.
2. Modification : en local, ajouter une ligne de commentaire à `config/loki/loki.yaml`, lancer
   `python3 scripts/render.py`, valider le commit, `git push`, puis **Redeploy** dans Coolify.

   ```bash
   grep -E 'loki\.[0-9a-f]{8}\.yaml' /data/coolify/applications/<app>/docker-compose.yaml | head -n 2
   ls -l /data/coolify/applications/<app>/config/loki/
   docker inspect loki-<uuid> --format '{{json .Mounts}}'
   docker logs config-guard-<uuid>
   ```

   Attendu : un nouveau fichier `loki.<nouvelle empreinte>.yaml` contenant la ligne ajoutée, monté
   par `loki` (la commande `-config.file` le désigne) ; `config-guard` passe. Noter si l'ancien
   `loki.<ancienne empreinte>.yaml` est toujours sur l'hôte et s'il apparaît encore dans l'onglet
   **Storages** de la ressource : Coolify supprime-t-il les stockages périmés ? (Attendu probable :
   non ; ils restent inoffensifs, rien ne les monte.)
3. Revenir au commit précédent (`git revert`, `git push`, Redeploy) : `loki` remonte l'ancien
   nom, dont le contenu enregistré est inchangé ; `config-guard` passe.

Vérifier aussi dans l'interface Coolify (onglet **Environment Variables**) si les `${…}` des
contenus (`${BIND_ADDR}`, `${__trace.traceId}`…) apparaissent comme de fausses variables : gêne
purement visuelle, à noter.

## S5 — Montages et droits propres au plan A

Ces points découlent de choix du plan A que le banc natif ne peut pas exercer :

```bash
docker inspect alloy-<uuid> --format '{{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} {{json .Mounts}}'
docker exec alloy-<uuid> sh -c 'ls -ld /var/lib/alloy /var/lib/alloy/queue && touch /var/lib/alloy/queue/.w && echo writable'
docker exec alloy-gateway-<uuid> sh -c 'touch /var/lib/alloy/.w && echo writable && df -h /var/lib/alloy'
for c in alloy-<uuid> alloy-gateway-<uuid>; do
  f=$(docker inspect "$c" --format '{{range .Mounts}}{{.Destination}} {{end}}' | tr ' ' '\n' | grep -E '^/etc/alloy/config\.[0-9a-f]{8}\.alloy$')
  docker exec "$c" sh -c 'if (: >> "$1") 2>/dev/null; then echo "WRITABLE $1"; else echo "refused $1"; fi' sh "$f"
done
docker ps -a --filter name=config-guard --format '{{.Names}} {{.Status}}'
docker inspect config-guard-<uuid> --format '{{json .Mounts}}'
docker inspect node-exporter-<uuid> --format '{{json .Mounts}}'
docker inspect loki-<uuid> --format '{{json .Mounts}}'
```

Le test d'écriture passe par un sous-shell `( … )` : dans le `sh` de l'image (dash), une
redirection qui échoue sur la commande spéciale `:` **termine le shell** ; écrit
`: >> "$1" && echo … || echo refused`, il n'affiche alors que `Permission denied`, sans
`refused`. Un `Permission denied` seul compte donc aussi comme **refusé**.

Attendu :

- `alloy` et `alloy-gateway` tournent en `473:473`, système de fichiers en lecture seule ;
  `/var/lib/alloy` (volume `alloy-data`, tmpfs de 64 Mo pour la passerelle) est inscriptible ;
- l'écriture dans le fichier de config est **refusée** (`refused …`) : Coolify reconstruit les
  montages `content:` sans leur `read_only: true` (`"RW":true` attendu, à noter), mais
  l'utilisateur 473 n'a pas le droit d'écrire le fichier que Coolify a créé ;
- `config-guard` est `Exited (0)` (tâche ponctuelle, visible avec `docker ps -a` seulement). Son
  montage `/guard` (syntaxe courte `:ro`) est le dossier `config` de l'application, en lecture
  seule (`"RW":false`), et contient les fichiers écrits par Coolify pour les autres services. Le
  montage de `guard.sh` lui-même est en `"RW":true` (Coolify retire `read_only` des montages
  `content:`) : sans conséquence, la commande du conteneur vérifie l'empreinte SHA-256 du script
  avant de l'exécuter, et le conteneur s'arrête aussitôt après ;
- les trois montages de `node-exporter` (`/proc`, `/sys`, `/`, syntaxe courte `:ro`) sont en
  lecture seule (`"RW":false`) ;
- `TENANT_HOST_REGEX`, marquée « Is Literal? », arrive intacte :
  `docker exec alloy-<uuid> printenv TENANT_HOST_REGEX` affiche la regex avec ses `$`.

### Variables vidées dans Coolify : le repli `${VAR:-défaut}` du compose ne s'applique pas

**Résultat du premier passage (déploiement réel) : KO.** `ENABLE_EXEMPLARS` vidée dans l'onglet
**Environment Variables** (variable conservée, valeur vide), puis **Redeploy** : Tempo reçoit
`ENABLE_EXEMPLARS=` (vide). Coolify substitue lui-même les variables et transmet la valeur vide ;
le repli `:-` du compose ne s'applique pas. Tempo reste `Up` sans aucun message. Par extension,
**tout** `${VAR:-défaut}` du compose est sans effet quand l'opérateur laisse `VAR` vide. Avant le
correctif, les conséquences mesurées sur le banc (binaires officiels) étaient :

| Variable vide | Effet avant le correctif |
|---|---|
| `TEMPO_RETENTION` | rétention Tempo de `0s` : le compacteur supprime chaque bloc dès sa fin (perte silencieuse des traces) |
| `TEMPO_MAX_ACTIVE_SERIES` | `0` = **aucun plafond** de séries span-metrics (risque mémoire, silencieux) |
| `ENABLE_EXEMPLARS` | lu comme `false` : sans effet, sauf à vouloir `true` |
| `LOKI_RETENTION_PROD`, `LOKI_RETENTION_DEFAULT` | Loki refuse de démarrer (`retention period must be >= 24h was 0s`), redémarre en boucle |
| `PROM_RETENTION_TIME`, `PROM_RETENTION_SIZE` | Prometheus refuse de démarrer (`empty duration string`, `units: invalid`), redémarre en boucle |
| `FARO_*`, `ALERT_*`, `CARDINALITY_ALERT_THRESHOLD`, `HOST_ENV` | aucun : Alloy (`coalesce`), `grafana-setup` et `config-guard` appliquaient déjà le défaut |

**Protection en place** : une variable vide vaut désormais une variable absente. Le défaut est
appliqué là où la valeur est lue, plus seulement dans le compose :

- Tempo et Loki : `${VAR:-défaut}` **dans leur fichier de config**, développé par le binaire
  (`-config.expand-env`, bibliothèque drone/envsubst, qui traite une valeur vide comme absente) ;
- Prometheus : script d'entrée `config/prometheus/start.sh` (`: "${VAR:=défaut}"`), qui ajoute
  les options de rétention et `--enable-feature` seulement si `PROM_ENABLE_FEATURES` est rempli ;
- `config-guard` reçoit ces variables **sans** repli et refuse le déploiement si une valeur
  **renseignée** est mal formée ou nulle (`0`, `0h`, `0GB`… : rétention nulle ou
  plafond illimité), `ENABLE_EXEMPLARS` autre que `true`/`false` compris ;
- `tests/test_empty_values.py` vérifie que chaque `${VAR:-défaut}` du compose a son défaut chez
  le consommateur, identique à `.env.example` ; `scripts/check.py` (`validators`) valide les
  configs Loki et Tempo une seconde fois avec toutes ces variables vides ; le banc natif
  (`harness/stack.py`) interpole désormais comme Coolify (une variable vide reste vide).

Vérification à rejouer après le déploiement du correctif :

1. Dans l'onglet **Environment Variables**, **vider** `TEMPO_MAX_ACTIVE_SERIES`,
   `TEMPO_RETENTION` et `PROM_RETENTION_TIME` (valeur vide, variable conservée), **Redeploy**,
   puis :

   ```bash
   docker inspect tempo-<uuid> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(TEMPO_MAX_ACTIVE_SERIES|TEMPO_RETENTION)='
   docker exec prometheus-<uuid> wget -qO- http://localhost:9090/api/v1/status/flags | grep -oE '"storage.tsdb.retention.(time|size)":"[^"]*"'
   docker run --rm --network container:tempo-<uuid> alpine:3.22 wget -qO- http://localhost:3200/status/config | grep -E 'max_active_series|^        block_retention'
   docker ps -a --filter name=<uuid> --format '{{.Names}} {{.Status}}'
   ```

   Attendu : Tempo reçoit toujours les variables **vides** (comportement de Coolify, inchangé),
   mais sa configuration effective affiche `max_active_series: 100000` et
   `block_retention: 168h0m0s` ; Prometheus affiche `"storage.tsdb.retention.time":"90d"` ; tous
   les conteneurs sont `Up` (et `config-guard` `Exited (0)`). Tempo est interrogé depuis un
   conteneur jetable qui partage son réseau : l'image Tempo ne fournit pas forcément `wget`.
2. Vérifier que Coolify a gardé l'`entrypoint` de Prometheus :
   `docker inspect prometheus-<uuid> --format '{{json .Config.Entrypoint}}'` affiche
   `["/bin/sh","/etc/prometheus/start.<8 hex>.sh"]`.
3. Saisir `TEMPO_MAX_ACTIVE_SERIES=0`, **Redeploy** : `config-guard` échoue
   (`docker logs config-guard-<uuid>` : `TEMPO_MAX_ACTIVE_SERIES must be a positive integer`) et
   aucun service ne démarre.
4. Remettre les valeurs de `.env.example` (ou laisser vide) et redéployer.

## S6 — `grafana-setup` du plan B sur la recette (bloquant avant la production)

Tableaux de bord et alertes, avec le vrai Grafana, le vrai SMTP et le vrai bot. **Prérequis
bloquant** : pas de mise en production tant que S6 n'est pas OK.

```bash
docker logs grafana-setup-<uuid> | grep -E 'datasources and folders: done|content files verified|grafana-setup: done|ERROR'
docker inspect grafana-setup-<uuid> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(GRAFANA_SETUP_(PINNED_URL|FILES)|HOST_ENV)=' | cut -c1-120
grep -n GRAFANA_SETUP_TAG tools/versions.env
GRAFANA_URL=https://grafana.example.com GRAFANA_SA_TOKEN=glsa_... python3 scripts/notify_test.py
```

Attendu :

- `grafana-setup: datasources and folders: done`, `grafana-setup: 8 content files verified (SHA-256)`
  puis `grafana-setup: done` : le conteneur joint `raw.githubusercontent.com` et le tag épinglé
  contient les fichiers attendus ;
- `GRAFANA_SETUP_PINNED_URL` se termine par le `GRAFANA_SETUP_TAG` du dépôt déployé : après un
  nouveau tag poussé puis redéployé, la valeur **littérale** du compose est bien mise à jour par
  Coolify (comme `CONFIG_GUARD_EXPECTED`, S4) ; `HOST_ENV` vaut `prod` sur le serveur de
  production ;
- `notify-test: gc-telegram: sent` et `notify-test: gc-email: sent`, puis le message dans le
  groupe Telegram et dans chaque boîte de `ALERT_EMAILS` ;
- dans Grafana, dossier **grafana-coolify** : six tableaux sans panneau en erreur, six règles
  **Normal** ou **Firing**, aucune en **Error** ; dossier de chaque projet : les liens ouvrent les
  tableaux avec le bon `var-project`.

## Résultats

| Spike | Date | Version Coolify | Résultat | Remarque |
|---|---|---|---|---|
| S0 | | | | |
| S1 | | | KO puis corrigé | Suffixe par déploiement, `loki-<uuid>` non résolu ; alias `gc-*` ajoutés, Grafana à rattacher, `GRAFANA_URL` interne. À rejouer après la fusion. |
| S2 | | | | |
| S3 | | | | |
| S4 | | | | |
| S5 | | | KO puis corrigé | Variable vidée : Coolify transmet la valeur vide, le repli `:-` du compose ne s'applique pas (Tempo `Up` sans erreur). Défauts appliqués par Tempo, Loki, `start.sh` de Prometheus ; valeurs nulles refusées par `config-guard`. À rejouer après la fusion. |
| S6 | | | | |
