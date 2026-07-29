<!--
title: ELYON QUANT — Market Context Engine Specification (+ Market DNA)
id: ENG-011 (Market Context Engine — motor obligatorio, primer gate)
owner: Quant Lead
reviewers: [CTO/Principal Architect, ML Lead, Risk Lead, QA Lead]
status: draft
version: 0.1
last_updated: 2026-07-29
supersedes: formaliza el concepto `instrument_profile` referenciado en ENG-001/ENG-002
-->

# ELYON QUANT — MARKET CONTEXT ENGINE (ENG-011)

> **El primer motor que se ejecuta.** Antes de que el Smart Money Engine
> (ENG-002) busque un solo Order Block, el Market Context Engine (MCE) determina
> **el contexto completo del mercado** y emite un **Context Score (0–100)**. Si el
> contexto **no supera el mínimo requerido**, el resto del motor **ni siquiera
> comienza a buscar entradas**. *Sin contexto favorable no hay setup.*
>
> Incluye la especificación de **MARKET DNA**: el perfil configurable por activo
> que adapta los **filtros** del motor **sin modificar las reglas** de la
> estrategia.

Este documento es **obligatorio** y prerrequisito del gate D4. El MCE es el
"portero" institucional: convierte la pregunta minorista *"¿hay una entrada?"* en
la pregunta profesional *"¿es este un mercado en el que deberíamos siquiera estar
buscando?"*.

---

## 0. Preámbulo

### 0.1 Posición en el pipeline (se ejecuta primero)
```
Market Data (ENG-000)
      │  velas cerradas por TF + calendario + Market DNA
      ▼
┌─────────────────────────── MARKET CONTEXT ENGINE (ENG-011) ───────────────────────────┐
│  Régimen · Tendencia · Consolidación · Expansión · Compresión · Acumulación ·          │
│  Distribución · Manipulación · Volatilidad · HTF/MTF/LTF · Killzones · Liquidez ·       │
│  Riesgo de noticias · Calidad de mercado   ──►   CONTEXT SCORE (0–100)  ──►  GATE       │
└───────────────┬───────────────────────────────────────────────────────────────────────┘
                │  gate = PASS ?
        NO ─────┤ CONTEXT_FAIL → NO se busca entrada (motor en espera; se registra por qué)
        SÍ ─────▼ CONTEXT_PASS → MarketContext{...} entregado a:
      Smart Money Engine (ENG-002) → Trading Engine (ENG-001, scoring de entrada)
                                     → Risk (ENG-005) → Execution (ENG-006)
                                     → Decision Replay (ENG-009) + Explainable AI (ENG-010)
```
El MCE **no busca entradas**: decide **si tiene sentido buscarlas** y con qué
**adaptación de filtros** (Market DNA). Es un filtro de alto nivel, barato y
frecuente, que protege al resto del sistema de operar en mercados de baja calidad.

### 0.2 Invariantes (hereda de ENG-002 §0.2)
Determinismo · no look-ahead (solo barras `≤ t`) · velas cerradas · idempotencia
incremental · **unidades relativas** (todo en múltiplos de ATR / percentiles /
parámetros de Market DNA, nunca absolutos hardcodeados) · **explicabilidad**
(cada componente del Context Score es trazable al DecisionRecord, ENG-009/010).

### 0.3 Dos scores, dos propósitos (evitar confusión)
| | **Context Score (ENG-011)** | **Entry Score (ENG-001 §26)** |
|--|------------------------------|-------------------------------|
| Pregunta | ¿Es un mercado en el que buscar? | ¿Este setup concreto es válido? |
| Momento | **Primero** (gate) | **Después**, solo si el gate pasa |
| Unidad | 0–100 (calidad de contexto) | 0–100 (confluencia del setup) |
| Efecto si falla | El motor **no escanea** | No se abre esa operación |
| Dueño | Market Context Engine | Trading/Smart Money Engine |

El Context Score **habilita** el trabajo del Entry Score; no lo reemplaza. Los
factores de contexto que hoy aparecen en el Entry Score (killzone, ATR, spread,
noticias) pasan a **originarse en el MCE** y se consumen aguas abajo (fuente única
de verdad del contexto).

---

## 1. Misión y principios

1. **Contexto antes que setup.** Ningún detector Smart Money se ejecuta si el
   contexto no pasa el gate. Ahorra cómputo y, sobre todo, evita operar en
   condiciones estructuralmente desfavorables.
2. **Régimen determina táctica.** El MCE no solo filtra: **clasifica el régimen**
   (tendencia, rango, acumulación…) para que el resto del motor **adapte su
   sensibilidad** (vía Market DNA), sin cambiar sus reglas.
3. **Adaptar filtros, no reglas.** Market DNA ajusta umbrales/tolerancias por
   activo; **jamás** modifica la lógica de la estrategia (BOS es BOS en todos los
   activos; lo que cambia es el `equal_level_tol`, no la definición).
4. **Barato y frecuente.** Se ejecuta en cada cierre de vela del TF de contexto;
   es `O(ventana)`, muy inferior al coste de todo el Smart Money Engine.
5. **Explicable.** El gate siempre dice **por qué** pasó o falló (alimenta ENG-010).

---

## 2. Arquitectura

### 2.1 Módulo `market_context`
Módulo del monolito modular con Clean Architecture (domain/application/
infrastructure/interfaces). **Se ejecuta antes** que `strategy_lab`/`execution` en
el ciclo de evaluación. Candidato a extracción a servicio solo si su frecuencia lo
justifica (normalmente vive con el motor).

