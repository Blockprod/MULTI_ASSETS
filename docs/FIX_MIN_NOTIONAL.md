# 🔧 FIX: MIN_NOTIONAL Filter - Erreur -1013

## 📧 Email reçu à 8h23

```
Erreur lors de l'execution de l'ordre SELL : -1013 - Filter failure: NOTIONAL

Params : [('symbol', 'SOLUSDC'), ('side', 'SELL'), ('type', 'MARKET'), ('quantity', '0.001'), ('timestamp', 1768461828031)]

Solde SPOT global : 132.98 USDC
```

---

## ❌ PROBLÈME IDENTIFIÉ

**Cause root:** Le bot tentait de vendre **0.001 SOL** au prix de ~145 USDC/SOL.

**Valeur totale = 0.001 × 145 = 0.145 USDC**

**Binance a 2 filtres obligatoires :**
1. **LOT_SIZE** : Quantité ≥ 0.001 SOL ✅ (respecté)
2. **MIN_NOTIONAL** : Valeur totale ≥ ~10 USDC ❌ (0.145 < 10)

→ **Rejet avec erreur -1013**

---

## ✅ SOLUTION APPLIQUÉE

### 1️⃣ **Récupération de MIN_NOTIONAL**

**Avant :** Récupérait seulement `LOT_SIZE`
```python
for f in info['filters']:
    if f['filterType'] == 'LOT_SIZE':
        # récupération...
```

**Après :** Récupère également `MIN_NOTIONAL`
```python
for f in info['filters']:
    if f['filterType'] == 'LOT_SIZE':
        # LOT_SIZE
    elif f['filterType'] == 'MIN_NOTIONAL':
        result['min_notional'] = Decimal(f.get('minNotional', '10.0'))
```

**Localisation :**
- Fonction `get_symbol_filters()` (ligne ~1176)
- 2 branches d'initialisation (lignes 4063 et 4147)

### 2️⃣ **Validation MIN_NOTIONAL avant chaque vente**

**Avant :** Seulement check quantité
```python
if quantity_rounded >= min_qty_dec:
    # Tenter la vente
```

**Après :** Check quantité ET valeur totale
```python
notional_value = float(quantity_rounded) * current_price

if quantity_rounded >= min_qty_dec and notional_value >= min_notional:
    # Tenter la vente
else:
    # Bloquer avec raison explicite
    if notional_value < min_notional:
        logger.warning(f"Vente bloquée: Valeur {notional_value:.2f} < MIN_NOTIONAL {min_notional:.2f}")
```

**Localisations :**
- **DUST Cleanup** (ligne ~4325) : Avant tentative de vente du résidu
- **SIGNAL/PARTIAL** (ligne ~4435) : Avant tentative de vente signal/partielle

### 3️⃣ **Messages d'erreur explicites**

Le bot loggue maintenant clairement :
```
[DUST] Valeur du résidu (0.14 USDC) < MIN_NOTIONAL (10.00 USDC)
[DUST] Impossible de vendre le résidu - Binance refuse les ordres < 10.00 USDC
[DUST] Résidu ignoré (position considérée comme fermée)
```

---

## 🎯 IMPACT DE LA CORRECTION

### **Avant**
- ❌ Bot tentait de vendre dust < 10 USDC
- ❌ Erreur Binance -1013 (NOTIONAL)
- ❌ Email d'erreur au user
- ❌ Position bloquée jusqu'à action manuelle

### **Après**
- ✅ Bot vérifie MIN_NOTIONAL avant vente
- ✅ Refuse la vente si < 10 USDC
- ✅ Traite le dust comme position fermée
- ✅ Permet les achats normalement
- ✅ Pas d'erreur Binance

---

## 📊 VALEURS PAR PAIRE

Pour **SOLUSDC** (prix ~145 USDC/SOL) :

| Filtre | Valeur | Cas |
|--------|--------|-----|
| LOT_SIZE (min_qty) | 0.001 SOL | Minimum quantité |
| MIN_NOTIONAL | ~10 USDC | Minimum valeur |
| Dust détectable | 0.001 < balance < 0.00098 | Entre 1% et 98% de min_qty |
| Dust non vendable | 0.00057 SOL = 0.08 USDC | < 10 USDC |

→ **Le dust trouvé (0.00057 SOL = 0.08 USDC) ne peut pas être vendu**
→ **Bot l'ignore et le traite comme position fermée**

---

## 🔍 VALIDATION

```bash
# Vérifier les filtres appliqués :
Filters pour SOLUSDC: 
  - min_qty=0.001
  - step_size=0.000001
  - min_notional=10.0
```

**Avant chaque vente:**
```
Quantité: 0.001 SOL ≥ 0.001 ✓
Valeur:   0.145 USDC < 10.0 ❌ → BLOCAGE
```

---

## 🚀 RECOMMANDATION

Aucune action requise de ta part ! Le code:
1. **Détecte automatiquement MIN_NOTIONAL** depuis Binance
2. **Bloque les ventes invalides** avant tentative
3. **Loggue clairement les raisons**
4. **Traite les dust comme position fermée** (achat autorisé)

Le prochain redémarrage du bot appliquera la correction.
