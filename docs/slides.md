# FORGE — soutenance

12 slides, cahier §15.5. Speaker notes in blockquotes. Every number here is measured and
traceable to `docs/evaluation.md`, `docs/STATE.md` or a committed test — nothing is
estimated. Render with any markdown-slide tool (`---` separates slides).

---

## 1 · Le problème

Une correction de bug en maintenance, c'est rarement l'écriture du patch.

| Étape | Temps réel |
|---|---|
| Comprendre *où* est le défaut dans 60 kLOC | le plus long |
| Écrire le correctif | quelques lignes |
| Prouver qu'on n'a rien cassé | la suite complète |

Un assistant qui *écrit du code* ne résout que la ligne du milieu.
Un assistant qui **cite ses sources et exécute les tests** attaque les trois.

> Le coût du bug n'est pas la frappe, c'est la recherche et la preuve.

---

## 2 · La solution, et ce que vous allez voir

**FORGE** — six agents spécialisés, deux points d'arrêt humains, un bac à sable.

La démo de 4 minutes :
`index` → question citée → rapport de bug → timeline des agents → approbation du plan
→ tests rouges → diff → **commentaire empoisonné et garde-fou déclenché** → coûts.

> Tout tourne hors-ligne, depuis des fixtures commitées. Pas de clé, pas de quota, pas de wifi.

---

## 3 · Architecture

```
browser ─▶ web/ (React) ─┐
                          ├─▶ api/ (FastAPI + SSE) ─▶ core/ (LangGraph + SQLite)
terminal ─▶ cli/ ────────┘                              │
                                          ┌─────────────┴─────────────┐
                                          ▼                           ▼
                                   rag/ (Qdrant                 sandbox/ (un
                                   + BM25 + rg)                 conteneur jetable)
                                          └────────── guardrails/ ────┘
```

**Deux chemins, pas un** : une *question* et une *demande de changement* n'exécutent pas
le même graphe.

> Point d'honnêteté : c'est l'UI qui choisit le chemin, pas le SUPERVISOR — `Route` n'a
> pas de membre `CHANGE`. C'est consigné (O8) et c'est la première dette à rembourser.

---

## 4 · Les six agents — pourquoi six et pas un

