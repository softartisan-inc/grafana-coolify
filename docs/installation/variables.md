# Variables d'environnement

[← Sommaire](../README.md)

Référence des variables du **package** (onglet **Environment Variables** de la ressource).
[`.env.example`](../../.env.example) fait foi : `scripts/check.py` vérifie qu'il correspond aux
`${VAR}` du compose. Colonne « Vérifiée par » : qui refuse une valeur invalide.

## Obligatoires

| Variable | Valeur | Vérifiée par |
|---|---|---|
| `IP_HASH_SALT` | `openssl rand -hex 24` (16 caractères `[A-Za-z0-9]` au moins). Le changer rend les anciennes empreintes d'IP impossibles à rapprocher des nouvelles. | `config-guard` |
| `FARO_API_KEY` | `openssl rand -hex 24`. Publique par nature (elle finit dans le code des pages), elle ne permet que d'envoyer. | `config-guard` |
| `GRAFANA_URL` | `http://grafana-<uuid-grafana>:3000` (adresse **interne**, voir [service Grafana](service-grafana.md)) | `grafana-setup` |
| `GRAFANA_SA_TOKEN` | `<GRAFANA_SA_TOKEN>` (`glsa_…`, rôle Admin) | `grafana-setup` |
| `LOKI_INTERNAL_URL` | `http://gc-loki:3100` (repli de dépannage : `http://loki:3100`) | `grafana-setup` |
| `TEMPO_INTERNAL_URL` | `http://gc-tempo:3200` (ou `http://tempo:3200`) | `grafana-setup` |
| `PROMETHEUS_INTERNAL_URL` | `http://gc-prometheus:9090` (ou `http://prometheus:9090`) | `grafana-setup` |

Une variable propre à `grafana-setup` vide ou invalide n'arrête **que** `grafana-setup` (code 1,
message clair) ; Loki, Tempo, Prometheus et Alloy démarrent quand même. Une variable vérifiée par
`config-guard` invalide empêche **tout** démarrage (`config-guard: FAILED - no service will start`).

Le package n'utilise pas `${VAR:?message}` : Coolify ne refuse pas le déploiement mais donne
le **texte du message** pour valeur (voir
[grafana-setup](../depannage/grafana-setup.md#valueerror-unknown-url-type)).

## Projets et Faro

| Variable | Valeur | Remarque |
|---|---|---|
| `PROJECTS` | `projet-a,projet-b` (minuscules, virgules **sans espace**) | Un dossier Grafana `gc-<projet>` chacun. Garder renseigné : vide, tout projet au bon format est accepté. |
| `FARO_SERVICES` | `web-app,desktop` | Fermé par défaut : vide, seuls les services de `HOST_MAP` passent. |
| `HOST_MAP` | `example.me=web-front:prod` | Hôte exact → service et env (logs Faro). |
| `RESERVED_SUBDOMAINS` | `www,api` | Jamais des tenants. |
| `TENANT_HOST_REGEX` | `^(?P<sub>[a-z0-9-]+?)(?P<dev>-dev)?\.example\.(me\|app)$` | Cocher **Is Literal?** (contient des `$`). |
| `FARO_RATE`, `FARO_BURST`, `FARO_MAX_PAYLOAD` | `100`, `200`, `5MiB` | Limites globales du récepteur. |

Tant que le point Faro est fermé (aucun domaine pour `alloy`), `HOST_MAP`,
`RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX` et `FARO_SERVICES` peuvent rester vides.

## Notifications et seuils

| Variable | Défaut | Remarque |
|---|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | vides | Tous deux ou aucun ([bot Telegram](../exploitation/alertes.md#bot-telegram)). |
| `ALERT_EMAILS` | vide | Obligatoire dès que Telegram est renseigné. Vide avec Telegram vide : les règles **ne notifient personne**. |
| `ALERT_ERROR_RATE` | `0.05` | 5 % de spans serveur en erreur. |
| `ALERT_P95_MS` | `1500` | Seuil de la latence p95, en ms, sur 5 min. |
| `ALERT_P95_MIN_CALLS` | `100` | Requêtes minimales d'un service sur 5 min pour que sa latence p95 soit évaluée (entier, 1 à 10 000 000). |
| `ALERT_SILENCE_MIN` | `15` | |
| `ALERT_DISK_PCT` | `80` | |
| `CARDINALITY_ALERT_THRESHOLD` | `200000` | |
| `HOST_ENV` | `prod` | Environnement que sert **le serveur** (label `env` de l'alerte Disque) ; `preprod` sur un serveur de recette. |

## Rétention et réglages

Une variable de cette section laissée **vide** prend son défaut : Coolify transmet la valeur vide
sans appliquer le repli du compose, Tempo, Loki et Prometheus appliquent donc le défaut
eux-mêmes ([spike S5](../spikes.md#variables-vidées-dans-coolify--le-repli-var-défaut-du-compose-ne-sapplique-pas)). Une valeur
renseignée mais mal formée ou nulle (`0`, `0h`, `0GB`) arrête le déploiement à `config-guard`.

| Variable | Défaut |
|---|---|
| `LOKI_RETENTION_PROD` / `LOKI_RETENTION_DEFAULT` | `720h` / `168h` (entre `24h` et `292y`, unités dans l'ordre `y w d h m s ms`. Loki refuse au démarrage une rétention de flux (`LOKI_RETENTION_PROD`) sous 24h ; `LOKI_RETENTION_DEFAULT` est aligné sur le même plancher, le minimum documenté par Loki. `config-guard` refuse les deux avant) |
| `TEMPO_RETENTION` | `168h` |
| `TEMPO_MAX_ACTIVE_SERIES` | `100000` (vide = `100000` ; `0`, qui voudrait dire « illimité », est refusé par `config-guard`) |
| `PROM_RETENTION_TIME` / `PROM_RETENTION_SIZE` | `90d` / `100GB` (durée non nulle jusqu'à `292y`, unités dans l'ordre `y w d h m s ms` ; taille entière non nulle sous `8EB`, unités `B KB MB GB TB PB EB` ou `KiB`… `EiB`, puissances de 2) |
| `PROM_ENABLE_FEATURES`, `ENABLE_EXEMPLARS` | vide, `false` (exemplars : activer les deux ensemble) |
| `GRAFANA_SETUP_MIRROR_URL` | vide (ce dépôt, au tag épinglé) |
| `ALLOY_INTERNAL_URL` | documentaire : `http://gc-alloy:4318` (ou `http://alloy:4318`) pour les applications du même hôte |

## À ne pas mettre ici

| Variable | Où | Pourquoi |
|---|---|---|
| `GF_SMTP_*` | service **Grafana** | Le package ne les lit pas ; elles finiraient dans ses logs de déploiement. |
| `SERVICE_FQDN_*` | nulle part | Coolify les crée quand un domaine est saisi sur le service. |
