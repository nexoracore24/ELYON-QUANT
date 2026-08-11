<!--
title: ELYON QUANT — Core Contract Stubs v1.0 (ENG-003/004/007/008)
id: API-CORE-002 (stubs de contrato de motores no especificados; cierra P0-F)
owner: CTO/Principal Architect
reviewers: [ML Lead, Quant Lead, Platform/Data Lead, Execution Lead, Risk Lead, QA Lead]
status: stub-frozen-candidate
version: 1.0
last_updated: 2026-07-29
closes: Core Architecture Review v1.0 — P0-F / W6 (dependencias colgantes)
extends: core-contracts-v1.0.md (C1–C9)
-->

# ELYON QUANT — CORE CONTRACT STUBS v1.0

> **Cierra P0-F.** Los motores **AI (ENG-003)**, **Backtesting (ENG-004)**, **Portfolio
> (ENG-007)** y **Broker Connectivity (ENG-008)** aún no tienen *bible* completa, pero
> el núcleo (ENG-000/001/002/005/006/009/010/011) **ya los referencia**. Para poder
> **congelar las fronteras** sin esperar sus specs completas, aquí se definen **stubs de
> contrato**: **solo la interfaz que el núcleo toca**, congelada lo suficiente para
> construir contra ella. Los detalles internos de cada motor se especificarán después
> **sin romper** estos stubs (política de versionado de Core Contracts §1).

Contratos añadidos (extienden C1–C9): **C10 `ai.v1`**, **C11 `backtest.v1`**,
**C12 `portfolio.v1`**, **C13 `broker-connectivity.v1`**. Todos heredan el Event
Envelope, el vocabulario de tipos y las reglas de gobierno de
[Core Contracts v1.0](core-contracts-v1.0.md).

---

## C10 · `ai.v1` (stub) — AI Engine (ENG-003)

- **Propósito.** Aportar *factores* de ML al scoring **como confluencia explicable**,
  nunca como decisión opaca.
- **Owner.** ML Lead. **Producer.** ENG-003. **Consumers.** Trading (scoring, ENG-001 §26),
  Explainable AI (ENG-010), Replay.
- **Interfaz que el núcleo toca — `AiFactorSet`:**

| Campo | Tipo | Req | Restricciones |
|-------|------|:---:|---------------|
| decisionId / correlationId | uuid | ✔ | enlaza C7 |
| symbol / asOf | symbol / timestampNs | ✔ | provenance |
| modelId / modelVersion | string | ✔ | model card ref |
| factors | array<{factorId, value:decimal, attribution:decimal, inputsRef}> | ✔ | `value` **cuantizado** (EDCS §3.4/4.2) |
| confidence | decimal | ○ | ∈ [0,1] |
| explanationRef | uuid | ✔ | mapea a `explanation.v1` (C8) |

- **Invariantes que el núcleo exige (⛔):**
  - **Advisory + cuantizado:** todo `value` cruza la frontera de cuantización EDCS
    antes de entrar al scoring (float interno del modelo nunca sale sin cuantizar).
  - **Explicable, no override:** un factor ML entra como **un factor más** con su peso
    visible (ADR-0008/ENG-010); **jamás** anula el scoring ni el riesgo.
  - **Fallback:** si la explicación/fidelidad no es fiable, el factor **no puntúa** (peso 0).
- **Diferido (spec completa ENG-003):** features, entrenamiento, MLOps, model/data cards,
  guardarraíles internos. **No** cambian esta interfaz (solo la producen).
- **Evolución:** nuevos `factorId` = adición **MINOR** (tolerancia `UNKNOWN` en consumidores).

---

## C11 · `backtest.v1` (stub) — Backtesting Engine (ENG-004)

- **Propósito.** Ejecutar el **mismo núcleo** en modo backtest sobre datos versionados y
  producir resultados **reproducibles**; **calibrar** pesos/umbrales/DNA/matriz de
  correlación.
- **Owner.** Quant Lead. **Producer.** ENG-004. **Consumers.** Trading/MCE/Risk (calibración),
  Replay, Dashboard.
- **Interfaz que el núcleo toca:**

**`BacktestRunRequested`** → `{ runId, strategyVersion, datasetId, dateRange,
configHash, dnaHash, edcsVersion, seed }` (⛔ `datasetId`+`configHash` obligatorios →
reproducibilidad, P0-D/EDCS).

**`BacktestCompleted`** → `{ runId, metrics{...}, decisionRecordsRef, reproHash,
byRegime{...} }` — `reproHash` permite verificar determinismo (re-ejecutar da el mismo).

**`CalibrationProposed`** (opcional) → `{ target ∈ {entry_score_weights,
context_score_weights, risk_limits, correlation_matrix}, values, newConfigHash }` — la
**salida de calibración** que alimenta MCE/Trading/Risk (los pesos "propuestos" de
ADR-0008 se fijan aquí).

- **Invariantes (⛔):** usa el **mismo** OMS/Risk/MCE/SMC (modo backtest, ENG-006 §17);
  `Clock` y `fx_snapshot` inyectados; **sin look-ahead** (as-of estricto). Consume
  DecisionRecord (C7), execution (C6), risk (C5).
- **Diferido (spec completa ENG-004):** paralelismo, walk-forward, optimización,
  mutation/golden harness. No cambian esta interfaz.

---

## C12 · `portfolio.v1` (stub) — Portfolio & Analytics (ENG-007)