### 2.2 Arquitectura interna (sub-detectores → agregador → gate)
```
                 ┌───────────────── MarketDNA (perfil del activo) ─────────────────┐
                 │ volatilidad · liquidez · horarios · spread · ATR · sensibilidad │
                 └───────────────────────────────┬────────────────────────────────┘
   velas HTF/MTF/LTF ─┐                           │ (adapta umbrales, NO reglas)
   calendario noticias │                          ▼
   spread/quotes  ─────┤     ┌──────────────────────────────────────────────┐
                       ├────►│  SUB-DETECTORES DE CONTEXTO                    │
                       │     │  A. Volatility Analyzer                       │
                       │     │  B. Regime Classifier (trend/range/exp/comp)  │
                       │     │  C. Accumulation/Distribution (Wyckoff)       │
                       │     │  D. Manipulation Detector                     │
                       │     │  E. MTF Context (HTF/MTF/LTF alignment)       │
                       │     │  F. Session & Killzone Context                │
                       │     │  G. Liquidity Availability                    │
                       │     │  H. News Risk                                 │
                       │     │  I. Market Quality (composite)                │
                       │     └───────────────────┬──────────────────────────┘
                       │                          ▼
                       │           ┌──────────── Context Aggregator ─────────────┐
                       │           │ pondera factores → CONTEXT SCORE (0–100)     │
                       │           │ aplica VETOS duros → PASS / FAIL             │
                       │           └───────────────────┬──────────────────────────┘
                       │                                ▼
                       │                    MarketContext{ regime, scores, gate, dna_ref }
                       │                                │
                       └────────────────────────────────► consumidores (ENG-002/001/005/003/004)
```

### 2.3 Entradas y salidas
- **Entradas:** velas cerradas por TF (HTF/MTF/LTF), calendario económico, spread/
  cotización en tiempo real, y el **Market DNA** del símbolo.
- **Salida:** objeto `MarketContext` (§3) + `ContextScore` + decisión de gate,
  emitido como evento `MarketContextEvaluated` (consumido por el resto y por el
  Decision Replay Engine).

---

## 3. Objeto de salida: `MarketContext`

Contrato conceptual (todo determinista y trazable):
```
MarketContext {
  symbol, dna_ref (dna_hash), timestamp (UTC), timeframe_triad,
  regime:            enum { TREND_UP, TREND_DOWN, RANGE, EXPANSION,
                            COMPRESSION, ACCUMULATION, DISTRIBUTION, MANIPULATION },
  regime_confidence: 0..1,
  htf: { regime, bias, volatility_state },        // contexto por nivel
  mtf: { regime, bias, volatility_state },
  ltf: { regime, bias, volatility_state },
  alignment:         enum { ALIGNED, PARTIAL, CONFLICT },
  volatility:        { atr, atr_regime ∈ {low,normal,high,extreme}, realized_vol,
                       expansion_state, compression_state },
  session:           { session, killzone, in_efficiency_window (per DNA) },
  liquidity:         { spread, spread_state, availability ∈ {high,normal,low},
                       target_pools_present },
  news_risk:         { level ∈ {none,low,medium,high}, next_event, block_active },
  market_quality:    { efficiency_ratio, noise, quality_state, score },
  context_score:     0..100,
  gate:              enum { PASS, FAIL },
  gate_reason:       string,          // motivo exacto (para XAI)
  factor_breakdown:  [ {factor, weight, points, condition} ],
  vetoes:            [ {veto_id, active, reason} ],
  dna_adjustments:   { ...umbrales efectivos aplicados... }   // trazabilidad
}
```
Este objeto es la **fuente única de contexto** para todo el resto del sistema.

---

## 4. Estados

### 4.1 Máquina de estados del motor
```
        ┌──────────┐  nueva vela cerrada (TF contexto)
        │  IDLE    │─────────────────────────────┐
        └──────────┘                              ▼
                                        ┌───────────────────┐
                                        │ EVALUATING_CONTEXT │ (corre A..I)
                                        └─────────┬─────────┘
                              vetos / score<umbral│  score≥umbral & sin vetos
                            ┌───────────▼──────┐   ┌────────▼─────────┐
                            │  CONTEXT_FAIL     │   │  CONTEXT_PASS     │
                            │ (no se escanea;   │   │ (entrega          │
                            │  registrar razón) │   │  MarketContext)   │
                            └───────────┬──────┘   └────────┬─────────┘
                                        │ nueva vela           │ Smart Money Engine escanea
                                        └──────────► IDLE ◄────┘
   * Veto crítico en cualquier estado (manipulación extrema / news / mercado cerrado)
     → CONTEXT_FAIL inmediato (y, si hay operativa abierta, notifica a Risk).
```

