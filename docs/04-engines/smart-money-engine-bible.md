<!--
title: ELYON QUANT — Smart Money Engine Bible
id: ENG-002 (Smart Money Engine, especificación técnica de detectores)
owner: Quant Lead
reviewers: [CTO/Principal Architect, ML Lead, QA Lead]
status: draft
version: 0.2
last_updated: 2026-07-29
supersedes: parcialmente cubierto en trading-engine-bible.md §6–§20
changelog:
  - v0.2 (2026-07-29): contrato de detector ampliado a 18 campos obligatorios;
    añadidos Entradas/Salidas y Diagrama lógico por detector (Apéndice E) y el
    capítulo de Integración con Market Context / Trading / Scoring / Risk (Apéndice F).
  - v0.1 (2026-07-28): 31 detectores (D01–D32) con contrato de 15 campos.
-->

# ELYON QUANT — SMART MONEY ENGINE BIBLE

> **Estándar oficial de detección Smart Money.** Este documento especifica, de
> forma **objetiva, determinista y verificable por tests unitarios**, **todos**
> los detectores que el Trading Engine (ENG-001) consume para leer el mercado.
>
> No es teoría: es una **especificación implementable**. Cada detector se define
> con matemática, reglas exactas, pseudocódigo, complejidad, tests y modos de
> fallo. Dos implementaciones que sigan esta Biblia deben producir **exactamente
> el mismo output** sobre los mismos datos.

---

## 0. Preámbulo

### 0.1 Relación con el Trading Engine
El Trading Engine Bible (ENG-001) define *qué* significa cada concepto y *cómo se
usa* en la decisión (scoring, entradas, riesgo). **Esta Biblia define *cómo se
detecta* cada concepto**, al nivel de detalle necesario para codificarlo. Cuando
ENG-001 dice "Order Block sin mitigar en discount", esta Biblia define
exactamente qué es un Order Block, cuándo está "sin mitigar" y cómo se calcula.

### 0.2 Requisitos transversales (invariantes ⛔)
1. **Determinismo:** dado el mismo `series` + misma `config`, la salida es
   idéntica bit a bit. Prohibido usar reloj de pared, aleatoriedad, orden de
   hash no determinista o estado global mutable no versionado.
2. **Velas cerradas:** todo detector estructural opera sobre **velas cerradas**
   (`use_closed_candles = true`). La vela en formación (`t`) solo se usa para
   gestión de posición, nunca para confirmar estructura.
3. **Causalidad (no look-ahead):** en el instante `t`, un detector solo puede
   usar información de barras `≤ t`. Un swing/OB/FVG se "confirma" con retardo
   conocido (p.ej. un swing high necesita `k` velas posteriores). El retardo de
   confirmación se documenta por detector.
4. **Idempotencia incremental:** procesar la barra `t` sobre un estado ya
   calculado hasta `t-1` produce el mismo resultado que recalcular desde cero
   hasta `t`.
5. **Unidades relativas:** los umbrales se expresan en múltiplos de `ATR` o en
   `pip_size`/`tick_size` del `instrument_profile`, nunca en valores absolutos
   hardcodeados. Esto hace el motor invariante al activo y al régimen.
6. **Trazabilidad:** cada detección emite un registro tipado con `origin_index`,
   `confirm_index`, `params_hash` y `state`, apto para el DecisionRecord (ENG-001
   §39) y la matriz de trazabilidad (BLD-003).

### 0.3 Modelo de datos y notación
- **Serie:** `series` = secuencia ordenada cronológicamente de velas
  `candle[0..n-1]`, índice creciente con el tiempo. `t` = índice de la última
  vela cerrada. `i-k` = `k` velas antes de `i`.
- **Vela `i`:** `O_i` (open), `H_i` (high), `L_i` (low), `C_i` (close),
  `V_i` (volume, según `volume_source` del perfil), `ts_i` (timestamp UTC).
- **Derivados:**
  - `body_i = |C_i − O_i|`
  - `range_i = H_i − L_i` (≥ 0; si `= 0`, vela dogi/plana → tratada como edge case)
  - `upper_wick_i = H_i − max(O_i, C_i)`
  - `lower_wick_i = min(O_i, C_i) − L_i`
  - `bull_i = C_i > O_i`, `bear_i = C_i < O_i`, `doji_i = C_i == O_i`
- **ATR:** `ATR_i` = Average True Range con `atr_period` (def. 14), calculado con
  Wilder smoothing, sobre velas cerradas. `TR_i = max(H_i−L_i, |H_i−C_{i-1}|, |L_i−C_{i-1}|)`.
- **`pip`/`tick`:** `pip_size` del perfil (para FX; `tick_size` para índices/cripto).
- **Zona:** intervalo de precio `[lo, hi]` con `lo ≤ hi`. "Precio toca la zona"
  ≡ `L_j ≤ hi ∧ H_j ≥ lo`. "Precio cierra a través" ≡ `C_j` fuera de `[lo,hi]`
  en la dirección de invalidación.

### 0.4 Plantilla del contrato de detector (18 campos obligatorios)
Cada detector cumple un contrato de **18 campos**. Para no duplicar contenido, los
campos se reparten entre el **cuerpo** del detector (Capas 0–7) y los apéndices
transversales **E** (I/O + diagrama lógico) y **F** (integración de sistema):

| # | Campo obligatorio | Dónde vive |
|---|-------------------|-----------|
| 1 | **Objetivo** | cuerpo del detector |
| 2 | **Definición formal** | cuerpo ("Definición matemática") |
| 3 | **Entradas** | **Apéndice E** (tabla I/O por detector) |
| 4 | **Salidas** | **Apéndice E** (tabla I/O por detector) |
| 5 | **Parámetros configurables** | cuerpo + Apéndice D (catálogo) |
| 6 | **Algoritmo paso a paso** | cuerpo ("Reglas de detección" = pasos numerados) |
| 7 | **Pseudocódigo** | cuerpo |
| 8 | **Diagrama lógico** | **Apéndice E** (flujo por detector) |
| 9 | **Casos válidos** | cuerpo |
| 10 | **Casos inválidos** | cuerpo |
| 11 | **Invalidaciones** | cuerpo |
| 12 | **Edge cases** | cuerpo |
| 13 | **Complejidad computacional** | cuerpo |
| 14 | **Casos de prueba** | cuerpo |
| 15 | **Falsos positivos** | cuerpo |
| 16 | **Falsos negativos** | cuerpo |
| 17 | **Dependencias** | cuerpo ("Relaciones") + Apéndice B (DAG) |
| 18 | **Integración con otros detectores / sistemas** | cuerpo ("Relaciones") + **Apéndice F** |

> Regla: **ningún detector puede quedar sin cualquiera de los 18 campos.** La
> completitud se verifica en CI (test de documentación: cada `D0x` debe resolver
> sus 18 campos entre cuerpo y apéndices).

### 0.5 Orden de ejecución (pipeline / DAG de dependencias)
Los detectores se ejecutan por capas; cada capa consume la anterior. Se documenta
en dependencia-orden (no en el orden de la lista original; ver **matriz de
cobertura**, Apéndice A, para el mapeo 1:1 con la lista solicitada).

```
Capa -1 Gate de contexto:  MARKET CONTEXT ENGINE (ENG-011) — se ejecuta ANTES.
                           Si su gate = FAIL, NADA de lo de abajo se ejecuta.
Capa 0  Primitivos:        Displacement, Imbalance
Capa 1  Estructura:        Swing High, Swing Low → Internal/External Structure
Capa 2  Eventos:           BOS, CHoCH, MSS
Capa 3  Liquidez:          Liquidity(model) → BSL, SSL, Equal Highs/Lows
                           → Liquidity Sweep → Inducement
Capa 4  Imbalance zonas:   FVG, Inverse FVG, Balanced Price Range
Capa 5  Order-flow POIs:   Order Block, Mitigation, Breaker, Rejection Block
Capa 6  Rango & pricing:   Dealing Range → Premium/Discount/Equilibrium → OTE
Capa 7  Contexto:          Volume Confirmation, Session Context
```

---

# Capa 0 — Primitivos

## D01 · Displacement

**Definición matemática.** Un *displacement* en `[a, b]` (a<b) es un tramo
direccional donde el desplazamiento neto de precio domina sobre el ruido:
`disp_move = |C_b − O_a|`, y se cumple `disp_move ≥ displacement_atr_mult · ATR_b`
**y** la fracción de cuerpos a favor `body_ratio = Σ body_i / Σ range_i (i∈[a,b]) ≥ displacement_body_ratio`,
con dirección `dir = sign(C_b − O_a)`.

**Objetivo.** Distinguir movimiento institucional (intención) de corrección/ruido.
Es prerrequisito de BOS/CHoCH/MSS válidos y de FVG/OB de calidad.

**Parámetros.** `displacement_atr_mult` (def. 1.5) · `displacement_body_ratio`
(def. 0.6) · `displacement_max_bars` (def. 3, longitud máx. del tramo) ·
`displacement_min_bars` (def. 1).

**Reglas de detección.**
1. Para una ventana que termina en `b` con longitud `L ∈ [min,max]`, sea `a=b−L+1`.
2. Calcular `disp_move`, `body_ratio`, `dir`.
3. Es displacement si `disp_move ≥ displacement_atr_mult·ATR_b` **y**
   `body_ratio ≥ displacement_body_ratio` **y** todas (o `≥ ceil(L·0.66)`) las
   velas comparten `dir` de cierre.
4. Preferir la ventana **más corta** que cumpla (movimiento más "limpio").

**Casos válidos.** Vela única de rango `2·ATR` con cuerpo 80 %. Tres velas
alcistas consecutivas con cuerpos grandes que suman `2.2·ATR`.

**Casos inválidos.** Tres velas que suman `2·ATR` pero solapadas y con mechas
dominantes (`body_ratio 0.35`) → corrección, no displacement. Vela grande pero
`bear` dentro de una secuencia alcista.

**Invalidaciones.** Un displacement es un evento puntual (no persiste como zona);
"se invalida" solo en el sentido de que un FVG/BOS que dependía de él se anula si
el precio revierte por completo el tramo dentro de `displacement_reversal_bars`.

**Edge cases.** `range_i = 0` (doji plano) → excluir de `Σ range` para no dividir
por cero; si `Σ range = 0`, no hay displacement. Gaps de sesión: usar `TR` con
cierre previo. ATR aún no "caliente" (primeras `atr_period` velas) → detector
devuelve `insufficient_data`.

**Pseudocódigo.**
```
function isDisplacement(series, b, cfg):
    for L in cfg.min_bars .. cfg.max_bars:
        a = b - L + 1
        if a < 0: continue
        dir = sign(C[b] - O[a])
        if dir == 0: continue
        move = abs(C[b] - O[a])
        sumBody = sum(body[i] for i in a..b)
        sumRange = sum(range[i] for i in a..b if range[i] > 0)
        if sumRange == 0: continue
        bodyRatio = sumBody / sumRange
        agree = count(sign(C[i]-O[i]) == dir for i in a..b)
        if move >= cfg.atr_mult * ATR[b]
           and bodyRatio >= cfg.body_ratio
           and agree >= ceil(L * 0.66):
            return Displacement(a, b, dir, move)   // shortest wins
    return none
```

**Complejidad.** Por barra: `O(max_bars)` (constante). Full scan: `O(n·max_bars)`
= `O(n)`.

**Casos de prueba.**
- T1: velas `[range=2·ATR, body=0.85·range, bull]` → `Displacement(dir=+1)`.
- T2: 3 velas solapadas `body_ratio=0.4` sumando `1.6·ATR` → `none`.
- T3: serie con `< atr_period` velas → `insufficient_data`.

**Falsos positivos.** Spikes de baja liquidez (rollover, apertura) que cumplen
ATR pero no son institucionales → mitigar con Session Context (D30) y spread.
**Falsos negativos.** Displacement escalonado en `> max_bars` velas → subir
`displacement_max_bars` por perfil.

**Relaciones.** Entrada de: BOS (D08), CHoCH (D09), MSS (D10), FVG (D18),
Order Block (D21). Consume: ATR.

---

## D02 · Imbalance

**Definición matemática.** *Imbalance* = ineficiencia de precio: negociación
unilateral donde falta solape entre velas no adyacentes. Forma general de 3 velas
`[i-1, i, i+1]`: **imbalance alcista** si `L_{i+1} > H_{i-1}` (gap
`= L_{i+1} − H_{i-1} > 0`); **imbalance bajista** si `H_{i+1} < L_{i-1}`
(gap `= L_{i-1} − H_{i+1}`). (El *Fair Value Gap*, D18, es la instancia canónica
de este primitivo; "Imbalance" es el concepto general que incluye también gaps de
apertura y *volume imbalance* vela-a-vela.)

**Objetivo.** Cuantificar zonas que el mercado tiende a rellenar; sustrato de FVG,
BPR e IFVG y confirmación de displacement.

**Parámetros.** `imbalance_min_size` (def. 0.10·ATR) · `imbalance_type`
∈ {`fvg3`(def.), `gap`, `volume`} · `imbalance_fill_threshold` (def. 0.5,
consequent encroachment).

**Reglas de detección.** Recorrer tríadas; marcar imbalance si `gap ≥ imbalance_min_size`.
Registrar zona `[lo,hi]`, `dir`, `size`, `state ∈ {unfilled, partially_filled, filled}`.
Estado se actualiza con cada nueva vela: `partially_filled` si el precio penetra
≥ `imbalance_fill_threshold` de la zona; `filled` si la rellena entera con cierre
al otro lado.

**Casos válidos.** Tríada con `L_{i+1} − H_{i-1} = 0.4·ATR`.
**Casos inválidos.** Solape (`L_{i+1} ≤ H_{i-1}`) → no hay imbalance. Gap `< min_size`.

**Invalidaciones.** `state → filled` cuando `C_j` cierra al lado opuesto tras
rellenar; un imbalance `filled` deja de ser POI (puede volverse IFVG, D19).

**Edge cases.** Doji central (`range_i≈0`) amplía el gap → válido si supera min.
Gaps de fin de semana (FX abre lunes) → clasificar `imbalance_type=gap`, tratar
aparte de FVG intradía. Series con huecos de datos → no confundir hueco de feed
con imbalance real (validar continuidad temporal `ts`).

**Pseudocódigo.**
```
function detectImbalance(series, i, cfg):
    up = L[i+1] - H[i-1]
    dn = L[i-1] - H[i+1]
    if up >= cfg.min_size:  return Imbalance([H[i-1], L[i+1]], +1, up)
    if dn >= cfg.min_size:  return Imbalance([H[i+1], L[i-1]], -1, dn)
    return none
```

**Complejidad.** Por barra `O(1)`; full scan `O(n)`; mantenimiento de estado de
zonas abiertas `O(m)` con `m` = imbalances vivos (acotado por poda, ver D18).

**Casos de prueba.** T1 gap up `0.4·ATR` → alcista unfilled. T2 solape → none.
T3 relleno al 60 % → partially_filled. T4 cierre al otro lado → filled.

**Falsos positivos.** Micro-gaps por baja liquidez → `min_size` filtra.
**Falsos negativos.** Imbalance rellenado y reevaluado como IFVG si no se rastrea
el estado → obligación de mantener el ciclo de vida.

**Relaciones.** Generaliza FVG (D18); alimenta BPR (D20), IFVG (D19); confirma
Displacement (D01).

---

# Capa 1 — Estructura

## D03 · Swing High

**Definición matemática.** `i` es *swing high* de grado `k` si
`H_i > H_{i−j}` **y** `H_i > H_{i+j}` para todo `j ∈ [1, k]` (comparación
estricta). Confirmación con retardo de `k` velas (`confirm_index = i + k`).

**Objetivo.** Primitivo de estructura: base de tendencia, BOS/CHoCH, liquidez BSL,
dealing range.

**Parámetros.** `swing_lookback` (`k`): `swing_lookback_major` (def. 5),
`swing_lookback_internal` (def. 2) · `swing_strict` (def. true, `>` vs `≥`) ·
`swing_equal_tol` (def. 0, tolerancia para tratar casi-iguales).

