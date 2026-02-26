# 📊 RÉSUMÉ COMPLET - LOGIQUE DU BOT DE TRADING

## 🎯 STRUCTURE GLOBALE

```
┌─────────────────────────────────────────────────────────────────┐
│                    BOUCLE PRINCIPALE (2 minutes)                 │
│  Récupère les données → Évalue conditions → Exécute ordres      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
        ┌───────────────────────┴───────────────────────┐
        ↓                                               ↓
 ┌────────────────┐                            ┌──────────────────┐
 │ POSITION OUVERTE?│                           │POSITION FERMÉE ? │
 │coin_balance >   │                            │coin_balance <=   │
 │  min_qty (0.001)│                            │  min_qty (0.001) │
 └────────────────┘                            └──────────────────┘
        │                                               │
        │ OUI → MODE VENTE                             │ NON → MODE ACHAT
        │                                               │
        ↓                                               ↓
 ┌─────────────────────────────────────┐   ┌──────────────────────────┐
 │   VÉRIFIER CONDITIONS DE VENTE      │   │  VÉRIFIER CONDITIONS    │
 │   (6 signaux possibles)             │   │  D'ACHAT (4 conditions) │
 └─────────────────────────────────────┘   └──────────────────────────┘
        │                                               │
        ├─ PARTIAL-1 (+2%)                             ├─ EMA1 > EMA2
        ├─ PARTIAL-2 (+4%)                             ├─ StochRSI < 80%
        ├─ SIGNAL (EMA croisement)                     ├─ RSI entre 30-70
        ├─ STOP-LOSS                                  ├─ Scénario spécifique
        ├─ TRAILING-STOP                              │
        └─ Reliquat (< 1.02×min_qty)                  └─ USDC > 0
```

---

## 🔴 MODE VENTE (quand `coin_balance > min_qty`)

### **Condition d'entrée :**
```python
position_has_crypto = coin_balance > min_qty  # 0.001 SOL pour SOLUSDC
```
→ Si TRUE : Le bot cherche à VENDRE

### **Signaux de vente (6 possibles) :**

#### **1️⃣ PARTIAL-1 : Prise de profit partielle à +2%**
```
Condition  : current_price >= entry_price × 1.02 (+ 2%)
Action     : Vendre 50% de la position
Puis       : Flag partial_taken_1 = True (sauvegardé immédiatement)
Empêche    : PARTIAL-2 tant que PARTIAL-1 n'est pas fait
```

**Exemple :** 
- Achat à 145 USDC
- PARTIAL-1 se déclenche à ≥ 147.90 USDC
- Vend 0.16 SOL (50% de 0.32 SOL)

#### **2️⃣ PARTIAL-2 : Prise de profit partielle à +4%**
```
Condition  : current_price >= entry_price × 1.04 (+ 4%) ET partial_taken_1 = True
Action     : Vendre 30% du reste
Puis       : Flag partial_taken_2 = True (sauvegardé immédiatement)
Logique    : Seulement après PARTIAL-1 complète
```

**Exemple :**
- Achat à 145 USDC, PARTIAL-1 à 147.90
- PARTIAL-2 se déclenche à ≥ 150.80 USDC
- Vend 30% du reste (de la position restante après PARTIAL-1)

#### **3️⃣ SIGNAL : Croisement baissier (Main strategy)**
```
Conditions :
  - EMA2 > EMA1 (crossover baissier)
  - StochRSI > 0.2 (momentum baissier)
  + Filtres additionnels selon le scénario

Action     : Vendre 100% de la position
Puis       : Reset complet de l'état (entry_price, max_price, etc.)
            et partial_taken_1 = False, partial_taken_2 = False
```

**Exemple :**
- EMA26 = 145.5, EMA50 = 146.2 → EMA2 > EMA1 ✓
- StochRSI = 0.65 > 0.2 ✓
- → SIGNAL VENTE : Vendre 100% du solde

#### **4️⃣ STOP-LOSS : Protection de capital**
```
Condition  : current_price < entry_price - (3 × ATR)
Action     : Vendre 100% immédiatement
Raison     : Perte maximale acceptable
```

**Exemple :**
- Entry = 145 USDC, ATR = 2.76 USDC
- STOP-LOSS = 145 - (3 × 2.76) = 136.72 USDC
- Si prix ≤ 136.72 → Vente d'urgence

