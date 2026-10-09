# Ajouter un projet

[← Sommaire](README.md)

## Prod et préprod : un seul projet, deux environnements

La prod et la préprod d'une même application **ne sont pas deux projets**. C'est un projet
(`project`) avec deux valeurs de l'étiquette `env` :

| | Prod | Préprod |
|---|---|---|
| Envoyé par l'application | `deployment.environment.name=prod` | `deployment.environment.name=preprod` |
| Rétention des logs | `LOKI_RETENTION_PROD` (30 j) | `LOKI_RETENTION_DEFAULT` (7 j) |
| Alertes `critical` | Telegram | email |
| Avertissements (`warning`) | email | email |
| Dans Grafana | même dossier `gc-<projet>`, filtre **env** en haut des tableaux | |

Rien à ajouter dans le package pour une préprod : `PROJECTS` reste inchangé. Chaque application
déclare son environnement à l'instrumentation :

- **backends** (OTLP) : attributs de ressource `project=<projet>`,
  `deployment.environment.name=prod|preprod`, `service.name` ; toute donnée sans projet ou avec
  un autre environnement est rejetée ;
- **navigateur, desktop, mobile** (Faro) : `app.namespace` → `project`, `app.name` →
  `service_name`, `app.environment` → `env`. Pour les logs Faro, `env` et `tenant` sont déduits
  de l'hôte de la page (`HOST_MAP`, `TENANT_HOST_REGEX`), par exemple `acme-dev.example.me` →
  `preprod`, `acme.example.me` → `prod`. Un hôte que ces règles ne reconnaissent pas (un client
  desktop, par exemple) garde l'`env` et le `tenant` du client, validés : voir
  [Un client desktop](#un-client-desktop-hors-navigateur).

`HOST_ENV` est un autre réglage : il décrit **le serveur** qui porte la stack (label `env` de
l'alerte Disque), pas les applications. Serveur unique qui héberge la prod : `HOST_ENV=prod`.

## Un nouveau projet

1. **Variables du package** : ajouter le nom à `PROJECTS`, en minuscules, séparé par une
   virgule **sans espace** :

   ```
   PROJECTS=projet-a,projet-b
   ```

   Format : `[a-z0-9][a-z0-9-]{0,63}`, sinon `grafana-setup` s'arrête
   (`invalid project name in PROJECTS`).
2. Si le projet a des fronts instrumentés avec Faro : compléter `FARO_SERVICES` (noms
   `app.name`), `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` (voir le README,
   « Envoyer des données »), et ajouter ses origines à la regex CORS de la
   [configuration Traefik](installation/traefik.md#origines-autorisées-cors-faro).
3. S'il envoie depuis **un autre serveur** : une ligne htpasswd à son nom dans
   [Traefik](installation/traefik.md#comptes-otlp-htpasswd), et un domaine pour
   `alloy-gateway` si la porte OTLP est encore fermée.
4. **Redeploy**. `grafana-setup` crée le dossier `gc-<projet>` et son tableau
   `grafana-coolify — <projet>`, dont les liens ouvrent les six tableaux avec `var-project`
   réglé.
5. Instrumenter l'application, puis vérifier dans **Explore** (Loki : `{project="<projet>"}`).

## Un client desktop (hors navigateur)

Exemple : `desktop-app`, un client desktop Electron, à côté du front web `web-front`
du même projet `demo`. Le desktop envoie au **même** point Faro
(`https://<domaine Faro>/collect`, même `FARO_API_KEY`) ; rien d'autre ne change dans le package.

### Ce qui diffère d'un navigateur

- **Pas d'hôte de page.** Le client réécrit `page_url` en `app://desktop-app/<route gabarit>`
  (par exemple `app://desktop-app/commandes/{id}`). L'hôte lu par Alloy est `desktop-app` : il
  n'est pas dans `HOST_MAP` et `TENANT_HOST_REGEX` ne le reconnaît pas, donc **rien n'est déduit
  de l'hôte** ; `env` et `tenant` sont ceux que le client envoie, puis validés (tableau
  ci-dessous).
  - Ne **pas** ajouter `desktop-app` à `HOST_MAP` : une entrée y fixe un seul `env` et retire le
    tenant, alors qu'un même poste parle à la prod ou à la préprod.
  - `TENANT_HOST_REGEX` doit rester ancrée (`^…$`) sur le domaine réel des tenants, comme
    l'exemple `^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me|app)$`. Sinon elle pourrait
    reconnaître l'hôte `desktop-app` et imposer un `env` faux.
- **CORS sans objet.** Le CORS ne protège que des pages web ; un client hors navigateur n'est
  arrêté que par la clé, le débit, la taille et `FARO_SERVICES`. La regex des origines ne change
  pas.
- **`tenant` en mode chemin.** Avant la connexion (API `api.<domaine>/api/<id>/`), le desktop peut
  n'envoyer aucun slug : la ligne est stockée sans `tenant`. C'est attendu ; le tenant qui fait
  foi est celui du span serveur.

### Validation des valeurs du client

Logs (`loki.process "faro"`) et traces (`otelcol.processor.transform "faro"`, puis les filtres
`faro` et `default`) appliquent les mêmes règles que pour un navigateur sur un hôte inconnu :

| Donnée | Envoyée par le desktop | Contrôle | Si le contrôle échoue |
|---|---|---|---|
| `project` | `app.namespace` (logs), `service.namespace` (traces) = `demo` | format `[a-z0-9][a-z0-9-]{0,63}`, présent dans `PROJECTS` | log rejeté (`invalid_project`, `unknown_project`, `missing_project`) ; trace supprimée |
| `service_name` | `app.name` (logs), `service.name` (traces) = `desktop-app` | présent dans `FARO_SERVICES` | log rejeté (`unknown_service`) ; trace supprimée |
| `env` | `app.environment` (logs), `deployment.environment.name` (traces) | `prod` ou `preprod` | log rejeté (`invalid_env`, `missing_env`) ; trace supprimée |
| `tenant` | attribut de session `tenant` (logs), attribut `tenant` de ressource ou de span (traces) | `[a-z0-9-]+` et absent de `RESERVED_SUBDOMAINS` | **seul le tenant est retiré**, la donnée est gardée |

« Validé » veut dire **bien formé et autorisé**, pas **authentifié** : un détenteur de la clé
peut déclarer `prod` ou `preprod` et n'importe quel slug bien formé. C'est le risque résiduel de
tout client Faro (clé en écriture seule, lisible dans le binaire) ; le span serveur, résolu par
le backend, reste la référence.

Garde-fous de cardinalité :

- **Loki** : les labels restent `project`, `env`, `service_name`. `desktop-app` ajoute au plus
  deux flux (`prod`, `preprod`) ; `tenant` est une métadonnée structurée, jamais un label, et ne
  crée aucun flux.
- **Prometheus** : `tenant` est une dimension des span-metrics de Tempo, donc un tenant inventé
  sur une trace crée des séries. Elles restent plafonnées par `TEMPO_MAX_ACTIVE_SERIES` et
  signalées par l'alerte **Cardinalité** (`CARDINALITY_ALERT_THRESHOLD`) ; le tableau
  « Cardinalité » les ventile par tenant.

### Mise en service (Coolify)

1. Ressource `grafana-coolify` → **Environment Variables** → relever la valeur actuelle de
   `FARO_SERVICES`.
2. **Ajouter** `desktop-app` à cette valeur, sans la remplacer, virgule **sans espace** :

   ```
   FARO_SERVICES=web-front,desktop-app
   ```

3. Laisser `PROJECTS` tel quel : il contient déjà `demo`. Ne toucher ni à `HOST_MAP`, ni à
   `RESERVED_SUBDOMAINS`, ni à `TENANT_HOST_REGEX`, ni au CORS de Traefik.
4. **Redeploy** (pas *Restart*), comme pour toute variable du package.

**Vérification**, dans Grafana → **Explore** :

- Loki : `{project="demo", service_name="desktop-app"}`, puis par environnement
  `{project="demo", env="preprod", service_name="desktop-app"}`, et par tenant
  `{project="demo", service_name="desktop-app"} | tenant="<slug>"` ;
- Tempo (TraceQL) : `{ resource.service.name = "desktop-app" }` ;
- Prometheus : `increase(loki_process_dropped_lines_total{reason=~"unknown_service|invalid_env|missing_env"}[1h])`
  n'augmente pas après le redéploiement (panneau « Rejets » du tableau « Santé du pipeline »).

En cas de rejet : [Dépannage de l'ingestion Faro](depannage/ingestion-faro.md).

**Interrupteur** : retirer `desktop-app` de `FARO_SERVICES` puis **Redeploy** coupe le desktop
seul (logs rejetés en `unknown_service`, traces supprimées) ; `web-front` n'est pas touché. Le
remettre rétablit le flux, sans rien réinstaller sur les postes.

## Retirer un projet

Retirer son nom de `PROJECTS` et redéployer : ses nouveaux logs Faro sont rejetés
(`unknown_project`). Le dossier Grafana et les données déjà stockées restent (expiration par la
rétention) ; supprimer le dossier à la main si besoin. Révoquer sa ligne htpasswd s'il en avait
une.

## Un serveur par projet

Le package se déploie aussi tel quel sur un autre serveur Coolify, avec son propre Grafana :
refaire l'[installation](README.md#ordre-de-lecture-pour-un-premier-déploiement). Sur un serveur
de recette, mettre `HOST_ENV=preprod`.
