# Open Data RGE — démarrage et exploitation

Python 3.11 ou ultérieur. Le projet collecte les sources officielles, calcule les
indicateurs, produit CSV/Parquet/JSON et prépare une publication data.gouv.fr et
GitHub. Le mode de démonstration fonctionne sans compte et sans réseau.

## 1. Installation

Depuis le dossier du projet, sous PowerShell :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --dry-run --source sample
```

Sous Linux/macOS :

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --dry-run --source sample
```

Les exemples suivants emploient `python` ; utiliser le chemin de l'environnement
créé ci-dessus ou l'activer. `requirements-lock.txt` capture l'environnement de
validation Python 3.13 ; `requirements.txt` laisse le résolveur choisir les versions
compatibles avec votre Python, notamment 3.11.

## 2. Données officielles, sans publier

```bash
python main.py --dry-run --source live
```

Cette commande télécharge la liste ADEME complète par curseur, les communes et
régions de l'API Geo, puis le Parquet du comparateur INSEE. Elle ne nécessite
aucune clé. Les snapshots utilisés sont sauvegardés dans `work/raw/`, hors de la
liste de publication. Le téléchargement national prend plusieurs minutes selon
les services. Une modification ADEME pendant la collecte interrompt l'exécution.

L'observation utilise par défaut la date courante à Paris ; titres et noms de
fichiers prennent cette année. `--as-of 2026-10-24` fixe la date pour une reproduction
locale. La liste courante n'étant pas une archive, la publication d'une observation
antérieure au jour d'exécution est refusée. Conserver les snapshots si une
reproduction exacte doit être possible ultérieurement.

Pour vérifier rapidement la connexion sans tirer de conclusions nationales :

```bash
python main.py --dry-run --source live --max-rge-rows 2000 --output work/apercu
```

Les résultats limités sont étiquetés « Échantillon » et ne peuvent pas être publiés.
Pour séparer une démonstration de vos résultats nationaux, utiliser également
`--output work/demo`. Les fichiers sous le même dossier de sortie sont remplacés.

## 3. Logements et indicateurs

Le mode par défaut est `insee` : recensement 2023, géographie 2026. Le Parquet long
est filtré avant chargement avec `GEO_OBJECT=COM`, `DS=DS_RP_LOGEMENT_PRINC`,
`TAB_MEASURE=DWELLINGS`, `TIME_PERIOD=2023`, `GEO_REF=2026`. Il ne mélange pas
communes, arrondissements, bassins de vie ni autres totaux. Une modification du
format ou l'absence du millésime demandé provoque une erreur explicite. Mettre à
jour `HOUSING_YEAR` et `HOUSING_GEO_YEAR` lorsque les nouvelles données existent.

Un fichier local est possible :

```bash
python main.py --dry-run --source live --housing-file work/logements.csv
```

Le CSV doit avoir une seule ligne par commune, avec `code_insee` en texte et
`nb_logements` numérique ; le séparateur est détecté. Fournir des valeurs pour le
millésime configuré, après harmonisation des communes. Le Parquet officiel est
également accepté. Les absences ne sont jamais transformées en zéro.

Pour choisir explicitement le proxy population / taille moyenne du ménage :

```bash
python main.py --dry-run --source live --housing-mode estimate
```

Le résultat porte `source_logements=estimation_population`, sans millésime INSEE.
Le paramètre `HOUSEHOLD_SIZE=2.2` est une hypothèse configurable, pas une valeur
inférée de la source. Ce proxy n'est pas le parc total de logements : il ignore
notamment la vacance et les résidences secondaires. Il n'est jamais activé en
silence lorsqu'INSEE est indisponible.

L'indice utilise les entreprises distinctes (SIREN) pour 1 000 logements :

| Situation | Classe |
|---|---|
| Logements absents ou nuls | Non calculable |
| Logements positifs, zéro entreprise | Désert RGE |
| Ratio positif et inférieur à 2 | Sous-capacité |
| Ratio entre 2 inclus et 5 exclu | Équilibré |
| Ratio supérieur ou égal à 5 | Forte couverture |