### 4.2 Máquina de estados del **régimen de mercado**
El régimen es un estado del *mercado* (no del motor). Transiciones objetivas:
```
                    ┌──────────────► COMPRESSION ──────────┐ (ATR↑ + range↑)
                    │ (ATR↓, range↓)                        ▼
   ACCUMULATION ◄───┤                                  EXPANSION
   (rango tras baja,│                                       │  (displacement sostenido)
    sweeps SSL)     │                                       ▼
        │ ruptura   └──────── RANGE ◄────────► TREND_UP / TREND_DOWN
        │ alcista            (ER bajo,           (ER alto, HH/HL o LH/LL, BOS)
        ▼                    oscilación)                 │
   TREND_UP                                              │ agotamiento + sweeps BSL/SSL
        ▲                                                ▼
        └───────────────── DISTRIBUTION ◄────────────────┘  (rango tras subida)
   MANIPULATION: estado transversal — se activa cuando hay sweeps con reversión de
   alta frecuencia / spikes de noticias; suele preceder a EXPANSION o revertir el
   régimen. Tiene prioridad de veto sobre la clasificación "normal".
```
`regime_confidence` mide la nitidez de la clasificación (0..1). Un régimen se
mantiene hasta que las condiciones de transición (medidas objetivas, §5) se
cumplen con confirmación (`regime_confirm_bars`), evitando parpadeo.

---

## 5. Algoritmos (sub-detectores de contexto)

> Todas las medidas son **objetivas y deterministas**, expresadas en múltiplos de
> ATR, percentiles o parámetros de Market DNA. Se calculan por TF (HTF/MTF/LTF).
> Primitiva compartida: **Efficiency Ratio (ER)** de Kaufman,
> `ER(w) = |C_t − C_{t−w}| / Σ_{i=t−w+1..t} |C_i − C_{i−1}|` ∈ [0,1]. ER→1 =
> movimiento direccional limpio; ER→0 = ruido/rango.

### 5.A Volatility Analyzer
**Objetivo.** Clasificar el régimen de volatilidad y detectar expansión/compresión.
**Medidas.**
- `atr = ATR(atr_period)`; `atr_ma = SMA(atr, vol_ma_period)`;
  `atr_ratio = atr / atr_ma`.
- `atr_regime`: `low` si `atr_ratio < dna.atr_low`; `normal` si dentro de
  `[dna.atr_low, dna.atr_high]`; `high` si `> dna.atr_high`; `extreme` si
  `> dna.atr_extreme`.
- `realized_vol` = desviación estándar de retornos log en `vol_window`
  (normalizada), para contraste con ATR.
- `atr_slope` = pendiente de `atr` sobre `vol_slope_window` (regresión simple).
**Salida.** `{atr, atr_regime, realized_vol, atr_slope}`.
**Parámetros.** `atr_period`(14), `vol_ma_period`(50), `vol_window`(20),
`vol_slope_window`(10); umbrales `dna.atr_low/high/extreme` (del Market DNA).
**Casos válidos.** `atr_ratio=1.0` → normal. `atr_ratio=2.2` con `dna.atr_high=1.6`,
`dna.atr_extreme=2.0` → extreme (candidato a veto).
**Casos inválidos.** `< atr_period` velas → `insufficient_data` (gate no evalúa).
**Pseudocódigo.**
```
function analyzeVolatility(series, dna, cfg):
    atr = ATR(series, cfg.atr_period)
    ratio = atr / SMA(atrSeries, cfg.vol_ma_period)
    regime = classify(ratio, dna.atr_low, dna.atr_high, dna.atr_extreme)
    return Vol(atr, regime, realizedVol(series, cfg.vol_window), slope(atrSeries, cfg.vol_slope_window))
```

### 5.B Regime Classifier (trend / range / expansion / compression)
**Objetivo.** Clasificar el régimen base combinando **ER**, **estructura** (usa
D05/D08 de ENG-002 a nivel de contexto) y **volatilidad**.
**Reglas objetivas.**
- **TREND_UP / TREND_DOWN:** `ER ≥ trend_er_min` (def. 0.5) **y** estructura con
  HH/HL (up) o LH/LL (down) **y** ≥1 BOS reciente en la dirección. `atr_regime ≠ extreme`.
- **RANGE / CONSOLIDATION:** `ER ≤ range_er_max` (def. 0.3) **y** precio oscilando
  dentro de una banda `[range_low, range_high]` con ≥ `range_min_touches` toques y
  **sin** BOS. Anchos por equal highs/lows (D14/D15).
- **EXPANSION:** `atr_slope > 0` **y** `range_recent ≥ expansion_atr_mult · atr_ma`
  **y** ER moderada-alta (`≥ expansion_er_min`, def. 0.4) → el mercado está
  "abriendo" rango (a menudo tras compresión).
- **COMPRESSION:** `atr_slope < 0` **y** `range_percentile(range_window) <
  compression_pct` (def. 0.25) **y** cuerpos pequeños/solapados → "squeeze".
  Compresión **precede** a expansión.
**Salida.** `regime_base ∈ {TREND_UP, TREND_DOWN, RANGE, EXPANSION, COMPRESSION}` +
`regime_confidence` (función de ER, nitidez de estructura y separación de umbrales).
**Casos válidos.** ER=0.72 + HH/HL + BOS alcista → TREND_UP (confidence alta).
ER=0.18 + oscilación con 4 toques → RANGE.
**Casos inválidos.** ER en zona muerta (0.3–0.5) sin estructura → régimen ambiguo
→ `RANGE` con baja confidence (y penalización de calidad, no operar).
**Pseudocódigo.**
```
function classifyRegime(series, vol, structure, dna, cfg):
    er = efficiencyRatio(series, cfg.er_window)
    if er >= cfg.trend_er_min and structure.trend != RANGE and structure.hasRecentBOS:
        return Regime(structure.trend==BULL ? TREND_UP : TREND_DOWN, conf(er, structure))
    if vol.slope < 0 and rangePercentile(series, cfg.range_window) < cfg.compression_pct:
        return Regime(COMPRESSION, conf(...))
    if vol.slope > 0 and recentRange(series) >= cfg.expansion_atr_mult*vol.atr_ma and er >= cfg.expansion_er_min:
        return Regime(EXPANSION, conf(...))
    if er <= cfg.range_er_max and boundedOscillation(series, cfg.range_min_touches):
        return Regime(RANGE, conf(...))
    return Regime(RANGE, lowConfidence)     // ambiguous -> treated as untradeable range
```

