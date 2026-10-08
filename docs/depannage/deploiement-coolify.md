# Dépannage : déploiement Coolify

[← Index du dépannage](README.md)

## `kex_exchange_identification: read: Connection reset by peer`

**Symptôme** : le déploiement échoue avant ou pendant `docker compose up -d` :

```
SSH connection failed. Retrying... (Attempt 1/3, waiting 2s)
Error: kex_exchange_identification: read: Connection reset by peer
Warning: Failed to write deployment configurations: Command execution failed (exit code 255): mkdir -p /data/coolify/applications/<uuid>
Deployment failed: Command execution failed (exit code 255): docker exec … docker compose … up -d
```

Signe avant-coureur dans un déploiement précédent :
`mux_client_request_session: session request failed: Session open refused by peer`.

**Cause** : sshd de l'hôte coupe les connexions de Coolify dès l'échange de clés. Coolify pilote
Docker par SSH, même sur son propre serveur. Sur le serveur concerné, des robots tentaient des
mots de passe en continu et `MaxStartups 10:30:100` (défaut) faisait refuser au hasard les
connexions au-delà de 10 en attente. Autres causes possibles : fail2ban ou un pare-feu qui a
banni Coolify, serveur saturé (charge et mémoire étaient normales ici).

**Correctif** :

1. Réessayer une fois : **Servers** → le serveur → **Validate Server**, puis **Redeploy**.
2. Si ça recommence : [diagnostic, fail2ban et limites de sshd](../serveur/durcissement.md),
   **sur l'hôte**.

**Vérification** : `sshd -T | grep -Ei 'maxstartups|maxsessions'` affiche
`maxstartups 100:30:300` et `maxsessions 50` ; **Validate Server** passe ; le redéploiement va
au bout.

Un déploiement interrompu à `up -d` a pu retirer les anciens conteneurs : la stack peut être
arrêtée jusqu'au redéploiement réussi.

## `docker: not found` ou `sshd: not found`

**Symptôme** : `sh: 2: docker: not found`, `sshd: not found`,
`No journal files were found.`, invite `$` d'un shell `sh`.

**Cause** : le terminal ouvert depuis une ressource Coolify est un shell **dans un conteneur**,
pas sur l'hôte. `uptime` y affiche pourtant les jours de l'hôte (noyau partagé), ce qui trompe.

