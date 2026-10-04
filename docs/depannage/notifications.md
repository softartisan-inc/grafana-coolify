# Dépannage : notifications

[← Index du dépannage](README.md)

## `getUpdates` renvoie une liste vide

**Symptôme** : `{"ok":true,"result":[]}`.

**Cause** : le jeton est valide, mais Telegram n'a rien transmis au bot : bot absent du groupe,
ou aucun message qui le cite depuis son ajout. Par défaut un bot ne voit pas les messages
ordinaires d'un groupe (`can_read_all_group_messages: false` dans `getMe`).

**Correctif** :

1. `curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"` : noter `username`.
2. Vérifier que le bot est membre du groupe, puis y envoyer `/start@<username>`.
3. Relancer `getUpdates` (même terminal : la variable est toujours là).
4. Toujours vide : retirer le bot du groupe, le rajouter, relancer aussitôt.

**Vérification** : `"chat":{"id":-…` apparaît ; puis `unset TELEGRAM_BOT_TOKEN`.

## Le terminal semble bloqué après read -rs

`read -rs TELEGRAM_BOT_TOKEN` et `curl …` sont **deux** commandes. Après la première, le
terminal attend : coller le jeton puis Entrée. Rien ne s'affiche, c'est voulu.

## Les alertes Telegram se sont arrêtées sans erreur

**Cause probable** : le groupe a été converti en supergroupe ; son identifiant a changé
(`-100…`). **Correctif** : relire l'identifiant ([bot Telegram](../exploitation/alertes.md#bot-telegram)),
mettre à jour `TELEGRAM_CHAT_ID`, redéployer, tester `gc-telegram`.

## `gc-email` : le test échoue ou rien n'arrive

**Causes** :

- `GF_SMTP_*` mis dans le **package** au lieu du service Grafana : le package ne les lit pas
  ([service Grafana](../installation/service-grafana.md#3-smtp)) ;
- Grafana non redémarré après l'ajout des variables ;
- adresse d'expédition non validée chez le fournisseur (Brevo : expéditeur validé ou domaine
  authentifié) ;
- message classé en spam.

**Vérification** : point de contact Email temporaire → **Test** ; puis `gc-email` → **Test**.

## Les règles se déclenchent mais personne n'est prévenu

**Symptôme** : log de `grafana-setup` :
`grafana-setup: notifications: skipped (ALERT_EMAILS is empty: the rules notify nobody)`.

**Cause** : `ALERT_EMAILS` vide (et Telegram vide) : la politique par défaut envoie au récepteur
vide de Grafana. **Correctif** : renseigner `ALERT_EMAILS`, redéployer.

## Une alerte critique de préprod arrive par email, pas sur Telegram

Comportement voulu : Telegram ne reçoit que `critical` **et** `env=prod`. L'alerte Disque prend
`env` = `HOST_ENV` : sur un serveur de recette (`HOST_ENV=preprod`), elle part par email.

## `notify_test.py` échoue à se connecter

**Cause** : `GRAFANA_URL` interne (`http://grafana-<uuid>:3000`) utilisé depuis un poste hors du
réseau `coolify`. **Correctif** : depuis votre poste, passer l'URL **publique** de Grafana au
script ; ou utiliser les boutons **Test** des points de contact.

## Faux `Down : 200 - OK, but keyword is not in…`

**Symptôme** : le moniteur est `Down` avec le message `200 - OK, but keyword is not in…` alors
que `curl -s https://<grafana>/api/health` affiche bien `"database": "ok"`.

**Cause** : le mot-clé contient un espace (`"database": "ok"`) ou d'autres caractères en trop.
UptimeRobot ne trouve que la forme sans espace.

**Correctif** : mot-clé exactement `"database":"ok"`, enregistrer.

**Vérification** : le moniteur repasse `Up` au cycle suivant ; refaire le
[test](../exploitation/sonde-externe.md#tester-la-sonde).