| Agent | Refuse de |
|---|---|
| SUPERVISOR | produire du contenu — il route, rien d'autre |
| RETRIEVER | faire confiance au texte récupéré (il le scanne d'abord) |
| PLANNER | émettre une étape qui ne cite rien |
| EDITOR | écrire sur le disque — il rend des edits, `apply` écrit |
| SANDBOX_ENGINEER | exécuter quoi que ce soit hors du conteneur |
| REVIEWER | approuver un patch dont les tests n'ont pas tourné |

**L'argument** : chaque frontière est un endroit où un objet typé est *validé*. Un agent
unique peut décider de sauter sa propre vérification de citations ; six nœuds avec des
payloads typés ne le peuvent pas — le contrôle vit dans l'arête, pas dans le prompt.

> La porte d'approbation est câblée à la sortie *succès* du planner par construction :
> un nœud ne peut pas la contourner.

---

## 5 · Pipeline RAG

`walker → chunkers (AST tree-sitter) → embed → Qdrant + BM25`

- Un chunk = **une fonction ou une classe**, pas 1000 caractères.
  Une citation tombe donc sur quelque chose de lisible.
- Récupération **hybride** : dense (MiniLM) + sparse (BM25) + ripgrep, fusionnés en RRF.
- Une requête en forme d'identifiant (`Lexer::scan`) **saute la voie dense** — pour un
  symbole exact, le lexical gagne.

617 chunks / 59 fichiers pour sqlparse, réindexation incrémentale en ~2 s.

---

## 6 · Évaluation RAG — l'ablation ★

| Configuration | Recall@10 | nDCG@10 | p95 |
|---|---|---|---|
| Chunking caractères + dense | 0.655 | 0.418 | 10 ms |
| **Chunking AST + dense** | **0.857** | 0.559 | 7 ms |
| AST + hybride (RRF) | 0.750 | **0.596** | 14 ms |
| AST + hybride + reranker | 0.774 | 0.547 | **2589 ms** |
| + expansion parent | 0.786 | 0.547 | 2345 ms |

**Ce que ça dit :**
1. Le chunking AST est la vraie victoire : Recall@10 **0.655 → 0.857**. La thèse centrale
   du cahier est vérifiée.
2. L'hybride améliore le *classement* (nDCG 0.559 → 0.596, MRR 0.474 → 0.561) mais fait
   *baisser* Recall@10 — le lexical ramène du bruit.
3. **Le reranker coûte 185× la latence et rend du nDCG.** Il est donc **désactivé** en
   production et conservé dans le harness d'évaluation.

> C'est la slide la plus forte parce qu'elle contient une décision *négative* mesurée.
> On n'a pas gardé une brique parce qu'elle sonne bien.

---

## 7 · Collaboration — la boucle de réparation

Trace réelle, reconstruite depuis le checkpoint (`notebooks/02_agent_traces.ipynb`) :

`retriever → planner → [PORTE] → regression → editor → [PORTE] → apply → verify → reviewer → editor …`

- **3 interruptions**, **3 passes d'editor** = 2 itérations de réparation
- Le bac à sable renvoie `453 passed, 28 failed` → le reviewer dit `revise`
- L'editor corrige **en lisant les échecs** :

```diff
-    if not isinstance(sql, str):
+    if not isinstance(sql, (str, bytes, TextIOBase)) and not hasattr(sql, 'read'):
```

> Personne ne lui a dit que sqlparse accepte des bytes et des flux. Il l'a déduit des
> 28 régressions. C'est ça, la collaboration : le reviewer et le bac à sable portent une
> information que l'editor n'avait pas au premier essai.

---

## 8 · Guardrails — trois couches

| Couche | Où | Attrape |
|---|---|---|
| `sentinel_in` | avant la récupération | injection directe, secrets, hors-périmètre |
| `injection` | **dans le retriever** | §8.2 — injection *indirecte* dans le code tiers |
| `policy` | avant tout outil / fichier | évasion de chemin, verbes git interdits |
| `sentinel_out` | avant toute sortie | citations invérifiables, secrets générés |

**La démo** : un commentaire *« Ignore all previous instructions… »* planté dans
`sqlparse/lexer.py`.
→ `[REDACTED] injection.override` — **retiré du pack avant que le planner le voie.**

872 événements journalisés, interrogeables par session / couche / action.
Les `allowed` sont journalisés aussi : un log qui n'enregistre que les refus ne prouve
pas qu'un contrôle a tourné.

> Le scan tourne aux **deux** endroits où du texte tiers devient prompt : le nœud RETRIEVER
> et `answer_question`. Il n'a longtemps tourné qu'au premier — `/v1/ask`, le bouton *Ask*
> de l'interface, passait à côté (O6, corrigé au gel). Un chunk propre revient **identique**,
> donc corriger cela n'a invalidé aucune fixture.

---

## 9 · Sécurité du bac à sable

Un conteneur jetable par exécution :
`--network=none` · rootfs en lecture seule · non-root · `cap_drop=ALL` ·
`no-new-privileges` · plafonds mémoire / CPU / PID · `RLIMIT_DATA` · timeout

**Le code de sortie fait autorité** — pas l'avis du modèle. `pytest` dans une boîte sans réseau.

Suite adverse **32/32** (D11). L'image est sans dépendances (pytest + ruff) : un patch ne
peut pas faire entrer un paquet.

> Sous `docker compose`, on **ne monte pas** `/var/run/docker.sock` : un conteneur qui
> tient la socket Docker est équivalent-root sur l'hôte — pire que ce que le bac à sable
> empêche. On accepte donc le repli documenté plutôt qu'une faille élégante.

---

## 10 · DÉMONSTRATION — 4 minutes

`CACHE_MODE=replay` · réseau débranché · fixtures commitées.

1. `docker compose up` → http://localhost:8000
2. Question → réponse **● grounded — 4 citations vérifiées**
3. Rapport de bug → timeline des 6 agents
4. Approbation du plan → tests **rouges** → diff coloré → approbation du patch
5. Commentaire empoisonné → **le panneau garde-fou s'ouvre tout seul**
6. Onglet coûts

> Vidéo de repli prête. Si quoi que ce soit tombe, on la lance et on continue de parler.

---

## 11 · Résultats — et ce qui ne marche pas

**Ce qui marche** : 396 tests verts hors-ligne · **8 portes fermées** (C1–C6, C8, C9) ·
`swe_mini` **4/4 réparés**, 0 régression, 1,0 itération · suite adverse **32/32** ·
`docker compose up` sur clone vierge · pipeline complet piloté dans un navigateur.

**Ce qui ne marche pas — mesuré, pas deviné :**

| # | Limite | Preuve |
|---|---|---|
| O7 | Le saut d'appel : **construit, mesuré, livré désactivé** | SM-01 : le correctif est hors du top-8 ; un saut le ramène pour ~390 jetons. Sur le golden set : **0,000 sur toutes les métriques**, +11,8 % de jetons — ses 42 questions *nomment* déjà le symbole |
| — | Le `swe_mini` 4/4 ne mesure pas la récupération | Le harnais **donne** le bon fichier à l'agent. Le chiffre porte sur la boucle de réparation, pas sur le système |
| O5 | Injection niveau 2 (classifieur) non construite | Heuristiques + spotlighting seulement ; `limitations.md` §7 |
| O8 | C'est l'UI qui route ask/change | `Route` n'a pas de membre `CHANGE` |
| — | Quota gratuit ≈ 20 requêtes/jour/modèle | `429` en pleine réparation le 03/08 |

> **Le dernier défaut trouvé, le jour du gel** : les 37 réponses enregistrées étaient
> indexées sous `gemini-flash-latest`, la configuration livrée disait `gemini-3.5-flash`.
> L'identifiant du modèle fait partie de la clé de cache — la démo hors-ligne était morte
> sur un clone neuf, dépôt parfaitement propre. Ni la suite verte ni le test machine vierge
> ne pouvaient le voir : aucun des deux n'appelle réellement un modèle. Corrigé, et un test
> le verrouille désormais.

> Cinq bugs n'ont été trouvés qu'en *pilotant l'interface*, pas par les tests. C'est en
> soi un résultat : les tests couvrent les pièces, l'usage couvre l'assemblage.

---

## 12 · Limites et suites

- **Multi-dépôts** — un dépôt par processus (`TARGET_REPO`). Une collection Qdrant, pas de
  filtre par dépôt. C'est un choix de périmètre, pas un oubli.
- **Python uniquement** — le chunker AST est tree-sitter Python (coupe assumée, J2).
- **Mémoire long terme** — court terme seulement (checkpoint SQLite).
- **Le saut d'appel (O7)** — construit et livré désactivé. La vraie suite n'est plus le code :
  c'est **un second golden set** écrit dans la forme de SM-01, où le site du correctif n'est
  jamais nommé. Le jeu actuel est structurellement incapable de mesurer la fonctionnalité.
- **L'injection directe est signalée, pas bloquée** (déviation assumée du §13.4,
  `limitations.md` §6) ; le niveau 2 par classifieur n'est pas construit (§7).

