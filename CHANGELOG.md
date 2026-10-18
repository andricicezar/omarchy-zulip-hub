# Historique des versions

## 2.6.0 — 2026-10-18

- **Connexion sans clé API.** L'ajout d'un compte ne demande plus que l'adresse
  du serveur : le panneau lit ses méthodes de connexion publiques
  (`/api/v1/server_settings`) et propose un bouton par méthode (Google,
  GitHub, GitLab…), ainsi que la connexion par mot de passe quand le serveur
  l'accepte. La connexion par navigateur suit le flux officiel des
  applications mobiles Zulip : la clé revient vers `zulip://login`, chiffrée
  par un masque jetable que seul le pont connaît. Le gestionnaire `zulip://`
  n'est associé que le temps de la connexion (dix minutes au plus), puis le
  précédent est rétabli. Le mot de passe sert une fois à `fetch_api_key` et
  n'est jamais conservé ; l'adresse e-mail et le nom du compte viennent du
  serveur.
- **Import des zuliprc** du dossier Téléchargements, supprimés une fois
  importés puisqu'ils contiennent la clé en clair.
- La saisie manuelle de la clé API reste disponible.

## 2.5.0 — 2026-10-18

- **Plusieurs serveurs Zulip.** La configuration accepte une liste
  `[[accounts]]` (`site`, `email`, `name` facultatif) ; l'ancienne section
  `[account]` reste lue et est convertie à la première modification. Le bridge
  ouvre une file d'événements par compte, dans un seul processus, et fusionne
  leurs états : compteurs additionnés, conversations récentes de tous les
  serveurs triées par date et marquées de leur compte, état par compte pour le
  diagnostic. Un compte sans clé ou hors ligne n'empêche pas les autres.
- **Chaque action vise le serveur de la conversation.** Lecture, réponse,
  ouverture et « marquer comme lu » transmettent le serveur avec
  l'identifiant ; le pont retrouve la ligne par ce couple dans son propre état
  et refuse tout serveur non configuré. La garantie anti-redirection reste
  entière.
- **Réglages → Comptes Zulip** liste les comptes, permet d'en ajouter, d'en
  modifier ou d'en retirer un. Le compositeur propose le compte d'envoi des
  nouveaux messages directs ; les notifications portent le nom du compte.

## 2.4.3 — 2026-09-02

- **Un refus définitif du serveur ne relance plus le bridge en boucle.** Sur
  une erreur non récupérable — une clé révoquée répond 401, classé non
  récupérable — le bridge sortait, et le service Quickshell le relançait cinq
  secondes plus tard, indéfiniment : environ 720 appels API par heure sur le
  compte, pour une erreur qui ne se résout pas d'elle-même. C'est la même
  famille de problème que la boucle sans temporisation corrigée en 2.1.2. Le
  bridge reste désormais en vie, inscrit la raison dans l'état local pour que
  le panneau l'affiche, et retente au quart d'heure : **4 appels par heure au
  lieu de 720**. La reconnexion du compte depuis les réglages le relance
  immédiatement, sans attendre.

## 2.4.2 — 2026-09-02

Dernières exigences de la revue de sécurité, relevées en relisant le message
du relecteur mot à mot plutôt que mon propre résumé.

- **Le corps d'un message est borné avant d'atteindre l'interface.** Un
  message volumineux émettait jusqu'à 2,9 Mo d'un seul tenant vers QML, qui
  l'analyse d'un bloc. Le contenu est tronqué, et toute réponse du pont dont
  la sérialisation dépasse le budget devient une erreur plutôt qu'un flot.
- **`write_atomic` ne suit plus de lien symbolique.** C'est la fonction qui
  écrit `hyprland.lua` : les deux autres fonctions d'écriture avaient été
  durcies, pas elle, et un répertoire parent remplacé par un lien détournait
  donc encore l'écriture vers sa cible. Le fichier temporaire est créé et
  publié relativement au parent, lui-même ouvert sans suivre de lien.

## 2.4.1 — 2026-09-02

Compléments à la revue de sécurité : trois exigences du relecteur n'étaient
que partiellement traitées en 2.4.0.

- **Profondeur d'imbrication.** Une charge de 200 Ko — très en deçà de la
  borne en octets — suffisait à faire tomber le bridge sur `RecursionError`,
  que rien n'attrapait. La profondeur est désormais mesurée sur les octets
  bruts, par un parcours itératif, avant toute analyse.
