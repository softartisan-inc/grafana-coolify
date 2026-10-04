# Durcissement du serveur

[← Sommaire](../README.md)

Un serveur Coolify exposé sur Internet reçoit en continu des tentatives de connexion SSH par
force brute. Or **Coolify pilote Docker par SSH**, même sur son propre serveur, et ouvre de
nombreuses connexions pendant un déploiement. Avec les limites par défaut de sshd, les robots
occupent les places et les connexions de Coolify sont coupées
([`kex_exchange_identification`](../depannage/deploiement-coolify.md#kex_exchange_identification-read-connection-reset-by-peer)).

Tout se fait **sur l'hôte**, en root ([accès](../installation/prerequis.md#accès-à-lhôte)).

> **Avant de commencer** : garder la session SSH actuelle **ouverte** pendant toutes les
> modifications et tester dans une **deuxième** session. La console web de l'hébergeur permet de
> reprendre la main en cas d'erreur.

## Diagnostic

```bash
fail2ban-client status sshd 2>/dev/null || echo "pas de fail2ban"
sshd -T | grep -Ei 'maxstartups|maxsessions'
journalctl -u ssh -u sshd --since "30 min ago" --no-pager | tail -30
```

Signe typique : des `Invalid user …` / `Failed password for root from <IP>` toutes les quelques
secondes, et `maxstartups 10:30:100` (au-delà de 10 connexions non authentifiées, sshd refuse
des connexions au hasard).

## A. fail2ban

```bash
apt update && apt install -y fail2ban
printf '%s\n' '[sshd]' 'enabled  = true' 'maxretry = 5' 'findtime = 10m' 'bantime  = 1h' 'ignoreip = 127.0.0.1/8 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16' > /etc/fail2ban/jail.d/sshd-local.conf
cat /etc/fail2ban/jail.d/sshd-local.conf
systemctl enable --now fail2ban && systemctl restart fail2ban
fail2ban-client status sshd
```

- `ignoreip` exempte **seulement les réseaux internes** : Coolify se connecte depuis les
  réseaux Docker. N'y mettre votre IP publique que si elle est **fixe** : une IP dynamique
  passerait un jour à quelqu'un d'autre. Une IP absente de `ignoreip` n'est pas bloquée, elle
  est seulement bannissable (5 échecs en 10 minutes, ban d'une heure).
- `/etc/fail2ban/jail.d/…: No such file or directory` : fail2ban n'est pas installé (relancer
  la ligne `apt`).
- Commandes sur une ligne avec `printf` : un bloc `<<'EOF'` collé avec des espaces bloque le
  terminal ([heredoc](../depannage/deploiement-coolify.md#le-terminal-affiche--et-attend-heredoc)).

Attendu : `Status for the jail: sshd`, puis des adresses dans `Banned IP list` au bout de
quelques minutes.

Se débannir : attendre une heure, ou depuis la console de l'hébergeur (ou une autre connexion)
`fail2ban-client set sshd unbanip <VOTRE_IP>`.

## B. Limites de sshd

```bash
printf '%s\n' 'MaxStartups 100:30:300' 'MaxSessions 50' > /etc/ssh/sshd_config.d/99-coolify-limits.conf
sshd -t && systemctl reload ssh
sshd -T | grep -Ei 'maxstartups|maxsessions'
```

`sshd -t` valide la configuration **avant** le rechargement : en cas d'erreur, sshd n'est pas
rechargé. Attendu : `maxsessions 50` et `maxstartups 100:30:300`. Puis ouvrir une deuxième
session SSH pour vérifier l'accès avant de fermer la première.

## C. Revalider Coolify

Coolify → **Servers** → le serveur → **Validate Server**, puis **Redeploy** de la ressource.
Si Sentinel affiche **Out of sync** : voir
[Sentinel](../depannage/deploiement-coolify.md#sentinel-out-of-sync).

## D. SSH par clé uniquement (à faire plus tard)

Mesure la plus efficace : sans mot de passe, plus rien à deviner, et plus de risque de se faire
bannir par une faute de frappe. C'est aussi la plus risquée (perte d'accès si la clé n'est pas
en place) : à faire à tête reposée.

1. Depuis votre poste : `ssh-keygen -t ed25519`, puis `ssh-copy-id root@<IP_PUBLIQUE>`.
2. Vérifier dans une **nouvelle** session que la connexion par clé passe sans mot de passe.
3. `printf '%s\n' 'PasswordAuthentication no' 'KbdInteractiveAuthentication no' 'PermitRootLogin prohibit-password' > /etc/ssh/sshd_config.d/98-keys-only.conf`
4. `sshd -t && systemctl reload ssh`, puis nouvelle session de test **avant** de fermer
   l'ancienne.

Vérifier que la clé de Coolify (**Keys & Tokens** → **Private Keys**) est bien celle qu'il utilise
pour ce serveur : elle est déjà une clé, elle n'est pas touchée par ce réglage.