**Reglas de detección.** Para cada `i` con `k` velas a cada lado disponibles,
verificar dominancia del `high`. Emitir `SwingHigh(index=i, price=H_i, grade=k,
confirm_index=i+k)`. Etiquetar luego como HH/LH comparando con el swing high
previo del mismo grado.

**Casos válidos.** `k=2`, highs `[10,11,13,12,11]` centro `13` → swing high.
**Casos inválidos.** Meseta `[10,13,13,12]` con `swing_strict` → **no** swing (no
estricto); con `swing_equal_tol>0` podría fusionarse (ver edge cases).

**Invalidaciones.** Un swing high no se "invalida" (es histórico); pierde
relevancia como nivel operable cuando es **barrido** (D16) o cuando queda fuera de
`structure_max_lookback` (poda). Su etiqueta HH/LH puede recalcularse si aparece
un swing intermedio omitido (no ocurre con detección causal correcta).

**Edge cases.** **Highs iguales** (mesetas): con `swing_strict=true` no producen
swing en el centro; se resuelven vía Equal Highs (D14). **Bordes de serie:** los
últimos `k` no confirmables → estado `pending`. **Datos insuficientes** (`i<k`).
**Empates por tolerancia:** `swing_equal_tol` colapsa dos highs a `swing_equal_tol`
en uno (útil en cripto con ruido de tick).

**Pseudocódigo.**
```
function isSwingHigh(series, i, k, strict):
    if i-k < 0 or i+k > lastClosed: return PENDING_or_INSUFFICIENT
    for j in 1..k:
        left  = strict ? (H[i] > H[i-j]) : (H[i] >= H[i-j])
        right = strict ? (H[i] > H[i+j]) : (H[i] >= H[i+j])
        if not (left and right): return false
    return true   // confirmed at i+k
```

**Complejidad.** Por candidato `O(k)`; full scan `O(n·k)` = `O(n)` (k constante).
Incremental: al cerrar la barra `i+k`, evaluar candidato `i` en `O(k)`.

**Casos de prueba.** T1 pico claro → swing confirmado en `i+k`. T2 meseta estricta
→ false. T3 `i+k` aún no cerrado → pending. T4 primeras `k` barras → insufficient.

**Falsos positivos.** Ruido en TF muy bajos genera swings triviales → usar grado
mayor o TF superior para estructura.
**Falsos negativos.** `k` demasiado grande omite swings reales de rango pequeño →
usar estructura *internal* (`k` pequeño) además de *external*.

**Relaciones.** Base de External/Internal Structure (D05/D06), BOS/CHoCH (D08/D09),
BSL (D12), Equal Highs (D14), Dealing Range (D25).

---

## D04 · Swing Low

**Definición matemática.** Espejo de D03: `i` es swing low de grado `k` si
`L_i < L_{i−j}` **y** `L_i < L_{i+j}` ∀`j∈[1,k]`.

**Objetivo / Parámetros / Reglas.** Idénticos a D03 sustituyendo `H→L` y `>→<`.
Etiquetado HL/LL frente al swing low previo.

**Casos válidos/inválidos, invalidaciones, edge cases.** Espejo exacto de D03
(mesetas de lows → Equal Lows D15; bordes → pending; empates → `swing_equal_tol`).

**Pseudocódigo.**
```
function isSwingLow(series, i, k, strict):
    if i-k<0 or i+k>lastClosed: return PENDING_or_INSUFFICIENT
    for j in 1..k:
        left  = strict ? (L[i] < L[i-j]) : (L[i] <= L[i-j])
        right = strict ? (L[i] < L[i+j]) : (L[i] <= L[i+j])
        if not (left and right): return false
    return true
```

**Complejidad.** `O(k)` por candidato; `O(n)` full scan.
**Casos de prueba.** Espejo de D03.
**Falsos positivos/negativos.** Espejo de D03.
**Relaciones.** SSL (D13), Equal Lows (D15), estructura y rango.

---

## D05 · External Structure (Swing Structure)

**Definición matemática.** Secuencia de swings de **grado mayor**
(`swing_lookback_major`) etiquetada: `HH` si `SH_n.price > SH_{n-1}.price`, `LH`
si `<`; `HL` si `SL_n.price > SL_{n-1}.price`, `LL` si `<`. La estructura externa
define la **tendencia macro** del timeframe: `bullish` ⇔ patrón dominante HH+HL;
`bearish` ⇔ LH+LL; `range` en otro caso.

**Objetivo.** Establecer el bias direccional del timeframe (input del MTF y del
scoring HTF-align).

**Parámetros.** `swing_lookback_major` (def. 5) · `structure_min_swings` (def. 2
por lado para declarar tendencia) · `range_tol_atr` (def. 0.25, solape que degrada
a range).

**Reglas de detección.** Mantener listas ordenadas de swing highs/lows mayores;
al confirmar cada nuevo swing, etiquetar y actualizar `trend_state` +
`protected_high`/`protected_low` (el HL protegido en alcista, el LH protegido en
bajista — referencia de CHoCH, D09).

**Casos válidos.** SH: `12→13→14`, SL: `9→10→11` → `bullish`.
**Casos inválidos.** SH sube pero SL baja simultáneamente (expansión de rango) →
`range`/ambiguo, no tendencia.

**Invalidaciones.** El `trend_state` cambia solo por CHoCH/MSS (D09/D10); un swing
aislado no lo cambia.

**Edge cases.** Primeros swings (sin previo para comparar) → `undetermined`.
Doble techo/suelo → `range`. Swings simultáneos por grado alto → resolver por
`confirm_index` (el que se confirma antes se ordena antes; empate imposible con
índices distintos → determinista).

**Pseudocódigo.**
```
function updateExternalStructure(state, newSwing):
    if newSwing.isHigh:
        newSwing.label = newSwing.price > state.lastHigh.price ? HH : LH
        state.lastHigh = newSwing
    else:
        newSwing.label = newSwing.price > state.lastLow.price ? HL : LL
        state.lastLow = newSwing
    if enough(state) and pattern(state) == {HH,HL}: state.trend = BULLISH
    elif pattern(state) == {LH,LL}:                 state.trend = BEARISH
    else:                                           state.trend = RANGE
    recomputeProtectedLevels(state)
```

**Complejidad.** Incremental `O(1)` por swing confirmado; `O(s)` reconstrucción
(`s`=nº de swings).

**Casos de prueba.** T1 HH+HL×2 → bullish. T2 LH+LL → bearish. T3 HH+LL → range.
T4 un solo swing → undetermined.

**Falsos positivos.** Etiquetar tendencia con 1 swing → `structure_min_swings` lo
evita. **Falsos negativos.** Tendencia lenta con `k` alto tarda en etiquetar →
complementar con internal.

**Relaciones.** Consume D03/D04; alimenta BOS/CHoCH/MSS (D08–D10), Dealing Range
(D25), premium/discount y el bias HTF del scoring (ENG-001 §26).

---

## D06 · Internal Structure

**Definición matemática.** Igual que External (D05) pero sobre swings de **grado
menor** (`swing_lookback_internal`), y **acotada dentro** del último tramo externo
(entre el `protected_low` y el swing high externo vigente, o viceversa). Describe
el micro-movimiento que ocurre *dentro* de la pierna externa.

**Objetivo.** Anticipar cambios (CHoCH interno) y localizar inducement (D17) y POIs
de refinamiento antes de que la estructura externa reaccione.

**Parámetros.** `swing_lookback_internal` (def. 2) · `internal_range_ref` ∈
{`last_external_leg`(def.), `dealing_range`}.

**Reglas de detección.** Detectar swings internos y etiquetarlos **relativos al
tramo externo actual**. Un CHoCH interno (D09) señala posible fin del retroceso
interno y reanudación externa; un *internal BOS* confirma continuación fina.

**Casos válidos.** Dentro de una pierna alcista externa, micro LL→LH→HL→HH interno
= retroceso que termina y reanuda al alza.
**Casos inválidos.** Swings internos que exceden el tramo externo (ya no son
internos → recalcular external).

**Invalidaciones.** Se reinicia al confirmarse un nuevo swing externo (nuevo tramo
→ nueva estructura interna).

**Edge cases.** Estructura interna == externa cuando el TF es muy limpio (pocas
oscilaciones) → interno degenera a externo, válido. Ruido excesivo → muchos swings
internos; poda por `structure_max_internal`.

**Pseudocódigo.**
```
function updateInternalStructure(state, series, cfg):
    leg = state.currentExternalLeg
    internalSwings = detectSwings(series within leg, cfg.internal_k)
    label(internalSwings relative to leg.direction)
    detect internalBOS / internalCHoCH on internalSwings
    return InternalStructure(internalSwings, internalEvents)
```

**Complejidad.** `O(L)` con `L`=tamaño del tramo externo; amortizado `O(n)`.

**Casos de prueba.** T1 retroceso interno con CHoCH interno alcista → señal de
reanudación. T2 interno rompe el rango externo → promueve a external.

**Falsos positivos.** CHoCH interno frecuente en chop → filtrar con displacement.
**Falsos negativos.** `internal_k` muy alto oculta el micro-giro → def. bajo.

**Relaciones.** Consume D03/D04/D05; base de Inducement (D17), del modelo de
entrada LTF (ENG-001 §27) y del CHoCH interno (D09).

---

## D07 · (reservado — cohesión de estructura)
> Sección reservada para mantener la numeración `D0x` alineada con capas; la
> estructura queda cubierta por D03–D06. Los eventos estructurales continúan en
> D08.

---

# Capa 2 — Eventos estructurales

## D08 · BOS (Break of Structure)

**Definición matemática.** Dado `trend_state` (D05), un **BOS** ocurre cuando el
precio cierra más allá del **último swing extremo en la dirección de la tendencia**:
- BOS alcista (tendencia bullish): `∃ j : C_j > SH_last.price` con
  `confirmation = close` (o `H_j > SH_last.price` si `bos_confirmation=wick`).
- BOS bajista (tendencia bearish): `C_j < SL_last.price`.
Requiere `isDisplacement` en el tramo de ruptura si `bos_requires_displacement`.

**Objetivo.** Confirmar **continuación** de tendencia; validar la vigencia de POIs
a favor y habilitar entradas de continuación.

**Parámetros.** `bos_confirmation` ∈ {`close`(def.), `wick`} ·
`bos_requires_displacement` (def. true) · `bos_failure_bars` (def. 3) ·
`bos_level_ref` ∈ {`swing_high/low`(def.), `structural_extreme`}.

**Reglas de detección.**
1. Mantener `SH_last`/`SL_last` de estructura externa (D05).
2. Al cerrar `j`, si la condición de cierre/mecha supera el nivel en la dirección
   de tendencia → candidato BOS.
3. Validar displacement (D01) en `[j−L+1, j]`. Si falla → `weak_bos` (marca, no
   confirma) o descartar según `bos_requires_displacement`.
4. Emitir `BOS(dir, level, break_index=j, displacement=…)`; actualizar estructura.

**Casos válidos.** Tendencia alcista, `SH_last=1.2050`, vela cierra `1.2075` con
displacement `1.8·ATR` → BOS alcista.
**Casos inválidos.** Cierre `1.2051` sin displacement (mecha de 1 pip) → no BOS
(o weak). Ruptura en tendencia `range` (sin tendencia definida) → no es BOS (podría
ser MSS/CHoCH según contexto).

**Invalidaciones.** BOS **falla** si dentro de `bos_failure_bars` el precio cierra
de vuelta al lado interior del nivel roto → `bos_failed` (señal de trampa/sweep,
relacionar con D16). Un BOS fallido puede anteceder un CHoCH.

**Edge cases.** Ruptura por gap de apertura (nivel superado sin trading) → válido
si hay displacement post-gap; marcar `gap_break`. Ruptura y cierre exacto en el
nivel (`C_j == level`) → **no** rompe (requiere estrictamente `>`/`<`). Nivel
barrido por mecha con `bos_confirmation=close` → no BOS (es sweep, D16).

**Pseudocódigo.**
```
function detectBOS(state, series, j, cfg):
    if state.trend == BULLISH:
        level = state.lastHigh.price
        broke = cfg.confirmation==close ? C[j] > level : H[j] > level
        dir = +1
    elif state.trend == BEARISH:
        level = state.lastLow.price
        broke = cfg.confirmation==close ? C[j] < level : L[j] < level
        dir = -1
    else: return none
    if not broke: return none
    if cfg.requires_displacement and not isDisplacement(series, j, dispCfg):
        return WeakBOS(dir, level, j)
    return BOS(dir, level, j)
```

**Complejidad.** `O(1)` por barra (+ `O(max_bars)` del displacement) = `O(1)`.

**Casos de prueba.** T1 cierre>nivel + displacement → BOS. T2 cierre>nivel sin
displacement → weak/none. T3 cierre==nivel → none. T4 reversión en 2 velas →
bos_failed.

**Falsos positivos.** Rupturas marginales sin intención → displacement + `close`.
**Falsos negativos.** BOS por gap descartado por no ver displacement clásico →
regla `gap_break`.

**Relaciones.** Consume D01, D05; distinto de CHoCH (D09: dirección opuesta) y MSS
(D10). Confirma continuación para OB/entradas (ENG-001 §7).

---

## D09 · CHoCH (Change of Character)

**Definición matemática.** **Primer** cierre en contra de la tendencia vigente que
rompe el **swing protegido**:
- CHoCH alcista (tendencia bearish): `C_j > protected_high` (el último LH
  relevante).
- CHoCH bajista (tendencia bullish): `C_j < protected_low` (el último HL
  relevante).
Con displacement si `choch_requires_displacement`.

**Objetivo.** Señalar **posible reversión** (cambio de carácter). Gatillo primario
de reversión del modelo de entrada LTF (ENG-001 §27) y disparador de re-etiquetado
del bias.

**Parámetros.** `choch_confirmation` (def. close) · `choch_requires_displacement`
(def. true) · `choch_scope` ∈ {`internal`, `external`, `both`(def.)} ·
`protected_level_ref` (HL en alcista / LH en bajista).

**Reglas de detección.** Igual mecánica que BOS pero **contra** la tendencia y
contra el **swing protegido** (no el extremo de continuación). Al confirmarse:
marcar `CHoCH(dir)`, cambiar `trend_state` provisional (interno) o definitivo
(external), redefinir dealing range y POIs.

**Casos válidos.** Tendencia bajista con `protected_high (LH)=1.1030`; cierra
`1.1045` con displacement → CHoCH alcista.
**Casos inválidos.** Ruptura del extremo de continuación (nuevo LL en bajista) →
eso es BOS bajista, **no** CHoCH. Ruptura sin displacement → sospecha de sweep.

**Invalidaciones.** CHoCH se invalida si el precio retoma inmediatamente la
tendencia previa cerrando de vuelta (`choch_failed`) dentro de `bos_failure_bars`
→ frecuentemente indica que el CHoCH era un barrido (D16), no un cambio real.

**Edge cases.** CHoCH **interno** vs **externo**: el interno (dentro del tramo)
anticipa; el externo confirma cambio de bias. Un CHoCH que además barre liquidez
(sweep del protected level) es de mayor calidad (relacionar con D16). `range` sin
tendencia → el "primer rompimiento" se trata como MSS (D10), no CHoCH.

**Pseudocódigo.**
```
function detectCHoCH(state, series, j, cfg):
    if state.trend == BEARISH:
        level = state.protectedHigh   // last LH
        broke = cfg.confirmation==close ? C[j] > level : H[j] > level
        dir = +1
    elif state.trend == BULLISH:
        level = state.protectedLow    // last HL
        broke = cfg.confirmation==close ? C[j] < level : L[j] < level
        dir = -1
    else: return none   // range -> see MSS
    if not broke: return none
    if cfg.requires_displacement and not isDisplacement(series, j, dispCfg):
        return WeakCHoCH(dir, level, j)
    return CHoCH(dir, level, j, sweptLiquidity = wasSwept(level, series, j))
```

**Complejidad.** `O(1)` por barra.

**Casos de prueba.** T1 cierre>protected_high (bearish) + displacement → CHoCH
alcista. T2 nuevo LL en bearish → clasifica BOS, no CHoCH. T3 CHoCH y reversión →
choch_failed. T4 CHoCH con sweep del protected → `sweptLiquidity=true`.

