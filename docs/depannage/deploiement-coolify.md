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
Pour les ouvrir : `https://faro.example.com:12347`, `https://otlp.example.com:4318`.

**Vérification** : `curl -sI http://alloy-<uuid>.<wildcard>` ne répond plus par le service
(404 de Traefik).

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

## `config-guard: FAILED - no service will start`

**Symptôme** : aucun service ne démarre ; les logs de `config-guard` contiennent une ou plusieurs
lignes `config-guard: ERROR: …`.

**Cause** : un fichier de config écrit par Coolify manque, est vide, est un **dossier**
(régression connue de Coolify sur les montages `content:`) ou diffère de sa source ; ou une
variable vérifiée par `config-guard` (`IP_HASH_SALT`, `FARO_API_KEY`, `PROJECTS`,
`FARO_SERVICES`, `HOST_MAP`, `RESERVED_SUBDOMAINS`, `TENANT_HOST_REGEX`, `HOST_ENV`) a un format
invalide.

**Correctif** : lire la ligne `ERROR`, corriger la variable, ou pour un fichier, vérifier que le
compose déployé est le `docker-compose.yaml` généré à jour (`python3 scripts/check.py`), puis
**Redeploy**. Spike S4 de [`docs/spikes.md`](../spikes.md) pour le détail des fichiers.

**Vérification** : `config-guard: all checks passed`.