### 5.C Accumulation / Distribution (Wyckoff objetivo)
**Objetivo.** Detectar **acumulación** (intención compradora construyéndose) y
**distribución** (vendedora) dentro de rangos en extremos.
**Reglas objetivas.**
- **ACCUMULATION:** `RANGE` situado **tras un movimiento bajista** (contexto HTF
  bajista o precio en discount del rango mayor) **+** ≥ `wyckoff_min_sweeps`
  barridos de **SSL** (D16) en la parte baja **sin** continuación bajista
  (el precio no hace nuevos mínimos: **absorción**) **+** formación de **higher
  lows internos** (D06).
- **DISTRIBUTION:** espejo — rango tras subida, barridos de **BSL** sin
  continuación alcista, lower highs internos.
**Salida.** `ACCUMULATION | DISTRIBUTION | none` + `confidence`.
**Casos válidos.** Rango en zona baja tras tendencia bajista, 2 sweeps de SSL sin
nuevos mínimos, HL internos → ACCUMULATION.
**Casos inválidos.** Rango sin sweeps de extremo (simple consolidación neutra) →
`none` (queda RANGE). Sweeps **con** continuación (rompe y sigue) → no es
absorción, es ruptura → régimen de tendencia/expansión.
**Pseudocódigo.**
```
function detectAccumulationDistribution(series, structure, sweeps, htf, cfg):
    if not isRange(structure): return none
    lowSweeps = countSweeps(sweeps, SSL, withinRangeLow)
    if priorContextBearish(htf) and lowSweeps >= cfg.min_sweeps
       and not madeNewLows(series) and hasHigherLowsInternal(structure):
        return Accumulation(conf(lowSweeps, structure))
    // mirror for Distribution (BSL, higher context bullish, no new highs, LH internal)
    return none
```

### 5.D Manipulation Detector
**Objetivo.** Identificar mercados de **manipulación** (stop hunts en cadena,
spikes de noticias, reversiones erráticas) donde no se debe operar con lógica normal.
**Reglas objetivas.**
- `sweep_reversal_count` (barridos con reversión, D16) en `manip_window` ≥
  `manip_min_reversals` (def. 3), **y**
- `avg_wick_ratio` en la ventana ≥ `manip_wick_ratio` (def. 0.55), **y**
- `ER` baja (`< manip_er_max`, def. 0.3) **con** `atr_regime ∈ {high, extreme}`
  → "violento pero no direccional".
- **Refuerzo:** ventana de noticias activa o spike post-noticia (`news_spike`).
**Salida.** `manipulation ∈ {none, elevated, extreme}` + `confidence`.
`extreme` → **veto duro** del gate.
**Casos válidos.** 4 sweeps con reversión + mechas 60 % + ER 0.2 + ATR extreme →
manipulation=extreme (veto).
**Casos inválidos.** 1 sweep aislado con continuación → no es manipulación (es toma
de liquidez sana previa a impulso).
**Pseudocódigo.**
```
function detectManipulation(series, sweeps, vol, news, cfg):
    rev = countSweepReversals(sweeps, cfg.manip_window)
    wick = avgWickRatio(series, cfg.manip_window)
    er = efficiencyRatio(series, cfg.manip_window)
    score = 0
    if rev >= cfg.min_reversals: score++
    if wick >= cfg.wick_ratio: score++
    if er < cfg.er_max and vol.regime in {HIGH,EXTREME}: score++
    if news.spike or news.block_active: score++
    return score>=3 ? EXTREME : (score==2 ? ELEVATED : NONE)
```

### 5.E MTF Context (HTF / MTF / LTF)
**Objetivo.** Calcular el contexto por nivel y su **alineación**.
**Reglas.** Ejecutar 5.A–5.D en cada TF de la tríada (`timeframe_triad`). Definir
`alignment`:
- **ALIGNED:** HTF y MTF comparten bias/régimen compatible (p.ej. HTF TREND_UP y
  MTF pull-back/accumulation) **y** LTF no contradice.
- **PARTIAL:** MTF neutral/rango bajo un HTF con tendencia.
- **CONFLICT:** HTF y MTF con bias opuestos → contexto de baja operabilidad.
**Salida.** `{htf, mtf, ltf, alignment}`.
**Casos válidos.** HTF TREND_UP + MTF RANGE en discount + LTF sin conflicto →
ALIGNED (contexto ideal de continuación).
**Casos inválidos.** HTF TREND_UP + MTF TREND_DOWN → CONFLICT (penaliza fuerte el
Context Score; con `require_alignment` puede vetar).
**Pseudocódigo.**
```
function mtfContext(dataByTF, dna, cfg):
    ctx = { tf: fullContext(dataByTF[tf], dna, cfg) for tf in cfg.triad }
    return { ...ctx, alignment: classifyAlignment(ctx.htf, ctx.mtf, ctx.ltf, cfg) }
```

