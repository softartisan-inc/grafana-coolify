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
  `preprod`, `acme.example.me` → `prod`.

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

## Retirer un projet

Retirer son nom de `PROJECTS` et redéployer : ses nouveaux logs Faro sont rejetés
(`unknown_project`). Le dossier Grafana et les données déjà stockées restent (expiration par la
rétention) ; supprimer le dossier à la main si besoin. Révoquer sa ligne htpasswd s'il en avait
une.

## Un serveur par projet

Le package se déploie aussi tel quel sur un autre serveur Coolify, avec son propre Grafana :
refaire l'[installation](README.md#ordre-de-lecture-pour-un-premier-déploiement). Sur un serveur
de recette, mettre `HOST_ENV=preprod`.
