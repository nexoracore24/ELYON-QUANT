# ADR-0008: Frontera de gestión de posición (Trading↔Execution) y separación Context/Entry Score

- **Estado:** Accepted
- **Fecha:** 2026-07-29
- **Decisores:** CTO/Principal Architect, Quant Lead, Execution Lead, Risk Lead
- **Relacionado:** [Core Architecture Review v1.0 §P0-E](../architecture/core-architecture-review-v1.0.md),
  ENG-001 (Trading §26–§33), ENG-006 (Execution/OMS), ENG-011 (Market Context),
  ENG-005 (Risk §19), [Core Contracts v1.0](../06-api/core-contracts-v1.0.md)
- **Cierra:** bloqueador **P0-E** de la Architecture Review (solapamiento de
  responsabilidades: gestión de posición Trading vs Execution; doble conteo
  Context Score vs Entry Score).

## Contexto

La Architecture Review detectó dos solapamientos de responsabilidad que, sin
resolver, impiden congelar las fronteras del núcleo (`v1.0` GA):

1. **Gestión de posición ambigua.** El Trading Engine describe la gestión de salida
   (SL/TP/BE/trailing/parciales/cierres, ENG-001 §27–§33), pero el Execution Engine
   (OMS, ENG-006) también "posee" la posición y su ciclo de vida. ¿Quién es la fuente
   de verdad y quién ejecuta?
2. **Doble conteo de contexto.** El Entry Score (ENG-001 §26) puntúa factores de
   **contexto** (killzone, régimen ATR/spread, bias) que el Market Context Engine
   (ENG-011) **ya evalúa** en su gate. El mismo contexto se cuenta dos veces.

Ambos son ambigüedades de **responsabilidad**, no de mecanismo. Se resuelven fijando
fronteras canónicas.

## Decisión

### Parte A — Gestión de posición: *Trading propone política, Execution posee estado y ejecuta*

Se adopta la separación **policy vs. state/mechanics**:

| Concern | **Trading (ENG-001)** — decide *política* | **Execution/OMS (ENG-006)** — *ejecuta + estado* |
|---------|-------------------------------------------|--------------------------------------------------|
| Setup / *timing* de entrada | Sí (modelo de entrada) | Ejecuta la orden |
| Niveles SL/TP | **Propone** (desde anclas SMC) | **Coloca y posee** las órdenes protectoras |
| BE / trailing / parciales | **Propone** en `managementPlan` (TradeIntent) | **Ejecuta** y **posee el estado** |
| Estado de la posición (`net_qty`, `avg_price`, SL/TP vivos) | — | **Fuente de verdad única** |
| Cierre (SL/TP alcanzado, *smart close* por POI opuesto) | Emite **señal**/intent de cierre | **Ejecuta** el cierre |
| Kill-switch / protección | Risk ordena | **Ejecuta** (SAFE_HALT/flatten/BE) |

**Reglas (⛔):**
- El Trading Engine **nunca** muta el estado de una posición directamente; **emite
  intents** (entrada + `managementPlan`, y señales de gestión posteriores como
  `AdjustStop`/`SmartClose`).
- El **Execution Engine es la única fuente de verdad** del estado de la posición y el
  **único ejecutor** de SL/TP/BE/trailing/parciales/cierres (event-sourced, ENG-006).
- Las secciones **ENG-001 §28–§33 son especificación de *política*** (qué gestión se
  desea), **no** de ejecución; la mecánica y el estado viven en ENG-006 §6.
- Consecuencia: la posición **nunca queda sin SL** porque su colocación/mantenimiento
  es responsabilidad del OMS (saga OpenPosition, ENG-006 §9), no del Trading Engine.

### Parte B — Separación Context Score / Entry Score (fin del doble conteo)

Se fija que **el contexto se evalúa una sola vez**, en el Market Context Engine:

- El **Context Score (ENG-011)** es un **gate** (habilita/bloquea el escaneo) **y** un
  **modulador de riesgo/tamaño** (vía riesgo dinámico, ENG-005 §19). **No** es un
  factor del Entry Score.