- **Borne agrégée.** Les bornes par champ ne s'additionnent pas : l'annuaire
  pouvait émettre jusqu'à 39 Mo vers l'interface. Son pire cas tombe à 269 Ko,
  et l'état écrit sur disque tient un budget explicite en abandonnant les
  conversations les plus anciennes.
- **Écriture relative à un descripteur.** `O_NOFOLLOW` ne protégeait que le
  dernier segment : un répertoire parent remplacé par un lien détournait
  l'écriture vers sa cible. Le parent est maintenant ouvert lui-même sans
  suivre de lien, et une descente segment par segment est disponible pour les
  chemins de confiance.

## 2.4.0 — 2026-09-02

Revue de sécurité de la place de marché Omarchy : cinq points corrigés.

- **La clé API ne peut plus suivre une redirection.** `urllib` recopie les
  en-têtes vers la cible d'une redirection en n'écartant que `content-length`
  et `content-type` : la clé partait donc vers l'hôte choisi par le serveur,
  fût-il d'un autre domaine. Seule une redirection vers exactement la même
  origine HTTPS est suivie, et l'adresse du serveur est canonicalisée avant que
  l'en-tête d'authentification ne soit fabriqué.
- **Tout ce qui vient du serveur est borné** avant d'être alloué ou persisté :
  taille de réponse, longueur des chaînes, taille des collections, limite de
  message imposée par le serveur. Les valeurs de configuration non finies sont
  refusées : `NaN` traversait toutes les bornes, une comparaison avec lui étant
  toujours fausse.
- **L'intégration Hyprland ne suit plus aucun lien symbolique.** Un lien au
  chemin du module n'est jamais considéré comme géré, un lien cassé n'est plus
  confondu avec une absence, la sauvegarde est créée exclusivement, et le
  retrait est redevenu une transaction avec retour arrière.
- **Le corps d'un message est rendu en texte brut.** Un rendu enrichi laissait
  un expéditeur distant piloter la mise en forme et le chargement de
  ressources embarquées, sans politique possible côté Qt.
- **Les commandes auxiliaires passent par un module unique** : exécutables
  résolus dans un `PATH` de confiance, environnement réduit à une liste
  blanche, sortie bornée pendant qu'elle est produite, terminaison du groupe de
  processus entier. `subprocess` ne subsiste que dans ce module. L'arrêt du
  bridge a désormais une échéance.

## 2.3.1 — 2026-09-02

- Le plugin ne pilote plus aucun service utilisateur. Cinq appels `systemctl`
  visaient encore `zulip-hub.service`, hérités de la 1.x : la 2.x n'installe
  plus d'unité systemd et le bridge suit le cycle de vie du shell, ces
  branches étaient donc inatteignables et auraient piloté un service
  inexistant. Le module d'accueil perd 56 lignes avec elles, ainsi que son
  champ de lancement de commandes et les imports `os` et `subprocess`.
- Un test verrouille désormais la propriété : aucune commande externe n'est
  lancée, quel que soit l'environnement.

## 2.3.0 — 2026-09-02

- Le panneau signale lui-même qu'une mise à jour est installée mais pas encore
  chargée, et propose un bouton pour redémarrer Omarchy Shell. La version lue
  au premier chargement est celle qui tourne réellement ; toute valeur
  différente lue ensuite vient du disque et révèle l'écart, sans qu'aucune
  version n'ait à être écrite en dur.

## 2.2.1 — 2026-09-02

- La liste des touches passe à la ligne au lieu d'être coupée aux deux bouts :
  elle dépassait la largeur du panneau depuis l'ajout de `O`.
- Procédure de mise à jour documentée. `omarchy plugin update` ne recharge pas
  le composant du widget tant que l'URL du plugin ne change pas : le bridge
  prend la nouvelle version tout de suite, l'interface seulement après un
  `omarchy-restart-shell`.

## 2.2.0 — 2026-09-02

- `Entrée` affiche désormais le message dans le panneau au lieu d'ouvrir la
  page web. Expéditeur, canal et topic viennent de l'état local, le corps est
  récupéré à la demande.
- `O` et un bouton sur la ligne conservent l'ouverture dans Zulip.
- Le corps d'un message n'est jamais écrit sur disque : il ne vit qu'en
  mémoire et disparaît à la fermeture du panneau.
