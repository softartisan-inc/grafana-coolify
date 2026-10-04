# Déploiement Coolify pas à pas

[← Sommaire](../README.md)

Prérequis faits : [service Grafana](service-grafana.md) sur le réseau `coolify` avec son jeton,
[configuration Traefik](traefik.md) enregistrée sans erreur.

## 1. Créer la ressource (sans déployer)

1. Projet Coolify → **New Resource** → **Application** → **Public Repository** :
   `https://github.com/softartisan-inc/grafana-coolify`, branche `main`.
2. **Build Pack** : **Docker Compose** ; **Docker Compose Location** : `/docker-compose.yaml`
   (le fichier généré, jamais `compose.template.yaml`).
3. Enregistrer **sans déployer**. Coolify liste 8 services : `config-guard`, `loki`, `tempo`,
   `prometheus`, `node-exporter`, `alloy`, `alloy-gateway`, `grafana-setup`.

## 2. Réseau

| Ressource | **Connect To Predefined Network** |
|---|---|
| Ce package (`grafana-coolify`) | **Désactivé** : le compose déclare lui-même le réseau externe `coolify` et y donne à `loki`, `tempo`, `prometheus`, `alloy` les alias `gc-loki`, `gc-tempo`, `gc-prometheus`, `gc-alloy`. Activée, l'option efface ces alias. |
| Service Coolify « Grafana » | **Activé** ([service Grafana](service-grafana.md)). |

Explication et vérification : README, étape 4 « Réseau et noms internes », et spike S1 de
[`docs/spikes.md`](../spikes.md) (PR #6). Symptôme si l'option reste activée sur le package :
[`gc-*` ne résout pas](../depannage/sources-de-donnees.md#les-alias-gc--ne-résolvent-pas-option-activée-sur-le-package).

> Sur un déploiement antérieur à la PR #6 (compose sans alias `gc-*`), l'option était activée
> sur le package et seuls les noms nus (`loki`…) résolvaient. Migration : README, étape 4.

## 3. Variables

Copier `.env.example` dans **Environment Variables**, puis remplir d'après le
[tableau des variables](variables.md). Points qui ont posé problème :

- `GRAFANA_URL` = adresse **interne** de Grafana, `http://grafana-<uuid-grafana>:3000`.
- `LOKI_INTERNAL_URL`, `TEMPO_INTERNAL_URL`, `PROMETHEUS_INTERNAL_URL` : alias stables, connus
  avant tout déploiement : `http://gc-loki:3100`, `http://gc-tempo:3200`,
  `http://gc-prometheus:9090`. Repli de dépannage seulement (risque de collision) : les noms
  nus `http://loki:3100`… **Jamais** le nom du conteneur (`loki-<uuid>-<horodatage>`) : il
  change à chaque déploiement. Détails : [sources de données](../depannage/sources-de-donnees.md).
- `TENANT_HOST_REGEX` contient des `$` : cocher **Is Literal?**.
- Ne pas ajouter de `GF_SMTP_*` ici (ils vont sur le service Grafana) ni de `SERVICE_FQDN_*`.
- Ne pas vider `ENABLE_EXEMPLARS` ni `TEMPO_MAX_ACTIVE_SERIES` : les supprimer, ou remettre la
  valeur de `.env.example`.

## 4. Premier déploiement

**Deploy**. Pendant et après :

- **Ne copier les logs de déploiement nulle part** : la ligne `[CMD] … base64 …` contient le
  `.env` complet, secrets compris ([sécurité](../depannage/securite-fuites.md)).
- Échec `kex_exchange_identification: read: Connection reset by peer` : problème SSH de
  l'hôte, pas du package ([dépannage](../depannage/deploiement-coolify.md#kex_exchange_identification-read-connection-reset-by-peer)).
- `config-guard` et `grafana-setup` finissent **exited** : normal, ce sont des tâches
  ponctuelles.

## 5. Retirer les domaines générés d'office

Avec un domaine wildcard, Coolify **remplit lui-même** le champ **Domains** d'`alloy` et
d'`alloy-gateway` (`http://alloy-<uuid>.<wildcard>`…), ce qui ouvre Faro et OTLP sur Internet.
Juste après le premier déploiement :

1. Ressource → service `alloy-gateway` → vider **Domains** (tant qu'aucun serveur distant
   n'envoie d'OTLP) → **Save**.
2. Service `alloy` → vider **Domains** (tant qu'aucune application n'est instrumentée avec
   Faro) → **Save**.
3. **Redeploy**.

Pour ouvrir un point plus tard, saisir le domaine **avec le port du conteneur** :
`https://faro.example.com:12347` pour `alloy`, `https://otlp.example.com:4318` pour
`alloy-gateway` (le port désigne le conteneur ; le public reste sur 443).

## 6. Contrôler

Onglet **Logs** de la ressource (ou `docker logs` sur l'hôte) :

| Service | Dernière ligne attendue |
|---|---|
| `config-guard` | `config-guard: all checks passed` |
| `grafana-setup` | `grafana-setup: done` |

Séquence normale de `grafana-setup` : `Grafana <version>`, `datasource gc-loki: created`
(puis `gc-tempo`, `gc-prometheus`), `folder gc-grafana-coolify: created`, un
`folder gc-<projet>` par projet, `datasources and folders: done`,
`8 content files verified (SHA-256)`, tableaux et alertes, `done`. Aux déploiements suivants,
`created` devient `unchanged` ou `updated`.

Puis dans Grafana : **Connections** → **Data sources** → **Save & test** sur `gc-loki`,
`gc-tempo` et `gc-prometheus` : les trois doivent être vertes. Enfin
[tester les notifications](../exploitation/alertes.md#tester) (spike S6).

En cas d'erreur : [index du dépannage](../depannage/README.md).

## 7. Mettre à jour

Toute évolution passe par le dépôt : modifier `config/` ou `compose.template.yaml`,
`python3 scripts/render.py`, commit, `git push`, puis **Redeploy**. Ne jamais retoucher les
fichiers sur le serveur. Après une mise à jour de **Coolify**, rejouer
`security.py --remote` (contrôle 7) et les spikes S2 et S4 ([vérifications](../exploitation/verifications.md)).
