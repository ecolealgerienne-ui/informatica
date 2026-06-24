# Setup Local — POC IA Migration (VSCode + WSL2)

## Prérequis Windows

- Windows 10/11 avec WSL2 activé
- VSCode installé : https://code.visualstudio.com/
- Git for Windows installé

---

## 1. Activer WSL2 (si pas encore fait)

Dans PowerShell **en administrateur** :

```powershell
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

Redémarrer Windows, puis ouvrir Ubuntu depuis le menu Démarrer et créer ton utilisateur.

---

## 2. Extensions VSCode à installer

Ouvrir VSCode → `Ctrl+Shift+X` → installer :

| Extension | ID | Utilité |
|---|---|---|
| WSL | `ms-vscode-remote.remote-wsl` | Ouvrir VSCode dans WSL |
| Python | `ms-python.python` | Interpréteur, debugger |
| Pylance | `ms-python.vscode-pylance` | Autocomplétion avancée |
| Ruff | `charliermarsh.ruff` | Linter/formatter rapide |
| GitLens | `eamodio.gitlens` | Visualisation git |
| DotENV | `mikestead.dotenv` | Coloration .env |

---

## 3. Setup Python dans WSL2

Ouvrir un terminal Ubuntu :

```bash
# Mise à jour système
sudo apt update && sudo apt upgrade -y

# Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Vérification
python3.11 --version
```

---

## 4. Cloner le repo dans WSL

> IMPORTANT : cloner dans le filesystem WSL, pas dans /mnt/c/

```bash
# Dans le terminal Ubuntu
cd ~
git clone <url-du-repo> informatica
cd informatica
```

Pour récupérer la branche du POC :

```bash
git fetch origin
git checkout claude/laughing-curie-04rzxm
```

---

## 5. Créer l'environnement virtuel

```bash
cd ~/informatica/poc-ia-migration

# Créer le venv
python3.11 -m venv .venv

# Activer
source .venv/bin/activate

# Installer les dépendances
pip install --upgrade pip
pip install -r requirements.txt
```

Pour activer le venv à chaque session, ajouter à `~/.bashrc` :

```bash
echo 'alias poc="cd ~/informatica/poc-ia-migration && source .venv/bin/activate"' >> ~/.bashrc
source ~/.bashrc
```

Ensuite taper `poc` pour se positionner et activer d'un coup.

---

## 6. Configurer les variables d'environnement

```bash
cd ~/informatica/poc-ia-migration
cp .env.example .env
```

Éditer `.env` :

```bash
# .env
BATCH_DATE=2026-01-01
# ANTHROPIC_API_KEY=sk-ant-...   # décommenter en Phase 2
```

> Le fichier `.env` est dans `.gitignore` — il ne sera jamais commité.

---

## 7. Ouvrir VSCode dans WSL

Dans le terminal Ubuntu :

```bash
cd ~/informatica
code .
```

VSCode s'ouvre en mode WSL (indicateur vert `WSL: Ubuntu` en bas à gauche).

Sélectionner l'interpréteur Python :
- `Ctrl+Shift+P` → `Python: Select Interpreter`
- Choisir `.venv/bin/python3.11`

---

## 8. Configurer le debugger VSCode

Créer `.vscode/launch.json` à la racine du repo :

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run Pipeline",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/poc-ia-migration/pipeline/run_pipeline.py",
      "cwd": "${workspaceFolder}/poc-ia-migration",
      "envFile": "${workspaceFolder}/poc-ia-migration/.env",
      "console": "integratedTerminal"
    },
    {
      "name": "Parser Agent",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/poc-ia-migration/agents/parser_agent.py",
      "cwd": "${workspaceFolder}/poc-ia-migration",
      "envFile": "${workspaceFolder}/poc-ia-migration/.env",
      "console": "integratedTerminal"
    },
    {
      "name": "Setup: Generate XML",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/poc-ia-migration/setup/generate_sample_xml.py",
      "cwd": "${workspaceFolder}/poc-ia-migration",
      "console": "integratedTerminal"
    },
    {
      "name": "Setup: Generate Dataset",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/poc-ia-migration/setup/generate_golden_dataset.py",
      "cwd": "${workspaceFolder}/poc-ia-migration",
      "console": "integratedTerminal"
    }
  ]
}
```

---

## 9. Vérification complète

```bash
cd ~/informatica/poc-ia-migration
source .venv/bin/activate

# Générer les fichiers de test
python setup/generate_sample_xml.py
python setup/generate_golden_dataset.py

# Vérifier les outputs
ls input/          # wf_clients_dim.xml
ls tests/          # golden_dataset.csv, ref_statut.csv, expected_output.csv
```

Résultat attendu :
```
[OK] input/wf_clients_dim.xml generated
[OK] tests/ref_statut.csv generated
[OK] tests/golden_dataset.csv generated (30 rows)
[OK] tests/expected_output.csv generated (23 rows after filter)
     Filtered out: 7 inactive clients
```

---

## 10. Structure finale attendue dans VSCode

```
informatica/
└── poc-ia-migration/
    ├── .env                  ← local uniquement, jamais commité
    ├── .venv/                ← local uniquement, jamais commité
    ├── input/
    │   └── wf_clients_dim.xml
    ├── rag_base/
    │   ├── transformation_map.json
    │   ├── python_templates.md
    │   └── pyspark_patterns.md
    ├── agents/               ← à venir
    ├── pipeline/             ← à venir
    ├── output/               ← généré au runtime
    ├── tests/
    │   ├── golden_dataset.csv
    │   ├── ref_statut.csv
    │   └── expected_output.csv
    └── setup/
        ├── generate_sample_xml.py
        └── generate_golden_dataset.py
```

---

## Prochaine étape

Une fois l'environnement validé, on attaque les agents dans cet ordre :
1. `agents/parser_agent.py`
2. `agents/codegen_agent.py`
3. `agents/fixer_agent.py`
4. `agents/qa_agent.py`
5. `pipeline/run_pipeline.py`