### 5.F Session & Killzone Context
**Objetivo.** Situar el momento en el mapa institucional y en las **horas de mayor
eficiencia del activo** (Market DNA).
**Reglas.** Reutiliza D31 (ENG-002) para sesión/killzone; añade
`in_efficiency_window` = `now ∈ dna.efficiency_hours`. `session_quality` alto en
killzone + ventana de eficiencia del activo.
**Salida.** `{session, killzone, in_killzone, in_efficiency_window}`.
**Casos válidos.** 08:30 NY para XAUUSD (NY-sensible) → killzone + eficiencia alta.
**Casos inválidos.** 14:00 NY (fuera de killzone) para EURUSD → penaliza.
**Pseudocódigo.**
```
function sessionContext(ts, dna, cfg):
    s = D31.sessionContext(ts, dna.session_cfg)
    s.in_efficiency_window = within(toZone(ts, dna.timezone), dna.efficiency_hours)
    return s
```

### 5.G Liquidity Availability
**Objetivo.** Evaluar si hay **liquidez suficiente y sana** para operar.
**Medidas.**
- `spread_state`: `ok` si `spread ≤ dna.spread_typical`; `wide` si `≤ dna.spread_max`;
  `blowout` (veto) si `> dna.spread_max`.
- `availability`: función de sesión (Asia suele ser `low` para FX), festivos
  (`holiday_calendar`), rollover, y presencia de **pools de liquidez objetivo**
  (D11–D15) hacia los que operar.
- `target_pools_present`: existen BSL/SSL claros como objetivo/TP.
**Salida.** `{spread, spread_state, availability, target_pools_present}`.
**Casos válidos.** Spread ≤ típico + killzone + pools objetivo presentes → `high`.
**Casos inválidos.** Rollover/festivo con spread `blowout` → veto de liquidez.
**Pseudocódigo.**
```
function liquidityAvailability(quote, session, pools, dna, cfg):
    spreadState = spread<=dna.spread_typical ? OK : (spread<=dna.spread_max ? WIDE : BLOWOUT)
    avail = f(session, holiday(now, dna), rollover(now))
    return Liquidity(spread, spreadState, avail, hasTargetPools(pools))
```

### 5.H News Risk
**Objetivo.** Cuantificar el riesgo por noticias (fuente: calendario, `dna.news`).
**Reglas.** `block_active` si hay evento de **alto impacto** que afecte a las
divisas/subyacente del activo dentro de `[−news_block_before, +news_block_after]`
(veto duro). `level` escala por proximidad e impacto.
**Salida.** `{level, next_event, block_active}`.
**Casos válidos.** Sin eventos de alto impacto en 2 h → `level=none`.
**Casos inválidos.** NFP en 10 min para pares USD/XAUUSD → `block_active=true` (veto).
**Pseudocódigo.**
```
function newsRisk(calendar, symbol, dna, now, cfg):
    ev = nextHighImpact(calendar, dna.news_currencies, now)
    block = ev != null and within(now, ev.time, cfg.block_before, cfg.block_after)
    return NewsRisk(level(ev, now), ev, block)
```

### 5.I Market Quality (composite)
**Objetivo.** Resumir la "operabilidad limpia" del mercado en un sub-score.
**Medidas (normalizadas 0..1, ponderadas).**
- **Eficiencia direccional** (ER fuera de la zona muerta) — mercados demasiado
  ruidosos puntúan bajo.
- **Estabilidad de spread** (varianza de spread baja).
- **ATR en banda operable** (ni plano ni extremo).
- **Baja frecuencia de gaps** (continuidad de precio).
- **Ausencia de manipulación** (5.D).
**Salida.** `{efficiency_ratio, noise, quality_state ∈ {poor,fair,good,excellent}, score}`.
**Casos válidos.** ER 0.6, spread estable, ATR normal, sin gaps → `good/excellent`.
**Casos inválidos.** ER 0.15, spread errático, ATR extreme → `poor` (arrastra el
Context Score por debajo del umbral).
**Pseudocódigo.**
```
function marketQuality(vol, series, manip, spreadHist, cfg):
    q = w1*efficiencyTerm(ER) + w2*spreadStability(spreadHist)
      + w3*atrBandTerm(vol) + w4*gapTerm(series) + w5*(manip==NONE?1:0)
    return Quality(ER, 1-ER, bucket(q), round(q*100))
```

---

## 6. Context Score (0–100) y Gate

### 6.1 Agregación ponderada
El Context Aggregator combina los sub-detectores en un score 0–100. Pesos por
defecto (⚙️ configurables por Market DNA / `strategy_version`):

| # | Factor de contexto | Fuente | Condición que puntúa | Peso |
|---|--------------------|--------|----------------------|------|
| 1 | **Régimen operable** | 5.B/5.C | Régimen tradeable (TREND / RANGE limpio / ACC-DIST en extremos) con `regime_confidence` alta | 22 |
| 2 | **Alineación MTF** | 5.E | HTF/MTF ALIGNED (o PARTIAL favorable) | 16 |
| 3 | **Calidad de mercado** | 5.I | `quality_state ≥ good` (ER sana, spread estable, sin gaps) | 16 |
| 4 | **Volatilidad operable** | 5.A | `atr_regime ∈ {normal, high}` dentro de banda DNA (ni plano ni extremo) | 12 |
| 5 | **Killzone / eficiencia** | 5.F | En killzone y en ventana de eficiencia del activo | 12 |
| 6 | **Liquidez disponible** | 5.G | Spread OK, `availability ≥ normal`, pools objetivo presentes | 10 |
| 7 | **Riesgo de noticias despejado** | 5.H | Sin evento de alto impacto inminente | 8 |
| 8 | **Ausencia de manipulación** | 5.D | `manipulation = none` | 4 |
|   | **TOTAL** | | | **100** |

