# Dépannage : sécurité et fuites

[← Index du dépannage](README.md)

## Les logs de déploiement contiennent le `.env` en clair

**Symptôme** : dans les logs de **déploiement** Coolify, une ligne de la forme

```
[CMD]: docker exec <id> bash -c 'echo '\''<longue chaîne base64>'\'' | base64 -d | tee /artifacts/<id>/.env > /dev/null'
```

**Cause** : Coolify transmet le fichier `.env` de la ressource **encodé en base64** (pas
chiffré) dans la ligne de commande. N'importe qui le décode en une commande. Il contient toutes
les variables de la ressource : `GRAFANA_SA_TOKEN`, `TELEGRAM_BOT_TOKEN`, `FARO_API_KEY`,
`IP_HASH_SALT`, et toute variable ajoutée par erreur (`GF_SMTP_PASSWORD` mis dans le package au
lieu du service Grafana, par exemple).

**Règles** :

- Ne **jamais** copier les logs de déploiement dans un ticket, une discussion, un assistant ou
  une PR. Pour demander de l'aide, envoyer les logs de **service** (onglet **Logs** :
  `config-guard`, `grafana-setup`), qui ne contiennent pas de secret, ou les lignes du log de
  déploiement **sans** la ligne `[CMD] … base64 …`.
- Coolify conserve aussi ces logs dans son historique de déploiements : limiter qui a accès à
  l'interface Coolify.

**Si c'est arrivé** : considérer comme compromis tous les secrets présents dans ce `.env` et
appliquer la [rotation des secrets](../exploitation/rotation-des-secrets.md), en commençant par
les plus graves (jeton Grafana, clé SMTP, jeton du bot). Supprimer le message partagé si
possible : cela ne dispense pas de la rotation.

**Vérification** : l'ancien jeton Grafana n'apparaît plus dans **Service accounts** ; l'ancienne
clé SMTP est révoquée ; `grafana-setup: done` avec les nouvelles valeurs ; tests des deux
points de contact réussis.

## Faro ou OTLP ouverts sans le vouloir

Voir [domaines générés d'office](deploiement-coolify.md#faro-et-otlp-exposés-sans-domaine-demandé).
Tant qu'un point est ouvert sans être utilisé, il accepte des données de quiconque détient la clé
Faro (publique) ou, pour OTLP, un mot de passe htpasswd.

## Connexion root par mot de passe ouverte sur Internet

**Symptôme** : `journalctl -u ssh` montre des `Accepted password for root from …` et des
centaines de `Failed password` / `Invalid user`.

**Correctif** : fail2ban et limites de sshd tout de suite, puis SSH par clé uniquement
([durcissement](../serveur/durcissement.md)).

## Avant de publier quoi que ce soit dans ce dépôt

Le dépôt est **public**. Avant un commit qui touche la documentation ou un exemple, rechercher
les fuites :

```bash
git diff --cached | grep -nE 'glsa_|xkeysib|xsmtpsib|\$apr1\$[^C]|\$2y\$|[A-Za-z0-9+/=]{60,}|[0-9a-f]{40,}|@gmail\.com'
```

Toute correspondance doit être un emplacement (`<GRAFANA_SA_TOKEN>`) ou un exemple manifeste
(`example.com`, `203.0.113.10`, `$apr1$CHANGEME$…`).
