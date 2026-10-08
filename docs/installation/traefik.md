# Middlewares Traefik

[← Sommaire](../README.md)

Les protections des points publics (Faro et OTLP) sont dans une **configuration dynamique**
propre au serveur, jamais dans le dépôt public (aucun hash de mot de passe publié). Traefik la
recharge à chaud : ni redéploiement, ni redémarrage.

## Installer le fichier

1. Coolify → **Servers** → le serveur → **Proxy** → **Dynamic Configurations** → nouveau
   fichier `grafana-coolify.yaml`.
2. Coller [`traefik/grafana-coolify.yaml.example`](../../traefik/grafana-coolify.yaml.example).
3. Remplacer la ligne htpasswd et la regex des origines (ci-dessous), puis enregistrer.

## Comptes OTLP (htpasswd)

Le compte OTLP protège la porte publique `alloy-gateway`, utilisée **seulement** par des
applications d'un **autre serveur**. Une application du même serveur Coolify envoie par le
réseau interne (`ALLOY_INTERNAL_URL`), sans mot de passe.

| Situation | Que faire |
|---|---|
| Tout tourne sur ce serveur | Laisser `alloy-gateway` **sans domaine** (porte fermée). Remplacer quand même la ligne d'exemple par une vraie, par hygiène. |
| Un projet envoie depuis un autre serveur | Une ligne par projet, et un domaine pour `alloy-gateway` : `https://otlp.example.com`, **Internal port** `4318` ([ouvrir Faro ou OTLP](deploiement-coolify.md#ouvrir-faro-ou-otlp)). |

Générer une ligne, sur votre poste :

```bash
printf '%s:%s\n' <projet> "$(openssl passwd -apr1 '<MOT_DE_PASSE_LONG>')"
```

Résultat de la forme `<projet>:$apr1$<sel>$<hash>`. Le coller sous `users:`, **entre guillemets
doubles**, sans doubler les `$` (en YAML, `$` n'a pas de sens particulier) :

```yaml
        users:
          - "<projet>:$apr1$<sel>$<hash>"
```

Garder le mot de passe en clair dans le gestionnaire de mots de passe : les serveurs émetteurs
l'envoient en `Authorization: Basic <base64 projet:mot-de-passe>`. Révoquer un projet = supprimer
sa ligne (effet immédiat).

Astuce : pour ne pas laisser le mot de passe dans l'historique du shell, `read -rs P` puis
`openssl passwd -apr1 "$P"` et `unset P`.

## Origines autorisées (CORS Faro)

La regex se met **entre guillemets simples** :

```yaml
        accessControlAllowOriginListRegex:
          - '^https://([a-z0-9-]+\.)?example\.(me|app)$'
```

Entre guillemets doubles, YAML lit `\` comme un échappement : `"…\.…"` est invalide
(`found unknown escape character`) et **Traefik ignore tout le fichier**, donc aucun des
middlewares n'existe. Forme équivalente si l'on tient aux guillemets doubles : doubler chaque
barre, `"^https://([a-z0-9-]+\\.)?example\\.(me|app)$"`.

Cette regex accepte `https://example.me`, `https://acme.example.me`, `https://acme-dev.example.app`
et refuse `https://evil-example.me` ou `https://example.me.attaquant.com`.

## Rattachement et vérification

Le rattachement est fait par les labels du compose : `alloy-gateway` → `gc-otlp-auth@file` ;
`alloy` → `gc-faro-cors@file`, `gc-faro-ratelimit@file`, `gc-faro-body@file`, dans cet ordre.
Après enregistrement, sur l'hôte :

```bash
docker logs coolify-proxy 2>&1 | grep -iE 'escape|yaml|middleware .* does not exist' | tail
```

Attendu : aucune ligne récente. Sinon voir [dépannage Traefik](../depannage/traefik.md).

## Limite de débit

`gc-faro-ratelimit` compte par IP source (50 req/s, rafale 100). Une agence derrière un seul NAT
partage ce quota : relever `average` et `burst` si besoin. Derrière Cloudflare ou un autre
proxy, décommenter `sourceCriterion.ipStrategy.depth: 1` et déclarer les plages du proxy dans
`forwardedHeaders.trustedIPs` du point d'entrée Traefik (README, étape 3).

Suite : [Déploiement Coolify](deploiement-coolify.md).