### 6.2 Vetos duros (⛔ — fuerzan `gate = FAIL` con cualquier score)
- `news.block_active` (ventana de alto impacto).
- `spread_state = blowout` (spread > `dna.spread_max`).
- `manipulation = extreme`.
- `atr_regime = extreme` (volatilidad ingobernable) salvo `allow_extreme_vol`.
- Mercado cerrado / festivo / baja liquidez crítica (`availability = none`).
- `alignment = CONFLICT` con `require_alignment = true`.
- `insufficient_data` (ventanas incompletas).

### 6.3 Gate
```
gate = PASS  ⇔  context_score ≥ context_score_threshold  AND  ningún veto activo
```
- `context_score_threshold` (⚙️ def. **60**, por DNA/estrategia).
- Bandas (⚙️): **< 45** contexto pobre (no operar) · **45–59** marginal
  (watch, no operar) · **60–79** operable · **≥ 80** contexto excelente
  (puede habilitar tamaño de riesgo superior en Risk Engine, dentro de límites).
- **Consecuencia dura:** si `gate = FAIL`, el Smart Money Engine **no se ejecuta**
  y el Trading Engine **no calcula Entry Score**. Se registra el motivo exacto
  (`gate_reason`) en el Decision Replay (ENG-009) → decisión `no_scan` explicable.

### 6.4 Histéresis (anti-parpadeo)
Para evitar oscilar PASS/FAIL en el borde del umbral, el gate aplica histéresis:
pasar a PASS requiere `≥ threshold`; volver a FAIL requiere caer por debajo de
`threshold − gate_hysteresis` (def. 5). Igual para transiciones de régimen
(`regime_confirm_bars`).

---

## 7. Parámetros (catálogo del motor)

| Grupo | Parámetro | Def. | Descripción |
|-------|-----------|------|-------------|
| Gate | `context_score_threshold` | 60 | Mínimo para habilitar escaneo |
| Gate | `gate_hysteresis` | 5 | Histéresis PASS/FAIL |
| Régimen | `er_window` | 20 | Ventana del Efficiency Ratio |
| Régimen | `trend_er_min` / `range_er_max` | 0.5 / 0.3 | Umbrales de tendencia/rango |
| Régimen | `expansion_atr_mult` / `expansion_er_min` | 1.5 / 0.4 | Detección de expansión |
| Régimen | `compression_pct` | 0.25 | Percentil de rango para compresión |
| Régimen | `regime_confirm_bars` | 2 | Confirmación anti-parpadeo |
| Volatilidad | `atr_period` / `vol_ma_period` | 14 / 50 | ATR y su media |
| Wyckoff | `wyckoff_min_sweeps` | 2 | Sweeps para acumulación/distribución |
| Manipulación | `manip_window` / `manip_min_reversals` | 20 / 3 | Ventana y reversiones |
| Manipulación | `manip_wick_ratio` / `manip_er_max` | 0.55 / 0.3 | Mechas y ER |
| MTF | `require_alignment` | false | Si true, CONFLICT = veto |
| Noticias | `news_block_before` / `after` | 15 / 15 min | Ventana de veto |
| Pesos | `weight_context_*` (8 factores) | §6.1 | Ponderación configurable |

> Los umbrales de volatilidad/spread/tolerancias **no** viven aquí en absoluto:
> vienen del **Market DNA** del activo (§8). El motor es agnóstico al activo.

---

## 8. MARKET DNA — perfil configurable por activo

### 8.1 Definición y propósito
**Market DNA** es el **perfil de personalidad** de cada activo: describe su
microestructura típica y aporta los **umbrales/tolerancias** con los que el motor
adapta sus **filtros**. Principio inviolable (⛔): **Market DNA adapta filtros,
NO modifica reglas.** La definición de BOS, CHoCH, OB, FVG o del scoring es
**idéntica** en todos los activos; lo que cambia por DNA es, p.ej., cuánto vale
`equal_level_tol` en ATR, el spread aceptable o las horas de eficiencia.
Además, el sistema usa el perfil **sin modificarlo automáticamente**: es
**configuración versionada** (con `dna_hash`); cualquier cambio pasa por RFC y se
**calibra en Backtesting** (ENG-004). No hay auto-mutación de reglas ni de perfil
en producción.

### 8.2 Esquema de un perfil Market DNA
```
MarketDNA {
  symbol, asset_class, session_timezone, dna_version, dna_hash,

  volatility:  { typical_atr_price, typical_atr_pct, vol_of_vol,
                 atr_low, atr_high, atr_extreme },        // multiplicadores de régimen
  liquidity:   { behavior, depth_class, session_liquidity_profile,
                 low_liquidity_windows },
  efficiency_hours: [ {from, to, tz} ],                    // cuándo opera "mejor"
  spread:      { typical, max },                           // en pips/ticks del activo
  atr:         { period, habitual_value, unit },
  news:        { currencies, high_impact_weight, block_before, block_after },

  detector_sensitivity: {                                  // OVERRIDES de filtros (no reglas)
     swing_lookback_major, swing_lookback_internal,
     equal_level_tol, sweep_min_penetration, sweep_wick_ratio,
     displacement_atr_mult, fvg_min_size, ob_zone_mode,
     fib_min_leg_atr, ... },

  recommended_params: {                                    // por defecto por activo
     context_score_threshold, killzones, entry_score_threshold_hint,
     atr_sl_mult, risk_profile_hint, ... }
}
```
- **detector_sensitivity** son *overrides* de los parámetros de ENG-002/ENG-001
  (mismos nombres) — el motor lee el efectivo = `dna.override ?? default`.