**Falsos positivos.** Barridos del protected level que cierran de vuelta →
displacement + seguimiento `choch_failed`. **Falsos negativos.** Reversión que
empieza por estructura interna y `choch_scope=external` la ignora → def. `both`.

**Relaciones.** Consume D01, D05, D06; opuesto conceptual de BOS (D08); precede a
MSS (D10) cuando el cambio se consolida; entrada del scoring de estructura
(ENG-001 §26 factor 2).

---

## D10 · MSS (Market Structure Shift)

**Definición matemática.** Cambio de estructura **confirmado y sostenido**: un
CHoCH (D09) **seguido** de un BOS en la nueva dirección, o un CHoCH con
displacement que además **barre** liquidez significativa y establece un nuevo
swing en la dirección contraria. Formalmente, MSS en `j` ⇔ `CHoCH(dir) at j` **y**
(`subsequent BOS(dir)` dentro de `mss_confirm_bars` **o**
`displacement_strong ∧ swept_significant_liquidity`).

**Objetivo.** Distinguir un **cambio de tendencia real** (MSS) de un simple primer
rompimiento que podría fallar (CHoCH aislado). Es la confirmación de mayor peso
para invertir el bias del timeframe.

**Parámetros.** `mss_confirm_bars` (def. 10) · `mss_requires_bos_followthrough`
(def. true) · `mss_min_displacement_atr` (def. 2.0, más exigente que BOS/CHoCH) ·
`mss_requires_sweep` (def. false, si true exige sweep previo).

**Reglas de detección.**
1. Tras un CHoCH(dir), abrir ventana `mss_confirm_bars`.
2. Confirmar MSS si aparece BOS(dir) (nuevo swing roto a favor del nuevo sentido)
   **o** si el CHoCH tuvo `displacement ≥ mss_min_displacement_atr` con sweep.
3. Emitir `MSS(dir, choch_index, confirm_index)`; fijar `trend_state = dir` a nivel
   externo definitivo.

**Casos válidos.** CHoCH alcista + BOS alcista 4 velas después → MSS alcista.
**Casos inválidos.** CHoCH sin follow-through dentro de la ventana → permanece
`CHoCH` provisional; si falla → `choch_failed`, sin MSS.

**Invalidaciones.** MSS se invalida (raro) si tras confirmarse el precio produce
inmediatamente un CHoCH opuesto con displacement (whipsaw estructural) → registrar
`mss_reversed` y marcar régimen inestable (posible chop, ENG-001 §37).

**Edge cases.** Diferenciación **CHoCH vs MSS**: no son sinónimos — CHoCH = primer
aviso; MSS = confirmación. Algunas escuelas los igualan; aquí **MSS ⊃ CHoCH**
(todo MSS parte de un CHoCH, no todo CHoCH llega a MSS). En `range`, el primer
CHoCH que rompe el rango con displacement fuerte se clasifica directamente como
MSS de inicio de tendencia.

**Pseudocódigo.**
```
function detectMSS(state, series, cfg):
    if state.pendingCHoCH is null: return none
    ch = state.pendingCHoCH
    withinWindow = (currentIndex - ch.index) <= cfg.confirm_bars
    if not withinWindow:
        state.pendingCHoCH = null; return none
    bosFollow = detectBOS(stateAfter(ch), series, currentIndex, bosCfg) matches ch.dir
    strong = ch.displacementATR >= cfg.min_disp and (not cfg.requires_sweep or ch.sweptLiquidity)
    if (cfg.requires_bos_followthrough and bosFollow) or (not cfg.requires_bos_followthrough and strong):
        return MSS(ch.dir, ch.index, currentIndex)
    return none
```

**Complejidad.** `O(1)` amortizado por barra (ventana acotada).

**Casos de prueba.** T1 CHoCH+BOS mismo sentido en ventana → MSS. T2 CHoCH sin
follow-through → sin MSS. T3 CHoCH fuerte+sweep con `requires_bos=false` → MSS.
T4 MSS seguido de CHoCH opuesto → mss_reversed.

**Falsos positivos.** Whipsaw en chop puede fabricar MSS → subir
`mss_min_displacement_atr`/exigir sweep por perfil. **Falsos negativos.** Cambio
real lento que excede `mss_confirm_bars` → ampliar ventana.

**Relaciones.** Consume D08, D09, D01, D16; es la señal de cambio de bias de mayor
peso; gobierna re-definición de Dealing Range (D25) y POIs.

---

# Capa 3 — Liquidez

## D11 · Liquidity (modelo general)

**Definición matemática.** Una *liquidity pool* es un nivel de precio `p` con
`type ∈ {BSL, SSL}` donde se acumulan órdenes en espera. Se instancia a partir de
extremos de precio "obvios": swing highs/lows, equal highs/lows, extremos de
sesión/día/semana. Cada pool tiene `strength(p)` (función monótona creciente del
nº de toques, antigüedad e importancia temporal) y `state ∈ {intact, swept}`.

**Objetivo.** Modelo unificado que alimenta BSL/SSL, sweeps, inducement y objetivos
de TP. El motor asume que el precio se dirige **hacia** la liquidez intacta.

**Parámetros.** `liquidity_sources` (def. `[swing, equal, session, pdh_pdl, pwh_pwl]`)
· `liquidity_max_pools` (def. 50, poda por proximidad/fuerza) ·
`liquidity_strength_weights` (toques, edad, jerarquía temporal).

**Reglas de detección.** Al confirmarse cada fuente (swing/equal/extremo temporal),
crear/actualizar el pool: fusionar con pools existentes a distancia
`< equal_level_tol`; sumar toques; recomputar `strength`. Marcar `swept` cuando un
Liquidity Sweep (D16) lo consume.

**Casos válidos.** Swing high confirmado → BSL intacto. Dos EQH + PDH cercanos →
un pool BSL de alta fuerza (fusión).
**Casos inválidos.** Nivel interior sin extremo (no es high/low relevante) → no es
liquidez. Pool duplicado a `<tol` no fusionado → error de mantenimiento.

**Invalidaciones.** `intact → swept` tras sweep. Poda de pools muy antiguos/débiles
(`> liquidity_max_pools`) o fuera de `structure_max_lookback`.

**Edge cases.** Pools que se re-forman tras barrido (nuevo swing en el mismo precio)
→ nuevo pool con historial. Solapamiento denso en cripto → `equal_level_tol` mayor.
Jerarquía temporal: PWH/PWL > PDH/PDL > sesión > swing intradía en `strength`.

**Pseudocódigo.**
```
function upsertLiquidity(pools, level, type, origin, cfg):
    near = findPool(pools, level, tol=cfg.equal_tol, type)
    if near: near.touches++; near.origins.add(origin); near.strength = recompute(near)
    else:    pools.add(Pool(level, type, origin, touches=1, state=INTACT))
    prune(pools, cfg.max_pools)
```

**Complejidad.** Upsert `O(log m)` con índice ordenado por precio; poda `O(m)`.

**Casos de prueba.** T1 swing high → BSL intact. T2 EQH+PDH fusionan → strength↑.
T3 sweep → swept. T4 poda al superar max_pools.

**Falsos positivos.** Marcar como liquidez niveles no-extremos → restringir a
fuentes válidas. **Falsos negativos.** Ignorar trendline liquidity → opción
`enable_trendline_liquidity`.

**Relaciones.** Especializa en BSL/SSL (D12/D13), Equal H/L (D14/D15); consumido por
Sweep (D16), Inducement (D17), Dealing Range (D25) y TP (ENG-001 §30).

---

## D12 · Buy Side Liquidity (BSL)

**Definición matemática.** Pool de liquidez con `type = BSL` situado **por encima**
del precio: sobre swing highs, equal highs, PDH/PWH, máximos de sesión. Representa
buy-stops y stop-loss de cortos. `BSL = { p ∈ Liquidity : p ≥ referencePrice ∧ type=BSL }`.

**Objetivo.** Objetivo alcista del precio (imán) y zona de TP para largos / origen
de reversión bajista tras su barrido en premium.

**Parámetros.** Heredados de D11; `bsl_sources` (subset de `liquidity_sources`).

**Reglas de detección.** Filtrar pools BSL intactos por encima del precio; ordenar
por proximidad y fuerza; el más relevante es `target_bsl`.

**Casos válidos.** EQH por encima intactos → BSL objetivo.
**Casos inválidos.** Máximo ya barrido (swept) → no es objetivo (salvo re-formado).

**Invalidaciones.** Barrido (D16) o cierre decidido por encima consolidando (deja
de ser techo).

**Edge cases.** BSL muy cercano vs lejano: el motor puede buscar el cercano como
"inducement" y el lejano como objetivo real (ver D17). Multiplicidad de BSL →
priorizar por `strength`.

**Pseudocódigo.**
```
function buySideLiquidity(pools, price):
    return sort(filter(pools, p -> p.type==BSL and p.state==INTACT and p.level>price),
                by = (proximity(price), -strength)).firstAsTarget()
```

**Complejidad.** `O(m log m)` (o `O(m)` con estructura ordenada).

**Casos de prueba.** T1 dos BSL intactos → target = más fuerte/cercano. T2 target
barrido → siguiente BSL.

**Falsos positivos/negativos.** Como D11.
**Relaciones.** Subtipo de D11; objetivo de Sweep bajista y TP de largos.

---

## D13 · Sell Side Liquidity (SSL)

**Definición matemática.** Espejo de D12: pools `type = SSL` **por debajo** del
precio (swing lows, equal lows, PDL/PWL, mínimos de sesión). Sell-stops y SL de
largos.

Todos los campos (objetivo, parámetros, reglas, casos, invalidaciones, edge cases,
pseudocódigo, complejidad, tests, FP/FN, relaciones) son **espejo exacto** de D12
con `BSL→SSL`, `por encima→por debajo`, `largos→cortos`. `target_ssl` = SSL intacto
más relevante por debajo del precio; objetivo de reversión alcista tras barrido en
discount y TP de cortos.

```
function sellSideLiquidity(pools, price):
    return sort(filter(pools, p -> p.type==SSL and p.state==INTACT and p.level<price),
                by = (proximity(price), -strength)).firstAsTarget()
```

**Relaciones.** Subtipo de D11; objetivo de Sweep alcista y TP de cortos.

---

## D14 · Equal Highs (EQH)

**Definición matemática.** Conjunto de ≥ `equal_min_touches` swing highs cuyos
precios caen dentro de una banda: `max(H) − min(H) ≤ equal_level_tol`, separados
≥ `equal_min_separation_bars`. Nivel del cluster `p* = mean(H)` (o `max(H)`,
según `equal_anchor`). Genera un pool BSL de fuerza incrementada.

**Objetivo.** Identificar imanes de liquidez de alta probabilidad de barrido y
objetivos de TP.

**Parámetros.** `equal_level_tol` (def. 0.10·ATR) · `equal_min_touches` (def. 2) ·
`equal_min_separation_bars` (def. 3) · `equal_anchor` ∈ {`mean`(def.),`extreme`} ·
`equal_max_touches_window` (ventana de agrupación).

**Reglas de detección.** Sobre swing highs confirmados (D03), agrupar los que estén
dentro de `equal_level_tol` y respeten la separación mínima; emitir `EqualHighs(level=p*,
touches, indices, strength)`.

**Casos válidos.** Highs `1.2050 / 1.2052` con `tol=0.10·ATR≈3 pips`, separados 6
velas → EQH.
**Casos inválidos.** Highs a 12 pips con `tol≈3` → no iguales. Dos highs adyacentes
(misma oscilación, separación 1) → no cuentan (`min_separation`).

**Invalidaciones.** EQH se marca `swept` al ser barrido (D16); deja de ser objetivo.

**Edge cases.** Tres+ toques → mayor fuerza. Highs "casi iguales" en el límite de
`tol` → decisión determinista por `≤` estricto sobre el valor de `equal_level_tol`
congelado (no ATR variable dentro del cluster: usar `ATR` del último toque para
reproducibilidad). Mesetas planas (D03 no dio swing) → EQH puede formarse a partir
de máximos locales de grado interno.

**Pseudocódigo.**
```
function detectEqualHighs(swingHighs, cfg, atrAtEval):
    tol = cfg.equal_level_tol * atrAtEval
    clusters = groupByPriceBand(swingHighs, tol, minSep=cfg.min_separation_bars)
    return [ EqualHighs(anchor(c, cfg.equal_anchor), c.size, c.indices)
             for c in clusters if c.size >= cfg.min_touches ]
```

**Complejidad.** Ordenar por precio `O(s log s)` + barrido lineal de clustering
`O(s)`.

**Casos de prueba.** T1 dos highs dentro de tol, sep 6 → EQH. T2 fuera de tol →
none. T3 tres toques → strength alta. T4 barrido → swept.

**Falsos positivos.** `tol` grande agrupa niveles distintos → def. conservador.
**Falsos negativos.** `tol` pequeño en cripto ruidoso → subir por perfil.

**Relaciones.** Especializa D11/D12 (BSL); objetivo primario de Sweep (D16) y de TP.

---

## D15 · Equal Lows (EQL)

**Definición matemática.** Espejo de D14 sobre swing lows: ≥ `equal_min_touches`
lows dentro de `equal_level_tol`. Genera pool SSL reforzado. Todos los campos son
espejo de D14 (`H→L`, `BSL→SSL`), con `p* = mean(L)` o `min(L)`.

```
function detectEqualLows(swingLows, cfg, atrAtEval):
    tol = cfg.equal_level_tol * atrAtEval
    clusters = groupByPriceBand(swingLows, tol, minSep=cfg.min_separation_bars)
    return [ EqualLows(anchor(c), c.size, c.indices) for c in clusters if c.size >= cfg.min_touches ]
```

**Relaciones.** Especializa D11/D13 (SSL); objetivo de Sweep alcista y TP de cortos.

---

## D16 · Liquidity Sweep (Stop Hunt)

**Definición matemática.** Barrido de un pool de liquidez `p` (D11) con rechazo:
existe una vela (o secuencia ≤ `sweep_confirm_bars`) tal que penetra el nivel y
cierra de vuelta.
- Sweep de BSL (arriba): `H_j > p + sweep_min_penetration` **y** `C_j < p` **y**
  `upper_wick_j / range_j ≥ sweep_wick_ratio`.
- Sweep de SSL (abajo): `L_j < p − sweep_min_penetration` **y** `C_j > p` **y**
  `lower_wick_j / range_j ≥ sweep_wick_ratio`.

**Objetivo.** Detectar la manipulación (toma de liquidez) que precede al movimiento
real. Es el temporizador clave de las entradas de reversión (ENG-001 §12/§27).

**Parámetros.** `sweep_min_penetration` (def. 0.05·ATR) · `sweep_wick_ratio`
(def. 0.5) · `sweep_confirm_bars` (def. 1–2) · `sweep_close_back` (def. true,
exige cierre de vuelta) · `sweep_requires_reaction` (def. false, exige impulso
posterior en `k` velas).

**Reglas de detección.** Para cada pool intacto cercano, al cerrar `j` evaluar
penetración + cierre de vuelta + mecha dominante. Al confirmar: marcar pool `swept`,
emitir `Sweep(pool, dir, penetration, index=j)`. Si `sweep_confirm_bars>1`,
permitir que el cierre de vuelta ocurra en `j+1`.

**Casos válidos.** SSL en `1.1000`; vela `L=1.0994, C=1.1006`, mecha inferior 60 %
del rango → sweep alcista.
**Casos inválidos.** Cierre **más allá** del nivel (`C_j > p` en BSL) → no es sweep,
es ruptura (posible BOS/CHoCH). Penetración `< min` → toque, no barrido. Mecha
pequeña (`< ratio`) con cierre lejos → ruptura, no rechazo.

**Invalidaciones.** Un "sweep" se re-clasifica como **ruptura** si en las siguientes
`sweep_confirm_bars` el precio cierra sostenidamente al otro lado (el rechazo
falló). El evento sweep en sí es histórico (no se borra), pero su implicación
direccional se anula.

