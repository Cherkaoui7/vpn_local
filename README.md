# VpnPrivate - VPN Rotator Windows

VpnPrivate (anciennement VPN Rotator) est un utilitaire réseau local robuste pour Windows, doté d'une interface graphique moderne en Python (CustomTkinter) et d'un CLI complet. Il contrôle le client OpenVPN, change automatiquement de serveur VPN, sélectionne les serveurs avec la latence la plus faible, récupère les identifiants VPNBook si nécessaire, et affiche des notifications Windows lors des changements d'état.

L'application est **100% autonome (portable)** et intègre ses propres binaires OpenVPN, ce qui permet de l'exécuter sur n'importe quel ordinateur Windows sans aucune installation requise.

---

## Fonctionnalités principales

1. **Interface graphique moderne (VpnPrivate)** :
   - **Tableau de bord** : Statut de connexion en temps réel (couleurs dynamiques), pays du serveur connecté, IP publique active, durée de connexion et compte à rebours avant la prochaine rotation.
   - **Sélecteur de serveur** : Menu déroulant listant tous les serveurs disponibles par leurs noms réels de pays (ex: `United Kingdom #68`) avec options *Auto (Plus rapide)* et *Aléatoire*.
   - **Logs en direct** : Zone de texte scrollable affichant en temps réel les logs d'OpenVPN ou du planificateur en arrière-plan.
   - **Gestionnaire de paramètres** : Permet de modifier le fichier `settings.json` (délai de rotation, protocole UDP/TCP, etc.) directement depuis l'interface.
   - **Scrapeur de crédentiels** : Récupère et met à jour automatiquement les mots de passe VPNBook gratuits depuis leur site officiel en un clic.
   - **Test de latence** : Mesure le ping de tous les serveurs `.ovpn` et affiche un classement en direct pour trouver le serveur le plus rapide.

2. **Totalement autonome et portable (VpnPrivate.exe)** :
   - **Binaires OpenVPN intégrés** : Les exécutables et DLL d'OpenVPN 2.7.5 sont empaquetés dans l'application. Aucun téléchargement externe d'OpenVPN n'est nécessaire.
   - **Initialisation automatique** : Au premier démarrage, l'application crée un dossier persistant dans votre profil utilisateur (`C:\Users\<Nom_Utilisateur>\.vpnprivate\`) et y extrait les fichiers de configuration, les paramètres par défaut, et les binaires OpenVPN.

3. **Vérification de routage et fuite IP** : Compare l'IP publique avant et après connexion pour s'assurer que le trafic passe bien par le tunnel VPN.

4. **Notifications Windows** : Envoi de notifications système lors des connexions, déconnexions, rotations et erreurs de routage.

5. **Interface en ligne de commande (CLI)** : Toujours disponible via l'exécutable pour les utilisateurs avancés.

---

## Démarrage rapide

### Mode Exécutable Autonome (Recommandé)

1. Récupère le fichier **`VpnPrivate.exe`** situé à la racine du projet.
2. Déplace-le où tu veux (sur ton **Bureau**, par exemple).
3. Fais un clic droit sur **`VpnPrivate.exe`** et choisis **Exécuter en tant qu'administrateur** (requis pour que l'application puisse modifier la table de routage Windows et attribuer les adresses IP).

*Note : Au premier lancement, l'application créera automatiquement le dossier `~/.vpnprivate/` pour stocker les paramètres locaux et l'historique.*

---

### Exécution depuis le code source

Si tu préfères exécuter l'application depuis les scripts Python :

1. Installe les dépendances dans l'environnement virtuel local :
   ```powershell
   .venv\Scripts\pip install -r requirements.txt
   ```
2. Lance l'interface graphique :
   ```powershell
   .venv\Scripts\python main.py gui
   ```

---

## Configuration (`settings.json`)

Les paramètres de l'application sont chargés depuis `~/.vpnprivate/settings.json` (ou modifiables directement dans l'onglet **Settings** de l'interface graphique).

| Paramètre | Type | Défaut | Description |
| :--- | :--- | :--- | :--- |
| `openvpn_path` | `string` | `openvpn_bin/openvpn.exe` | Chemin vers l'exécutable OpenVPN (utilise par défaut la version intégrée). |
| `configs_dir` | `string` | `configs` | Dossier contenant les fichiers `.ovpn`. |
| `auth_file` | `string` | `auth.txt` | Fichier local contenant les identifiants VPN (récupérés automatiquement). |
| `logs_dir` | `string` | `logs` | Dossier de stockage des logs de connexion. |
| `rotation_seconds` | `int` | `1800` | Intervalle avant la rotation automatique des serveurs (en secondes). |
| `selection_mode` | `string` | `"latency"` | `"latency"` pour utiliser le serveur le plus rapide, ou `"random"`. |
| `avoid_same_server` | `bool` | `true` | Évite de reprendre le même serveur lors de la rotation suivante. |
| `connect_timeout_seconds` | `int` | `25` | Timeout de connexion en TCP. |
| `udp_connect_timeout_seconds` | `int` | `8` | Timeout de connexion en UDP avant le repli automatique sur TCP. |
| `public_ip_check` | `bool` | `true` | Vérifie que l'IP publique a bien changé après la connexion. |
| `force_udp` | `bool` | `true` | Tente de se connecter d'abord en UDP avant d'essayer le TCP. |

---

## Commandes CLI (Optionnel)

Tu peux également utiliser l'exécutable ou le script Python en ligne de commande pour contrôler le VPN en arrière-plan :

### Démarrer le planificateur de rotation
Lance la rotation automatique en tâche de fond. Le terminal peut être fermé; le VPN reste actif.
```powershell
VpnPrivate.exe start
```

### Arrêter le VPN et le planificateur
Coupe le processus d'arrière-plan, ferme la connexion OpenVPN active et nettoie l'état réseau.
```powershell
VpnPrivate.exe stop
```

### Vérifier l'état de la connexion
Affiche l'état courant : serveur actif (nom formaté), PID OpenVPN, heure de connexion, IP publique et temps restant avant la prochaine rotation.
```powershell
VpnPrivate.exe status
```

### Forcer une rotation immédiate
Ferme la connexion au serveur actuel et bascule immédiatement sur un nouveau serveur rapide.
```powershell
VpnPrivate.exe rotate
```

### Se connecter une seule fois (Sans rotation)
Établit une connexion vers le serveur le plus rapide sans démarrer le planificateur de rotation.
```powershell
VpnPrivate.exe once
```

---

## Sécurité Git

Les fichiers locaux sensibles ou temporaires sont ignorés par Git dans le dépôt :
```text
auth.txt
logs/
vpn_state.json
rotator_state.json
```
Ne committez jamais vos identifiants ou fichiers d'état d'exécution.
