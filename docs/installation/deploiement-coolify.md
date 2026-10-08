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
| Ce package (`grafana-coolify`) | **Désactivé** : le compose déclare lui-même le réseau externe `coolify` et y donne à `loki`, `tempo`, `prometheus`, `alloy` les alias `gc-loki`, `gc-tempo`, `gc-prometheus`, `gc-alloy`. Option désactivée, seuls les services qui déclarent `coolify` dans le compose y sont rattachés : `loki`, `tempo`, `prometheus`, `alloy` avec leur alias, et `grafana-setup` sans alias ; `config-guard`, `node-exporter` et `alloy-gateway` restent sur le seul réseau `<uuid>` de la ressource (Traefik y est connecté). Option activée, Coolify rattache tout le package à `coolify` mais efface les alias `gc-*`. |
| Service Coolify « Grafana » | **Activé** ([service Grafana](service-grafana.md)). |

Explication et vérification : README, étape 4 « Réseau et noms internes », et spike S1 de
[`docs/spikes.md`](../spikes.md) (PR #6). Symptôme si l'option reste activée sur le package :
[`gc-*` ne résout pas](../depannage/sources-de-donnees.md#les-alias-gc--ne-résolvent-pas-option-activée-sur-le-package).

> Sur un déploiement antérieur à la PR #6 (compose sans alias `gc-*`), l'option était activée
> sur le package et les URL utilisaient les noms nus (`loki`…), qui ne résolvent, avec ce
> compose, que l'option activée ; d'où l'ordre : mettre à jour le dépôt avant de désactiver
> l'option. Migration (en bref : mettre à jour, désactiver l'option, redéployer, puis
> seulement passer les URL à `gc-*`) : [README, étape 4](../../README.md#4-réseau-et-noms-internes),
> « Migration d'un déploiement existant ».

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
- Une variable de rétention ou de réglage laissée vide (`TEMPO_MAX_ACTIVE_SERIES`,
  `ENABLE_EXEMPLARS`, `*_RETENTION*`…) prend la valeur de `.env.example` ; ne jamais y saisir `0`
  (`config-guard` le refuse : plafond illimité ou rétention nulle).

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

### Ouvrir Faro ou OTLP

Comportement vérifié sur **Coolify v4.3.23** : sous chaque domaine, un champ **Internal port**
donne le port du conteneur vers lequel Traefik envoie les requêtes (le public reste sur 443).

| Service | Domains (sans port) | Internal port |
|---|---|---|
| `alloy` (Faro) | `https://faro.example.com` | `12347` |
| `alloy-gateway` (OTLP/HTTP) | `https://otlp.example.com` | `4318` |

1. Service → **Domains** : saisir le domaine **sans** `:port`, puis le port dans **Internal
   port** → **Save**. Si Coolify ouvre une fenêtre d'avertissement sur le port (port saisi
   différent de celui qu'il attend), la **confirmer** : sans confirmation, Coolify restaure
   **sans le dire** le domaine et le port précédents, et rien n'est enregistré.
2. **Redeploy** de la ressource entière. **Restart** ne suffit pas : il relance les conteneurs avec
   leurs anciens labels Traefik.
3. Vérifier le port réellement routé (voir ci-dessous).

Ordre de choix du port (v4.3.23) : port écrit dans l'URL, puis **Internal port** enregistré
pour ce domaine, puis port par défaut du service, le suffixe de `SERVICE_FQDN_<SERVICE>_<PORT>`
ou, selon le type de ressource, le **premier port de `expose`** : le compose
met `12347` en tête pour `alloy` et `4318` pour `alloy-gateway` (contrôle `ports` de
`scripts/check.py`). Une valeur déjà enregistrée dans **Internal port** l'emporte toujours :
un `4317` (OTLP gRPC) resté là d'un déploiement antérieur envoie Faro au mauvais port.

**Le champ garde 4317 malgré la correction** (valeur enregistrée par domaine) :

1. Supprimer le domaine du service → **Save** → **Redeploy**.
2. Ressaisir le domaine sans port et **Internal port** `12347` → **Save** → **Redeploy**.

**Vérification**, sur l'hôte (le motif `^alloy-<uuid>` n'attrape pas `alloy-gateway-<uuid>`) :

```bash
docker inspect $(docker ps --format '{{.Names}}' | grep '^alloy-<uuid>' | head -1) | grep loadbalancer.server.port
```

Attendu : `…loadbalancer.server.port=12347` (`4318` pour `alloy-gateway-<uuid>`). Puis un
`POST https://faro.example.com/collect` ne répond plus `500` (voir
[dépannage](../depannage/deploiement-coolify.md#faro--500-internal-server-error-sur-collect)).

**Versions plus anciennes de Coolify** (pas de champ **Internal port**) : le port s'écrit dans
l'URL, `https://faro.example.com:12347` pour `alloy`, `https://otlp.example.com:4318` pour
`alloy-gateway`.

Derrière Cloudflare ou un autre proxy, la limite de débit Faro doit lire l'IP du client :
`ipStrategy.depth: 1` et `trustedIPs` ([limite de débit](traefik.md#limite-de-débit)).

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
