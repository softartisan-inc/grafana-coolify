# Configurer le service Grafana

[← Sommaire](../README.md)

Le service Coolify « Grafana » n'est pas modifié dans son image ni dans son compose : on règle
seulement son réseau et ses variables d'environnement, depuis Coolify. Trois choses, **avant**
le premier déploiement du package.

## 1. Le relier au réseau `coolify`

Un service « one-click » de Coolify vit par défaut **sur son propre réseau privé** (nommé
d'après son UUID). Sans réglage, Grafana et la stack ne partagent aucun réseau : `grafana-setup`
ne peut pas joindre Grafana (`Grafana not healthy after 120s`) et Grafana ne peut pas lire
Loki, Tempo ni Prometheus.

1. Coolify → le **service Grafana** → **Settings** (ou **General**) → cocher
   **Connect To Predefined Network** → **Save**.
2. **Restart** (ou **Redeploy**) du service Grafana.
3. Sur l'hôte, vérifier :

   ```bash
   G=$(docker ps --format '{{.Names}}' | grep -i '^grafana' | head -1); echo "$G"
   docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$G"
   docker run --rm --network coolify curlimages/curl -sS -m 5 "http://$G:3000/api/health"
   ```

   Attendu : `coolify` dans la liste des réseaux, puis `{"database":"ok","version":"…"}`.
   Toujours `-sS` : avec `-s` seul, une erreur de résolution ne s'affiche pas et une réponse
   vide passe pour un succès.

Ne pas utiliser `docker network connect` à la main : le rattachement disparaît au prochain
redéploiement de Grafana.

Le nom affiché (`grafana-<uuid-grafana>`) est la valeur de `GRAFANA_URL` du package :

```
GRAFANA_URL=http://grafana-<uuid-grafana>:3000
```

Adresse **interne**, en `http://`, port `3000`. Il reste stable tant que le service Grafana
n'est pas recréé. L'URL publique (`https://grafana.example.com`) échoue souvent depuis un
conteneur du même serveur (le trafic doit sortir puis revenir, ce que beaucoup d'hébergeurs
bloquent) : voir [grafana-setup](../depannage/grafana-setup.md#grafana-setup-error-grafana-not-healthy-after-120s).

## 2. Compte de service et jeton

Grafana → **Administration** → **Users and access** → **Service accounts** →
**Add service account** : nom `grafana-coolify`, rôle **Admin**, puis
**Add service account token**. Copier le jeton (`glsa_…`) dans `GRAFANA_SA_TOKEN` du package.

Le jeton ne sert qu'au déploiement : `grafana-setup` l'utilise quelques secondes puis s'arrête,
et ne l'écrit jamais dans ses logs. À l'expiration, la stack continue de tourner ; seul le
**prochain déploiement** échoue à l'étape `grafana-setup`.

| Expiration | Pour | Contre |
|---|---|---|
| 90 jours | fenêtre d'abus courte si le jeton fuit | une rotation par trimestre ; un redéploiement peut échouer à l'improviste |
| **1 an (conseillé)** | une rotation par an | fenêtre plus longue |
| aucune | pas de maintenance | un jeton Admin qui fuit reste valable indéfiniment : à proscrire |

Choisir 90 jours si plusieurs personnes ont accès aux variables Coolify. Dans tous les cas,
**créer un rappel d'agenda** un mois avant l'échéance ; procédure :
[rotation des secrets](../exploitation/rotation-des-secrets.md#jeton-du-compte-de-service-grafana).

## 3. SMTP

Les alertes non critiques (et toutes celles de préprod) partent par email, via le SMTP **de
Grafana**. Les variables `GF_SMTP_*` se mettent dans **Environment Variables du service
Grafana**, jamais dans le package (qui ne les lit pas, et dont les logs de déploiement les
afficheraient) :

| Variable | Exemple |
|---|---|
| `GF_SMTP_ENABLED` | `true` |
| `GF_SMTP_HOST` | `smtp.example.com:587` |
| `GF_SMTP_USER`, `GF_SMTP_PASSWORD` | identifiants du compte d'envoi (`<SMTP_USER>`, `<SMTP_KEY>`) |
| `GF_SMTP_FROM_ADDRESS`, `GF_SMTP_FROM_NAME` | `alertes@example.com`, `Monitoring` |

Redémarrer Grafana, puis tester : **Alerting** → **Contact points** → point de contact Email
temporaire → **Test**. Supprimer ensuite ce point de test : le package crée le sien
(`gc-email`).

Avec Brevo (ou tout relais transactionnel), `GF_SMTP_FROM_ADDRESS` doit être un **expéditeur
validé** ou appartenir à un **domaine authentifié** chez le fournisseur, sinon l'envoi est
refusé.

Suite : [Middlewares Traefik](traefik.md).