`TENSION_LOW` et `TENSION_HIGH` changent les bornes. Les seuils ne sont pas une
norme officielle de pénurie. La couverture correspond à l'implantation, pas au
rayon d'intervention ni à la disponibilité des équipes. Les études, audits et
architectes sont exclus du périmètre travaux. Les domaines photovoltaïque,
pompe à chaleur, isolation, chauffage et menuiserie ne sont pas exclusifs.

## 4. Publication réelle

Copier `.env.example` vers `.env`, puis renseigner les secrets localement.

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

| Paramètre | Usage |
|---|---|
| `DATAGOUV_API_KEY` | Clé du profil data.gouv.fr ayant accès à l'organisation |
| `DATAGOUV_ORG_ID` | Organisation existante recommandée ; sinon recherche puis création |
| `ORGANIZATION_SIREN` | SIREN réel à 9 chiffres, contrôle de cohérence facultatif |
| `ORGANIZATION_SIRET` | SIRET réel à 14 chiffres, exigé par data.gouv.fr à la création |
| `ORGANIZATION_LOGO_URL` | URL HTTPS du logo réel, nécessaire à la création |
| `DATAGOUV_DATASET_ID` | Facultatif ; sinon détection dans l'organisation, par édition annuelle |
| `GITHUB_TOKEN` | Jeton GitHub autorisé sur le dépôt cible |
| `GITHUB_OWNER` | Compte ou organisation propriétaire |
| `GITHUB_REPO` | Par défaut `open-data-conformite-rge-france` |
| `GITHUB_TRANSPORT` | `api` par défaut ; `git` pour un transfert compressé, avec Git installé |
| `ENABLE_PAGES` | `true` pour configurer GitHub Pages sur `/docs` |

Pour un dépôt existant, un jeton finement limité avec Contents en écriture suffit
à synchroniser les fichiers ; Pages et Administration en écriture sont également
nécessaires pour configurer Pages. La création d'un dépôt exige les droits
correspondants sur le compte ou l'organisation. Un jeton classique doit disposer
des droits documentés par GitHub, notamment `repo` pour l'API Pages. Le dépôt
existant doit être public ; le programme ne change jamais sa visibilité.

```bash
python main.py --publish --source live
```

Le mode `--publish` est explicite et exige une collecte complète. Le programme :

1. produit localement les exports et contrôle leur qualité ;
2. vérifie l'authentification aux deux services ;
3. retrouve/crée l'organisation et le dépôt ;
4. retrouve/crée le dataset annuel ;
5. charge le schéma, les données et les contrôles, puis la documentation distante ;
6. pousse les fichiers publics par commit Git, sans écraser les autres fichiers ;
7. configure Pages, puis relie les URL retournées par les services.

Un build GitHub Pages est asynchrone. `pages_status` n'est pas une promesse que la
page soit déjà accessible. Le JSON-LD ne fabrique aucun identifiant data.gouv.fr
ni DOI. La publication ne déploie pas le site Legibat : utiliser le fragment HTML
livré pour cette dernière étape dans votre CMS.

Les contrôles bloquent l'envoi si la collecte est partielle, s'il n'existe aucun
établissement valide, si tous les
logements sont absents, ou si les seuils configurés de lignes non rattachées (15 %)
et invalides (5 %) sont dépassés. Les SIRET affectés à plusieurs communes sont
exclus de tous les agrégats ; si ces exclusions dépassent 0,1 % des établissements
localisés (`MAX_CONFLICTING_FRACTION`), la publication est bloquée. Un seuil zéro
impose un contrôle strict sans adresse conflictuelle.
Examiner `dist_data/quality-report.json` avant
de modifier ces seuils. Les sorties locales sont conservées même si la publication
est bloquée.

## 5. Reprise, erreurs et sécurité des secrets

`.state/publication.json` conserve les identifiants d'organisation, datasets,
ressources et empreintes. Une relance remplace les fichiers sur les mêmes ressources.
Les recherches distantes permettent aussi de retrouver les objets si l'état local
est absent. Les ressources ambiguës font échouer le traitement ; elles ne sont
pas supprimées automatiquement.

