# Dépannage : Traefik

[← Index du dépannage](README.md)

## `found unknown escape character`

**Symptôme** : à l'enregistrement de la configuration dynamique, ou dans les logs du proxy :

```
Found unknown escape character "\." at line 23 (near "- "^https://([a-z0-9-]+\.)?example\.(me|app)$"").
```

**Cause** : la regex des origines est entre **guillemets doubles**. Dans ce style YAML, `\`
introduit une séquence d'échappement et `\.` n'en est pas une. Traefik rejette alors **tout le
fichier** : aucun middleware `gc-*` n'existe, et les routeurs qui les référencent échouent.

**Correctif** : guillemets **simples**, qui gardent la regex telle quelle :

```yaml
        accessControlAllowOriginListRegex:
          - '^https://([a-z0-9-]+\.)?example\.(me|app)$'
```

Équivalent : garder les guillemets doubles et doubler chaque barre (`\\.`). Les lignes htpasswd,
elles, restent entre guillemets doubles : `$` n'y pose aucun problème.

**Vérification** :

```bash
docker logs coolify-proxy 2>&1 | grep -iE 'escape|middleware .* does not exist' | tail
```

Aucune ligne postérieure à l'enregistrement.

## `middleware "gc-…@file" does not exist`

**Cause** : fichier dynamique absent, mal nommé, ou rejeté (YAML invalide, voir ci-dessus) ;
ou nom de middleware modifié. Les labels du compose attendent exactement `gc-otlp-auth`,
`gc-faro-cors`, `gc-faro-ratelimit`, `gc-faro-body`.

**Correctif** : recoller [`traefik/grafana-coolify.yaml.example`](../../traefik/grafana-coolify.yaml.example)
puis refaire les remplacements ([Traefik](../installation/traefik.md)).

**Vérification** : spike S2 de [`docs/spikes.md`](../spikes.md).

## OTLP : `401` avec le bon mot de passe

**Causes** : `$` doublés dans la ligne htpasswd (`$$apr1$$…`, réflexe de Compose, inutile ici) ;
ligne hors des guillemets ; espaces autour du `:` ; en-tête construit à partir d'un autre
mot de passe que celui passé à `openssl passwd`.

**Vérification** :

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u '<projet>:<MOT_DE_PASSE>' -X POST -H 'Content-Type: application/json' --data '{"resourceLogs":[]}' https://<domaine OTLP>/v1/logs
```

Attendu : `200`. Sans `-u` : `401`.

## Faro : erreur CORS dans le navigateur

**Causes** : en-tête refusé par la requête préliminaire (voir l'entrée
[`idempotency-key`](#request-header-field-idempotency-key-is-not-allowed-by-access-control-allow-headers-in-preflight-response)) ;
origine de la page absente de la regex (sous-domaine, `www`, autre TLD) ; regex
sans `^`/`$` ou à point non échappé (accepterait `evil-example.me`) ; réponse 429 de la limite
de débit (elle porte bien les en-têtes CORS : regarder le code HTTP).

**Correctif** : ajuster `accessControlAllowOriginListRegex`, enregistrer (rechargement à chaud).

## `Request header field idempotency-key is not allowed by Access-Control-Allow-Headers in preflight response`

**Symptôme** : dans la console du navigateur, sur chaque envoi Faro :

```
Access to fetch at 'https://<domaine Faro>/collect' from origin 'https://<application>' has been blocked by CORS policy: Request header field idempotency-key is not allowed by Access-Control-Allow-Headers in preflight response.
```

Plus aucun log ni trace Faro n'arrive, pour toutes les applications web.

**Cause** : le SDK Faro Web 2.x envoie l'en-tête `Idempotency-Key` sur chaque `POST /collect`.
Une configuration dynamique antérieure ne l'autorise pas dans `gc-faro-cors` : le navigateur
refuse la requête préliminaire (preflight) et n'envoie jamais le `POST`.

**Correctif** : Coolify → **Servers** → le serveur → **Proxy** → **Dynamic Configurations** →
`grafana-coolify.yaml`, ajouter une ligne sous `accessControlAllowHeaders`, puis enregistrer.
Traefik recharge le fichier à chaud : ni redéploiement, ni redémarrage.

```yaml
        accessControlAllowHeaders:
          - Content-Type
          - x-api-key
          - x-faro-session-id
          - Idempotency-Key
```

**Vérification** : depuis n'importe quel poste, une requête préliminaire comme celle du
navigateur (remplacer l'origine par une origine acceptée par la regex) :

```bash
curl -s -o /dev/null -D - -X OPTIONS -H 'Origin: https://<application>' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: content-type,idempotency-key,x-api-key,x-faro-session-id' https://<domaine Faro>/collect | grep -i '^access-control-allow-headers'
```

Attendu : `access-control-allow-headers: Content-Type,x-api-key,x-faro-session-id,Idempotency-Key`.
Puis recharger l'application : la console ne montre plus l'erreur et `POST /collect` répond `2xx`.

## Tous les clients d'une agence reçoivent `429`

**Cause** : quota `gc-faro-ratelimit` par IP, partagé derrière un NAT, ou par le proxy amont
(Cloudflare). **Correctif** : relever `average`/`burst`, ou `ipStrategy.depth: 1` avec
`trustedIPs` ([Traefik](../installation/traefik.md#limite-de-débit)).