- Le Markdown est rendu ; les extensions propres à Zulip — mentions, émojis,
  liens de canaux — restent affichées littéralement.

## 2.1.3 — 2026-09-02

- Répondre à une conversation dont on est le seul participant fonctionne.
  Zulip la range sous « Messages avec vous-même » ; le pont en retirait
  l'unique destinataire et refusait l'envoi.

## 2.1.2 — 2026-09-02

- Plus aucun nouvel essai immédiat. Une file d'événements rejetée relançait
  l'enregistrement sans la moindre temporisation, seul chemin d'erreur du
  bridge à ne pas appliquer de report exponentiel. Répété, il consommait le
  quota d'API du compte — le même que celui du client Zulip, qui se retrouvait
  alors bloqué en `RATE_LIMIT_HIT`.
- Le délai réclamé par le serveur est désormais lu dans la réponse et
  respecté, y compris au-delà du report maximal, plutôt qu'ignoré.

## 2.1.1 — 2026-09-02

- Un seul bridge à la fois. Le shell peut tenir deux instances du service du
  plugin en vie simultanément — observé après `omarchy plugin update`, qui
  charge la nouvelle avant de détruire l'ancienne. Les deux ouvraient alors
  chacune une file d'événements Zulip et les notifications arrivaient en
  double. Un verrou exclusif fait patienter le second bridge, qui prend le
  relais de lui-même si le premier s'arrête.

## 2.1.0 — 2026-09-02

- Réponse depuis le panneau : `A` ou le bouton ↩ répond dans la conversation
  d'origine, aussi bien pour un message direct que pour un canal.
- La destination d'une réponse est déduite par le bridge depuis son état local ;
  l'interface ne transmet qu'un identifiant de message, jamais un destinataire.
- Envoi dans un canal et son topic, avec repli sur les serveurs Zulip
  antérieurs à la 2.0.

## 2.0.0 — 2026-09-01

- Distribution native via Omarchy Plugin Marketplace avec manifeste racine namespacé.
- Bridge Python supervisé par le service Quickshell du plugin, sans installation systemd.
- Raccourcis Hyprland facultatifs, installables et retirables depuis les réglages.
- Documentation publique bilingue et procédure de retrait conservatrice.

## 1.2.0 — 2026-09-01

- `Super+Z` ouvre directement Zulip Hub ; `Super+Maj+Z` conserve le workspace dédié.
- Interface intégralement pilotable au clavier avec un curseur visible dans toutes les vues.
- Accueil simplifié à « Nouveau MP » et « Réglages » ; les actions avancées sont regroupées dans les réglages.
- Nouveau logo Zulip monochrome vectoriel accordé au thème Omarchy.
- Interface française ou anglaise selon la locale système.
- Redémarrage sûr d’Omarchy Shell après installation ou mise à jour, avec contrôle de la version réellement chargée.

## 1.1.0 — 2026-09-01

- Composeur graphique de messages directs individuels et collectifs.
- Annuaire Zulip recherchable avec contacts récents prioritaires.
- Brouillon conservé uniquement en mémoire et envoi sécurisé par stdin.
- Limite de message fournie par le serveur et protection contre les renvois ambigus.

## 1.0.0 — 2026-09-01

- Mise à jour transactionnelle avec migration versionnée et rollback complet.
- Préservation de l'état actif et activé du service systemd utilisateur.
- Porte de sortie automatisée couvrant Python, QML, Hyprland et le manifeste.
- Licence MIT et documentation utilisateur finale.

## 0.7.0

- Réglages graphiques des notifications, canaux et commandes d'ouverture.
- Diagnostic du bridge et reconnexion persistante.
- Correction de l'encodage JSON des booléens pour l'API Zulip.

## 0.6.0

- Assistant graphique sécurisé pour connecter le compte sans terminal.
- Clé API transmise par stdin et stockée dans Secret Service.

## 0.5.0

- Installation, sauvegarde, activation et désinstallation gérées.

## 0.4.0

- Workspace spécial Hyprland et raccourci `Super+Z`.

## 0.3.0

- Notifications natives sécurisées et liens profonds Zulip.

## 0.2.0

- Widget Omarchy Shell, compteurs et conversations récentes.

## 0.1.0

- Bridge événementiel Zulip, état local atomique et stockage du secret.
