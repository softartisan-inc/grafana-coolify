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

**Causes** : origine de la page absente de la regex (sous-domaine, `www`, autre TLD) ; regex
sans `^`/`$` ou à point non échappé (accepterait `evil-example.me`) ; réponse 429 de la limite
de débit (elle porte bien les en-têtes CORS : regarder le code HTTP).

**Correctif** : ajuster `accessControlAllowOriginListRegex`, enregistrer (rechargement à chaud).

## Tous les clients d'une agence reçoivent `429`

**Cause** : quota `gc-faro-ratelimit` par IP, partagé derrière un NAT, ou par le proxy amont
(Cloudflare). **Correctif** : relever `average`/`burst`, ou `ipStrategy.depth: 1` avec
`trustedIPs` ([Traefik](../installation/traefik.md#limite-de-débit)).