Les lectures et PUT idempotents réessaient certaines erreurs réseau, 429 et 5xx.
Les POST de création/upload ne sont pas répétés aveuglément après une réponse
incertaine. Relancer après résolution de l'incident pour réconcilier l'état.
Les URLs de pagination authentifiée doivent rester dans l'origine de l'API.
Les secrets et corps d'erreur des API ne sont pas affichés. Le dépôt reçoit une
liste blanche de fichiers : jamais `.env`, `.state`, snapshots ou journaux privés.

Les mises à jour de branche Git sont non forcées. En cas de commit concurrent,
relancer après le conflit : l'historique distant n'est pas réécrit. L'état de
publication local indique une exécution incomplète si une étape distante échoue.

Codes de sortie : `0` pour réussite locale ou publication, `1` pour échec de
traitement/publication, `2` pour arguments ou configuration invalides.

## 6. Mise à jour mensuelle

Le workflow `.github/workflows/monthly.yml` est livré, mais n'est pas envoyé par le
jeton d'exploitation du pipeline. L'installer dans le dépôt avec votre procédure
habituelle (interface GitHub ou commit Git autorisé). Cette séparation évite
d'exiger le droit Workflows pour chaque mise à jour des données.

Configurer dans GitHub : secrets `DATAGOUV_API_KEY` et `RGE_GITHUB_TOKEN`, variables
`DATAGOUV_ORG_ID`, `HOUSING_YEAR`, `HOUSING_GEO_YEAR` et, si création d'organisation,
le SIREN et l'URL du logo. Définir `ENABLE_RGE_PUBLICATION=true` pour activer
l'exécution. Le workflow propose aussi un déclenchement manuel. Il planifie le
troisième jour du mois à 05:23 UTC ; aucun calendrier externe n'est créé par ce projet.

La découverte distante assure la reprise sur un runner éphémère. Les diagnostics
et l'état de reprise sont conservés comme artefact du run. La concurrence est limitée
à une publication. GitHub peut différer ou suspendre des workflows planifiés :
vérifier les runs et les notifications de votre dépôt.

## 7. Pages et intégration Legibat

- `docs/index.html` : page GitHub Pages, tableau régional, graphique SVG et JSON-LD.
- `landing/legibat.html` : page complète avec styles pour consultation.
- `landing/legibat-fragment.html` : article et JSON-LD à coller dans le CMS ; adapter
  les styles à ceux du site, sans supprimer les informations méthodologiques.
- `dataset.jsonld` : métadonnées séparées.

L'iframe n'est ajouté qu'après obtention d'un vrai dataset. Il utilise le
`preview_url` officiel du CSV s'il est disponible ; sinon la carte officielle du
dataset. Le traitement tabulaire de data.gouv.fr peut être différé. Le fragment
généré sans publication explique cette absence au lieu d'inventer un identifiant.

Les liens locaux Legibat suivent la convention demandée ; le pipeline ne crée
pas les pages de communes et n'atteste pas qu'elles existent. Le balisage Dataset,
le TableSchema et la Licence Ouverte facilitent la réutilisation sans garantir
un backlink dofollow, un score de qualité, un moissonnage ou une indexation rapide.

## 8. Tests et arborescence

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
```

Les tests de publication emploient `httpx.MockTransport` et ne contactent pas les
comptes réels. La validation CSV et Data Package utilise Frictionless ; la lecture
Parquet vérifie notamment les types et les codes géographiques. Les API réelles
d'écriture ne sont testables qu'avec les identifiants et droits de vos comptes.

```text
open-data-conformite-rge-france/
├── main.py
├── config.py
├── common.py
├── dataset_builder.py
├── datagouv_client.py
├── github_publisher.py
├── landing_page_generator.py
├── metadata.py
├── requirements.txt / requirements-dev.txt / requirements-lock.txt
├── .env.example / .gitignore / pyproject.toml
├── .github/workflows/monthly.yml
├── tests/
├── GUIDE.md / SOURCES.md / LICENSE / DATA_LICENSE.md
├── README.md / DATASET_DESCRIPTION.md
├── CITATION.cff / datapackage.json / dataset.jsonld / publication.json
├── dist_data/     CSV, Parquet, JSON, TableSchema, provenance, qualité, manifeste
├── docs/          index.html, .nojekyll, assets/couverture-regions.svg
└── landing/       legibat.html et legibat-fragment.html
```
