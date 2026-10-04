# Documentation de l'opérateur

Guide de déploiement et d'exploitation de `grafana-coolify` sur un serveur Coolify, découpé en
pages courtes. Le [README du dépôt](../README.md) reste la référence complète (masquage, envoi
des données, développement) ; ces pages en reprennent le chemin pratique et y ajoutent **tous les
incidents rencontrés lors du premier déploiement réel**, avec leur correctif.

> Dépôt public : aucune page ne contient de vraie valeur. Les emplacements `<GRAFANA_SA_TOKEN>`,
> `<IP_PUBLIQUE>`, `<uuid>`… sont à remplacer par vos valeurs, **jamais** à recopier dans un
> ticket ou une discussion.

## Ordre de lecture pour un premier déploiement

1. [Prérequis](installation/prerequis.md) : serveur, accès à l'hôte, accès sortant.
2. [Service Grafana](installation/service-grafana.md) : réseau `coolify`, compte de service,
   SMTP. À faire **avant** le premier déploiement du package.
3. [Middlewares Traefik](installation/traefik.md) : configuration dynamique, htpasswd, regex des
   origines.
4. [Déploiement Coolify pas à pas](installation/deploiement-coolify.md) : ressource, domaines,
   noms internes, redéploiement, contrôle final.
5. [Variables d'environnement](installation/variables.md) : tableau de référence.
6. [Durcissement du serveur](serveur/durcissement.md) : fail2ban et limites de sshd, à faire dès
   que les déploiements échouent en SSH (et de toute façon avant la production).

## Exploitation

- [Ajouter un projet](ajouter-un-projet.md) : prod et préprod d'un même projet, ou un nouveau
  projet.
- [Alertes et notifications](exploitation/alertes.md) : routage Telegram / email, bot Telegram,
  tests.
- [Tableaux de bord](exploitation/tableaux-de-bord.md) : ce qui est créé, comment le modifier.
- [Rotation des secrets](exploitation/rotation-des-secrets.md) : procédure par secret, en routine
  ou après une fuite.
- [Vérifications](exploitation/verifications.md) : `security.py`, `smoke.py`, `notify_test.py`,
  spikes, sonde externe.

## Dépannage

[Index des erreurs par message exact](depannage/README.md), puis une page par domaine :

- [Déploiement Coolify](depannage/deploiement-coolify.md) (SSH, terminal, domaines, Sentinel) ;
- [grafana-setup](depannage/grafana-setup.md) ;
- [Sources de données](depannage/sources-de-donnees.md) ;
- [Traefik](depannage/traefik.md) ;
- [Notifications](depannage/notifications.md) ;
- [Sécurité et fuites](depannage/securite-fuites.md).

## Autres documents

- [Spikes Coolify](spikes.md) : vérifications à jouer sur une vraie instance (S0 à S6), avec le
  tableau des résultats.
- [Spécification](superpowers/specs/2026-09-27-grafana-coolify-design.md) et plans
  ([A, ingestion](superpowers/plans/2026-09-28-plan-a-ingestion.md),
  [B, exploitation](superpowers/plans/2026-10-01-plan-b-exploitation.md)).

## Conventions de ces pages

- **Hôte** : une session shell sur le serveur lui-même (SSH depuis votre poste, ou console web de
  l'hébergeur). Le terminal d'une ressource Coolify ouvre un **conteneur**, pas l'hôte : voir
  [« docker: not found »](depannage/deploiement-coolify.md#docker-not-found-ou-sshd-not-found).
- `<uuid>` : identifiant de la ressource Coolify (visible dans son URL et dans le nom des
  conteneurs, `loki-<uuid>-<horodatage>`).
- Les commandes sont données sur **une seule ligne** chaque fois que possible : un bloc
  `<<'EOF'` collé avec des espaces devant `EOF` bloque le terminal (voir
  [heredoc](depannage/deploiement-coolify.md#le-terminal-affiche--et-attend-heredoc)).