#### **5️⃣ TRAILING-STOP : Protection profit avec Tracking**
```
Condition  : current_price < max_price - (5.5 × ATR)
Activation : Quand profit >= 2%
Action     : Vendre 100% si prix baisse
Avantage   : Laisse courir les gains, protège contre retournement
```

**Exemple :**
- Entry = 145 USDC, Max atteint = 150 USDC
- Trailing = 150 - (5.5 × 2.76) = 134.82 USDC
- Si prix redescend ≤ 134.82 → Vente de trailing stop

#### **6️⃣ DUST/RELIQUAT : Nettoyage automatique**
```
Condition  : 0.001% < coin_balance < 0.00098 SOL (1% à 98% de min_qty)
Action     : Tentative vente forcée du résidu
Puis       : Reset complet si succès
But        : Éviter les soldes bloquants < min_qty
```

---

## 🟢 MODE ACHAT (quand `coin_balance <= min_qty`)

### **Condition d'entrée :**
```python
position_has_crypto = coin_balance > min_qty  # FALSE
```
→ Si FALSE : Le bot cherche à ACHETER

### **Conditions d'achat (toutes doivent être TRUE) :**

#### **1️⃣ Condition EMA : Momentum haussier**
```
Condition : EMA1 > EMA2
Logique   : Les EMAs rapides > EMAs lentes = tendance haussière
```

**Exemple :**
- EMA26 = 146.5, EMA50 = 145.0 → 146.5 > 145.0 ✓ ACHAT possible

#### **2️⃣ Condition StochRSI : Momentum overbought**
```
Condition : StochRSI < 0.8 (80%)
Logique   : < 80% = pas overbought, momentum haussier pas saturé
```

**Exemple :**
- StochRSI = 0.65 < 0.8 ✓ ACHAT possible
- StochRSI = 0.85 > 0.8 ✗ Trop overbought, attendre

#### **3️⃣ Condition RSI : Zone saine (momentum filter)**
```
Condition : 30 ≤ RSI ≤ 70
Logique   : 
  - RSI < 30 = Oversold (trop bas, potentiel rebond)
  - RSI > 70 = Overbought (trop haut, potentiel correction)
  - 30-70 = Zone équilibrée pour achat
```

**Exemple :**
- RSI = 50 ✓ Zone saine
- RSI = 20 ✗ Trop oversold
- RSI = 80 ✗ Trop overbought

#### **4️⃣ Condition Volatilité (si disponible)**
```
Condition : Volatilité Zscore entre -1.5 et +1.5
Logique   : Filtre les périodes de volatilité extrême
But       : Éviter d'acheter en crises ou rallyes excessifs
```

#### **5️⃣ Condition MACD (si scénario = StochRSI_TRIX)**
```
Condition : MACD_HISTOGRAM > -0.0005
Logique   : Histogram > 0 = momentum haussier confirmé
```

#### **6️⃣ Conditions additionnelles selon le scénario :**

**Scénario: StochRSI_SMA**
```
Condition : Prix > SMA200 (tendance long terme haussière)
```

**Scénario: StochRSI_ADX**
```
Condition : ADX > 25 (tendance forte confirmée)
```

**Scénario: StochRSI_TRIX**
```
Condition : TRIX_HISTO > 0 (croisement haussier de TRIX)
```

#### **7️⃣ Condition Capital : USDC disponible**
```
Condition : USDC_disponible > 0
Source    : Capital récupéré de TOUTES les ventes depuis dernier achat
Logique   : Jamais utiliser le solde wallet, seulement les ventes
```

---

## 💰 CAPITAL POUR ACHAT (Position Sizing)

### **Calcul du capital:**
```python
usdc_for_buy = get_usdc_from_all_sells_since_last_buy()
```

**Récupère:**
- Toutes les ventes depuis le dernier BUY
- Via l'historique Binance (requête API 500 trades)
- Déduit les frais si en USDC
- **Ne prend JAMAIS le solde wallet en compte**

### **Modes de dimensionnement (4 choix):**

#### **1️⃣ BASELINE (par défaut, 95% du capital)**
```python
gross_coin = (usdc_for_buy * 0.95) / entry_price
# 95% pour sécurité, 5% garde de cash
```

