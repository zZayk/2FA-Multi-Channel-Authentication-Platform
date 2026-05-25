**Entreprise :** L2T Tunisie
**Durée :** 4 mois 
**Domaines :** Développement Logiciel · Cybersécurité · Machine Learning

---
# 1. Contexte et Problématique 

L2T est une entreprise spécialisée dans les solutions de messagerie SMS à destination des entreprises. Dans ce cadre, deux problématiques majeures se posent : 

1. **La sécurisation des accès** : de nombreuses applications clientes ont besoin d'un service d'authentification forte (2FA) fiable, standardisé et facilement intégrable via une API. 

2. **La protection contre les abus** : les plateformes SMS sont régulièrement ciblées par des comportements malveillants tels que le flood d'OTP, l'énumération de numéros, l'usurpation d'expéditeur ou les campagnes d'envoi non autorisées. 

Ce projet vise à concevoir et développer une **plateforme complète** répondant à ces deux problématiques de manière cohérente et intégrée.

---
# 2. Objectifs du Projet 

- Concevoir et développer un **service d'authentification à deux facteurs (2FA) par SMS**, exposé sous forme d'API REST consommable par des applications tierces. 

- Implémenter un **moteur anti-abus** capable de détecter en temps réel les comportements suspects et d'y répondre automatiquement. 

- Fournir un **tableau de bord d'administration** permettant la supervision, la gestion des alertes et la consultation des rapports d'incidents. 

- Livrer une solution **documentée, testée et conteneurisée**, prête à être démontrée en environnement de production. 
---
# 3. Périmètre du Projet (Scope)
## 3.1 Module 2FA — Service OTP

- Génération de codes OTP selon les standards **TOTP (RFC 6238)** et **HOTP (RFC 4226)** 

- Envoi des codes via l'**API SMS de L2T** 

- Gestion du cycle de vie des codes : expiration configurable, usage unique, nombre de tentatives maximum - Exposition d'une **API REST sécurisée** (authentification par clé API, HTTPS

- Documentation interactive via **Swagger / OpenAPI**
## 3.2 Module Anti-Abus — Moteur de Détection

- **Profilage comportemental** des comptes : analyse des volumes d'envoi, fréquences, destinations et contenus 

- **Système de scoring de risque** dynamique basé sur des règles métier configurables (ex. : seuils d'envoi, horaires suspects, géolocalisation via GeoIP) 

- **Détection d'anomalies non supervisée** via l'algorithme Isolation Forest 

- **Traitement asynchrone** des événements en temps réel via une file de messages (Redis + Celery) 

- Gestion automatique et manuelle des **blacklists** (numéros, expéditeurs, plages IP)
## 3.3 Module Dashboard — Interface d'Administration 

- Visualisation en temps réel des **métriques clés** : volume d'OTP envoyés, taux d'échec, score d'abus par compte 

- Gestion des **alertes de sécurité** et des comptes bloqués 

- Génération de **rapports d'incidents** exportables (PDF / CSV) 

- Interface responsive développée avec **React.js** 
---
# 4. Stack Technologique 
| Couche                    | Technologie                     | Rôle                                                    |
| ------------------------- | ------------------------------- | ------------------------------------------------------- |
| **Backend API**           | Python — FastAPI                | Exposition des endpoints REST, logique métier           |
| **Génération OTP**        | `pyotp`                         | Implémentation TOTP/HOTP (RFC 6238/4226)                |
| **File de messages**      | Redis + Celery                  | Traitement asynchrone des événements d'abus             |
| **Détection d'anomalies** | Scikit-learn (Isolation Forest) | Scoring et détection comportementale                    |
| **Analyse de données**    | Pandas                          | Profilage et agrégation des logs                        |
| **Frontend**              | React.js + Chart.js             | Dashboard d'administration temps réel                   |
| **Base de données**       | PostgreSQL                      | Stockage persistant des comptes, logs, incidents        |
| **Cache / Sessions**      | Redis                           | Gestion des OTP actifs et des sessions                  |
| **Conteneurisation**      | Docker + Docker Compose         | Déploiement reproductible de l'ensemble des services    |
| **Documentation API**     | Swagger / OpenAPI               | Documentation interactive et intégration facilitée      |
| **Géolocalisation**       | MaxMind GeoIP2                  | Enrichissement des événements pour le scoring de risque |

---
# 5. Planning Prévisionnel 
| Mois       | Phase                           | Livrables                                                                                       |
| ---------- | ------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Mois 1** | Analyse & Socle 2FA             | Architecture technique, service OTP fonctionnel, intégration API SMS L2T, documentation Swagger |
| **Mois 2** | Moteur Anti-Abus                | Profilage comportemental, règles de scoring, Isolation Forest, pipeline Redis/Celery            |
| **Mois 3** | Dashboard & Reporting           | Interface React, alertes temps réel, gestion des blacklists, export de rapports                 |
| **Mois 4** | Tests, Sécurisation & Livraison | Tests de charge, audit de sécurité basique (OWASP), conteneurisation Docker, rapport de stage   |

---
# 6. Résultats Attendus 

- Une **API 2FA fonctionnelle et documentée**, intégrable par les clients de L2T 

- Un **moteur anti-abus opérationnel** avec des métriques de détection mesurables 

- Un **dashboard d'administration** déployable et utilisable par les équipes internes 