- **Propósito.** Mantener posiciones/PnL/exposición para reporte y ser **tercera fuente
  de reconciliación** del riesgo.
- **Owner.** Quant Lead. **Producer.** ENG-007. **Consumers.** Risk (reconciliación),
  Dashboard, Replay.
- **Interfaz que el núcleo toca (read models):**

| Objeto | Campos clave | Consumidor |
|--------|--------------|------------|
| `PositionView` | `positionId, symbol, netQty:decimal, avgPrice:price, state` | Dashboard, Risk (reconcile) |
| `PnLSnapshot` | `account, realized:money, unrealized:money, asOf, dataVersion` | Dashboard, Risk |
| `ExposureReport` | `account, byDimension{committed:decimal}, asOf` | Risk (§6.4 reconcile) |

- **Invariantes (⛔):**
  - **Valuación desde el MDE** (SSOT, C1) — nunca del broker directo.
  - **Consumidor puro** del ciclo de vida de Execution (C6): no participa en el camino de
    reserva de riesgo (Risk Concurrency §7.6); es fuente de **reconciliación**, no autoridad.
  - `committed` reportado debe conciliar con el `Committed` del Risk y las posiciones del OMS.
- **Diferido (spec completa ENG-007):** métricas avanzadas, atribución, tearsheets,
  reporting. No cambian esta interfaz.

---

## C13 · `broker-connectivity.v1` (stub) — Broker Connectivity (ENG-008)

- **Propósito.** El **puerto Broker Adapter (ACL)** que el OMS usa para hablar con
  brokers/exchanges (MT5/IB/Binance/paper/sim), sin filtrar sus peculiaridades al núcleo.
- **Owner.** Execution Lead. **Producer/Owner de adapters.** ENG-008. **Consumer.** Execution/OMS (ENG-006).
- **Interfaz que el núcleo toca — puerto `BrokerAdapter`:**

| Operación | Firma (conceptual) | Notas |
|-----------|--------------------|-------|
| `place` | `(order{coid, idempotencyKey, ...}) → ack{boid} | error` | idempotente por `coid` (exactly-once, ENG-006 §10) |
| `cancel` | `(coid|boid) → ack | error` | idempotente |
| `modify` | `(coid, {sl?, tp?}) → ack | error` | para SL/TP nativos |
| `query` | `(coid|boid) → orderState` | **query-before-resend** (anti-duplicado) |
| `reports` | `stream<ExecutionReport{brokerEventId, qty, price, ...}>` | dedup por `brokerEventId` |
| `health` | `stream<FeedHealth{state ∈ {CONNECTED,DEGRADED,DISCONNECTED,RECONNECTING}}>` | alimenta breakers/health (ENG-006 §11/§12) |

**`BrokerCapabilities`** (descriptor por broker/modo): `{ supportsNativeSL, supportsNativeTP,
supportsPartialFills, supportsModify, mode ∈ {LIVE,PAPER,BACKTEST}, ... }` → el OMS adapta
su gestión (p.ej. gestiona SL/TP internamente si el broker no los soporta).

- **Invariantes (⛔):**
  - **El adapter no decide negocio** — solo traduce y reporta (ENG-006 §4.2).
  - **Idempotencia** end-to-end (`coid`/`idempotencyKey`/`brokerEventId`).
  - **Multi-broker/multi-modo** con el **mismo** puerto → solo cambia la implementación.
- **Diferido (spec completa ENG-008):** mapeos por broker (FIX/API), autenticación,
  reconexión, rate limits, **manejo de credenciales** (seguridad, SEC-000). No cambian
  este puerto.

---

## Registro de stubs (Freeze Register — adenda a Core Contracts §11)

| Contrato | Versión | Owner | Estado | Nota |
|----------|---------|-------|--------|------|
| C10 `ai.v1` | 1.0.0 | ML | **stub-frozen-candidate** | interfaz core congelada; spec ENG-003 diferida |
| C11 `backtest.v1` | 1.0.0 | Quant | **stub-frozen-candidate** | run/result/calibración; spec ENG-004 diferida |
| C12 `portfolio.v1` | 1.0.0 | Quant | **stub-frozen-candidate** | read models + reconcile; spec ENG-007 diferida |
| C13 `broker-connectivity.v1` | 1.0.0 | Execution | **stub-frozen-candidate** | puerto BrokerAdapter; spec ENG-008 diferida |

**Definición de "stub-frozen":** la **interfaz que el núcleo consume/produce** está
congelada bajo la política de versionado (§1 de Core Contracts); el **interior** del
motor se especificará después y **solo puede añadir** (MINOR), nunca romper el stub. Esto
permite implementar el núcleo **ahora** contra fronteras estables (cierre de P0-F).

## Regla de promoción

Cuando cada motor publique su bible completa, su contrato pasa de
`stub-frozen-candidate` a `frozen` **manteniendo** este stub como su superficie mínima
hacia el núcleo (cualquier cambio de la superficie sigue la Breaking Changes Policy).

---

> **Versión 1.0 — `stub-frozen-candidate` (🟡).** Cierra el bloqueador **P0-F** de la
> Architecture Review: las cuatro dependencias colgantes tienen ahora su **interfaz
> core congelada**. Con esto, los **6 bloqueadores P0 están cerrados a nivel de diseño**
> → la arquitectura puede promoverse hacia `CORE ARCHITECTURE v1.0` GA una vez verdes las
> *contract/conformance suites* de implementación (gates de código, no de documento).