**Exemple :**
- USDC récupérés = 100 USDC
- À acheter = 100 × 0.95 = 95 USDC
- Prix SOL = 145 USDC
- Quantité = 95 / 145 = 0.655 SOL

#### **2️⃣ RISK-BASED (1% risk avec ATR)**
```python
# Calcule la taille pour risquer exactement 1% du capital
# Si ATR = 3, entry = 145 → Stop = 136.8
# Taille = capital / (entry - stop) pour 1% loss
```

#### **3️⃣ FIXED_NOTIONAL (10% du capital par trade)**
```python
notional = usdc_for_buy * 0.1  # 10% du capital par ordre
```

#### **4️⃣ VOLATILITY_PARITY (volatilité fixe)**
```python
# Ajuste la taille selon la volatilité ATR
# Volatilité haute → taille petite
# Volatilité basse → taille grande
```

---

## 🔄 SYNCHRONISATION ET PROTECTION (Triple-Couche)

### **Couche 1️⃣ : Flags locaux (en mémoire)**
```python
pair_state['partial_taken_1'] = True/False
pair_state['partial_taken_2'] = True/False
```
→ Empêche l'exécution répétée dans le même cycle

### **Couche 2️⃣ : Sauvegarde immédiate (fichier)**
```python
save_bot_state()  # APPEL IMMÉDIAT après chaque flag
# Sauve dans bot_state.pkl
```
→ Protège contre les crashes entre cycles

### **Couche 3️⃣ : Vérification API Binance (source de vérité)**
```python
api_partial_1, api_partial_2 = check_partial_exits_from_history(pair, entry_price)
# Reconstruit l'état réel depuis Binance
```

**Si désynchronisation détectée :**
```
Avant : local PARTIAL-1=False, API PARTIAL-1=True
→ Corrige automatiquement : local PARTIAL-1=True
→ Sauvegarde correction
```

---

## 📧 EMAILS ENVOYÉS (Notifications de trading)

### **ACHAT**
```
Sujet  : [BOT CRYPTO] Achat execute - SOLUSDC
Infos  : Quantité, prix, capital utilisé, timestamp
Quand  : À chaque BUY order FILLED
```

### **PARTIAL-1**
```
Sujet  : [BOT CRYPTO] Vente executee - SOLUSDC (PARTIAL-1)
Infos  : 50% vendu, prix entrée/sortie, signal type
Quand  : À chaque PARTIAL-1 FILLED
```

### **PARTIAL-2**
```
Sujet  : [BOT CRYPTO] Vente executee - SOLUSDC (PARTIAL-2)
Infos  : 30% vendu, prix entrée/sortie
Quand  : À chaque PARTIAL-2 FILLED
```

### **SIGNAL (Croisement baissier)**
```
Sujet  : [BOT CRYPTO] Vente executee - SOLUSDC (SIGNAL)
Infos  : 100% vendu, EMA config, timestamp
Quand  : À chaque SIGNAL sell FILLED
```

### **STOP-LOSS / TRAILING-STOP**
```
Sujet  : [BOT CRYPTO] Vente executee - SOLUSDC (STOP-LOSS ou TRAILING-STOP)
Infos  : Type de stop, prix activation, perte %
Quand  : À chaque ordre stop FILLED
```

---

## 🧮 PARAMÈTRES CLÉS (SOLUSDC 4h, StochRSI_ADX)

| Paramètre | Valeur | Role |
|-----------|--------|------|
| **Pair** | SOLUSDC | Paire de trading |
| **Timeframe** | 4h | Chandelles de 4 heures |
| **Scenario** | StochRSI_ADX | Strategy + ADX filter |
| **EMA1** | 26 | EMA rapide |
| **EMA2** | 50 | EMA lente |
| **min_qty** | 0.001 SOL | Min tradable (Binance) |
| **ATR** | Dynamique | Volatilité pour stops |
| **Partial-1** | +2% | 1er prise partielle |
| **Partial-2** | +4% | 2e prise partielle |
| **Stop-Loss** | entry - 3×ATR | Protection de capital |
| **Trailing Stop** | max - 5.5×ATR | Protection profit |
| **Exécution** | Toutes 2 min | Fréquence vérification |

---

## ✅ RÉSUMÉ DE LA LOGIQUE APRÈS CORRECTION

**AVANT (❌ BUG) :**
```
position_has_crypto = coin_balance > 0.0
→ Residue 0.00057 SOL > 0 → MODE VENTE
→ Bloquait les achats même avec solde < tradable
```