**Edge cases.** **Sweep vs breakout** es la distinción crítica: misma penetración,
distinto cierre. Doble barrido (barre BSL y luego SSL, o viceversa) en rango →
`double_sweep`, señal de trampa (turtle soup). Sweep por gap → validar con `TR`.
Sweep de varios pools apilados en una sola vela → un evento por pool, mismo `j`.

**Pseudocódigo.**
```
function detectSweep(pools, series, j, cfg):
    results = []
    for p in poolsNear(price, pools):
        pen = cfg.min_penetration * ATR[j]
        if p.type==BSL and H[j] > p.level + pen and C[j] < p.level
           and upperWick[j]/range[j] >= cfg.wick_ratio:
            p.state = SWEPT; results.add(Sweep(p, dir=-1, j))   // BSL sweep -> bearish bias
        if p.type==SSL and L[j] < p.level - pen and C[j] > p.level
           and lowerWick[j]/range[j] >= cfg.wick_ratio:
            p.state = SWEPT; results.add(Sweep(p, dir=+1, j))   // SSL sweep -> bullish bias
    return results
```

**Complejidad.** `O(poolsNear)` por barra ≈ `O(1)` con índice de proximidad.

**Casos de prueba.** T1 penetra SSL y cierra arriba con mecha 60 % → sweep alcista.
T2 cierra por debajo del SSL → breakout, no sweep. T3 mecha 30 % → none. T4
penetración 0.02·ATR → none.

**Falsos positivos.** Mechas de baja liquidez → combinar con Session Context (D30)
y spread. **Falsos negativos.** Cierre de vuelta en `j+1` con `sweep_confirm_bars=1`
→ subir a 2.

**Relaciones.** Consume D11–D15; habilita CHoCH/MSS de calidad (D09/D10),
Inducement (D17); factor 3 del scoring (ENG-001 §26).

---

## D17 · Inducement (IDM)

**Definición matemática.** *Inducement* es la liquidez **menor** (swing interno o
equal high/low pequeño) situada **antes** de un POI, cuya toma "induce" a los
minoristas a entrar prematuramente y provee el combustible para que el precio
alcance el POI real. Formalmente: dado un POI en la dirección de la tendencia, el
IDM es el `SSL`/`BSL` interno más cercano **entre** el precio actual y el POI, en
el lado contrario al movimiento esperado. La validez del POI suele requerir que su
IDM haya sido **barrido** primero.

**Objetivo.** Filtrar POIs "vírgenes" no fiables: un POI cuyo inducement **no** ha
sido tomado tiene menor probabilidad de reacción. El IDM también refina el timing
de entrada (se espera sweep de IDM → reacción en POI).

**Parámetros.** `inducement_scope` ∈ {`internal`(def.)} · `inducement_lookback`
(rango de búsqueda entre precio y POI) · `inducement_required` (def. true para
setups de alta convicción) · `inducement_tol` (= `equal_level_tol`).

**Reglas de detección.**
1. Identificar el POI objetivo (OB/FVG, D18/D21) en la dirección del bias.
2. Buscar el primer swing interno opuesto (D06) entre el precio y el POI → candidato
   IDM.
3. El setup es "válido con inducement" si ese IDM ha sido barrido (D16) antes de que
   el precio alcance el POI.

**Casos válidos.** Bias alcista, bullish OB abajo; existe un micro swing low (IDM)
sobre el OB; el precio barre ese IDM y luego reacciona en el OB → setup válido.
**Casos inválidos.** POI sin ningún swing menor previo (no hay inducement) → setup
`no_inducement` (menor score). IDM identificado pero **no** barrido cuando el precio
llega al POI → esperar o descartar.

**Invalidaciones.** Si el precio alcanza el POI **sin** barrer el IDM y lo atraviesa,
el POI se considera de baja calidad (probable continuación, no reacción).

**Edge cases.** Múltiples inducements apilados → tomar el más cercano al POI como
IDM operativo. Inducement == protected level del CHoCH (coinciden) → refuerza la
señal. En tendencias muy limpias sin retrocesos internos, el inducement puede no
existir → `inducement_required=false` por perfil para no vetar todo.

**Pseudocódigo.**
```
function detectInducement(poi, internalStructure, sweeps, price, cfg):
    zone = between(price, poi.level)
    idm = nearestOppositeInternalSwing(internalStructure, zone, poi.direction)
    if idm is null: return Inducement(state = ABSENT)
    taken = existsSweep(sweeps, idm) before price reaches poi
    return Inducement(level = idm.level, state = taken ? TAKEN : PENDING)
```

**Complejidad.** `O(s_internal)` en la zona (acotado) ≈ `O(1)` amortizado.

**Casos de prueba.** T1 IDM barrido antes del POI → TAKEN (válido). T2 sin swing
menor → ABSENT. T3 IDM presente no barrido → PENDING (esperar). T4 IDM y protected
level coinciden → refuerzo.

**Falsos positivos.** Confundir el objetivo de liquidez mayor con inducement →
inducement es siempre el **menor/cercano**, el objetivo es el mayor/lejano.
**Falsos negativos.** `inducement_lookback` corto omite el IDM real → calibrar.

**Relaciones.** Consume D06, D11–D16; refina la validez de POIs (D21–D24) y el
modelo de entrada (ENG-001 §27). Concepto avanzado clave que separa SMC "retail" de
"institucional".

---

# Capa 4 — Zonas de imbalance

## D18 · Fair Value Gap (FVG)

**Definición matemática.** Instancia canónica del imbalance de 3 velas (D02):
- **Bullish FVG** en `i`: `L_{i+1} > H_{i-1}`, zona `[H_{i-1}, L_{i+1}]`,
  `size = L_{i+1} − H_{i-1}`.
- **Bearish FVG** en `i`: `H_{i+1} < L_{i-1}`, zona `[H_{i+1}, L_{i-1}]`,
  `size = L_{i-1} − H_{i+1}`.
Válido si `size ≥ fvg_min_size` y la vela central `i` es de displacement (D01).
Nivel de precisión `CE` (consequent encroachment) = `mid(zone)`.

**Objetivo.** Zona de reentrada de alta probabilidad (el precio tiende a rellenar
el gap) y confirmación de intención institucional detrás de BOS/CHoCH/OB.

**Parámetros.** `fvg_min_size` (def. 0.10·ATR) · `fvg_requires_displacement`
(def. true) · `fvg_fill_threshold` (def. 0.5 → CE) · `fvg_invalidate_on` ∈
{`close_through`(def.), `full_fill`} · `fvg_max_active` (poda).

**Reglas de detección.** Recorrer tríadas cerradas; si cumple, emitir
`FVG(dir, zone, CE, state=unfilled, origin_index=i)`. Actualizar estado con cada
vela: `partially_filled` si penetra ≥ `fvg_fill_threshold`; `filled/invalidated`
si cierra a través.

**Casos válidos.** `H_{i-1}=1.2000`, `L_{i+1}=1.2010`, `size=0.10·ATR`, vela central
displacement → bullish FVG `[1.2000,1.2010]`, CE `1.2005`.
**Casos inválidos.** Gap `< fvg_min_size`. Vela central sin displacement
(`fvg_requires_displacement=true`) → descartar. Solape (no hay gap).

**Invalidaciones.** `close_through`: `C_j < zone.lo` (bullish) → invalidated y
candidato a IFVG (D19). `full_fill`: precio recorre toda la zona.

**Edge cases.** FVG dentro de otro FVG (anidados) → registrar ambos; el mayor
domina. FVG creado por gap de apertura de sesión → marcar `session_gap`, tratar con
`imbalance_type=gap`. Relleno exacto hasta el borde (`H_j == zone.lo`) → no invalida
(requiere cruce estricto). Vela central doji con mechas largas → puede crear FVG
grande; válido si supera min.

**Pseudocódigo.**
```
function detectFVG(series, i, cfg):
    if L[i+1] > H[i-1]:
        size = L[i+1]-H[i-1]
        if size >= cfg.min_size*ATR[i] and (not cfg.req_disp or isDisplacement(series,i,dcfg)):
            return FVG(+1, [H[i-1], L[i+1]], CE=mid, UNFILLED, i)
    if H[i+1] < L[i-1]:
        size = L[i-1]-H[i+1]
        if size >= cfg.min_size*ATR[i] and (not cfg.req_disp or isDisplacement(series,i,dcfg)):
            return FVG(-1, [H[i+1], L[i-1]], CE=mid, UNFILLED, i)
    return none

function updateFVG(fvg, candle_j, cfg):
    if crossesFully(candle_j, fvg.zone): fvg.state = FILLED
    elif closeThrough(candle_j, fvg.zone, fvg.dir): fvg.state = INVALIDATED  // -> IFVG
    elif penetration(candle_j, fvg.zone) >= cfg.fill_threshold: fvg.state = PARTIAL
```

**Complejidad.** Detección `O(1)`/barra; mantenimiento de activos `O(a)`
(`a`=FVG vivos, acotado por `fvg_max_active`).

**Casos de prueba.** T1 gap 0.4·ATR + displacement → FVG unfilled. T2 gap<min →
none. T3 penetración 60 % → partial. T4 cierre por debajo (bullish) → invalidated.

**Falsos positivos.** Micro-gaps / gaps de sesión sin intención → `min_size` +
`requires_displacement`. **Falsos negativos.** FVG válido con vela central de rango
justo por debajo del umbral de displacement → calibrar `displacement_atr_mult`.

**Relaciones.** Instancia de D02; confirma BOS/CHoCH/MSS (D08–D10) y OB (D21);
insumo de IFVG (D19), BPR (D20), OTE (D29) y del factor imbalance del scoring.

---

## D19 · Inverse Fair Value Gap (IFVG)

**Definición matemática.** Un FVG (D18) **invalidado por cierre a través** invierte
su polaridad y se convierte en IFVG, actuando como S/R en sentido contrario:
- Un **bullish FVG** con `C_j < zone.lo` → **bearish IFVG** sobre la misma zona
  (ahora resistencia).
- Un **bearish FVG** con `C_j > zone.hi` → **bullish IFVG** (soporte).
Estado inicial `active`; se refuerza si coincide con un CHoCH (D09) simultáneo.

**Objetivo.** POI de reversión/confirmación de precisión: tras el fallo de un FVG,
la zona invertida ofrece entradas de alta calidad (frecuente en el modelo LTF
sweep→CHoCH→IFVG).

**Parámetros.** `ifvg_confirm` ∈ {`close_through`(def.)} · `ifvg_requires_choch`
(def. false; true endurece) · `ifvg_max_retests` · hereda `fvg_*`.

**Reglas de detección.** Al invalidarse un FVG por cierre a través, instanciar IFVG
de polaridad opuesta sobre la misma zona; registrar `origin_fvg`, `flip_index=j`.
En cada retest, evaluar rechazo (mecha) para confirmar su vigencia.

**Casos válidos.** Bullish FVG `[1.2000,1.2010]`; vela cierra `1.1996` → bearish
IFVG `[1.2000,1.2010]`; en el retest el precio rechaza a la baja.
**Casos inválidos.** FVG solo **rellenado** sin cierre a través → sigue siendo FVG,
no IFVG. Cierre a través por 1 tick sin cuerpo (mecha) con `close_through` de cuerpo
→ no flip.

**Invalidaciones.** El IFVG se invalida si el precio vuelve a cerrar a través en
sentido inverso (re-flip) o tras `ifvg_max_retests` fallidos → `spent`.

**Edge cases.** Cadena de flips (FVG→IFVG→re-flip) en zonas muy disputadas → limitar
con `ifvg_max_retests` y marcar zona `contested` (evitar operar). IFVG que coincide
con OB/breaker → confluencia fuerte. IFVG intradía anulado por gap nocturno.

**Pseudocódigo.**
```
function promoteToIFVG(fvg, flipCandle_j, cfg):
    if fvg.state != INVALIDATED: return none
    invDir = -fvg.dir
    if cfg.requires_choch and not chochAt(j, invDir): return none
    return IFVG(invDir, fvg.zone, origin=fvg, flip_index=j, state=ACTIVE)
```

**Complejidad.** `O(1)` por flip; retest tracking `O(a)`.

**Casos de prueba.** T1 bullish FVG cerrado por debajo → bearish IFVG. T2 relleno
sin cierre a través → sigue FVG. T3 re-flip → spent. T4 IFVG+CHoCH → reforzado.

**Falsos positivos.** Zonas contested generan IFVG efímeros → filtrar por
`ifvg_requires_choch`/confluencia. **Falsos negativos.** Flip por mecha ignorado con
`close_through` de cuerpo → aceptable (prioriza calidad).

**Relaciones.** Deriva de D18; refuerza con D09; POI de reversión junto a Breaker
(D23); usado en el modelo de entrada premium (ENG-001 §17/§27).

---

## D20 · Balanced Price Range (BPR)

**Definición matemática.** Solapamiento de un **FVG alcista** y un **FVG bajista**
en la misma banda de precio, formados en momentos distintos: `BPR = zone(bullFVG) ∩
zone(bearFVG) ≠ ∅`. La intersección es una zona de doble imbalance (oferta y demanda
consumidas) que actúa como S/R de precisión.

**Objetivo.** POI de alta calidad: la superposición de dos imbalances opuestos marca
un nivel donde el precio reaccionó con fuerza en ambos sentidos → reentrada fiable.

**Parámetros.** `bpr_min_overlap` (def. 0.05·ATR, intersección mínima) ·
`bpr_max_gap_bars` (def. 20, separación temporal máx. entre los dos FVG) ·
hereda `fvg_*`.

**Reglas de detección.** Mantener FVG activos (D18); al crear un FVG, buscar un FVG
de dirección opuesta cuya zona intersecte con `overlap ≥ bpr_min_overlap` y dentro de
`bpr_max_gap_bars`; emitir `BPR(zone=intersection, dir_context, indices)`.

**Casos válidos.** Bearish FVG `[1.2010,1.2025]` y, 8 velas después, bullish FVG
`[1.2005,1.2018]` → BPR `[1.2010,1.2018]`.
**Casos inválidos.** Dos FVG opuestos sin intersección → no BPR. Intersección
`< bpr_min_overlap`. Separados `> bpr_max_gap_bars`.

**Invalidaciones.** BPR se invalida cuando el precio cierra decididamente a través de
toda su zona (ambos imbalances rellenados y superados).

**Edge cases.** BPR anidado en OTE o en un OB → confluencia máxima. Múltiples FVG
solapados → tomar la intersección común. BPR con las dos FVG del mismo displacement
extendido (raro) → válido si direcciones opuestas reales.

**Pseudocódigo.**
```
function detectBPR(activeFVGs, newFVG, cfg):
    for f in activeFVGs where f.dir == -newFVG.dir
        and abs(f.origin_index - newFVG.origin_index) <= cfg.max_gap_bars:
        inter = intersect(f.zone, newFVG.zone)
        if width(inter) >= cfg.min_overlap*ATR:
            return BPR(inter, [f, newFVG])
    return none
```

**Complejidad.** `O(a)` por nuevo FVG (`a`=FVG activos).

**Casos de prueba.** T1 FVG opuestos con solape 0.6·ATR → BPR. T2 sin solape →
none. T3 separación 40 velas → none. T4 BPR dentro de OTE → confluencia.

**Falsos positivos.** Solapes triviales → `bpr_min_overlap`. **Falsos negativos.**
FVG opuestos algo separados en tiempo → ampliar `bpr_max_gap_bars`.

**Relaciones.** Consume D18; POI premium junto a OB/Breaker/IFVG; refuerza OTE (D29)
y el scoring de imbalance.

---

# Capa 5 — Order-flow POIs

> **Marco común de POI.** Los cuatro detectores de esta capa emiten un objeto
> `POI{ type, dir, zone[lo,hi], mean(50%), origin_index, state ∈ {fresh,
> tested, mitigated, invalidated}, confidence }`. Comparten reglas de
> **mitigación** (`poi_mitigation_threshold`, def. 0.5: `tested` al tocar,
> `mitigated` al penetrar ≥ umbral) e **invalidación** (`poi_invalidate_on_close`,
> def. true: cierre de cuerpo a través de la zona → `invalidated`). Se refinan en
> LTF y se puntúan en el scoring (ENG-001 §26 factor 4).

## D21 · Order Block (OB)

