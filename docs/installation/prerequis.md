# Prérequis

[← Sommaire](../README.md)

## Serveur

- Un serveur Coolify (v4) avec son proxy **Traefik**.
- Le service Coolify **Grafana 12.0 ou plus récent** (variante PostgreSQL recommandée), déjà
  installé : le package ne contient pas Grafana, il l'alimente.
- Environ **6 Go de RAM** libres (somme des `mem_limit` : ~5,7 Gio) et de la place disque pour
  90 jours de métriques (`PROM_RETENTION_SIZE=100GB` par défaut).

## Accès à l'hôte

Plusieurs diagnostics demandent `docker`, `sshd` ou `journalctl` **sur l'hôte**. Prévoir l'un de
ces accès :

| Accès | Quand l'utiliser |
|---|---|
| SSH depuis votre poste (`ssh root@<IP_PUBLIQUE>`) | Cas normal. |
| Console web de l'hébergeur (VNC, « rescue console »…) | Quand SSH lui-même est en panne ou que vous êtes banni par fail2ban. |
| Terminal d'une ressource dans Coolify | **Jamais pour ces diagnostics** : il ouvre un shell dans un conteneur (pas de `docker`, pas de `sshd`). |

Vérifier qu'on est bien sur l'hôte :

```bash
ls /data/coolify && which sshd docker
```

Attendu : les dossiers `applications`, `proxy`, `ssh`… puis deux chemins (`/usr/sbin/sshd`,
`/usr/bin/docker`).

## Accès sortant vers GitHub

`grafana-setup` télécharge les tableaux de bord et les alertes depuis
`raw.githubusercontent.com`, au tag épinglé. **Sur l'hôte** (pas depuis votre poste : c'est le
serveur qui doit joindre GitHub) :

```bash
curl -sI https://raw.githubusercontent.com/softartisan-inc/grafana-coolify/grafana-setup-content-v1/README.md | head -1
```

Attendu : `HTTP/2 200`. Sans `curl` :
`wget -S --spider <même URL> 2>&1 | grep 'HTTP/'`. Sans accès à Internet, prévoir un miroir
(`GRAFANA_SETUP_MIRROR_URL`, voir le README, « Source des tableaux et des alertes »).

## Ce qu'il faut avoir sous la main

| Élément | Où le créer | Page |
|---|---|---|
| Nom du conteneur Grafana, réseau `coolify` activé | Coolify, service Grafana | [Service Grafana](service-grafana.md) |
| Jeton du compte de service (`glsa_…`) | Grafana | [Service Grafana](service-grafana.md) |
| Identifiants SMTP (facultatif) | fournisseur d'email | [Service Grafana](service-grafana.md#3-smtp) |
| Jeton du bot Telegram et identifiant du groupe | @BotFather | [Alertes](../exploitation/alertes.md#bot-telegram) |
| Une ligne htpasswd par projet émetteur distant | votre poste | [Traefik](traefik.md) |
| Deux valeurs aléatoires (`IP_HASH_SALT`, `FARO_API_KEY`) | `openssl rand -hex 24` | [Variables](variables.md) |

Ranger chaque secret dans un gestionnaire de mots de passe : ils ne doivent apparaître que dans
Coolify.

## Spikes

Les vérifications de [`docs/spikes.md`](../spikes.md) se jouent une fois sur le serveur cible.
**S6** (vraies notifications) est un prérequis bloquant de la mise en production.

Suite : [Service Grafana](service-grafana.md).