**APRÈS (✅ FIXE) :**
```
position_has_crypto = coin_balance > min_qty (0.001)
→ Residue 0.00057 SOL ≤ 0.001 → MODE ACHAT
→ Permet les achats dès que position fermée à 100%
→ Résidu < min_qty traité comme poussière, non comme position ouverte
```

---

## 🎬 WORKFLOW COMPLET - EXEMPLE DE CYCLE

```
CYCLE 1: BOT DÉMARRE (USDC = 100, SOL = 0)
  └─ Position fermée? OUI (SOL ≤ 0.001)
  └─ Condition d'achat? 
     ├─ EMA1 > EMA2? OUI (145.5 > 145.0)
     ├─ StochRSI < 0.8? OUI (0.65)
     ├─ RSI 30-70? OUI (50)
     └─ USDC > 0? OUI (100)
  └─ → ACTION: ACHAT
     └─ Quantité = (100 × 0.95) / 145 = 0.655 SOL
     └─ État: entry_price=145, partial_taken_1=False, partial_taken_2=False
     └─ Email: "Achat execute 0.655 SOL à 145 USDC"

CYCLE 2-5: PRIX MONTE
  └─ Position ouverte? OUI (SOL = 0.655 > 0.001)
  └─ Vérifier signaux:
     ├─ Prix 147.9? ✓ PARTIAL-1 DÉCLENCHÉ
     │  └─ Vend 50% = 0.3275 SOL
     │  └─ Flag: partial_taken_1 = True (sauvegardé)
     │  └─ Email: "PARTIAL-1: 0.3275 SOL vendu"
     └─ État: SOL = 0.3275 (reste), partial_taken_1=True, partial_taken_2=False

CYCLE 6-8: PRIX CONTINUE MONTÉE
  └─ Position ouverte? OUI (SOL = 0.3275)
  └─ Vérifier signaux:
     └─ Prix 150.8? ✓ PARTIAL-2 DÉCLENCHÉ (car partial_taken_1=True)
        └─ Vend 30% du reste = 0.098 SOL
        └─ Flag: partial_taken_2 = True
        └─ Email: "PARTIAL-2: 0.098 SOL vendu"
        └─ État: SOL = 0.2295 (reste), partial_taken_1=True, partial_taken_2=True

CYCLE 9+: PRIX BAISSERAIT
  └─ Position ouverte? OUI (SOL = 0.2295)
  └─ Vérifier signaux:
     ├─ EMA2 > EMA1? OUI (146.2 > 145.1)
     ├─ StochRSI > 0.2? OUI (0.75)
     └─ → SIGNAL VENTE DÉCLENCHÉ
        └─ Vend 100% = 0.2295 SOL
        └─ Email: "SIGNAL: 0.2295 SOL vendu"
        └─ Reset: entry_price=None, partial_taken_1=False, partial_taken_2=False
        └─ État: SOL ≈ 0 (dust peut rester)

CYCLE 10: DUST DÉTECTÉ
  └─ Résidu 0.00089 SOL (entre 1% et 98% de min_qty)?
  └─ → Tentative vente du dust
     └─ Si succès: reset complet
     └─ Si échecc: continue (dust < min_qty = considéré fermé)

CYCLE 11+: RETOUR À L'ACHAT
  └─ Position fermée? OUI (SOL ≤ 0.001)
  └─ Vérifier conditions d'achat...
  └─ → Retour au CYCLE 1
```

---

## 🚨 CAS LIMITES GÉRÉS

| Cas | Logique |
|-----|---------|
| **Bot crash entre PARTIAL-1 et PARTIAL-2** | API sync reconstruit état depuis Binance |
| **bot_state.pkl corrompu** | API sync reconstruit flags de zéro |
| **Résidu < min_qty reste après vente** | Traité comme position fermée, achat possible |
| **PARTIAL-1 non exécutée, PARTIAL-2 possible** | Bloquée jusqu'à PARTIAL-1 = True |
| **Prix oscille autour de seuil de vente** | Même cycle = une seule exécution (flags protègent) |
| **Binance API timeout lors de sync** | Fallback sur flags locaux, continue |
| **Capital insuffisant pour achat minimum** | Log warning, pas d'ordre, attend prochain cycle |