**Definición matemática.** La **última vela contraria** antes de un displacement
(D01) que rompe estructura (BOS/CHoCH, D08/D09):
- **Bullish OB:** sea `m` el índice de inicio del displacement alcista; el OB es la
  última vela con `bear_k` (C<O) en `k ≤ m`, `k` máximo tal que `bear_k`. Zona =
  `[L_k, H_k]` (`ob_zone_mode=full`) o `[C_k, O_k]` (`body`). `mean = mid(zone)`.
- **Bearish OB:** última vela `bull` antes de un displacement bajista.
Validez ⇔ displacement posterior **y** (rompe estructura **o** deja FVG) **y**
`state≠invalidated`.

**Objetivo.** Zona de origen institucional para reentradas **a favor** del impulso.

**Parámetros.** `ob_zone_mode` ∈ {`full`(def.),`body`} · `ob_use_mean_threshold`
(def. true, entrada en 50 %) · `ob_requires_fvg` (def. false; true endurece) ·
`ob_requires_structure_break` (def. true) · `poi_mitigation_threshold` (0.5) ·
`ob_max_active`.

**Reglas de detección.**
1. Detectar displacement (D01) que produce BOS/CHoCH.
2. Retroceder desde el inicio del displacement a la última vela de color contrario
   → OB candidato.
3. Validar (estructura rota y/o FVG presente). Emitir POI `order_block`.
4. Actualizar `state` con cada vela (tested/mitigated/invalidated).

**Casos válidos.** Última vela roja antes de 3 velas verdes de `2·ATR` que hacen BOS
alcista y dejan FVG → bullish OB sin mitigar.
**Casos inválidos.** Vela contraria sin displacement posterior. Displacement sin
ruptura de estructura ni FVG (`ob_requires_structure_break=true`). "OB" ya
atravesado con cierre (invalidated).

**Invalidaciones.** Cierre de cuerpo a través de la zona → `invalidated` (y candidato
a Breaker, D23). Mitigación completa → `mitigated` (pierde prioridad).

**Edge cases.** Varias velas contrarias seguidas antes del impulso → el OB es la
**última** (más cercana al impulso); opcionalmente la serie completa como "OB
extendido" (`ob_extend_series`). OB y FVG solapados (frecuente) → registrar ambos;
la confluencia sube confidence. Vela de OB con mecha larga → `body` vs `full` cambia
la zona (documentar cuál usa el perfil). Doji como última contraria → usar la
anterior con cuerpo.

**Pseudocódigo.**
```
function detectOrderBlock(series, dispEvent, structEvent, cfg):
    m = dispEvent.startIndex; dir = dispEvent.dir
    k = lastIndexBefore(m, color = opposite(dir))     // last opposite candle
    if k is null: return none
    if cfg.requires_structure_break and structEvent is null: return none
    if cfg.requires_fvg and not fvgWithin(dispEvent): return none
    zone = cfg.zone_mode==full ? [L[k],H[k]] : [min(O[k],C[k]), max(O[k],C[k])]
    return POI(order_block, dir, zone, mean=mid(zone), origin=k, state=FRESH)
```

**Complejidad.** `O(1)` amortizado por evento (búsqueda local acotada por `max_bars`).

**Casos de prueba.** T1 última roja + displacement + BOS → bullish OB fresh. T2 sin
displacement → none. T3 precio cierra bajo la zona → invalidated. T4 penetra 60 % →
mitigated.

**Falsos positivos.** Cualquier vela contraria etiquetada OB sin displacement/rotura
→ reglas de validez. **Falsos negativos.** OB con displacement escalonado no
detectado → depende de D01 (`max_bars`).

**Relaciones.** Consume D01/D08/D09/D18; base de Mitigation/Breaker (D22/D23);
refinado por Inducement (D17); POI principal del modelo de entrada.

---

## D22 · Mitigation Block

**Definición matemática.** POI que se forma cuando la estructura cambia **sin barrer
la liquidez previa** (no hubo sweep, D16, del extremo anterior). Es la última vela
contraria del movimiento previo cuando el nuevo swing **no** toma el extremo anterior:
- **Bullish mitigation:** el precio hace un `HL` (mínimo más alto, no barre el low
  previo) y rompe al alza; el mitigation block = última vela `bear` antes del impulso.
- **Bearish mitigation:** `LH` que no toma el high previo + impulso bajista.
Distinción con OB: mismo mecanismo, pero **condicionado a ausencia de sweep** del
extremo previo (institución mitiga sin recolectar liquidez).

**Objetivo.** Capturar zonas de reentrada donde el "dinero inteligente" equilibra
posiciones sin cazar stops; útil cuando no hubo inducement clásico.

**Parámetros.** `mitigation_requires_no_sweep` (def. true) · hereda marco POI y
`ob_zone_mode`.

**Reglas de detección.** Igual que OB (D21) pero exigiendo que el swing que precede
al impulso **no** haya barrido el extremo previo (verificado con D16 sobre el
extremo). Si hubo sweep → es OB/breaker, no mitigation.

**Casos válidos.** Tendencia alcista, retroceso que forma HL sin tocar el low previo,
impulso alcista con displacement → bullish mitigation en la última vela bajista.
**Casos inválidos.** El retroceso barrió el low previo (hubo sweep) → clasificar como
OB, no mitigation. Sin displacement posterior.

**Invalidaciones.** Marco POI común (mitigated/invalidated).

**Edge cases.** Frontera OB↔mitigation ambigua cuando el sweep es marginal → el
detector decide por D16 estricto (penetración+cierre); documentar prioridad: si D16
marcó swept → OB; si no → mitigation. Menor `confidence` por defecto que OB con sweep.

**Pseudocódigo.**
```
function detectMitigationBlock(series, dispEvent, priorExtreme, sweeps, cfg):
    if wasSwept(priorExtreme, sweeps): return none      // that's an OB/breaker path
    return detectOrderBlock(series, dispEvent, structEvent, cfg)
                .asType(mitigation_block)
```

**Complejidad.** `O(1)` amortizado.

**Casos de prueba.** T1 HL sin sweep + impulso → mitigation. T2 mismo patrón pero con
sweep del low → OB. T3 sin displacement → none.

**Falsos positivos.** Clasificar como mitigation algo que sí barrió liquidez → D16
estricto. **Falsos negativos.** Sweep marginal mal detectado desvía la clasificación
→ calibrar `sweep_min_penetration`.

**Relaciones.** Variante de D21 condicionada por D16; menor prioridad que OB+sweep en
el scoring (ENG-001 §15).

---

## D23 · Breaker Block

**Definición matemática.** Un **OB fallido**: un order block (D21) que es
**invalidado** (cierre a través) y cuyo fallo coincide con un **CHoCH** (D09) en la
dirección de la ruptura; el antiguo OB se **invierte** y actúa en sentido contrario.
- **Bullish breaker:** un *bearish OB* roto al alza tras CHoCH alcista → soporte.
- **Bearish breaker:** un *bullish OB* roto a la baja tras CHoCH bajista → resistencia.
Preferentemente el OB original había sido creado tras un **sweep** de liquidez.

**Objetivo.** POI de **reversión** de alta calidad (fallo de estructura + toma de
liquidez + cambio de carácter concentrados en una zona).

**Parámetros.** `breaker_requires_choch` (def. true) · `breaker_requires_prior_sweep`
(def. false; true = solo breakers "premium") · hereda marco POI.

**Reglas de detección.**
1. Cuando un OB pasa a `invalidated` (D21) por cierre a través.
2. Verificar CHoCH (D09) en la dirección de la ruptura dentro de
   `breaker_confirm_bars`.
3. Emitir breaker de polaridad opuesta sobre la zona del OB original.

**Casos válidos.** Bullish OB en `1.2000–1.2010` roto a la baja con cierre `1.1990` +
CHoCH bajista → bearish breaker `1.2000–1.2010` (resistencia).
**Casos inválidos.** OB roto **sin** CHoCH (`breaker_requires_choch=true`) → no
breaker (solo OB invalidado). Ruptura por mecha sin cierre.

**Invalidaciones.** El breaker se invalida si el precio cierra de vuelta a través en
sentido inverso (re-flip) → `spent`; marco POI para tested/mitigated.

**Edge cases.** Breaker coincidente con IFVG (D19) sobre zonas próximas → confluencia
de reversión máxima. Cadena OB→breaker→re-break en chop → `contested`, evitar. Zona
del breaker = zona del OB original (no la vela de ruptura).

**Pseudocódigo.**
```
function detectBreaker(invalidatedOB, series, cfg):
    dirBreak = -invalidatedOB.dir
    if cfg.requires_choch and not chochWithin(invalidatedOB.invalidation_index, dirBreak, cfg.confirm_bars):
        return none
    if cfg.requires_prior_sweep and not invalidatedOB.hadPriorSweep:
        return none
    return POI(breaker, dirBreak, invalidatedOB.zone, mean=mid, origin=invalidatedOB.origin, state=FRESH)
```

**Complejidad.** `O(1)` por invalidación de OB.

**Casos de prueba.** T1 bullish OB roto abajo + CHoCH bajista → bearish breaker. T2 OB
roto sin CHoCH → none. T3 re-flip → spent. T4 breaker+IFVG → confluencia.

**Falsos positivos.** Rupturas de OB en chop sin cambio real → exigir CHoCH/sweep.
**Falsos negativos.** CHoCH ligeramente fuera de ventana → ajustar `breaker_confirm_bars`.

**Relaciones.** Deriva de D21 + D09 (+ D16); opuesto direccional al OB; POI de
reversión junto a IFVG (D19); factor 4 del scoring.

---

## D24 · Rejection Block

**Definición matemática.** POI basado en **mechas** (no en cuerpos): zona formada por
las **colas largas** de velas que rechazan un nivel, tras un barrido de liquidez.
Para un rejection alcista: conjunto de velas con `lower_wick_i / range_i ≥
rejection_wick_ratio` que barren un SSL y cierran arriba; la zona = `[min(L), max(open/close bottoms)]`
de esas mechas. Diferencia con OB: el OB usa el **cuerpo** de la última vela contraria;
el rejection block usa la **mecha** de rechazo (útil cuando no hay un cuerpo claro,
p.ej. barridos en velas de mecha larga).

**Objetivo.** Capturar POIs en escenarios de rechazo violento donde el cuerpo del OB
es pequeño o inexistente pero la mecha marca claramente la zona defendida.

**Parámetros.** `rejection_wick_ratio` (def. 0.6) · `rejection_requires_sweep`
(def. true) · `rejection_zone_mode` ∈ {`wick`(def.)} · hereda marco POI.

**Reglas de detección.**
1. Detectar velas con mecha dominante (ratio ≥ umbral) que **barren** un pool (D16).
2. Formar la zona con las mechas de rechazo (una o varias consecutivas).
3. Emitir POI `rejection_block`.

**Casos válidos.** Vela con mecha inferior 70 % que barre EQL y cierra arriba →
bullish rejection block en la zona de la mecha.
**Casos inválidos.** Mecha dominante **sin** barrido de liquidez
(`rejection_requires_sweep=true`). Vela de cuerpo dominante → es OB, no rejection.

**Invalidaciones.** Cierre de cuerpo a través de la zona de la mecha → invalidated.
Marco POI común.

**Edge cases.** Rejection block == OB cuando la vela tiene mecha y cuerpo relevantes →
registrar ambos, mayor confidence por confluencia. Varias mechas escalonadas → zona
compuesta (`min` de lows a `max` de bases). Pin-bar aislado sin sweep → descartar.

**Pseudocódigo.**
```
function detectRejectionBlock(series, sweeps, cfg):
    cluster = consecutiveCandles(where lowerWick/range >= cfg.wick_ratio and sweptSSL)
    if cfg.requires_sweep and not anySweep(cluster, sweeps): return none
    zone = [min(L[cluster]), max(min(O,C) over cluster)]
    return POI(rejection_block, +1, zone, mean=mid, origin=cluster.first, state=FRESH)
    // mirror for bearish (upper wicks, swept BSL)
```

**Complejidad.** `O(cluster)` ≈ `O(1)` amortizado.

**Casos de prueba.** T1 mecha 70 % + sweep EQL → bullish rejection. T2 mecha sin sweep
→ none. T3 cuerpo dominante → OB, no rejection. T4 cierre a través → invalidated.

**Falsos positivos.** Pin-bars de ruido sin sweep → `requires_sweep`. **Falsos
negativos.** Rechazo repartido en dos velas → clustering.

**Relaciones.** Consume D16; complementa OB (D21) en escenarios de mecha; POI del
scoring. Frecuentemente coincide con el low/high del sweep que ancla el SL (ENG-001
§29).

---

# Capa 6 — Rango y valoración (pricing)

## D25 · Dealing Range

**Definición matemática.** Rango operativo vigente `[range_low, range_high]` definido
por el **último swing extremo relevante y su contrario** en el timeframe: tras un MSS
alcista, `range_low` = el low que originó el impulso (a menudo el del sweep), y
`range_high` = el high alcanzado; y viceversa. Formalmente, el dealing range es el
`leg` impulsivo más reciente confirmado por BOS/CHoCH/MSS, delimitado por
`[origin_extreme, terminal_extreme]`.

**Objetivo.** Marco de referencia para premium/discount/equilibrium (D26–D28) y OTE
(D29). Sin dealing range no hay valoración relativa del precio.

**Parámetros.** `range_ref` ∈ {`last_impulse_leg`(def.), `swing_to_swing`,
`session`} · `range_update_on` ∈ {`bos`,`choch`,`mss`(def. todos)} ·
`range_min_size_atr` (def. 1.0, ignora rangos triviales).

**Reglas de detección.** Al confirmarse un evento estructural (D08–D10), fijar
`[range_low, range_high]` según `range_ref`; recalcular niveles Fib. Mientras no haya
nuevo evento, el rango persiste.

**Casos válidos.** MSS alcista desde `1.1980` (low del sweep) hasta `1.2040` → dealing
range `[1.1980,1.2040]`.
**Casos inválidos.** Rango `< range_min_size_atr` (movimiento trivial) → no se adopta;
mantener el anterior. Sin evento estructural (chop) → rango indefinido/`stale`.

**Invalidaciones.** Se **redefine** (no se invalida) con cada nuevo BOS/CHoCH/MSS. Un
CHoCH opuesto invierte el contexto premium/discount.

**Edge cases.** Rango dentro de rango (fractal): HTF define el macro-range, LTF uno
interno → mantener por timeframe. Extremos ambiguos tras doble sweep → usar el extremo
que originó el displacement dominante. Rango "stale" (muy antiguo sin actividad) →
marcar y bajar confianza de premium/discount.

**Pseudocódigo.**
```
function updateDealingRange(state, structEvent, cfg):
    leg = impulsiveLeg(structEvent, cfg.range_ref)
    if size(leg) < cfg.min_size*ATR: return state.range   // keep previous
    return Range(low = leg.low, high = leg.high, ref_event = structEvent, fibs = fib(leg))
```

**Complejidad.** `O(1)` por evento estructural.

**Casos de prueba.** T1 MSS alcista → rango del leg. T2 leg trivial → mantiene
anterior. T3 CHoCH opuesto → rango redefinido, contexto invertido.

**Falsos positivos.** Adoptar micro-rangos como dealing range → `range_min_size_atr`.
**Falsos negativos.** No actualizar en BOS (solo MSS) puede desfasar el rango →
`range_update_on` incluye bos por defecto.

**Relaciones.** Consume D03–D10; base de D26–D29; el más importante para pricing.

---

## D26 · Premium / D27 · Discount / D28 · Equilibrium

> Se documentan juntos por compartir base matemática (mismo dealing range, misma
> partición Fib); cada uno conserva sus 14 campos.

**Definición matemática.** Sobre `Range=[range_low, range_high]` (D25),
`eq = range_low + 0.5·(range_high − range_low)`:
- **Discount:** `price < eq − equilibrium_band·(range_high−range_low)` (mitad
  inferior). Sesgo: **compras**.
- **Premium:** `price > eq + equilibrium_band·(...)` (mitad superior). Sesgo:
  **ventas**.