- **recommended_params** son sugerencias por activo (el usuario/estrategia puede
  ajustarlas dentro de límites); **no** alteran la lógica.

### 8.3 Perfiles de referencia (starting calibration, ⚙️)
Valores **relativos** de partida; se calibran en Backtesting. "↑/↓/=" = respecto a
un FX major típico (EURUSD como referencia).

| Activo | Clase | Volatilidad/ATR | Liquidez | Horas eficientes | Spread | Sensibilidad de detectores (destacados) |
|--------|-------|-----------------|----------|------------------|--------|------------------------------------------|
| **EURUSD** | FX major | = (referencia) | Muy alta | London KZ + NY KZ | Muy bajo | Tolerancias base; `equal_level_tol≈0.10·ATR` |
| **GBPUSD** | FX major | ↑ (más nervioso) | Alta | London KZ (GBP news) | Bajo | `equal_level_tol`↑, `sweep_min_penetration`↑ (más mechas) |
| **XAUUSD** | Metal | ↑↑↑ (ATR grande) | Alta pero "spiky" | NY KZ + solape LDN-NY | Medio-alto | `equal_level_tol`↑↑, buffers SL↑, `displacement_atr_mult`= ; muy news-sensible (USD) |
| **NAS100** | Índice | ↑↑ (intradía alto) | Alta en RTH | Cash open NY (RTH) | Medio | Gaps de apertura; killzones de cash; `fvg_min_size`↑ |
| **US30** | Índice | ↑ (algo < NAS) | Alta en RTH | Cash open NY (RTH) | Medio | Similar a NAS100, vol algo menor |
| **BTCUSD** | Cripto | ↑↑↑ (24/7) | Variable, real vol | `crypto_activity_windows` (no killzones clásicas) | Medio-alto | Volumen **real** (peso↑); tolerancias↑↑; sin news-window clásica (eventos on-chain) |
| **ETHUSD** | Cripto | ↑↑↑↑ (aún > BTC) | Variable | `crypto_activity_windows` | Alto | Como BTC con tolerancias mayores; correlación BTC (riesgo correlacionado, ENG-005) |

> Estos perfiles son **puntos de partida**. La calibración fina (ATR habitual,
> spreads, ventanas de eficiencia, sensibilidades) se obtiene con datos
> históricos en ENG-004 y se versiona. Añadir un activo nuevo = crear su Market DNA
> (no tocar el motor).

### 8.4 Cómo se aplica (adaptar sin mutar)
```
effectiveParam(name) = marketDNA.detector_sensitivity[name] ?? engineDefault(name)
```
- El MCE y el Smart Money Engine leen **parámetros efectivos** resolviendo primero
  el DNA. La **estrategia** (reglas/estructura del scoring) permanece intacta.
- `dna_hash` entra en el `config_hash` del DecisionRecord → **reproducibilidad**:
  un backtest sabe con qué DNA se evaluó cada decisión.

---

## 9. Diagramas

### 9.1 Flujo de agregación del Context Score
```
 A Vol ─┐
 B Reg ─┤                         ┌────────────┐   score≥thr & no-veto   ┌────────┐
 C AccD ┤─► pesos §6.1 ─► Σ 0..100│  AGGREGATOR │──────────────────────► │  PASS  │─► escanear
 D Manip┤        ▲                └─────┬──────┘                          └────────┘
 E MTF ─┤        │ vetos §6.2            │ veto activo / score<thr
 F Sess ┤        └───────────────────────┴──────────────────────────────► │  FAIL  │─► no escanear
 G Liq ─┤                                                                  └────────┘  (registrar razón)
 H News ┤
 I Qual ┘        Market DNA alimenta umbrales de A,F,G,H y sensibilidades.
```

### 9.2 Secuencia por vela (TF de contexto)
```
onBarClose(tf_context):
   MarketContext ctx = MCE.evaluate(dataByTF, dna, calendar, quote)
   emit MarketContextEvaluated(ctx)              // → Decision Replay (siempre)
   if ctx.gate == PASS:  SmartMoneyEngine.scan(ctx)   // solo entonces
   else:                 record no_scan(ctx.gate_reason)
```

---

## 10. Casos válidos / inválidos (nivel motor)

**Válidos (gate PASS).**
- HTF TREND_UP + MTF RANGE en discount + London KZ + spread OK + sin noticias +
  quality good → `context_score≈86` → PASS (contexto excelente).
- ACCUMULATION en discount tras barridos de SSL, killzone NY, ATR normal →
  `context_score≈72` → PASS (buscar longs de reversión).

**Inválidos (gate FAIL).**
- COMPRESSION pura (ATR↓, ER 0.15) → `context_score≈38` → FAIL (esperar expansión).
- MANIPULATION extreme (4 sweeps con reversión + noticias) → **veto** → FAIL.
- Spread `blowout` en rollover → **veto de liquidez** → FAIL.
- HTF/MTF CONFLICT con `require_alignment` → **veto** → FAIL.

---

## 11. Casos de prueba (deterministas)

- **T1 (gate PASS):** dataset trending, ER 0.7, killzone, spread típico, sin news →
  `regime=TREND_UP`, `context_score≥80`, `gate=PASS`.
