# Qualifications RGE actives par famille de travaux et département — 2026

## Objet et lecture

Ce jeu adopte un format long : une ligne par département ou territoire et par famille de travaux, y compris lorsque le nombre de qualifications est nul. Les cinq familles sont le photovoltaïque, les pompes à chaleur, l'isolation, le chauffage et la menuiserie. Une qualification est une combinaison distincte établissement, organisme, code de qualification et domaine normalisé. Plusieurs qualifications peuvent appartenir à un même établissement. Il ne faut donc pas interpréter ces effectifs comme des entreprises, des artisans ou des emplois. Les familles se chevauchent, notamment pompes à chaleur et chauffage, et leurs effectifs ne doivent pas être additionnés. Le taux de couverture compte les communes disposant d'au moins une qualification de la famille parmi toutes les communes du référentiel départemental ; il ne mesure ni une part de marché ni une disponibilité pour intervenir. Les domaines non classés dans ces cinq familles sont documentés dans le contrôle qualité de la source.

## Observation et sources

Observation au **2026-11-09**. Ce jeu dérivé est produit par Legibat, organisme privé indépendant, à partir de la [cartographie communale source](https://www.data.gouv.fr/datasets/cartographie-2026-de-la-couverture-dartisans-rge-par-commune-vs-nombre-de-logements). La collecte complète utilise la [liste RGE de l'ADEME](https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2), les [communes de l'API Geo](https://geo.api.gouv.fr/communes) et le [comparateur de territoires INSEE](https://www.insee.fr/fr/statistiques/2521169). Les logements correspondent au recensement 2023, géographie 2026. Le millésime de population n'est pas fourni par l'API Geo utilisée ; seule sa date de collecte est attestée.

## Traitement reproductible

Les qualifications doivent être valides à la date d'observation. Les activités d'étude, d'audit et d'architecture sont exclues du périmètre travaux. Le rattachement communal utilise le code INSEE lorsqu'il est disponible, sinon le code postal et le nom normalisé de commune. Les correspondances ambiguës sont exclues, sans répartition artificielle entre plusieurs communes. Les établissements aux localisations contradictoires sont exclus de tous les agrégats. Les SIRET et les combinaisons de qualification sont dédupliqués avant calcul. Paris, Lyon et Marseille sont ramenés à la commune entière. Les territoires conservés sont ceux présents dans le référentiel API Geo ; la couverture du référentiel ne doit pas être confondue avec un inventaire exhaustif de tous les territoires français.

Les dénominateurs absents restent manquants. Les compteurs nuls désignent une absence de qualification ou d'établissement retenu après ces contrôles, et non une absence de service sur le terrain. Les contrôles de la source bloquent toute publication partielle ou de démonstration. Le fichier provenance.json conserve la date de collecte, les paramètres et les empreintes du Parquet et du manifeste sources. Les fichiers manifest.json et schema.json permettent de contrôler les exports et leur structure.

## Formats et réutilisation

Le CSV UTF-8 utilise le point-virgule, les décimales un point ; importer les codes géographiques comme texte pour préserver les zéros et les codes corses. Le Parquet conserve les types et les valeurs manquantes. synthese.json contient les agrégats de lecture. Les exports ne contiennent ni identifiants d'entreprises ni coordonnées individuelles.

Mise à jour hebdomadaire avec le pipeline RGE, programmée le lundi à 7 h 23, heure de Paris ; l'heure effective dépend du lancement par GitHub Actions. Le code et les contrôles sont disponibles dans le [dépôt public](https://github.com/Sotani77/open-data-conformite-rge-france). Le [tableau de lecture et la méthode](https://sotani77.github.io/open-data-conformite-rge-france/qualifications-rge-metiers-departements/) donnent accès aux téléchargements. Licence Ouverte 2.0 : citer ADEME, API Geo, INSEE et les transformations Legibat, avec la date d'observation. Aucune disponibilité, assurance décennale ou conformité de chantier n'est attestée par ces indicateurs.


[Publication data.gouv.fr](https://www.data.gouv.fr/datasets/qualifications-rge-actives-par-famille-de-travaux-et-departement-2026)