**Correctif** : SSH depuis votre poste (`ssh root@<IP_PUBLIQUE>`) ou console web de l'hébergeur
([accès à l'hôte](../installation/prerequis.md#accès-à-lhôte)). Pour lire simplement les logs
d'un service, l'onglet **Logs** de la ressource suffit.

**Vérification** : `ls /data/coolify && which sshd` liste `applications`, `proxy`, `ssh`… puis
`/usr/sbin/sshd`.

## Le terminal affiche `>` et attend (heredoc)

**Symptôme** : après avoir collé une commande `cat > fichier <<'EOF'` … `EOF`, le terminal
affiche `>` indéfiniment.

**Cause** : un heredoc ne se termine que sur une ligne contenant **exactement** `EOF`, sans
espace avant ni caractère après. Le copier-coller depuis une page ou un message ajoute souvent
une indentation (`  EOF`), parfois un `;`.

**Correctif** : **Ctrl + C** (rien n'est écrit), puis utiliser la forme sur une ligne :

```bash
printf '%s\n' 'ligne 1' 'ligne 2' > /chemin/du/fichier
```

**Vérification** : `cat /chemin/du/fichier`.

## Faro et OTLP exposés sans domaine demandé

**Symptôme** : après le premier déploiement, le champ **Domains** d'`alloy` et
d'`alloy-gateway` contient `http://alloy-<uuid>.<wildcard>` et
`http://alloy-gateway-<uuid>.<wildcard>` ; les logs de déploiement montrent
`SERVICE_FQDN_ALLOY=…` et `SERVICE_FQDN_ALLOY_GATEWAY=…`.

**Cause** : avec un domaine wildcard sur le serveur, Coolify génère d'office un domaine pour
chaque service qui en déclare un. Les points Faro et OTLP sont alors ouverts sur Internet (et
sans le port attendu).

**Correctif** : vider **Domains** d'`alloy-gateway` et d'`alloy` tant qu'ils ne servent pas,
**Save**, **Redeploy** ([étape 5](../installation/deploiement-coolify.md#5-retirer-les-domaines-générés-doffice)).
Pour les ouvrir : domaine sans port et **Internal port** `12347` (Faro) ou `4318` (OTLP)
([ouvrir Faro ou OTLP](../installation/deploiement-coolify.md#ouvrir-faro-ou-otlp)).

**Vérification** : `curl -sI http://alloy-<uuid>.<wildcard>` ne répond plus par le service
(404 de Traefik).

## Faro : `500 Internal Server Error` sur `/collect`

**Symptôme** : le domaine Faro est en place, mais `POST https://faro.example.com/collect`
répond `500` (`Internal Server Error`), sans aucune ligne dans les logs d'`alloy`. Le SDK Faro
des applications n'envoie rien.

**Cause** : Traefik route le domaine vers le mauvais port du conteneur `alloy`, en général
`4317` (OTLP **gRPC**), qui ne parle pas HTTP/1 : Traefik renvoie `500`. Coolify choisit le
port dans cet ordre (vérifié sur v4.3.23) : le port écrit dans l'URL, puis le champ
**Internal port** enregistré pour ce domaine, puis le port par défaut du service : pour une
**Application** (ce dépôt), le premier port tcp de `expose` ; pour un **Service**, le suffixe
de `SERVICE_FQDN_<SERVICE>_<PORT>`, sinon le premier port de `expose`. Avant ce correctif, `4317` était le premier port
de `expose` d'`alloy` ; un **Internal port** enregistré à `4317` reste en place même après la
mise à jour du compose.

**Vérification du port routé**, sur l'hôte :

```bash
docker inspect $(docker ps --format '{{.Names}}' | grep '^alloy-<uuid>' | head -1) | grep loadbalancer.server.port
```

Attendu : `12347`. Toute autre valeur (`4317`, `4318`, `12345`) confirme la cause.

**Correctif** ([ouvrir Faro ou OTLP](../installation/deploiement-coolify.md#ouvrir-faro-ou-otlp)) :

1. Service `alloy` → **Domains** : `https://faro.example.com` (sans port), **Internal port**
   `12347` → **Save** (confirmer la fenêtre d'avertissement sur le port si elle s'ouvre : sans
   confirmation, Coolify restaure en silence l'ancienne valeur) → **Redeploy** de la ressource
   entière (pas **Restart** : les labels ne seraient pas régénérés).
2. Si le champ revient à `4317` ou si le label ne change pas : supprimer le domaine → **Save**
   → **Redeploy**, puis ressaisir le domaine et **Internal port** `12347` → **Save** →
   **Redeploy**.
3. Versions plus anciennes de Coolify (sans champ **Internal port**) :
   `https://faro.example.com:12347` dans **Domains**.

Même logique pour `alloy-gateway` : port `4318` (le seul que Traefik doit joindre).

**Vérification** : le label vaut `12347`, et une requête depuis une origine autorisée n'est
plus en `500` (un `400` ou `401` sans clé Faro valide prouve qu'Alloy répond). Derrière
Cloudflare, des `429` en série ensuite relèvent de la
[limite de débit](../installation/traefik.md#limite-de-débit) (`ipStrategy.depth: 1`).

## Sentinel Out of sync

**Symptôme** : **Validate Server** réussit, mais le serveur affiche Sentinel **Out of sync**.

**Cause** : Sentinel (conteneur `coolify-sentinel`) remonte l'état des conteneurs et les
métriques vers Coolify. Après des coupures SSH, Coolify n'a pas pu le relancer ou le mettre à
jour. Cela **ne bloque pas** les déploiements, mais l'interface peut afficher des états
inexacts.

**Correctif** : Coolify → **Servers** → le serveur → onglet **Sentinel** → **Restart** (ou
désactiver puis réactiver). Ne touche pas à la stack.

**Vérification** : l'état repasse en synchronisé ; `docker ps --filter name=coolify-sentinel`
le montre `Up`.

## `service "config-guard" didn't complete successfully: exit 1`

**Symptôme** : le log de déploiement de Coolify s'arrête sur
`service "config-guard" didn't complete successfully: exit 1`. Ce log ne montre que l'état
renvoyé par `docker compose`, jamais la sortie des conteneurs : la raison n'y figure pas.

**Cause** : `config-guard` a refusé la configuration (voir l'entrée suivante). Tant qu'il échoue,
**aucun service ne démarre** : l'ingestion des logs, métriques et traces est arrêtée.

**Correctif** : lire la raison dans les logs du conteneur, **sur l'hôte** (le conteneur est
arrêté, d'où `docker ps -a`) :

```bash
docker logs $(docker ps -a --format '{{.Names}}' | grep -E '^config-guard-[a-z0-9]+-[0-9]+$' | head -1) 2>&1 | grep ERROR
```

Exemple réel (`TEMPO_MAX_ACTIVE_SERIES=0` saisi dans Coolify) :

```
config-guard: ERROR: TEMPO_MAX_ACTIVE_SERIES must be a positive integer, 0 means no limit (empty: 100000)
```

Remettre une valeur valide (celle de `.env.example`, ou vide pour le défaut) dans l'onglet
**Environment Variables**, puis **Redeploy** sans attendre : la stack ne collecte rien tant que le
déploiement échoue.

**Vérification** : le déploiement va au bout ; la même commande `docker logs` affiche
`config-guard: all checks passed` (sans `grep ERROR`) ; les conteneurs sont `Up`.

## `config-guard: FAILED - no service will start`

**Symptôme** : aucun service ne démarre ; les logs de `config-guard` contiennent une ou plusieurs
lignes `config-guard: ERROR: …`.

**Cause** : un fichier de config écrit par Coolify manque, est vide, est un **dossier**
(régression connue de Coolify sur les montages `content:`) ou diffère de sa source ; ou une
variable vérifiée par `config-guard` (`IP_HASH_SALT`, `FARO_API_KEY`, `PROJECTS`,
`FARO_SERVICES`, `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX`, `HOST_ENV`, les
rétentions, `TEMPO_MAX_ACTIVE_SERIES`, `ENABLE_EXEMPLARS`) a un format invalide ou une valeur
nulle, ou une rétention Loki (`LOKI_RETENTION_PROD`, `LOKI_RETENTION_DEFAULT`) est inférieure à
`24h`, supérieure à `292y` ou mal ordonnée (`1h1d`). Loki refuse au démarrage une rétention de
flux (`LOKI_RETENTION_PROD`) sous 24h ; `LOKI_RETENTION_DEFAULT` est aligné sur le même
plancher, le minimum documenté par Loki. La même règle de durée vaut pour
`PROM_RETENTION_TIME`, sans plancher : non nulle, au plus `292y`, unités `y w d h m s ms` chacune
une seule fois et dans cet ordre (`90d`, `2w3d` ; pas `1h1d` ni `1d1d`). `PROM_RETENTION_SIZE` est
un entier non nul suivi d'une seule unité, sous `8EB` : `B KB MB GB TB PB EB` ou `KiB`… `EiB`
(puissances de 2 ; `100GB`, pas `1.5GB`, `1GB512MB` ni `100gb`). Les règles et les défauts de
chaque variable sont dans le [tableau des variables](../installation/variables.md#rétention-et-réglages).

**Correctif** : lire la ligne `ERROR`, corriger la variable, ou pour un fichier, vérifier que le
compose déployé est le `docker-compose.yaml` généré à jour (`python3 scripts/check.py`), puis
**Redeploy**. Spike S4 de [`docs/spikes.md`](../spikes.md) pour le détail des fichiers.

**Vérification** : `config-guard: all checks passed`.

## Variable vide transmise par Coolify (le défaut du compose est ignoré)

**Symptôme** : une variable vidée dans l'onglet **Environment Variables** (variable gardée,
valeur vide) arrive vide dans le conteneur :
`docker inspect tempo-<uuid>-<horodatage> --format '{{range .Config.Env}}{{println .}}{{end}}'` affiche
`ENABLE_EXEMPLARS=` au lieu de `ENABLE_EXEMPLARS=false`. Le conteneur reste `Up`, sans message.

**Cause** : Coolify substitue lui-même les variables et transmet la valeur vide : le repli
`${VAR:-défaut}` du compose ne s'applique pas (spike S5). Avant le correctif, une rétention Tempo
vide valait `0s` (traces supprimées aussitôt) et un `TEMPO_MAX_ACTIVE_SERIES` vide valait `0`
(aucun plafond de séries) ; sur une rétention vide, Loki redémarrait en boucle ; sur le banc,
Prometheus aussi. Sur Coolify, Prometheus n'était pas touché : ses options de rétention étaient
dans `command:`, interpolées par Docker Compose depuis le `.env` écrit par Coolify, où
`${X:-90d}` couvre la valeur vide. Les variables de Loki, elles, passent par `environment:`.

**Correctif** : aucun à faire sur une version à jour : le défaut de `.env.example` est appliqué
par Tempo et Loki (dans leur configuration), par `config/prometheus/start.sh` pour Prometheus,
par Alloy et par `grafana-setup`. Sur un déploiement plus ancien, supprimer la variable ou lui
remettre la valeur de `.env.example`, puis **Redeploy**. Ne jamais saisir `0` : `config-guard`
le refuse.

**Vérification** : la variable reste vide dans `docker inspect` (comportement de Coolify), mais
la configuration effective porte le défaut, par exemple
`docker run --rm --network container:tempo-<uuid>-<horodatage> alpine:3.22 wget -qO- http://localhost:3200/status/config | grep max_active_series`
affiche `max_active_series: 100000`. Procédure complète : spike S5 de
[`docs/spikes.md`](../spikes.md#variables-vidées-dans-coolify--le-repli-var-défaut-du-compose-ne-sapplique-pas).
