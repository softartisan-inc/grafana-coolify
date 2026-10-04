# Dépannage : `grafana-setup`

[← Index du dépannage](README.md)

`grafana-setup` est une tâche ponctuelle : elle finit **exited**. Un échec n'arrête qu'elle ;
Loki, Tempo, Prometheus et Alloy tournent quand même. Ses logs (onglet **Logs**, service
`grafana-setup`) ne contiennent jamais le jeton et peuvent être partagés. Après toute
correction : **Redeploy** (elle est relançable sans effet de bord).

## `ValueError: unknown url type`

**Symptôme** (versions antérieures à la PR #4) : trace Python terminée par

```
ValueError: unknown url type: 'GRAFANA_URL is required/api/health'
```

Dans les logs de déploiement, les variables vides valent leur propre message d'erreur :
`GRAFANA_URL=GRAFANA_URL is required`, `LOKI_INTERNAL_URL=LOKI_INTERNAL_URL is required`…

**Cause** : le compose utilisait `${GRAFANA_URL:?GRAFANA_URL is required}`. Docker Compose
refuserait de démarrer, mais **Coolify** remplace la variable par le texte du message et
déploie.

**Correctif** : corrigé dans le package (PR #4 et #5) : plus aucun `${VAR:?…}` ; une variable
manquante donne `grafana-setup: ERROR: GRAFANA_URL is required` (ou
`… must be an http:// or https:// URL, got '…'`). Renseigner les variables
([tableau](../installation/variables.md#obligatoires)) et redéployer. Sur une ressource créée
avant la correction, vérifier qu'aucune variable n'a gardé pour valeur un texte « … is
required » et la vider ou la corriger.

**Vérification** : `grafana-setup: Grafana <version>` en début de log.

## `grafana-setup: ERROR: Grafana not healthy after 120s`

**Symptôme** : la ligne ci-dessus après deux minutes d'attente.

**Cause** : `grafana-setup` n'obtient aucune réponse de `GRAFANA_URL/api/health` (ce point ne
demande pas de jeton : c'est le réseau ou l'URL). Deux causes, rencontrées ensemble :

1. `GRAFANA_URL` est l'URL **publique** : depuis un conteneur du serveur, le trafic doit sortir
   puis revenir par le proxy, ce que beaucoup d'hébergeurs bloquent.
2. Le service Grafana n'est **que sur son propre réseau** (service « one-click ») : aucun réseau
   commun avec la stack.

**Correctif** :

1. Service Grafana → **Connect To Predefined Network** → **Save** → **Restart**
   ([service Grafana](../installation/service-grafana.md#1-le-relier-au-réseau-coolify)).
2. Sur l'hôte, tester (`-sS` pour voir l'erreur) :

   ```bash
   G=$(docker ps --format '{{.Names}}' | grep -E '^grafana-[a-z0-9]+$' | head -1)
   docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$G"
   docker run --rm --network coolify curlimages/curl -sS -m 5 "http://$G:3000/api/health"
   ```

3. `GRAFANA_URL=http://grafana-<uuid-grafana>:3000` dans le package, **Redeploy**.

**Vérification** : le réseau `coolify` apparaît, `curl` renvoie `{"database":"ok",…}`, puis
`grafana-setup: done`.

Diagnostic qui trompe : `curl -s` (sans `S`) n'affiche rien quand le nom ne résout pas. Avec
`-sS` : `curl: (6) Could not resolve host: grafana-<uuid-grafana>`.

## `Grafana unreachable (…)` ou `invalid request to …`

**Cause** : URL mal formée (`invalid request`) ou hôte injoignable (`unreachable`, avec la raison :
nom inconnu, connexion refusée, délai). Même diagnostic que ci-dessus. Les identifiants d'une
URL `https://user:mot-de-passe@…` sont masqués dans le message.

## `HTTP 401` / `HTTP 403` sur `/api/…`

**Cause** : jeton expiré, supprimé ou sans le rôle **Admin**. **Correctif** : nouveau jeton
([rotation](../exploitation/rotation-des-secrets.md#jeton-du-compte-de-service-grafana)).

## HTTP 404 ou SHA-256 differs au téléchargement

**Symptôme** : `grafana-setup: datasources and folders: done`, puis
`grafana-setup: ERROR: datasources and folders are provisioned, dashboards and alerts are not: …`
suivi de `…: HTTP 404` ou de `…: SHA-256 <a> differs from the pinned <b>: nothing was run`.

**Cause** : `HTTP 404` : le tag `GRAFANA_SETUP_TAG` n'est pas poussé sur GitHub. `SHA-256` : un
fichier téléchargé (`dashboards.py`, `alerting.py`, `dashboards/*.json`) a changé sans que le
suffixe du tag ait monté. Un message réseau (nom inconnu, délai) : le conteneur ne joint pas
`raw.githubusercontent.com` ([prérequis](../installation/prerequis.md#accès-sortant-vers-github)).

**Correctif** : README, « Modifier un tableau de bord ou une alerte » (monter le suffixe,
`render.py`, tag poussé avec la branche) ; ou `GRAFANA_SETUP_MIRROR_URL` sans accès à GitHub.

**Vérification** : `grafana-setup: 8 content files verified (SHA-256)` puis `grafana-setup: done`.

## `legacy alerting provisioning API unavailable`

**Cause** : une version de Grafana a retiré l'API `/api/v1/provisioning/*`. Sources et tableaux
sont en place, seules les alertes manquent. Voir le README, « Alertes ».

## `invalid project name in PROJECTS`

En pratique `config-guard` refuse déjà ce cas (`PROJECTS must look like …`, aucun service ne
démarre). **Correctif** : noms en minuscules, `[a-z0-9][a-z0-9-]{0,63}`, séparés par des virgules sans
espace ([ajouter un projet](../ajouter-un-projet.md)).

## `Grafana <version> is not supported`

**Cause** : Grafana antérieur à 12.0. Mettre à jour le service Grafana.