- El **Entry Score (ENG-001 §26)** puntúa **solo la confluencia del setup** (estructura,
  sweep, POI, imbalance, premium/discount, OTE/Fibonacci, volumen, liquidez objetivo).
- Los factores hoy en el Entry Score que son **contexto puro** —**killzone/sesión** y
  **régimen ATR/spread**— **se eliminan** del Entry Score (ya los cubre el gate del MCE
  y el veto de spread/vol). El factor **HTF bias alignment** se **mantiene** pero se
  **consume del `MarketContext`** (no se re-detecta): mide si el setup está alineado con
  el bias que ya calculó el MCE.
- **Renormalización propuesta** del Entry Score (los 14 puntos liberados se redistribuyen
  proporcionalmente; **valores a fijar en calibración, ENG-004**):

  | Factor | Antes | **Ahora** |
  |--------|:----:|:--------:|
  | HTF bias alignment (del MarketContext) | 15 | **17** |
  | Estructura LTF (CHoCH/BOS) | 15 | **17** |
  | Liquidity sweep | 12 | **14** |
  | Calidad del POI | 12 | **14** |
  | Imbalance (FVG/IFVG) | 10 | **12** |
  | Premium/Discount | 8 | **9** |
  | OTE / Fibonacci | 6 | **7** |
  | Volumen | 4 | **5** |
  | Liquidez objetivo | 4 | **5** |
  | ~~Killzone/sesión~~ → **gate MCE** | 8 | **0** |
  | ~~Régimen ATR + spread~~ → **veto/modulador MCE/Risk** | 6 | **0** |
  | **Total** | 100 | **100** |

**Regla (⛔):** ningún factor de contexto (killzone, régimen de volatilidad, spread,
noticias) se puntúa dentro del Entry Score; el contexto **habilita** (gate) y **modula
el riesgo** (tamaño), no suma confluencia de setup.

## Opciones consideradas

1. **Dejar ambas responsabilidades duplicadas** — statu quo; produce fuente de verdad
   ambigua (bugs de posición) y sesgo por doble conteo del contexto. Rechazada.
2. **Que el Trading Engine posea el estado de la posición** — rompe el event sourcing
   y la reconciliación del OMS (que es autoridad frente al broker). Rechazada.
3. **Mantener killzone/ATR en el Entry Score y quitarlos del gate** — invierte el
   diseño: el contexto dejaría de ser un *gate* barato previo. Rechazada.
4. **(Elegida)** Trading = política; Execution = estado/ejecución. Contexto = gate +
   modulador; Entry Score = solo confluencia de setup.

## Consecuencias

- **Positivas:** fuente de verdad única de la posición (OMS); fin del doble conteo →
  score sin sesgo de contexto; fronteras congelables; menor acoplamiento del Trading
  Engine (baja su carga de "god coordinator", RF1/RF2 de la review).
- **Negativas / trade-offs:** hay que **recalibrar** los pesos del Entry Score en
  ENG-004 (los valores de la tabla son propuestos, no finales); requiere reconciliar la
  redacción de ENG-001 §26/§28–§33 (se marca como *política*, no ejecución).
- **Contratos:** `trade-intent.v1` (C4) ya lleva `managementPlan` (política) →
  coherente; el estado/eventos de posición viven en `execution.v1` (C6). Sin cambios
  incompatibles (las señales de gestión posteriores, p.ej. `SmartClose`, se añaden como
  eventos/campos **MINOR** en C4/C6 si hacen falta).

## Checklist de conformidad (⛔)

- [ ] El Trading Engine no muta estado de posición; solo emite intents/señales.
- [ ] El OMS es la única fuente de verdad del estado de posición y el único ejecutor de
      SL/TP/BE/trailing/parciales/cierres.
- [ ] La posición nunca queda sin SL (responsabilidad del OMS).
- [ ] El Entry Score **no** incluye factores de contexto (killzone, ATR/spread, noticias).
- [ ] El HTF bias del Entry Score se **consume** del `MarketContext` (no re-detección).
- [ ] Pesos del Entry Score **renormalizados** y marcados para calibración en ENG-004.
- [ ] ENG-001 §28–§33 etiquetadas como *especificación de política* (no ejecución).