- **Equilibrium:** `|price − eq| ≤ equilibrium_band·(range_high−range_low)` (banda
  neutra alrededor del 50 %). Sesgo: **ninguno / evitar**.

**Objetivo.** No comprar caro ni vender barato: condicionar la dirección de los POIs a
su ubicación en el rango (filtro clave del scoring, ENG-001 §18 factor 6).

**Parámetros.** `equilibrium_band` (def. 0.05) · `require_discount_for_longs`
(def. true) · `require_premium_for_shorts` (def. true) · `avoid_equilibrium`
(def. true).

**Reglas de detección.** Dado `price` (o el nivel de un POI), calcular su posición
`pos = (price − range_low)/(range_high − range_low) ∈ [0,1]` y clasificar en
premium/discount/equilibrium según bandas.

**Casos válidos.** POI en `pos=0.28` → discount → habilita long. POI en `pos=0.74` →
premium → habilita short.
**Casos inválidos.** POI long en premium (`pos=0.7`) con `require_discount_for_longs`
→ rechazado. Precio en `pos=0.5±band` → equilibrium, evitar.

**Invalidaciones.** Cambian con el dealing range (D25); un nuevo rango recalcula todas
las zonas.

**Edge cases.** `range_high == range_low` (rango degenerado) → indefinido, `pos=NaN` →
tratar como `equilibrium`/veto. Precio fuera del rango (`pos<0` o `>1`, tras extensión)
→ `extended_premium/discount`; el motor puede esperar reintegración al rango.

**Pseudocódigo.**
```
function classifyPricing(price, range, cfg):
    if range.high == range.low: return EQUILIBRIUM
    pos = (price - range.low) / (range.high - range.low)
    band = cfg.equilibrium_band
    if pos > 0.5 + band: return PREMIUM
    if pos < 0.5 - band: return DISCOUNT
    return EQUILIBRIUM
```

**Complejidad.** `O(1)`.

**Casos de prueba.** T1 pos 0.28 → discount. T2 pos 0.74 → premium. T3 pos 0.5 →
equilibrium. T4 rango degenerado → equilibrium/veto. T5 pos 1.15 → extended_premium.

**Falsos positivos.** Clasificación errónea por rango mal definido → depende de D25.
**Falsos negativos.** Banda de equilibrio grande oculta zonas operables → def. 0.05.

**Relaciones.** Consumen D25; condicionan la dirección de POIs (D21–D24) y son insumo
directo de OTE (D29) y del scoring.

---

## D29 · OTE (Optimal Trade Entry)

**Definición matemática.** Sobre el `leg` impulsivo del dealing range (D25), la
ventana OTE es el retroceso de Fibonacci `[ote_low, ote_high]` con óptimo `ote_optimal`:
- Long (leg alcista de `low`→`high`): niveles medidos desde `high` (0.0) a `low`
  (1.0) del retroceso; OTE = precios en `[high − ote_high·(high−low), high −
  ote_low·(high−low)]`, es decir la banda `0.618–0.786` en zona de **discount**.
- Short: espejo en **premium**.
`ote_optimal = 0.705` (sweet spot).

**Objetivo.** Localizar la subzona de mejor riesgo/beneficio dentro del retroceso,
para maximizar RR (ENG-001 §19 factor 7). Nunca opera sola: exige confluencia con POI
en discount/premium.

**Parámetros.** `ote_low` (def. 0.618) · `ote_high` (def. 0.786) · `ote_optimal`
(def. 0.705) · `ote_requires_poi` (def. true) · `ote_requires_pd` (def. true, exige
premium/discount coherente).

**Reglas de detección.** Calcular la banda OTE del leg vigente; un setup puntúa OTE si
el POI/entrada cae dentro de `[ote_low, ote_high]` **y** en la zona premium/discount
correcta. Marcar `at_optimal` si está a `≤ ote_tol` de `ote_optimal`.

**Casos válidos.** Bullish OB en retroceso 0.70 dentro de discount → OTE + at_optimal.
**Casos inválidos.** Entrada en 0.5 (equilibrium, fuera de OTE). Retroceso 0.9
(demasiado profundo, fuera de banda). OTE en premium para un long (`ote_requires_pd`).

**Invalidaciones.** Se recalcula con el dealing range; si el precio supera 1.0
(barre el origen del leg) el retroceso se invalida (posible cambio de estructura →
D25 nuevo).

**Edge cases.** Leg muy corto → banda OTE estrecha (pocos pips) → poco práctica;
`range_min_size_atr` de D25 mitiga. Solapamiento OTE ∩ OB ∩ FVG ∩ discount = "golden
pocket" (máxima confluencia). Retroceso exactamente en 0.618/0.786 (bordes) → incluir
(`≤`/`≥` inclusivos).

**Pseudocódigo.**
```
function computeOTE(leg, cfg):
    span = leg.high - leg.low
    if leg.dir == +1:   // long
        hiPrice = leg.high - cfg.ote_low  * span   // shallower bound
        loPrice = leg.high - cfg.ote_high * span   // deeper bound
        optimal = leg.high - cfg.ote_optimal * span
    else:               // short (mirror)
        loPrice = leg.low + cfg.ote_low  * span
        hiPrice = leg.low + cfg.ote_high * span
        optimal = leg.low + cfg.ote_optimal * span
    return OTE([min(loPrice,hiPrice), max(loPrice,hiPrice)], optimal)

function scoreOTE(entryOrPOI, ote, pricing, cfg):
    inBand = within(entryOrPOI, ote.zone)
    pdOK = not cfg.requires_pd or coherent(pricing, entryOrPOI.dir)
    return inBand and pdOK
```

**Complejidad.** `O(1)`.

**Casos de prueba.** T1 POI en 0.70 discount → OTE at_optimal. T2 POI en 0.50 → fuera.
T3 POI en 0.70 pero premium (long) → rechazado por pd. T4 retroceso 0.9 → fuera.

**Falsos positivos.** OTE sin POI marcada como señal → `ote_requires_poi`. **Falsos
negativos.** Sweet spot ligeramente fuera por `ote_tol` estrecho → calibrar.

**Relaciones.** Consume D25/D26–D28 y el **Fibonacci Institucional (D32)** —del que
la OTE es la subzona `0.618–0.786`—; confluye con POIs (D21–D24) y BPR/FVG; factor 7
del scoring; define el precio de entrada de precisión.

---

# Capa 7 — Contexto

## D30 · Volume Confirmation

**Definición matemática.** Confirmación de que un evento (displacement/BOS/CHoCH/
sweep) va acompañado de actividad anómala: `volume_confirmed(j) ⇔ V_j ≥
volume_ma(j) · volume_spike_mult`, con `volume_ma(j) = SMA(V, volume_ma_period)`
excluyendo `j`. Fuente `V` según `volume_source` del perfil (tick vs real).

**Objetivo.** Factor de **apoyo** (bajo peso) que refuerza la validez del
desplazamiento; nunca condición dura por sí sola (esp. en FX con tick volume).

**Parámetros.** `volume_source` ∈ {`tick`(FX/Oro), `real`(índices/cripto)} ·
`volume_ma_period` (def. 20) · `volume_spike_mult` (def. 1.5) · `volume_weight`
(def. bajo, ver scoring) · `volume_enabled` (def. true).

**Reglas de detección.** Al evaluar un evento en `j`, calcular la media móvil y
comparar; emitir `VolumeConfirmation(j, ratio = V_j/volume_ma)`. Opcional:
divergencia (volumen decreciente en extensión → refuerza reversión).

**Casos válidos.** `V_j = 1.8·SMA20` en la vela de displacement → confirmado.
**Casos inválidos.** `V_j ≈ SMA` → no confirma (no resta; solo no suma). `volume_enabled=false`
o datos de volumen ausentes → `unavailable` (neutro).

**Invalidaciones.** N/A (es una lectura puntual, no una zona).

**Edge cases.** Tick volume ausente/plano en algunos brokers → `unavailable`, peso 0.
Cripto 24/7 con estacionalidad de volumen → `volume_ma` sobre ventana coherente con
`crypto_activity_windows`. Barras de baja liquidez con volumen bajo pero rango alto →
tratar con cautela (posible spike sin volumen real).

**Pseudocódigo.**
```
function volumeConfirmation(series, j, cfg):
    if not cfg.enabled or volumeMissing(j): return UNAVAILABLE
    ma = sma(V, j-1, cfg.ma_period)          // excluding j
    if ma == 0: return UNAVAILABLE
    ratio = V[j] / ma
    return ratio >= cfg.spike_mult ? CONFIRMED(ratio) : NOT_CONFIRMED(ratio)
```

**Complejidad.** `O(1)` con media incremental; `O(period)` naïve.

**Casos de prueba.** T1 V=1.8·MA → confirmed. T2 V=1.0·MA → not_confirmed. T3 volumen
ausente → unavailable. T4 MA=0 (inicio) → unavailable.

**Falsos positivos.** Spikes de tick por baja liquidez (no volumen real) → peso bajo +
Session Context. **Falsos negativos.** Volumen real relevante repartido en varias
velas → suavizar con ventana.

**Relaciones.** Confirma D01/D08/D09/D16; factor 10 del scoring (peso bajo por diseño,
ENG-001 §21/§26).

---

## D31 · Session Context

**Definición matemática.** Función determinista `session(ts) → { session ∈ {asia,
london, ny, overlap, off}, killzone ∈ {london_kz, ny_kz, silver_bullet, none},
in_killzone ∈ bool }`, calculada convirtiendo `ts` (UTC) a `session_timezone` del
perfil y comparando con las ventanas `killzones`. Además expone extremos de sesión
(session high/low, PDH/PDL, PWH/PWL) como fuentes de liquidez (D11).

**Objetivo.** Situar cada evento en su contexto temporal institucional (filtro/boost
del scoring, ENG-001 §24 factor 8) y alimentar la liquidez temporal.

**Parámetros.** `session_timezone` (por perfil; def. `America/New_York`) ·
`killzones` (mapa de ventanas: `london_kz`, `ny_kz`, `silver_bullet`, `london_close`)
· `trade_only_in_killzones` (def. true) · `outside_killzone_policy` ∈
{`penalize`(def.), `veto`} · `session_dst_aware` (def. true).

**Reglas de detección.** Para `ts`, convertir a la zona horaria (con DST si
`session_dst_aware`), determinar sesión y killzone por pertenencia a intervalos;
mantener y actualizar session/day/week highs y lows como pools de liquidez.

**Casos válidos.** `ts=08:30 NY` → session=ny/overlap, killzone=ny_kz, in_killzone=true.
**Casos inválidos.** `ts=14:00 NY` → off/none (fuera de killzone) → penalizar/vetar.

**Invalidaciones.** N/A (mapeo puro); los extremos de sesión se reinician al cambiar
de sesión/día/semana.

**Edge cases.** **DST** (cambios de horario EEUU/UK desalineados unas semanas/año) →
usar zona IANA con reglas DST, **no** offset fijo (fuente de bugs no deterministas si
se hardcodea). Festivos / medio día de mercado → `holiday_calendar` opcional reduce
actividad. **Cripto 24/7** → sin sesiones clásicas; usar `crypto_activity_windows` por
volumen observado. Cambio de día/semana en la zona del perfil (no en UTC) para
PDH/PDL/PWH/PWL.

**Pseudocódigo.**
```
function sessionContext(ts_utc, cfg):
    local = toZone(ts_utc, cfg.timezone, dst = cfg.dst_aware)   // IANA, DST-correct
    s  = classifySession(local, cfg.sessions)
    kz = firstMatchingKillzone(local, cfg.killzones)            // deterministic order
    return SessionCtx(session=s, killzone=kz, in_killzone = kz != none)
```

**Complejidad.** `O(1)` (conversión de zona + comparaciones de intervalo).

**Casos de prueba.** T1 08:30 NY → ny_kz. T2 14:00 NY → none. T3 fecha en cambio DST →
killzone correcta según regla IANA. T4 símbolo cripto → activity window, no killzone
clásica.

**Falsos positivos.** Marcar killzone con offset fijo en semana de DST → **bug**; usar
IANA. **Falsos negativos.** Killzones demasiado estrechas omiten setups válidos →
calibrar por perfil.

**Relaciones.** Alimenta D11 (liquidez de sesión) y el filtro temporal del scoring;
condiciona Volume Confirmation (D30) y los vetos (ENG-001 §24/§35).

---

# Capa 6 (adenda) — Fibonacci Institucional

## D32 · Institutional Fibonacci

> **Fibonacci NO es un indicador independiente en ELYON QUANT.** Es una
> herramienta de **valoración anclada a la estructura** que vive **dentro** del
> Smart Money Engine: sus niveles solo tienen sentido sobre un *leg* impulsivo
> definido por estructura (D25) y sus salidas alimentan OTE (D29),
> premium/discount (D26–D28) y objetivos de TP. **Nunca genera una entrada por sí
> solo**: es una **confirmación adicional dentro del Scoring Engine** (ENG-001
> §26). Si el Fibonacci "sugiere" algo pero no hay estructura + liquidez + POI, no
> hay operación.

**Definición matemática.** Dado un *leg* impulsivo del dealing range (D25) con
**swing origen** `O` (donde inicia el impulso) y **swing destino** `T` (donde
termina), `span = T − O` (con signo; leg alcista → `T`=high, `O`=low, `span>0`).
El motor almacena los niveles como **precios absolutos** anclados a `(O, T)` para
evitar ambigüedad de convención de plataforma:
- **Retrocesos** `r ∈ [0,1]`: `P_ret(r) = T − r·span` (`r=0` en destino,
  `r=1` en origen).
- **Proyecciones** `e > 1`: `P_proj(e) = O + e·span` (`e=1` en destino; `e>1`
  proyecta **más allá** del destino en la dirección del impulso).

> Nota de convención (determinista): los **retrocesos** se miden desde el
> **destino** (`0` = destino, extremo del impulso; `1` = origen, retroceso total),
> y las **proyecciones** desde el **origen** (objetivos más allá del destino).
> Es la práctica institucional estándar y elimina el "repintado" por convención.

### Niveles canónicos (los 10 obligatorios)

| Nivel | Rol | Precio canónico | Significado operativo |
|-------|-----|-----------------|-----------------------|
| **0** | ancla | `T` (destino) | Extremo del impulso (0 % retroceso) |
| **0.5** | retroceso | `T − 0.5·span` | **Equilibrium**: frontera premium/discount (D28) |
| **0.618** | retroceso | `T − 0.618·span` | Inicio de la zona **OTE** |
| **0.705** | retroceso | `T − 0.705·span` | **OTE principal** (sweet spot de entrada) |
| **0.786** | retroceso | `T − 0.786·span` | Fin de la zona OTE (retroceso profundo) |
| **1** | ancla | `O` (origen) | Retroceso total; **su ruptura invalida el leg** |
| **1.272** | proyección | `O + 1.272·span` | Primer objetivo (TP1) más allá del destino |
| **1.618** | proyección | `O + 1.618·span` | Objetivo extendido (TP2) |
| **2.0** | proyección | `O + 2.0·span` | Objetivo amplio (TP3 / runner) |
| **2.618** | proyección | `O + 2.618·span` | Objetivo de expansión máxima |

- **Zona OTE** = `[0.618, 0.786]`, óptimo `0.705` → coincide con D29 (mismos
  parámetros `ote_low/ote_optimal/ote_high`). Fibonacci **provee** los niveles;
  OTE **es** la subzona `0.618–0.786` de este Fibonacci.
- **Premium/Discount**: el nivel `0.5` es el equilibrio; por encima (hacia `0`)
  = premium; por debajo (hacia `1`) = discount (para un leg alcista). Consistente
  con D26–D28.

**Objetivo.** Cuantificar de forma objetiva (a) la **zona de entrada de precisión**
(OTE) dentro de un retroceso institucional y (b) los **objetivos de TP** por
proyección, **siempre** anclado a estructura real. Es un **multiplicador de
confluencia**, no una señal.

**Parámetros configurables.**
- `fib_leg_source` ∈ {`dealing_range_leg`(def.), `last_displacement_leg`,
  `structural_swing`} — de dónde sale el leg.