- **T2 (gate FAIL por régimen):** dataset chop, ER 0.15, ATR bajo → `regime=RANGE`
  baja confianza, `context_score<45`, `gate=FAIL`.
- **T3 (veto noticias):** evento alto impacto en 10 min → `gate=FAIL`,
  `gate_reason=veto:news_window` **aunque** el score sea alto.
- **T4 (veto manipulación):** 4 sweeps con reversión + mechas 60 % + ER 0.2 →
  `manipulation=extreme`, `gate=FAIL`.
- **T5 (DNA):** mismo patrón en EURUSD vs XAUUSD → `equal_level_tol` efectivo
  distinto (por DNA), misma **clasificación de régimen** (reglas idénticas).
- **T6 (histéresis):** score oscilando 59↔61 no debe alternar gate cada vela.
- **T7 (determinismo):** dos ejecuciones sobre el mismo dataset+DNA → `MarketContext`
  idéntico bit a bit.
- **T8 (insufficient_data):** serie < ventanas → `gate=FAIL/insufficient_data`.

---

## 12. Integraciones

### 12.1 Con el Trading Engine (ENG-001)
- **Gate previo:** el Trading Engine **no calcula Entry Score** si `gate=FAIL`. La
  máquina de estados de ENG-001 gana un estado inicial `CONTEXT` antes de
  `SCANNING`.
- **Fuente de contexto:** los factores de contexto del Entry Score (killzone, ATR,
  spread, noticias, bias MTF) se **leen del `MarketContext`** (fuente única), no se
  recalculan → coherencia y no-duplicación.
- **Modulación:** `context_score ≥ 80` puede habilitar (vía Risk) el modo de alta
  convicción; contexto marginal (60–69) puede exigir Entry Score más alto.

### 12.2 Con el Smart Money Engine (ENG-002)
- **Orden:** el Smart Money Engine **solo se ejecuta tras PASS**.
- **Adaptación de sensibilidad:** el `regime` orienta qué POIs priorizar
  (TREND → continuaciones/OB a favor; RANGE/ACC-DIST → reversiones en extremos),
  y el **Market DNA** provee los umbrales efectivos de cada detector (`equal_level_tol`,
  `sweep_min_penetration`, `displacement_atr_mult`, `fvg_min_size`, `fib_min_leg_atr`…).
  Se **adaptan filtros, no reglas**.

### 12.3 Con el Risk Engine (ENG-005)
- **Riesgo dependiente del contexto:** el `context_score` y el `regime` modulan el
  tamaño (menor en contexto marginal/alta volatilidad; mayor —dentro de límites— en
  contexto excelente).
- **Kill-switch de contexto:** `manipulation=extreme`, `atr_regime=extreme` o
  `news.block_active` pueden **pausar** nuevas entradas y proteger posiciones
  abiertas (mover a BE), coordinado con el kill-switch de Risk.

### 12.4 Con el AI Engine (ENG-003)
- **Features de contexto:** `regime`, `regime_confidence`, ER, volatilidad,
  alineación MTF y quality son **features** para modelos ML (predicción de
  continuación, filtro de calidad). El AI Engine puede **refinar** la clasificación
  de régimen, pero siempre como **factor explicable** (ENG-010): nunca override
  opaco. La clasificación base por reglas siempre está disponible como *fallback*.
- **Explicabilidad:** el desglose del Context Score entra en el DecisionRecord;
  la narrativa XAI cita por qué el contexto pasó o falló.

### 12.5 Con el Backtesting (ENG-004)
- **Contexto histórico:** el MCE se ejecuta **por barra** sobre datos históricos
  (mismo determinismo/no look-ahead) → cada backtest sabe el `MarketContext` vigente.
- **Calibración de Market DNA:** los perfiles (§8.3) se ajustan con datos reales
  (ATR habitual, spreads, ventanas de eficiencia, sensibilidades) y se versionan.
- **Análisis segmentado por régimen:** el backtesting reporta performance **por
  régimen** (¿el sistema gana en TREND y pierde en RANGE?) → insumo para calibrar
  pesos del Context Score y umbrales del gate.
- **Reproducibilidad:** `dna_hash` en el `config_hash` garantiza que un backtest sea
  reproducible bit a bit (alineado con ENG-009).

---

## 13. Garantías y relaciones

- **Determinismo / no look-ahead / velas cerradas** (ENG-002 §0.2); dos ejecuciones
  ⇒ mismo `MarketContext`.
- **Explicabilidad 100 %:** todo gate PASS/FAIL tiene `gate_reason` y desglose de
  factores (ENG-010).
- **Cobertura:** cada barra del TF de contexto produce exactamente un
  `MarketContext` (registrado en ENG-009).
- **Separación DNA/reglas (⛔):** Market DNA nunca altera la lógica de la estrategia;
  solo umbrales/tolerancias; sin auto-mutación en producción.

**Relaciones:** consume ENG-000 (datos), Market DNA (config); **precede** y
habilita ENG-002/ENG-001; modula ENG-005; provee features a ENG-003; se valida y
calibra en ENG-004; se registra/explica en ENG-009/ENG-010.

> **Versión 0.1 — Borrador (🟨).** Motor obligatorio, primer gate del pipeline.
> Aprobación (🟩) requiere revisión de Quant Lead, CTO, ML, Risk y QA; prerrequisito
> del gate D4. Cambios de umbrales/pesos/DNA vía RFC + calibración en ENG-004.
