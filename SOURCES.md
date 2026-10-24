# Sources techniques vérifiées le 24 octobre 2026

- [ADEME — liste des entreprises RGE](https://data.ademe.fr/datasets/liste-des-entreprises-rge-2) et [métadonnées Data Fair](https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2). La variante `-new` existe également ; le pipeline utilise l'endpoint demandé et contrôle ses champs.
- [API Geo — documentation](https://geo.api.gouv.fr/decoupage-administratif/communes).
- [INSEE — base du comparateur de territoires](https://www.insee.fr/fr/statistiques/2521169), [Parquet officiel](https://www.insee.fr/fr/statistiques/fichier/2521169/comparateur.parquet). Format long : `GEO_OBJECT=COM`, `DS=DS_RP_LOGEMENT_PRINC`, `TAB_MEASURE=DWELLINGS`, `TIME_PERIOD=2023`, `GEO_REF=2026`.
- [Data.gouv.fr — jeux de données et ressources](https://guides.data.gouv.fr/guide-data.gouv.fr/readme-1/reference/datasets). Premier upload : `POST /datasets/{id}/upload/`. Remplacement : `POST /datasets/{id}/resources/{rid}/upload/`.
- [Data.gouv.fr — organisations](https://guides.data.gouv.fr/api-de-data.gouv.fr/reference/organizations), champs `business_number_id`, `logo` et `url`.
- [Data.gouv.fr — frontend officiel et route d'intégration](https://github.com/datagouv/cdata/tree/main/pages/embeds/datasets). Le générateur utilise `preview_url` retourné par l'API lorsque disponible ; sinon la carte officielle `/embeds/datasets/{id}`.
- [GitHub — arbres Git](https://docs.github.com/en/rest/git/trees), [base Git et dépôts vides](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-your-git-database), [Pages](https://docs.github.com/en/rest/pages/pages).
- [Frictionless Table Schema](https://specs.frictionlessdata.io/table-schema/) et [Data Package](https://specs.frictionlessdata.io/data-package/).
- [Google — Dataset](https://developers.google.com/search/docs/appearance/structured-data/dataset), [conditions techniques d'indexation](https://developers.google.com/search/docs/essentials/technical).

Les métadonnées géographiques, une licence ouverte, un schéma et des liens cohérents
améliorent la description et la réutilisabilité. Ils n'activent pas automatiquement
un moissonneur tiers, ne garantissent pas un lien « dofollow », une note de 100 %,
une mise en avant éditoriale ou une indexation sous 24 heures. `CITATION.cff` ne crée
pas de DOI ou de dépôt Zenodo ; cela nécessite une intégration distincte.
