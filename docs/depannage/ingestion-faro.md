# Dépannage : ingestion Faro

[← Index du dépannage](README.md)

Entrées de **prévention** : écrites pour la mise en service d'un client desktop
([`desktop-app`](../ajouter-un-projet.md#un-client-desktop-hors-navigateur)), valables pour tout
client Faro. Les rejets se lisent dans Prometheus (Alloy est scrapé) ou dans le panneau
« Rejets » du tableau **Santé du pipeline** :

```
increase(loki_process_dropped_lines_total[1h])
```

Le compteur porte le motif (`reason`), **pas** le service ni le tenant : une ligne rejetée n'est
stockée nulle part. Pour savoir quel client est en cause, comparer l'heure de la hausse avec la
mise en service ou le redéploiement concerné.

## `loki_process_dropped_lines_total{reason="unknown_service"}` augmente

**Symptôme** : aucun log `{service_name="desktop-app"}` dans Explore, et le compteur
`unknown_service` monte dès que des postes tournent. Les traces du desktop manquent aussi
(`otelcol_processor_filter_spans_filtered_total{component_id="otelcol.processor.filter.faro"}`
augmente).

**Cause** : le nom envoyé (`app.name` des logs, `service.name` des traces) n'est pas dans
`FARO_SERVICES` : variable non complétée, faute de frappe, espace après la virgule, ou variable
modifiée sans **Redeploy**. C'est aussi l'effet voulu de l'interrupteur (service retiré de la
liste).

**Correctif** : ajouter le nom à la valeur existante, sans la remplacer
(`FARO_SERVICES=web-front,desktop-app`), puis **Redeploy**.

**Vérification** : la valeur réellement vue par Alloy, sur l'hôte :

```bash
docker inspect $(docker ps --format '{{.Names}}' | grep '^alloy-<uuid>' | head -1) --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^FARO_SERVICES='
```

Puis le compteur ne bouge plus, et `{project="demo", service_name="desktop-app"}` répond
dans Explore.

## `reason="invalid_env"` ou `reason="missing_env"` augmente

**Symptôme** : lignes rejetées alors que le service est listé.

**Cause** : l'environnement envoyé par le client (`app.environment`) n'est ni `prod` ni `preprod`,
ou il manque. Pour un client sans hôte de page reconnu (desktop : `page_url` en
`app://desktop-app/…`), rien ne le corrige côté serveur : la valeur du client est la seule
source. Le client desktop ne doit rien envoyer quand il ne reconnaît pas l'hôte d'API ; une
hausse vient donc d'un build mal configuré, ou d'un émetteur qui n'est pas l'application.

**Correctif** : côté client (configuration du build, table hôte → `env`). Ne **pas** ajouter
l'hôte `desktop-app` à `HOST_MAP` pour forcer un `env` : il ne vaudrait que pour un seul des deux
environnements.

**Vérification** : le compteur ne bouge plus ; Explore montre les deux valeurs attendues,
`{project="demo", service_name="desktop-app", env=~"prod|preprod"}`.

## Lignes du desktop stockées sans `tenant`

**Symptôme** : `{project="demo", service_name="desktop-app"} | tenant="<slug>"` ne renvoie
rien, alors que les lignes existent sans filtre.

**Cause** (aucune n'est un rejet : la ligne est gardée, seul le tenant manque) :

- le desktop est en mode chemin et l'utilisateur n'est pas encore connecté : aucun slug n'est
  envoyé (attendu) ;
- le slug envoyé n'est pas de la forme `[a-z0-9-]+` (majuscules, espace…) : il est retiré ;
- le slug est dans `RESERVED_SUBDOMAINS` : il est retiré.

**Correctif** : côté client si le slug est mal formé ; sinon rien à faire. Le tenant qui fait foi
reste celui du span serveur enfant (`api`).

**Vérification** : `{project="demo", service_name="desktop-app"} | tenant!=""` renvoie les
lignes des postes connectés.

## Traces du desktop absentes, logs présents

**Cause** : une trace Faro n'a pas d'URL de page ; elle est validée sur ses propres attributs de
ressource. Elle est supprimée si `service.name` n'est pas dans `FARO_SERVICES` ou si
`service.namespace` n'est pas dans `PROJECTS` (compteur
`otelcol_processor_filter_spans_filtered_total{component_id="otelcol.processor.filter.faro"}`),
ou si `deployment.environment.name` n'est ni `prod` ni `preprod` (même compteur,
`component_id="otelcol.processor.filter.default"`).

**Correctif** : côté client, la réécriture de la ressource des traces (ces trois attributs).

**Vérification** : TraceQL `{ resource.service.name = "desktop-app" }` dans Explore (Tempo).