**Ce que je referais autrement** : brancher l'interface plus tôt. Les cinq défauts trouvés
le dernier jour étaient tous *inatteignables* tant que rien n'appelait ces routes.

> Merci. Questions.

---

## Annexe — les quatre questions certaines

**« Pourquoi multi-agents plutôt qu'un agent avec des outils ? »**
Parce que chaque frontière valide un objet typé. Un agent unique peut sauter sa propre
vérification de citations ; six nœuds ne le peuvent pas — le contrôle est dans l'arête.
Et la porte d'approbation est câblée par construction, pas demandée dans un prompt.

**« Comment savez-vous qu'il n'hallucine pas ? »**
Je ne fais pas confiance au modèle : chaque citation est revérifiée *en code* contre le
pack réellement récupéré (`ContextPack.supports`). `grounded: false` est le signal honnête
qu'une réponse n'est pas étayée. Et un patch n'est jamais « validé » par un avis — c'est le
code de sortie de pytest dans un conteneur sans réseau.

**« Et si le modèle écrit du code malveillant ? »**
Il ne s'exécute jamais sur l'hôte. Conteneur jetable, sans réseau, rootfs en lecture seule,
non-root, `cap_drop=ALL`, plafonds mémoire/CPU/PID. Suite adverse 32/32. Et l'EDITOR n'écrit
pas sur le disque : il rend des edits qu'un nœud séparé applique, après approbation humaine.

**« Combien coûte une requête ? »**
Une exécution complète ≈ 29 appels LLM / ~300k tokens d'entrée. L'onglet coûts rapporte
tours, appels, tokens et latence par session. **Je ne convertis pas en euros** : rien dans
le backend ne tarifie un fournisseur, et un chiffre inventé sur une slide de résultats
serait pire que pas de chiffre.
