# Dépannage : index par symptôme

[← Sommaire](../README.md)

Chercher le message exact (Ctrl + F) dans la première colonne. Chaque entrée suit le même
schéma : **symptôme**, **cause**, **correctif**, **vérification**. Tous ces cas ont été
rencontrés lors du premier déploiement réel, sauf mention « prévention ».

## Déploiement Coolify et serveur

| Message ou symptôme | Où | Page |
|---|---|---|
| `kex_exchange_identification: read: Connection reset by peer` | log de déploiement | [SSH coupé](deploiement-coolify.md#kex_exchange_identification-read-connection-reset-by-peer) |
| `Session open refused by peer` | log de déploiement | [SSH coupé](deploiement-coolify.md#kex_exchange_identification-read-connection-reset-by-peer) |
| `Failed to write deployment configurations: … exit code 255` | log de déploiement | [SSH coupé](deploiement-coolify.md#kex_exchange_identification-read-connection-reset-by-peer) |
| `docker: not found`, `sshd: not found`, `No journal files were found.` | terminal | [mauvais terminal](deploiement-coolify.md#docker-not-found-ou-sshd-not-found) |
| Le terminal affiche `>` et attend | terminal | [heredoc](deploiement-coolify.md#le-terminal-affiche--et-attend-heredoc) |
| `/etc/fail2ban/jail.d/…: No such file or directory` | hôte | [fail2ban non installé](../serveur/durcissement.md#a-fail2ban) |
| Domaines `alloy-<uuid>.<wildcard>` remplis sans demande | Coolify | [domaines générés](deploiement-coolify.md#faro-et-otlp-exposés-sans-domaine-demandé) |
| Sentinel **Out of sync** | Coolify | [Sentinel](deploiement-coolify.md#sentinel-out-of-sync) |
| `config-guard: FAILED - no service will start` (prévention) | logs `config-guard` | [config-guard](deploiement-coolify.md#config-guard-failed---no-service-will-start) |
| `ENABLE_EXEMPLARS=` (vide) dans `docker inspect`, conteneur `Up` sans erreur | Coolify | [variable vide](deploiement-coolify.md#variable-vide-transmise-par-coolify-le-défaut-du-compose-est-ignoré) |

## `grafana-setup`

| Message | Page |
|---|---|
| `ValueError: unknown url type: 'GRAFANA_URL is required/api/health'` | [valeur `:?`](grafana-setup.md#valueerror-unknown-url-type) |
| `GRAFANA_URL=GRAFANA_URL is required` dans les variables | [valeur `:?`](grafana-setup.md#valueerror-unknown-url-type) |
| `grafana-setup: ERROR: GRAFANA_URL is required`, `… must be an http:// or https:// URL` | [valeur `:?`](grafana-setup.md#valueerror-unknown-url-type) |
| `grafana-setup: ERROR: Grafana not healthy after 120s` | [réseau de Grafana](grafana-setup.md#grafana-setup-error-grafana-not-healthy-after-120s) |
| `curl: (6) Could not resolve host: grafana-<uuid>` | [réseau de Grafana](grafana-setup.md#grafana-setup-error-grafana-not-healthy-after-120s) |
| `Grafana unreachable (…)`, `invalid request to …` (prévention) | [URL de Grafana](grafana-setup.md#grafana-unreachable--ou-invalid-request-to-) |
| `HTTP 401` / `HTTP 403` (prévention) | [jeton](grafana-setup.md#http-401--http-403-sur-api) |
| `HTTP 404`, `SHA-256 … differs from the pinned …` (prévention) | [contenu téléchargé](grafana-setup.md#http-404-ou-sha-256-differs-au-téléchargement) |
| `legacy alerting provisioning API unavailable` (prévention) | [API d'alerting](grafana-setup.md#legacy-alerting-provisioning-api-unavailable) |

## Sources de données (Grafana)

| Message | Page |
|---|---|
| `lookup prometheus-<uuid> on 127.0.0.11:53: no such host` | [nom suffixé](sources-de-donnees.md#lookup-prometheus-uuid--no-such-host) |
| `Unable to connect with Loki. Please check the server logs for more details.` | [nom suffixé](sources-de-donnees.md#lookup-prometheus-uuid--no-such-host) |
| `lookup gc-prometheus … no such host` alors que le déploiement a réussi | [option activée sur le package](sources-de-donnees.md#les-alias-gc--ne-résolvent-pas-option-activée-sur-le-package) |
| Données absentes ou d'une autre stack, test vert (prévention) | [collision](sources-de-donnees.md#la-source-répond-mais-interroge-la-mauvaise-stack-collision) |

## Traefik

| Message | Page |
|---|---|
| `Found unknown escape character "\."` | [regex entre guillemets](traefik.md#found-unknown-escape-character) |
| `middleware "gc-…@file" does not exist` (prévention) | [fichier dynamique](traefik.md#middleware-gc-file-does-not-exist) |
| `401` OTLP avec le bon mot de passe (prévention) | [htpasswd](traefik.md#otlp--401-avec-le-bon-mot-de-passe) |

## Notifications

| Message ou symptôme | Page |
|---|---|
| `{"ok":true,"result":[]}` sur `getUpdates` | [bot muet](notifications.md#getupdates-renvoie-une-liste-vide) |
| Terminal muet après `read -rs` | [deux commandes](notifications.md#le-terminal-semble-bloqué-après-read--rs) |
| `notifications: skipped (ALERT_EMAILS is empty: the rules notify nobody)` | [personne n'est prévenu](notifications.md#les-règles-se-déclenchent-mais-personne-nest-prévenu) |
| `GF_SMTP_*` présents dans le package | [SMTP](notifications.md#gc-email--le-test-échoue-ou-rien-narrive) |

## Sécurité

| Symptôme | Page |
|---|---|
| Ligne `[CMD] … base64 …` copiée hors de Coolify | [fuite du `.env`](securite-fuites.md#les-logs-de-déploiement-contiennent-le-env-en-clair) |
| `Accepted password for root` dans les logs SSH | [root par mot de passe](securite-fuites.md#connexion-root-par-mot-de-passe-ouverte-sur-internet) |
