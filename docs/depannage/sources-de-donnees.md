# Dépannage : sources de données

[← Index du dépannage](README.md)

Le fonctionnement nominal (alias `gc-*`, réglage du réseau) est décrit dans le README, étape 4
« Réseau et noms internes », et vérifié par le spike S1 de [`docs/spikes.md`](../spikes.md).
Cette page ne reprend que les pannes.

## `lookup prometheus-<uuid> … no such host`

**Symptôme** : `grafana-setup: done`, mais **Save & test** échoue sur les trois sources :

```
Unable to connect with Loki. Please check the server logs for more details.
Post "http://prometheus-<uuid>:9090/api/v1/query": dial tcp: lookup prometheus-<uuid> on 127.0.0.11:53: no such host
Get "http://tempo-<uuid>:3200/api/echo": dial tcp: lookup tempo-<uuid> on 127.0.0.11:53: no such host
```

**Cause** : Coolify nomme chaque conteneur `<service>-<uuid>-<horodatage>`, avec un horodatage
**neuf à chaque déploiement**. `prometheus-<uuid>` n'est enregistré nulle part sur le réseau
`coolify`, et le nom complet ne vaudrait que jusqu'au prochain redéploiement.

**Correctif** : utiliser un alias stable dans les trois variables, puis **Redeploy**
(`grafana-setup` met à jour l'URL des sources : `datasource gc-loki: updated`…) :

```
LOKI_INTERNAL_URL=http://gc-loki:3100
TEMPO_INTERNAL_URL=http://gc-tempo:3200
PROMETHEUS_INTERNAL_URL=http://gc-prometheus:9090
```

Repli de dépannage : les noms nus `http://loki:3100`, `http://tempo:3200`,
`http://prometheus:9090`. Avec le compose de la PR #6, ils résolvent que l'option
**Connect To Predefined Network** du package soit activée ou non (Compose ajoute le nom du
service aux alias sur chaque réseau déclaré). Avec un compose antérieur à la PR #6 (sans alias
`gc-*`), ils sont la seule option, et seulement option activée : désactivée, aucun service n'est
sur `coolify`. Risque : la [collision](#la-source-répond-mais-interroge-la-mauvaise-stack-collision)
plus bas.

**Vérification** : **Save & test** vert sur `gc-loki`, `gc-tempo`, `gc-prometheus`. Sur l'hôte,
les alias réellement enregistrés :

```bash
for s in loki tempo prometheus alloy; do c=$(docker ps --format '{{.Names}}' | grep "^$s-<uuid>" | head -1); echo "$s -> $c"; docker inspect -f '{{json .NetworkSettings.Networks.coolify.Aliases}}' "$c"; done
```

## Les alias `gc-*` ne résolvent pas (option activée sur le package)

**Symptôme** : le déploiement réussit, `config-guard` passe, mais **Save & test** échoue avec
`lookup gc-prometheus on 127.0.0.11:53: no such host` (idem `gc-loki`, `gc-tempo`) ; la boucle
ci-dessus montre des alias sans `gc-*` (seulement le nom nu et l'identifiant court du conteneur).

**Cause** : **Connect To Predefined Network** est **activé** sur la ressource `grafana-coolify`.
Pour une ressource Application, option désactivée, Coolify garde telles quelles les entrées
`networks` du compose (seul le réseau `<uuid>` est ajouté à tous les services). Option activée,
il écrit son entrée **après** les réseaux du compose et remplace
`coolify: {aliases: [gc-loki]}` par `coolify: null` : les alias `gc-*` disparaissent sans aucun
message, les noms nus (`loki`…) résolvent toujours.
Détail du code de Coolify en cause : spike S1 de [`docs/spikes.md`](../spikes.md).

**Correctif** : ressource `grafana-coolify` → **désactiver** Connect To Predefined Network →
**Save** → **Redeploy**. Un déploiement existant qui utilise encore des noms nus suit la
migration du [README, étape 4](../../README.md#4-réseau-et-noms-internes). Laisser l'option **activée** sur le service Grafana (ressource Service :
elle n'y efface rien).

**Vérification** : la boucle affiche `gc-loki`, `gc-tempo`, `gc-prometheus`, `gc-alloy` ; puis
**Save & test** vert.

## La source répond, mais interroge la mauvaise stack (collision)

**Symptôme** : **Save & test** vert, mais données absentes, d'un autre projet, ou résultats qui
changent d'une requête à l'autre.

**Cause** : URL sur un **nom nu** (`http://prometheus:9090`) alors qu'une autre ressource du
serveur a aussi un service `prometheus` sur le réseau `coolify` : Docker répond par l'un ou
l'autre.

**Correctif** : passer aux alias `gc-*`. Pour voir les candidats :

```bash
for a in loki tempo prometheus alloy; do echo "== $a"; docker network inspect coolify -f '{{range .Containers}}{{.Name}} {{end}}' | tr ' ' '\n' | grep -E "^$a(-|$)"; done
```

Chaque bloc ne doit lister que des conteneurs de la ressource `grafana-coolify`.

## Grafana ne joint aucune source, même avec les bons noms

**Cause** : le service Grafana n'est pas sur le réseau `coolify`. **Correctif** :
[service Grafana](../installation/service-grafana.md#1-le-relier-au-réseau-coolify) ; c'est la
même cause que [`Grafana not healthy after 120s`](grafana-setup.md#grafana-setup-error-grafana-not-healthy-after-120s).

## Les applications du même serveur n'atteignent pas Alloy

**Cause** : `ALLOY_INTERNAL_URL` pointe sur un nom de conteneur suffixé, ou l'application n'est
pas sur le réseau `coolify`. **Correctif** : `http://gc-alloy:4318` (OTLP/HTTP) ou
`gc-alloy:4317` (gRPC), et **Connect To Predefined Network** sur la ressource de l'application.
Cette option ne concerne que les applications au build pack **Docker Compose** : une application
Nixpacks ou Dockerfile est déjà rattachée au réseau `coolify`.