- `fib_anchor_origin` ∈ {`impulse_start`(def.), `sweep_extreme`} — el origen se
  ancla en el inicio del displacement o, preferido, en el **extremo del sweep**
  que originó el impulso.
- `fib_retracement_levels` (def. `[0, 0.5, 0.618, 0.705, 0.786, 1]`).
- `fib_projection_levels` (def. `[1.272, 1.618, 2.0, 2.618]`).
- `ote_low/ote_optimal/ote_high` (0.618/0.705/0.786) — compartidos con D29.
- `fib_recalc_on` (def. `[bos, choch, mss, new_leg_extreme]`).
- `fib_freeze_on_entry` (def. **true**) — al abrir una operación, el Fibonacci del
  setup se **congela** para todo su ciclo de vida (los niveles que definieron la
  entrada, SL y TP no se mueven → no repaint, trazabilidad con DecisionRecord).
- `fib_min_leg_atr` (def. 1.0) — ignora legs triviales.

### Detección automática del swing correcto (origen y destino)

Regla determinista para elegir el leg (evita el subjetivismo del "¿qué swing
tomo?"):
1. Tomar el **leg impulsivo vigente** del Dealing Range (D25), es decir el último
   tramo confirmado por **BOS/CHoCH/MSS** (D08–D10) con **displacement** (D01).
2. `destino T` = el **extremo alcanzado** por ese impulso (el high del BOS alcista
   / low del BOS bajista).
3. `origen O` = el **extremo desde el que arrancó** el displacement. Con
   `fib_anchor_origin = sweep_extreme` (preferido), `O` = el extremo de la **vela
   de barrido** (D16) que precedió al impulso (el low del sweep de SSL para un leg
   alcista). Esto ancla el Fibonacci a la manipulación real, no a un swing
   arbitrario.
4. Validar `|span| ≥ fib_min_leg_atr · ATR`; si no, no se traza (leg trivial).
5. Alinear con el **bias HTF** (D05/MTF): solo se opera la OTE del leg cuya
   dirección coincide con el bias (ENG-001 §5).

### Cuándo recalcular Fibonacci
- Al confirmarse un **nuevo evento estructural** (BOS/CHoCH/MSS) que **redefine el
  dealing range** (D25) → nuevo leg → nuevo Fibonacci.
- Cuando el precio hace un **nuevo extremo** que **extiende el destino** del leg
  vigente en la dirección del impulso (se actualiza `T`, se recalculan niveles).
- Al cambiar de timeframe de análisis (cada TF mantiene su propio Fibonacci).

### Cuándo **NO** recalcular (anti-repaint / determinismo)
- Durante el **retroceso o la consolidación** dentro del leg vigente: el
  Fibonacci está **anclado y estable** (es precisamente cuando se usa para
  entrar).
- Ante **swings internos** (D06) que **no** rompen estructura mayor: el micro-ruido
  no re-ancla el Fibonacci.
- **Nunca** durante el ciclo de vida de una operación abierta si
  `fib_freeze_on_entry = true`: los niveles que definieron entrada/SL/TP quedan
  congelados (evita mover objetivos "a conveniencia" y garantiza que el
  DecisionRecord sea reproducible).

### Cómo usar OTE
El motor busca que el **POI** (OB/FVG/Breaker) al que retrocede el precio caiga
dentro de `[0.618, 0.786]` del Fibonacci, con preferencia por el **0.705**. La
confluencia `OTE ∩ POI ∩ (discount|premium)` es el "golden pocket": máxima
puntuación del factor OTE/Fibonacci del scoring. La **entrada** se dispara por el
modelo LTF (sweep → CHoCH → FVG), **no** por tocar el 0.705 (Fibonacci confirma,
no gatilla).

### Combinación con otros detectores (confluencia, no señal)
- **+ Order Blocks (D21):** un OB cuya zona intersecta la OTE (`0.618–0.786`)
  puntúa más; el OB define la zona, el Fibonacci confirma que está "a buen precio".
  Refinamiento: entrada en `max(OB.mean, fib_0705)`.
- **+ FVG (D18):** un FVG dentro de la OTE es confluencia de imbalance + valor; el
  `CE` del FVG cercano al 0.705 es entrada de precisión.
- **+ Liquidity Sweeps (D16):** el **origen** del Fibonacci se ancla al extremo del
  sweep; así el 0.0 nace de la toma de liquidez y la OTE mide el retroceso del
  impulso post-manipulación. Sweep + OTE + POI = setup A+.
- **+ BOS/CHoCH (D08/D09):** el leg del Fibonacci **es** el displacement que produjo
  el BOS/CHoCH. Un CHoCH define un nuevo leg de reversión → se traza Fibonacci
  sobre él y se busca la OTE para la entrada a favor del nuevo carácter. Un BOS de
  continuación redefine el destino y proyecta nuevos objetivos (1.272–2.618).
- **+ Proyecciones para TP:** los niveles `1.272/1.618/2.0/2.618` son objetivos de
  cierre parcial (ENG-001 §30/§33), subordinados a la **liquidez** como objetivo
  primario (si hay un pool de liquidez antes del 1.618, manda la liquidez).

**Casos válidos.** Leg alcista anclado en el low de un sweep de SSL; el precio
retrocede a un bullish OB situado en el 0.70 (dentro de OTE, en discount) →
Fibonacci confirma (suma al score). Proyección 1.618 coincide con BSL objetivo → TP.
**Casos inválidos.** Trazar Fibonacci sobre un tramo **no impulsivo** (sin
displacement) → rechazado. Origen elegido en un swing arbitrario no ligado a
estructura → prohibido (usar `fib_leg_source`). Usar el 0.705 como **única** razón
de entrada → **prohibido por diseño** (Fibonacci nunca opera solo).

**Invalidaciones.** El Fibonacci del leg se invalida/redefine cuando el precio
**cierra más allá del nivel `1` (origen)** en contra (el retroceso superó el 100 %
→ probable cambio de estructura, D25 nuevo). Un leg cuyo destino es superado se
**extiende**, no se invalida.

**Edge cases.** Leg muy corto (`< fib_min_leg_atr`) → OTE estrecha e inoperable →
no trazar. Múltiples legs anidados (HTF vs LTF) → un Fibonacci por TF; el HTF manda
el bias, el LTF afina la OTE. Extensión repetida del destino en tendencias fuertes
→ recalcular niveles pero **congelar** los de operaciones ya abiertas. Gap que
salta un nivel → niveles siguen válidos como precios; el "toque" puede no ocurrir.

**Pseudocódigo.**
```
function computeInstitutionalFib(dealingRange, sweeps, cfg):
    leg = selectImpulsiveLeg(dealingRange, cfg.leg_source)        // BOS/CHoCH/MSS + displacement
    T = leg.terminalExtreme
    O = (cfg.anchor_origin == sweep_extreme && leg.originSweep != null)
        ? leg.originSweep.extreme : leg.startExtreme
    span = T - O
    if abs(span) < cfg.min_leg_atr * ATR: return none            // trivial leg
    retr = { r: T - r*span for r in cfg.retracement_levels }     // 0..1
    proj = { e: O + e*span for e in cfg.projection_levels }      // >1 targets
    ote  = [ T - cfg.ote_high*span , T - cfg.ote_low*span ]      // 0.618..0.786 band
    return Fib(O, T, span, retr, proj, ote, optimal = T - cfg.ote_optimal*span)

function scoreFibConfluence(poi, entry, fib, pricing, cfg):
    // confirmation only — never a standalone trigger
    inOTE = within(poi.zone, fib.ote) or within(entry, fib.ote)
    pdOK  = coherent(pricing, poi.dir)                            // discount long / premium short
    atOptimal = distance(entry, fib.optimal) <= cfg.ote_tol
    return confluenceScore(inOTE, pdOK, atOptimal)               // feeds Scoring Engine factor 7
```

**Complejidad.** Cómputo del Fibonacci `O(1)` por leg (conjunto fijo de niveles);
recálculo solo en eventos estructurales `O(1)`.

**Casos de prueba.**
- T1: leg alcista, POI en retroceso 0.70 en discount → `inOTE=true`, confluencia alta.
- T2: POI en 0.5 (equilibrium) → fuera de OTE, sin bonus.
- T3: entrada basada **solo** en tocar 0.705 sin POI/estructura → el motor **no
  opera** (Fibonacci no gatilla).
- T4: precio cierra bajo el nivel `1` (origen) → Fibonacci invalidado/redefinido.
- T5: `fib_freeze_on_entry=true` + nuevo extremo tras abrir → niveles del trade
  **no** cambian.

**Falsos positivos.** Trazado sobre tramos correctivos etiquetados como impulso →
mitigado por exigir displacement (D01) en el leg. Anclaje en swing arbitrario →
prohibido por `fib_leg_source`.
**Falsos negativos.** OTE válida omitida porque el leg quedó justo bajo
`fib_min_leg_atr` → calibrar por perfil.

**Relaciones.** Consume D25 (dealing range), D16 (sweep para el origen), D01/D08/
D09/D10 (leg y su validez); **provee** a D29 (OTE es su subzona) y a D26–D28
(equilibrium = nivel 0.5); alimenta el **factor 7 del Scoring Engine** (ENG-001
§26) y los objetivos de TP (§30). **Regla dura (⛔):** `fib_standalone_entry = false`
— el motor rechaza cualquier entrada cuya única justificación sea un nivel Fibonacci.

---

# Apéndice A — Matriz de cobertura (lista solicitada → sección)

| # | Concepto solicitado | Sección | # | Concepto solicitado | Sección |
|---|---------------------|---------|---|---------------------|---------|
| 1 | Swing High | D03 | 16 | Mitigation Blocks | D22 |
| 2 | Swing Low | D04 | 17 | Breaker Blocks | D23 |
| 3 | Internal Structure | D06 | 18 | Rejection Blocks | D24 |
| 4 | External Structure | D05 | 19 | Fair Value Gaps | D18 |
| 5 | BOS | D08 | 20 | Inverse Fair Value Gaps | D19 |
| 6 | CHoCH | D09 | 21 | Balanced Price Range | D20 |
| 7 | MSS | D10 | 22 | Premium | D26 |
| 8 | Liquidity | D11 | 23 | Discount | D27 |
| 9 | Buy Side Liquidity | D12 | 24 | Equilibrium | D28 |
| 10 | Sell Side Liquidity | D13 | 25 | Dealing Range | D25 |
| 11 | Liquidity Sweep | D16 | 26 | OTE | D29 |
| 12 | Inducement | D17 | 27 | Displacement | D01 |
| 13 | Equal Highs | D14 | 28 | Imbalance | D02 |
| 14 | Equal Lows | D15 | 29 | Volume Confirmation | D30 |
| 15 | Order Blocks | D21 | 30 | Session Context | D31 |

*(D07 reservado por cohesión de capas; los 30 conceptos solicitados están cubiertos.)*

**Adenda obligatoria:** | 31 | **Fibonacci Institucional** | **D32** | — parte del
Smart Money Engine, nunca indicador independiente; provee la OTE (D29) y los
objetivos de proyección. |

# Apéndice B — Grafo de dependencias de detectores

```
ATR, pip ─┬─► D01 Displacement ─┬─► D08 BOS ──┐
          └─► D02 Imbalance ─────┤             ├─► D10 MSS
                                  └─► D09 CHoCH ┘
D03 SwingHigh ─┐                         ▲
D04 SwingLow ──┼─► D05 External ─────────┘
               └─► D06 Internal ─► D17 Inducement
D05/D03/D04 ─► D11 Liquidity ─┬─► D12 BSL ─► D14 EqualHighs ─┐
                              └─► D13 SSL ─► D15 EqualLows ──┴─► D16 Sweep ─► D17
D02 ─► D18 FVG ─► D19 IFVG ; D18×2 ─► D20 BPR
D01+D08/09 ─► D21 OrderBlock ─┬─► D22 Mitigation
                              ├─► D23 Breaker (+D09,+D16)
                              └─► D24 Rejection (+D16)
D05..D10 ─► D25 DealingRange ─┬─► D26/D27/D28 Premium/Discount/Eq ─► D29 OTE
                              └─► D32 Institutional Fibonacci ─► D29 OTE + TP targets
D16 Sweep ─► D32 (origin anchor)
D31 Session ─► D11 (session liquidity) ;  D30 Volume ─► confirma D01/D08/D09/D16
```

# Apéndice C — Garantías de determinismo y testing

1. **Sin no-determinismo:** prohibido reloj de pared, `random`, iteración sobre
   estructuras sin orden estable, o dependencia del orden de llegada de ticks
   (se opera sobre velas cerradas ordenadas por `ts`, desempate por índice).
2. **ATR/medias congelados por barra:** todos los umbrales relativos usan el
   `ATR`/`MA` de la barra de evaluación (documentado por detector) para que un
   recálculo produzca el mismo resultado.
3. **Golden datasets:** cada detector tendrá *fixtures* de velas sintéticas
   (los "Casos de prueba" de cada sección) con salida esperada exacta →
   tests unitarios. Property-based testing para invariantes (p.ej. "un swing high
   confirmado nunca cambia de índice", "un FVG invalidado nunca vuelve a unfilled").
4. **Reproducibilidad de pipeline:** ejecutar todos los detectores sobre un dataset
   histórico dos veces debe dar `DecisionRecord` idénticos (`config_hash` estable).
5. **Trazabilidad (BLD-003):** cada regla `⛔/⚙️` y cada "Caso de prueba" se enlaza a
   un test; la cobertura de detectores es objetivo alto (mutation testing en la
   lógica de estructura y sweeps).

# Apéndice D — Catálogo de parámetros (por detector)

> Todos los parámetros son configurables por `instrument_profile` y
> `strategy_version`, versionados con `params_hash`. Valores por defecto de diseño,
> a calibrar en backtesting/walk-forward (ENG-004).

| Detector | Parámetros clave (def.) |
|----------|--------------------------|
| D01 Displacement | `displacement_atr_mult`(1.5), `displacement_body_ratio`(0.6), `max_bars`(3) |
| D02 Imbalance | `imbalance_min_size`(0.10·ATR), `imbalance_fill_threshold`(0.5) |
| D03/D04 Swings | `swing_lookback_major`(5), `swing_lookback_internal`(2), `swing_strict`(true) |
| D05/D06 Structure | `structure_min_swings`(2), `range_tol_atr`(0.25) |
| D08 BOS | `bos_confirmation`(close), `bos_requires_displacement`(true), `bos_failure_bars`(3) |
| D09 CHoCH | `choch_confirmation`(close), `choch_requires_displacement`(true), `choch_scope`(both) |
| D10 MSS | `mss_confirm_bars`(10), `mss_min_displacement_atr`(2.0), `mss_requires_sweep`(false) |
| D11–D15 Liquidity | `equal_level_tol`(0.10·ATR), `equal_min_touches`(2), `equal_min_separation_bars`(3) |
| D16 Sweep | `sweep_min_penetration`(0.05·ATR), `sweep_wick_ratio`(0.5), `sweep_confirm_bars`(1–2) |
| D17 Inducement | `inducement_required`(true), `inducement_scope`(internal) |
| D18 FVG | `fvg_min_size`(0.10·ATR), `fvg_requires_displacement`(true), `fvg_fill_threshold`(0.5) |
| D19 IFVG | `ifvg_confirm`(close_through), `ifvg_requires_choch`(false) |
| D20 BPR | `bpr_min_overlap`(0.05·ATR), `bpr_max_gap_bars`(20) |
| D21 OB | `ob_zone_mode`(full), `ob_use_mean_threshold`(true), `ob_requires_structure_break`(true) |
| D22 Mitigation | `mitigation_requires_no_sweep`(true) |
| D23 Breaker | `breaker_requires_choch`(true), `breaker_requires_prior_sweep`(false) |
| D24 Rejection | `rejection_wick_ratio`(0.6), `rejection_requires_sweep`(true) |
| D25 Dealing Range | `range_ref`(last_impulse_leg), `range_min_size_atr`(1.0) |
| D26–D28 Pricing | `equilibrium_band`(0.05), `require_discount_for_longs`(true), `require_premium_for_shorts`(true) |
| D29 OTE | `ote_low`(0.618), `ote_optimal`(0.705), `ote_high`(0.786), `ote_requires_poi`(true) |
| D30 Volume | `volume_source`(perfil), `volume_ma_period`(20), `volume_spike_mult`(1.5) |
| D31 Session | `session_timezone`(perfil), `killzones`(perfil), `outside_killzone_policy`(penalize) |
| D32 Fibonacci | `fib_leg_source`(dealing_range_leg), `fib_anchor_origin`(sweep_extreme), `fib_freeze_on_entry`(true), `fib_standalone_entry`(false ⛔), `fib_min_leg_atr`(1.0) |

---

# Apéndice E — Entradas / Salidas y Diagrama lógico por detector

> Completa los campos **3 (Entradas)**, **4 (Salidas)** y **8 (Diagrama lógico)**
> del contrato (§0.4) para los 31 detectores. Toda entrada es información de barras
> `≤ t` (no look-ahead); toda salida es un registro tipado con `origin_index`,
> `confirm_index`, `state` y `params_hash`. Los **parámetros efectivos** se resuelven
> vía Market DNA (`dna.override ?? default`, ENG-011 §8).

### E.1 Tabla de Entradas / Salidas (31 detectores)

| Detector | Entradas | Salidas |
|----------|----------|---------|
| D01 Displacement | `series` (velas cerradas TF), `ATR` | `Displacement{a,b,dir,move}` \| `none` |
| D02 Imbalance | `series`, `ATR` | `Imbalance{zone,dir,size,state}` \| `none` |
| D03 Swing High | `series`, `k`(lookback) | `SwingHigh{index,price,grade,confirm_index,label}` |
| D04 Swing Low | `series`, `k` | `SwingLow{index,price,grade,confirm_index,label}` |
| D05 External Structure | swings mayores (D03/D04) | `{trend_state,protected_high/low,labeled_swings}` |
| D06 Internal Structure | swings menores, tramo externo (D05) | `{internal_swings,internal_BOS/CHoCH}` |
| D08 BOS | `trend_state`(D05), swings, D01 | `BOS{dir,level,break_index,displacement}` \| `WeakBOS`/`none` |
| D09 CHoCH | `trend_state`, `protected_level`, D01, D16 | `CHoCH{dir,level,j,sweptLiquidity}` \| `none` |
| D10 MSS | `pendingCHoCH`(D09), D08, D16 | `MSS{dir,choch_index,confirm_index}` \| `none` |
| D11 Liquidity | swings(D03/D04), equal(D14/D15), extremos sesión | `Pool{level,type,origin,strength,state}` |
| D12 BSL | pools(D11), `price` | `target_bsl` (pool BSL relevante) |
| D13 SSL | pools(D11), `price` | `target_ssl` (pool SSL relevante) |
| D14 Equal Highs | swing highs(D03), `ATR` | `EqualHighs{level,touches,indices,strength}` |
| D15 Equal Lows | swing lows(D04), `ATR` | `EqualLows{level,touches,indices,strength}` |
| D16 Liquidity Sweep | pools(D11–D15), `series`, `ATR` | `Sweep{pool,dir,penetration,index}` (+ pool→swept) |
| D17 Inducement | POI, estructura interna(D06), sweeps(D16), `price` | `Inducement{level,state∈{TAKEN,PENDING,ABSENT}}` |
| D18 FVG | `series`, `ATR`, D01 | `FVG{dir,zone,CE,state,origin_index}` \| `none` |
| D19 Inverse FVG | FVG invalidado(D18), `flip_candle`, D09 | `IFVG{dir,zone,origin,flip_index,state}` \| `none` |
| D20 Balanced Price Range | FVG activos opuestos(D18) | `BPR{zone,indices}` \| `none` |
| D21 Order Block | D01, D08/D09, D18 | `POI{order_block,dir,zone,mean,state,confidence}` |
| D22 Mitigation Block | D01, extremo previo, sweeps(D16) | `POI{mitigation_block,...}` \| `none` |
| D23 Breaker Block | OB invalidado(D21), D09, D16 | `POI{breaker,dir,zone,...}` \| `none` |
| D24 Rejection Block | `series`, sweeps(D16) | `POI{rejection_block,dir,zone,...}` \| `none` |
| D25 Dealing Range | eventos estructurales(D08–D10) | `Range{low,high,ref_event,fibs}` |
| D26/27/28 Premium/Discount/Equilibrium | `price`/POI, `Range`(D25) | `pricing ∈ {PREMIUM,DISCOUNT,EQUILIBRIUM}` |
| D29 OTE | leg(D25), D32 | `OTE{zone,optimal}` + `at_optimal` |
| D30 Volume Confirmation | `series`(volumen), `dna.volume_source` | `CONFIRMED(ratio)`\|`NOT_CONFIRMED`\|`UNAVAILABLE` |
| D31 Session Context | `ts_utc`, `dna`(timezone,killzones) | `SessionCtx{session,killzone,in_killzone,in_efficiency}` |
| D32 Institutional Fibonacci | dealing range(D25), sweeps(D16), `ATR` | `Fib{O,T,span,retr,proj,ote,optimal}` \| `none` |

### E.2 Diagramas lógicos (flujo determinista por detector)

> Notación: `[cond]` = condición booleana; `►` = emite; `⊘` = `none`; los
> parámetros se leen del Market DNA efectivo.

**D01 Displacement / D02 Imbalance (primitivos)**
```
D01: ventana[a..b] → [move≥disp·ATR] & [body_ratio≥th] & [misma dir] ► Displacement ⋮ else ⊘
D02: tríada[i-1,i,i+1] → [gap≥min_size] ? ► Imbalance(zone,dir) : ⊘ → track state(unfilled→partial→filled)
```

**D03 Swing High / D04 Swing Low (espejo)**
```
para i con k velas a cada lado:
   [H_i > H_{i±j} ∀j∈1..k]  (Swing High)   → confirmado en i+k ► SwingHigh
   [L_i < L_{i±j} ∀j∈1..k]  (Swing Low)     → confirmado en i+k ► SwingLow
   bordes → PENDING ; i<k → INSUFFICIENT ; luego etiquetar HH/HL/LH/LL vs previo
```

**D05 External / D06 Internal Structure**
```
nuevo swing confirmado → etiqueta(HH/HL/LH/LL) → patrón{HH,HL}=BULL / {LH,LL}=BEAR / else RANGE
                       → recalcular protected_high/low   (D05: grado mayor; D06: dentro del tramo externo)
```

**D08 BOS / D09 CHoCH**
```
D08 (a favor tendencia): cierre supera último swing extremo → [displacement?] ► BOS : WeakBOS/⊘
D09 (contra tendencia):  cierre supera protected_level      → [displacement?] ► CHoCH(+sweptLiq?) : Weak/⊘
   fakeout dentro de failure_bars → *_failed
```

**D10 MSS**
```
CHoCH(dir) → abrir ventana confirm_bars → [BOS(dir) follow-through] OR [displacement fuerte + sweep]
          ► MSS(dir) : (sin confirmación) ⊘ ; CHoCH opuesto tras MSS → mss_reversed
```

**D11 Liquidity / D12 BSL / D13 SSL**
```
extremo/equal/sesión confirmado → upsert Pool (fusiona a <tol, strength++) 
   filtrar type=BSL & level>price ► target_bsl   |   type=SSL & level<price ► target_ssl
   sweep(D16) → state=swept
```

**D14 Equal Highs / D15 Equal Lows (espejo)**
```
swings(mismo tipo) → agrupar por banda [max-min ≤ equal_tol·ATR] con min_separation
   [cluster.size ≥ min_touches] ► EqualHighs/EqualLows(level,touches) : ⊘
```

**D16 Liquidity Sweep**
```
para pool cercano:
   BSL: [H_j > p+pen] & [C_j < p] & [upper_wick/range ≥ ratio] ► Sweep(bearish bias) + pool.swept
   SSL: [L_j < p-pen] & [C_j > p] & [lower_wick/range ≥ ratio] ► Sweep(bullish bias) + pool.swept
   cierre MÁS ALLÁ del nivel → NO es sweep (es breakout → D08/D09)
```

**D17 Inducement**
```
POI objetivo → buscar swing interno opuesto entre price y POI (=IDM)
   IDM ausente ► ABSENT | IDM barrido antes de llegar al POI ► TAKEN (válido) | si no ► PENDING (esperar)
```

**D18 FVG / D19 IFVG / D20 BPR**
```
D18: tríada → [gap≥min_size] & [vela central displacement] ► FVG(zone,CE) ; update(partial/filled/invalidated)
D19: FVG con cierre-a-través (invalidated) → polaridad opuesta ► IFVG (refuerzo si CHoCH)
D20: FVG_bull.zone ∩ FVG_bear.zone (≥min_overlap, ≤max_gap_bars) ► BPR(intersección)
```

**D21 OB / D22 Mitigation / D23 Breaker / D24 Rejection (marco POI)**
```
D21: displacement+BOS/CHoCH → última vela contraria = OB ► POI{fresh} ; cierre a través → invalidated
D22: cambio de estructura SIN sweep del extremo previo → última vela contraria ► mitigation_block
D23: OB invalidado + CHoCH(dir opuesto) → polaridad invertida ► breaker
D24: velas de mecha dominante que barren pool → zona de mechas ► rejection_block
   común: tested(toca) → mitigated(≥umbral) → invalidated(cierre a través)
```

**D25 Dealing Range → D26/27/28 Pricing → D29 OTE / D32 Fibonacci**
```
evento estructural → fijar Range[low,high] (leg impulsivo) → fibs
   pos=(price-low)/(high-low): >0.5+band ► PREMIUM | <0.5-band ► DISCOUNT | else EQUILIBRIUM
   D32: O(origen=sweep_extreme), T(destino) → retr=T-r·span, proj=O+e·span, OTE=[0.618..0.786]
   D29: POI/entry ∈ OTE band & pricing coherente ► score OTE (⛔ fib nunca gatilla solo)
```

**D30 Volume / D31 Session (contexto)**
```
D30: [V_j ≥ SMA(V,period)·spike_mult] ► CONFIRMED : NOT_CONFIRMED ; sin datos ► UNAVAILABLE
D31: ts_utc → toZone(IANA,DST) → clasifica sesión/killzone → in_killzone / in_efficiency_window
```

---

# Apéndice F — Integración de los detectores con Market Context, Trading, Scoring y Risk

> Completa el campo **18 (integración de sistema)** del contrato. La integración
> **entre detectores** ya vive en "Relaciones" de cada cuerpo y en el DAG
> (Apéndice B); aquí se documenta la integración con los **otros motores**.

### F.1 Con el **Market Context Engine** (ENG-011) — *aguas arriba*
- **Gate previo (⛔):** ningún detector de esta Biblia se ejecuta si el gate del MCE
  es `FAIL`. El MCE decide *si* hay que leer estructura; el Smart Money Engine la lee.
- **Market DNA → parámetros efectivos:** el MCE (vía Market DNA) provee los umbrales
  con los que corren los detectores (`equal_level_tol`, `sweep_min_penetration`,
  `displacement_atr_mult`, `fvg_min_size`, `fib_min_leg_atr`, `swing_lookback`…).
  **Adapta filtros, no reglas** (la definición de cada detector es invariante).
- **Régimen → priorización (no override):** el `regime` del MCE orienta *qué*
  salidas se ponderan más aguas abajo (TREND → OB/continuación a favor; RANGE/ACC-DIST
  → breaker/rejection/reversión en extremos). Los detectores **siempre** emiten lo que
  detectan; la priorización ocurre en el Scoring, no alterando la detección.
- **Reutilización:** el MCE consume internamente D14/D15 (equal), D16 (sweep) y D31
  (sesión) para su propio análisis de liquidez/manipulación/sesión — misma
  especificación, sin duplicar lógica.

### F.2 Con el **Trading Engine** (ENG-001) — *modelo de entrada*
El modelo de entrada canónico (ENG-001 §27) es una **secuencia de detectores**:
```
D31 killzone → D25/D32 bias & OTE → D16 sweep → D09 CHoCH(+D01) → D21/D23/D24 POI
            → D18/D19 FVG/IFVG → D29 OTE ∩ POI ∩ discount(D27) → ARMED → ENTER
```
- Los detectores producen las **features**; el Trading Engine las **orquesta** en su
  máquina de estados (`CONTEXT_GATE → SCANNING → ARMED → SCORING → ENTERING → MANAGING`).
- **Anclas de gestión:** D16 (mecha del sweep) y D21/D24 (zona del POI) definen el
  **SL**; D11–D15 (pools) y D32 (proyecciones) definen el **TP** (ENG-001 §29/§30).
- **Invalidación de setup:** la invalidación de cualquier detector clave del setup
  (OB roto, FVG rellenado, CHoCH fallido) cancela el `ARMED` (no repaint: la
  invalidación es un evento nuevo, no reescribe el pasado).

### F.3 Con el **Scoring Engine** (ENG-001 §26) — *mapa factor → detectores*
Cada factor del Entry Score se alimenta de detectores concretos:

| Factor de scoring (peso) | Detectores que lo alimentan |
|--------------------------|-----------------------------|
| 1. HTF bias alignment (15) | D05/D06 (estructura), D25 (rango), + `MarketContext.alignment` |
| 2. Estructura LTF CHoCH/BOS (15) | D08, D09, D10, D01 |
| 3. Liquidity sweep (12) | D16 (+ D11–D15) |
| 4. Calidad del POI (12) | D21, D22, D23, D24 (+ D17 inducement) |
| 5. Imbalance FVG/IFVG (10) | D18, D19, D20 |
| 6. Premium/Discount (8) | D25, D26, D27, D28 |
| 7. OTE / Fibonacci (6) | D29, D32 (⛔ nunca gatilla solo) |
| 8. Killzone/sesión (8) | D31 (+ `MarketContext.session`) |
| 9. Régimen ATR + spread (6) | `MarketContext` (ENG-011) |
| 10. Volumen (4) | D30 |
| 11. Liquidez objetivo (4) | D11, D12, D13 |

- **Vetos duros** del scoring que dependen de detectores: setup **sin** D16 (sweep),
  POI **mitigado** (D21–D24 `state`), entrada en `EQUILIBRIUM` (D28), **sin** D18 en
  el desplazamiento → bajan/anulan el score.
- **Determinismo del score:** como cada factor mapea a salidas deterministas de
  detectores, la suma es **exacta y explicable** (base de la Explicabilidad, ENG-010).

### F.4 Con el **Risk Engine** (ENG-005) — *anclas y protección*
- **SL objetivo:** D16 (extremo de la mecha del barrido) y D21/D24 (borde del POI) →
  Risk deriva el `1R` y el *position sizing* del SL (ENG-001 §29/§34).
- **TP objetivo:** D11–D15 (pools de liquidez) y D32 (proyecciones 1.272–2.618) →
  Risk valida el `RR mínimo` (⛔ no opera si `RR < min_rr`).
- **Invalidación → riesgo:** el nivel de invalidación de cada detector es el punto de
  "tesis rota"; Risk lo usa como cota dura del SL (`never_widen_sl`).
- **Kill-switch de contexto:** manipulación/expansión extrema (detectadas vía D16 en
  cadena y volatilidad del MCE) pueden pausar entradas y proteger lo abierto,
  coordinado con el kill-switch de Risk (ENG-005 §34.4).

---

> **Versión 0.2 — Borrador (🟨).** Estándar oficial de detección Smart Money de
> ELYON QUANT: **31 detectores** (D01–D32) con el **contrato de 18 campos** (§0.4)
> completado por los apéndices E (I/O + diagrama lógico) y F (integración con Market
> Context, Trading, Scoring y Risk). **Reglas obligatorias garantizadas:**
> determinismo total, cero interpretación humana, **no-repaint** tras confirmación,
> reproducibilidad bit a bit, parámetros 100 % configurables (vía Market DNA) y
> validación por tests unitarios (golden datasets, Apéndice C). Su aprobación (🟩)
> requiere revisión de Quant Lead, CTO y QA Lead; prerrequisito del gate D4 junto al
> Trading Engine Bible (ENG-001). Todo cambio de reglas/umbrales se gestiona por
> RFC/ADR y se re-valida con golden datasets y backtesting (ENG-004).
