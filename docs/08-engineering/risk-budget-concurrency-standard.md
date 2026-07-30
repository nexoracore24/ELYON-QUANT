<!--
title: ELYON QUANT — Risk Budget Concurrency Standard (ADR-CORE-RISK-CONCURRENCY)
id: ENGX-RISKCONC-001 (autoridad oficial de concurrencia del presupuesto de riesgo; cierra P0-C)
owner: Risk Lead
reviewers: [CTO/Principal Architect, Execution Lead, Quant Lead, Platform/Data Lead, QA Lead]
status: frozen-candidate
version: 1.0
last_updated: 2026-07-29
closes: Core Architecture Review v1.0 — P0-C (concurrencia del Risk Engine)
formal_adr: docs/adr/0007-risk-budget-concurrency.md
-->

# ELYON QUANT — RISK BUDGET CONCURRENCY STANDARD

> **Autoridad oficial sobre la concurrencia del presupuesto de riesgo de toda la
> plataforma.** Objetivo único e innegociable: **dos o más operaciones concurrentes
> NUNCA pueden consumir el mismo presupuesto de riesgo.** Cierra el bloqueador
> **P0-C** de la [Core Architecture Review v1.0](../architecture/core-architecture-review-v1.0.md)
> y es la referencia definitiva del tema en ELYON QUANT.

El Risk Engine (ENG-005) declara límites por operación/día/semana/mes/símbolo/
sesión/estrategia/correlación/exposición, pero su `RiskState` es **estado global
compartido por cuenta**. Sin control de concurrencia, dos pre-trades simultáneos
pueden **ambos** leer "hay presupuesto", **ambos** aprobar y **juntos** violar un
límite (clásica carrera *check-then-act*). Este documento lo elimina por diseño.

---

## 1. Filosofía

### 1.1 El riesgo es un recurso compartido
El presupuesto de riesgo de una cuenta (cuánto puede perder hoy/semana/mes, cuánta
exposición abierta, cuántas operaciones) es un **recurso finito y compartido** por
todas las señales, estrategias y símbolos de esa cuenta. Como todo recurso
compartido, su consumo concurrente **debe coordinarse**.

### 1.2 El riesgo es un presupuesto reservable (no solo consultable)
El error de diseño clásico es **consultar** disponibilidad y luego **consumir** en
dos pasos (check-then-act). En su lugar, el riesgo se trata como un **presupuesto
reservable**: pedir presupuesto **reserva** atómicamente esa porción, de modo que
un peticionario concurrente ya la ve **descontada**. La reserva **es** el acto de
consumo provisional. "Comprobar y reservar" es **una sola operación atómica**.

### 1.3 Principios de consistencia
- **Fuerte donde importa:** las mutaciones de presupuesto de **una cuenta** son
  **serializables** (linealizables). El acto de reservar/comprometer/liberar es
  transaccional y atómico.
- **Aislamiento por cuenta:** la unidad de contención es la **cuenta** (`tenantId +
  accountId`). Cuentas distintas **no** se coordinan → escala horizontal.
- **Eventual solo en lectura:** las proyecciones de consulta (dashboards) pueden ser
  eventuales; **el camino de reserva NUNCA usa una proyección eventual** — usa el
  estado fuerte del agregado.

### 1.4 Invariantes globales (⛔)
- **I1 — No double-spend:** en ningún instante `Reserved + Committed ≤ Total` para
  **cada** dimensión de límite. La suma de reservas + comprometido jamás excede el
  presupuesto.
- **I2 — All-or-nothing multidimensional:** una reserva se concede **solo si cabe en
  TODAS** las dimensiones aplicables a la vez; si falla una, no reserva ninguna.
- **I3 — Atomicidad:** reservar/comprometer/liberar es atómico y **no duplicable**
  (idempotente por clave).
- **I4 — Sin fugas:** todo presupuesto reservado se **libera o compromete** en tiempo
  finito (expiración obligatoria); no hay reservas que "se pierdan" reteniendo
  presupuesto para siempre.
- **I5 — Reproducible:** el orden de aplicación de reservas se **registra** (event
  sourcing) → el estado es reconstruible y el replay es fiel (encaja con EDCS y
  ENG-009).

---

## 2. Modelo de presupuesto

El presupuesto se modela **por cuenta** y **por dimensión** (una cuenta tiene un
vector de presupuestos: pérdida diaria, semanal, mensual, riesgo por símbolo, por
sesión, por estrategia, por grupo de correlación, exposición total, nº de
operaciones…). Todas las magnitudes monetarias/de riesgo son **Decimal canónico**
(EDCS); las de conteo son enteras.

| Cantidad | Definición | Cálculo |
|----------|------------|---------|
| **Total Risk Budget** | Presupuesto máximo permitido por una dimensión de límite | Derivado del `RiskProfile` (p.ej. `max_daily_loss_pct × equityBaseDayStart`; `max_total_open_risk_pct × equity`; `max_trades_per_day`) |
| **Reserved Risk** | Suma de reservas **activas** (PENDING) aún no comprometidas ni liberadas | `Σ reservation.amount where state=PENDING` |
| **Committed Risk** | Riesgo **real** de posiciones vivas / órdenes aceptadas | `Σ committed.amount where position open` |
| **Available Risk** | Presupuesto libre para nuevas reservas | **`Total − Reserved − Committed`** (por dimensión) |
| **Released Risk** | Presupuesto devuelto a *Available* por cancelación/rechazo/no-fill | evento `ReservationReleased` (contador) |
| **Expired Reservations** | Reservas liberadas por vencer su TTL sin comprometerse | evento `ReservationExpired` (subconjunto de released) |

**Regla de admisión (⛔):** una petición de `requested[d]` en cada dimensión `d` se
**concede** ⇔ `∀d: Available[d] ≥ requested[d]` (comparación **exacta** sobre Decimal
cuantizado, EDCS §7). Si se concede, en la **misma transacción atómica**:
`Reserved[d] += requested[d]` para todo `d`. La condición y la mutación son
**indivisibles** (§4).

> **Elegancia del modelo:** como todas las dimensiones de una cuenta viven en **un
> único agregado** (`RiskBudgetAggregate`), reservar a través de N dimensiones es
> **una sola transición atómica** — **no** hay adquisición de múltiples cerrojos ni,
> por tanto, riesgo de *deadlock* multi-recurso.

---

## 3. Reserva de riesgo: ciclo de vida

### 3.1 Estados y transiciones
```
                         reserve (atómico, CAS)
        AVAILABLE ───────────────────────────────► RESERVED (PENDING)
        (presupuesto libre)                          │   │   │
                                                     │   │   │ commit (orden aceptada/fill)
                                                     │   │   ▼
                                                     │   │  COMMITTED ──(posición cerrada)──► (committed liberado → AVAILABLE)
                                                     │   │
                                     cancel/reject/  │   │
                                     no-fill         │   │ TTL vencido
                                          ▼          ▼   ▼
                                      RELEASED     EXPIRED
                                     (→ AVAILABLE) (→ AVAILABLE, con alerta)
```

### 3.2 Operaciones del ciclo
- **Creación (reserve).** El pre-trade del Risk Engine, tras evaluar todos los
  límites, ejecuta `ReserveRisk(intentId, dimensions, amounts)` **atómico**: si cabe
  en todas las dimensiones, crea una `Reservation{reservationId, intentId, state=PENDING,
  expiresAt, version}` y descuenta de *Available*. Devuelve `reservationId` dentro de
  la `RiskDecision (APPROVE)`.
- **Confirmación (commit).** Cuando Execution confirma que la orden fue **aceptada/
  fillada**, emite `CommitReservation(reservationId)`: `Reserved -= amount; Committed
  += actualAmount`. El *committed* refleja el riesgo real (ajustado a fill parcial).
- **Cancelación (release).** Si la orden se **rechaza/cancela/no se llena**, Execution
  emite `ReleaseReservation(reservationId, reason)`: `Reserved -= amount` → vuelve a
  *Available*.
- **Expiración (expire).** Si una reserva PENDING supera `expiresAt` sin commit ni
  release, un proceso determinista la **expira**: `ReservationExpired` → libera
  presupuesto. Es la **red de seguridad** contra reservas huérfanas (I4). `reservation_ttl`
  se configura **mayor** que el ciclo de vida máximo esperado de una orden.
- **Recuperación (recover).** Tras reinicio/incidencia, el estado se **reconstruye**
  del event stream y se **reconcilia** con Execution/OMS (§6).

### 3.3 Reglas del ciclo (⛔)
- Un `reservationId` transita **una sola vez** a un estado terminal (COMMITTED/
  RELEASED/EXPIRED). Reintentos son **idempotentes**.
- `commit` de una reserva ya `EXPIRED` → caso de **reconciliación** (§6): si Execution
  demuestra un fill real, se **re-compromete** (el fill del broker es autoridad); nunca
  se ignora un fill real por haber expirado la reserva.
- El *committed* de una posición se **libera** al cerrarse la posición (devuelve
  exposición al presupuesto).

---

## 4. Atomicidad: cómo una reserva nunca se duplica

La reserva es un **check-and-act atómico** sobre el `RiskBudgetAggregate` de la
cuenta. Se garantiza con la combinación de:

### 4.1 Serialización por agregado (single-writer lógico)
Todas las mutaciones de presupuesto de una cuenta se aplican **en serie** sobre su
agregado. Cuentas distintas corren en paralelo. La serialización se materializa con
optimistic concurrency (no cerrojos pesimistas de larga duración).

### 4.2 Optimistic Concurrency + Compare-and-Swap (CAS)
- El agregado lleva un **`version`** monótono. Toda mutación lee `(state, version=V)`,
  calcula la transición y la **aplica condicionalmente**: "escribe **solo si** la
  versión sigue siendo `V`" (CAS). Materialización:
  - **Event store / SQL:** `append event WHERE aggregate.version = V` (o
    `UPDATE ... SET version=V+1 WHERE version=V`); 0 filas afectadas ⇒ conflicto.
  - **Redis:** `WATCH key; MULTI/EXEC` o **script Lua** atómico (check + decrement en
    una operación indivisible).
- **En conflicto** (otra reserva ganó y cambió la versión): **reintento** →
  re-leer `(state, version)`, re-evaluar admisión (el *Available* ya está reducido) y
  re-aplicar. El reintento es **acotado** (`max_cas_retries`) con backoff determinista
  (`Clock` inyectado). Si el presupuesto ya no cabe → **denegación** (no se fuerza).

### 4.3 Idempotencia
- `ReserveRisk` lleva `idempotencyKey = intentId`. Si el mismo `intentId` reintenta
  (reintento de red, reproceso), el agregado **detecta** que ya existe una reserva para
  ese `intentId` y **devuelve la existente** (no crea una segunda). Igual para
  commit/release (idempotentes por `reservationId`).

### 4.4 Versionado
- El `version` del agregado (a) habilita el CAS y (b) permite **reconstruir** el estado
  desde eventos y detectar divergencias. Entra en los eventos emitidos.

### 4.5 Por qué esto elimina el double-spend
Dos reservas concurrentes leen `version=V`. Solo **una** gana el CAS (pasa a `V+1`);
la otra **falla** y reintenta sobre `V+1`, donde ve el *Available* **ya reducido** por
la primera. Nunca ambas descuentan sobre el mismo *Available*. **I1 garantizada.**

---

## 5. Escenarios concurrentes

| Escenario | Comportamiento garantizado |
|-----------|----------------------------|
| **Dos señales simultáneas (misma cuenta)** | Ambas intentan `reserve`; el CAS serializa → una gana, la otra reintenta sobre el *Available* reducido. Si cabe, ambas operan; si no, la segunda es **denegada** (`ReservationDenied`). **Nunca** double-spend. |
| **Múltiples estrategias (misma cuenta)** | Compiten por el presupuesto de cuenta **y** por su sub-presupuesto de estrategia; la reserva es atómica **a través de ambas dimensiones** (all-or-nothing, I2). |
| **Múltiples símbolos (misma cuenta)** | Comparten presupuesto de cuenta/diario/exposición, pero cada símbolo tiene su dimensión propia; una sola transición atómica resuelve todas. |
| **Múltiples cuentas** | Agregados **independientes** → **sin contención**; escalan en paralelo (partición por `accountId`). |
| **Múltiples procesos** | El CAS se realiza sobre el **store compartido** (event store/SQL/Redis), no sobre cerrojos en memoria de proceso → seguro **entre procesos y réplicas**. |
| **Reinicio durante una reserva** | La reserva está **persistida** (event-sourced). Al reiniciar, el estado se reconstruye; la reserva sigue `PENDING` si no venció su TTL → se honra; si venció → se libera. Reconciliación con OMS (§6). |
| **Pérdida de red** | La reserva permanece `PENDING`; **no** se libera "por si acaso" ni se duplica; se resuelve cuando Execution confirma/rechaza, o por **expiración** (red de seguridad) reconciliada con el OMS. |
| **Timeout del broker** | Igual que pérdida de red: la reserva se mantiene hasta que Execution resuelva el desenlace real (fill/reject) o expire; el `reservation_ttl` **excede** el ciclo de vida máximo de una orden para no liberar presupuesto de una orden que aún puede llenarse. |

**Regla anti-fantasma (⛔):** una reserva **nunca** se libera solo porque "parezca"
perdida; se libera por **evento explícito** (`commit`/`release`/`expire`) y toda
expiración se **reconcilia** contra la verdad del OMS/broker antes de darse por buena.

---

## 6. Recovery

### 6.1 Reconstrucción tras reinicio
El `RiskBudgetAggregate` se **reconstruye reproduciendo su event stream** (event
sourcing): `RiskReserved/Committed/Released/Expired/BudgetPeriodReset` → estado
idéntico al previo (encaja con EDCS y ENG-009). Los **snapshots** periódicos del
agregado aceleran la reconstrucción (snapshot + eventos posteriores).

### 6.2 Reservas huérfanas
Una reserva `PENDING` sin orden viva correspondiente en el OMS (ENG-006) es
**huérfana**. La reconciliación (periódica + al arranque) las detecta cruzando
`ReservationId ↔ Trade/Order`:
- Sin orden en el OMS **y** TTL vencido → `ReservationExpired` (libera).
- Con orden **fillada** en el OMS → `CommitReservation` (el fill real manda).
- Con orden viva dentro de TTL → se mantiene.

### 6.3 Reservas expiradas
Un proceso de expiración **determinista** (con `Clock` inyectado; en backtest guiado
por event-time) barre las `PENDING` con `now ≥ expiresAt` y emite `ReservationExpired`.
Es idempotente (una reserva expira una vez). Toda expiración se **alerta** (una tasa
alta de expiraciones señala un problema aguas abajo).

### 6.4 Reconciliación
Cruce periódico de **tres fuentes**: `RiskBudget` (reserved/committed) ⇄ `OMS`
(órdenes/posiciones vivas) ⇄ `Portfolio` (posiciones/PnL). Divergencias:
- `Committed` del riesgo debe = riesgo de posiciones vivas del OMS. Si no → ajustar
  (el **OMS/broker es autoridad** del estado real; el riesgo converge emitiendo
  eventos de corrección, nunca sobrescribiendo).
- `Reserved` debe corresponder a órdenes en vuelo (no filladas ni rechazadas).

---

## 7. Integración

### 7.1 Con el Trading Engine (ENG-001)
El Trading Engine no reserva directamente: emite el `TradeIntent`; el **pre-trade del
Risk Engine** hace la reserva atómica como parte de su evaluación (ENG-005 §2). El
`reservationId` viaja en la `RiskDecision(APPROVE)` de vuelta.

### 7.2 Con el Risk Engine (ENG-005)
**Aquí se cierra P0-C.** El flujo pre-trade de ENG-005 §2 se **extiende**: tras evaluar
los límites, en vez de solo "aprobar", ejecuta `ReserveRisk` atómico sobre el
`RiskBudgetAggregate`. El agregado **es** el `RiskState` de cuenta, ahora versionado y
protegido por CAS. La decisión sigue siendo determinista dado el **orden serializado**
de reservas (que se registra).

### 7.3 Con el Execution Engine (ENG-006)
El `reservationId` fluye con la orden. El OMS:
- Al **aceptar/fillar** → `CommitReservation(reservationId, actualAmount)`.
- Al **rechazar/cancelar/no-fill** → `ReleaseReservation(reservationId, reason)`.
- Al **cerrar posición** → libera el *committed*.
Encaja con el ciclo de vida del OMS (CREATED→…→FILLED→…→CLOSED) y su idempotencia.

### 7.4 Con Decision Replay (ENG-009)
Todos los eventos de reserva (incluidas **denegaciones**) se registran → el **orden
serializado** de reservas es parte del log y el replay es **fiel**. Una denegación por
concurrencia es una decisión explicable (por qué no se operó).

### 7.5 Con Market Context (ENG-011)
El contexto **modula el importe solicitado** (riesgo dinámico, ENG-005 §19) pero **no**
cambia el modelo de concurrencia; es un input al `requested`. Un contexto crítico puede
además disparar kill-switch (congela nuevas reservas).

### 7.6 Con Portfolio (ENG-007, futuro)
Consume `Committed`/`Released` para reporte de exposición y es **tercera fuente** de la
reconciliación (§6.4). No participa en el camino de reserva.

---

## 8. Event Sourcing (eventos necesarios)

Sobre el agregado `RiskBudgetAggregate{tenantId, accountId, version}` (todos con
Event Envelope, provenance y `version`):

| Evento | Efecto | Emisor |
|--------|--------|--------|
| `RiskBudgetInitialized` | Crea el presupuesto por dimensión (desde `RiskProfile`) | Risk |
| `BudgetPeriodReset` | Reinicia día/semana/mes en la frontera (`boundary_timezone`) | Risk |
| `BudgetAdjusted` | Ajuste por depósito/retirada/cambio de equity | Risk |
| `RiskReserved` | `Reserved += amount` (multi-dimensión); crea `Reservation{PENDING, expiresAt}` | Risk (pre-trade) |
| `ReservationDenied` | No hubo presupuesto (registro de la denegación) | Risk |
| `ReservationCommitted` | `Reserved -= amount; Committed += actual` | Execution |
| `ReservationReleased` | `Reserved -= amount` (cancel/reject/no-fill) | Execution |
| `ReservationExpired` | `Reserved -= amount` (TTL vencido) | Risk (sweeper) |
| `CommittedRiskReleased` | `Committed -= amount` (posición cerrada) | Execution/Portfolio |
| `BudgetReconciled` | Corrección tras reconciliación (converge al OMS) | Risk |

**Invariante de proyección (⛔):** el estado (`Available/Reserved/Committed`) es la
proyección determinista de estos eventos; reconstruible y reproducible bit a bit.

---

## 9. CQRS

- **Commands (write, sobre el agregado con CAS):** `ReserveRisk`, `CommitReservation`,
  `ReleaseReservation`, `ExpireReservation`, `ResetPeriod`, `AdjustBudget`,
  `ReconcileBudget`.
- **Queries (read models, eventualmente consistentes):** `AvailableBudget` (por cuenta/
  dimensión), `ActiveReservations`, `CommittedRisk`, `BudgetUtilization`,
  `ReservationHistory`.
- **Regla dura (⛔):** el comando `ReserveRisk` usa el **write-side fuerte** (el
  agregado versionado), **nunca** una proyección de lectura (que es eventual) — leer de
  una proyección para decidir reintroduciría la carrera check-then-act. Las queries son
  solo para dashboards/reportes.

---

## 10. Observabilidad

Métricas obligatorias (SLIs, alimentan OPS-006 y el Dashboard):
- **`reservation_latency`** — tiempo de `ReserveRisk` (incl. reintentos CAS); p50/p95/p99.
- **`reservation_conflicts`** — nº de fallos de CAS / reintentos (indicador de contención).
- **`expired_reservations`** — reservas liberadas por TTL (una tasa alta = problema).
- **`concurrent_denials`** — reservas denegadas por falta de presupuesto bajo concurrencia.
- **`duplicate_preventions`** — reservas idempotentes evitadas (mismo `intentId`).
- Complementarias: `budget_utilization` (por dimensión), `reservation_hold_time`,
  `orphan_reservations_reconciled`, `commit_after_expiry` (reconciliaciones).
- **Alertas:** contención sostenida (`reservation_conflicts` alto), fuga
  (`expired_reservations` alto), divergencia (`orphan_reservations_reconciled` alto).

---

## 11. Contratos afectados (actualización)

La concurrencia añade campos y eventos **retrocompatibles** (MINOR, §1 de Core
Contracts v1.0 — no rompe consumidores):

- **`risk.v1` → `1.1.0`:**
  - `RiskDecision`: **+`reservationId` (opt)**, **+`reservationExpiresAt` (opt)**.
  - Nuevos eventos: `RiskReserved`, `ReservationCommitted`, `ReservationReleased`,
    `ReservationExpired`, `ReservationDenied`, `BudgetReconciled` (todos con Event
    Envelope).
- **`execution.v1` → `1.1.0`:** los comandos/eventos de orden **transportan**
  `reservationId` (opt) para poder commit/release en el desenlace.
- **`decision-record.v1`:** una `ReservationDenied` es un `DecisionRecord`
  (`decisionType=RISK`, `primaryReason="reservation_denied_concurrency"`).

> Estas adiciones se aplican bajo la política de versionado de Core Contracts (campos
> opcionales + eventos nuevos = **MINOR**, compatible `BACKWARD`). No requieren MAJOR.

---

## 12. ADR — Decisión arquitectónica

### ADR-0007 · Reserva de presupuesto de riesgo con agregado por cuenta + CAS
- **Contexto.** El `RiskState` de cuenta es estado global compartido; el pre-trade
  concurrente puede aprobar dos órdenes que juntas violan un límite (P0-C, carrera
  check-then-act). Se necesita coordinación **sin** sacrificar el determinismo ni la
  escalabilidad.
- **Decisión.** Modelar el riesgo como **presupuesto reservable** con **reserva en dos
  fases** (`reserve → commit/release`) sobre un **agregado por cuenta**
  (`RiskBudgetAggregate`, `tenantId+accountId`), **event-sourced** y protegido por
  **optimistic concurrency + compare-and-swap** sobre un `version`. Reserva atómica
  **all-or-nothing multidimensional**; **idempotente** por `intentId`; con **TTL** y
  expiración que impide fugas; reconciliada con el OMS (autoridad del estado real).
- **Alternativas descartadas.**
  1. **Sin reserva (solo consultar y aprobar)** — inseguro (el double-spend es el bug
     que resolvemos).
  2. **Cerrojo global de riesgo (un lock para todo)** — cuello de botella; serializa
     cuentas independientes; mata la escalabilidad.
  3. **Cerrojo pesimista de BD por fila** — contención y riesgo de *deadlock*
     (múltiples dimensiones); peor rendimiento que CAS optimista.
  4. **Gestor de locks distribuido (p.ej. lock manager externo)** — complejidad y nuevo
     SPOF; innecesario dado que el agregado por cuenta acota la contención.
  5. **Consistencia eventual del presupuesto** — permite ventanas de double-spend;
     inaceptable para riesgo.
- **Consecuencias.**
  - **Positivas:** elimina el double-spend por diseño (I1); contención acotada a la
    cuenta → escala por partición; determinismo preservado (orden serializado
    registrado); reproducible y auditable (event sourcing); sin deadlock (un solo
    agregado).
  - **Negativas / trade-offs:** bajo alta contención de **una** cuenta, reintentos CAS
    añaden latencia (mitigado: la operativa de una cuenta es casi secuencial); requiere
    disciplina de TTL/expiración y reconciliación; añade eventos y estado.
- **Seguimiento:** vigilar `reservation_conflicts`/`reservation_latency`; si una cuenta
  *enterprise* sufre contención alta, particionar su presupuesto por sub-cuenta/estrategia.

*(Se promueve a `docs/adr/0007-risk-budget-concurrency.md` como ADR formal.)*

---

## 13. Casos de prueba

### 13.1 Normales
- **T1 Reserva→commit→cierre:** `reserve(0.5%)` → APPROVE(reservationId) → fill →
  `commit` → `Committed=0.5%` → cierre → `Committed=0%`. Presupuesto coherente.
- **T2 Reserva→release:** orden rechazada → `release` → `Available` restaurado.
- **T3 Multi-dimensión:** reserva que consume cuenta+símbolo+estrategia a la vez;
  liberar restaura todas.

### 13.2 Concurrentes / extremos
- **T4 Dos reservas simultáneas, cabe una:** solo una gana el CAS; la otra reintenta y
  es **denegada** (`ReservationDenied`); `Reserved` refleja **una** sola. **No** double-spend.
- **T5 Dos reservas, caben ambas:** ambas commit; `Σ ≤ Total`.
- **T6 Contención CAS:** N peticiones concurrentes → `reservation_conflicts` > 0, pero
  el resultado final respeta `Reserved+Committed ≤ Total` (property-based).
- **T7 Idempotencia:** el mismo `intentId` reintentado no crea una segunda reserva
  (`duplicate_preventions++`).
- **T8 Expiración:** reserva PENDING supera TTL → `ReservationExpired` → liberada;
  `expired_reservations++`.
- **T9 Reinicio durante reserva:** matar el proceso tras `RiskReserved` y antes de
  commit → reconstruir del event stream → reserva sigue PENDING (o expira) → estado correcto.
- **T10 Reserva huérfana:** PENDING sin orden en OMS + TTL vencido → reconciliación la
  libera. Con orden fillada → la **commitea**.
- **T11 Commit tras expiración:** reserva expirada pero el broker sí llenó → la
  reconciliación **re-compromete** (el fill real manda) (`commit_after_expiry++`).
- **T12 Multi-proceso:** dos réplicas compiten por el mismo agregado vía CAS del store
  → sin double-spend (no basta un lock en memoria).
- **T13 Multi-cuenta:** reservas en cuentas distintas **no** se bloquean entre sí
  (sin contención cruzada).
- **T14 Determinismo/replay:** reproducir el event stream de reservas → mismo
  `Available/Reserved/Committed` bit a bit.
- **T15 Property — invariante I1:** bajo cualquier entrelazado de reserve/commit/
  release/expire, **nunca** `Reserved+Committed > Total` para ninguna dimensión.

---

## 14. Checklist obligatorio (validar cualquier implementación futura)

> Ninguna implementación de riesgo se aprueba (ni el bible ENG-005 promociona a 🟢)
> sin marcar **todos** los ítems. QA verifica en el gate; se enlaza a T1–T15 (BLD-003).

**Modelo y atomicidad**
- [ ] `Available = Total − Reserved − Committed` calculado en **Decimal** (EDCS), por
      dimensión.
- [ ] Reserva = **check-and-act atómico**; admisión **all-or-nothing** en TODAS las
      dimensiones (I2).
- [ ] Serialización por **agregado de cuenta** (`tenantId+accountId`); cuentas
      independientes sin contención.
- [ ] **Optimistic concurrency + CAS** sobre `version`; reintento acotado con backoff
      determinista.
- [ ] **Idempotencia** por `intentId` (reserva) y `reservationId` (commit/release/expire).
- [ ] Comparaciones **exactas** sobre Decimal cuantizado (sin epsilon, EDCS §7).

**Ciclo de vida y fugas**
- [ ] Estados `AVAILABLE→RESERVED→COMMITTED→RELEASED→EXPIRED` implementados con
      transiciones únicas a estado terminal.
- [ ] **TTL** de reserva **> ciclo de vida máximo** de la orden; expiración determinista
      (`Clock` inyectado / event-time en backtest).
- [ ] **Sin liberación fantasma**: solo por evento explícito; expiración **reconciliada**
      con el OMS.
- [ ] `committed` liberado al cerrar la posición.

**Persistencia, recovery, ES/CQRS**
- [ ] **Event-sourced**: estado = proyección de eventos; snapshots opcionales.
- [ ] Reconstrucción tras reinicio idéntica (T9, T14).
- [ ] Reconciliación periódica riesgo⇄OMS⇄portfolio; **OMS/broker autoridad** del estado real.
- [ ] `ReserveRisk` usa el **write-side fuerte**, nunca una proyección eventual.

**Observabilidad y contratos**
- [ ] Métricas `reservation_latency`, `reservation_conflicts`, `expired_reservations`,
      `concurrent_denials`, `duplicate_preventions` expuestas + alertas.
- [ ] Contratos actualizados: `risk.v1`≥1.1.0 (reservationId + eventos),
      `execution.v1`≥1.1.0 (transporta reservationId); cambios **MINOR** compatibles.
- [ ] Toda reserva/commit/release/denegación en Event Envelope con provenance
      (dataVersion/configHash) → replayable (ENG-009).

**Verificación**
- [ ] Batería T1–T15 verde, incluido **property-based** de I1 y test de **carrera**
      multi-proceso.

---

> **Versión 1.0 — `frozen-candidate` (🟡).** Referencia definitiva sobre la concurrencia
> del presupuesto de riesgo en ELYON QUANT. Cierra **P0-C**. De cumplimiento
> **obligatorio**: ninguna implementación de riesgo se aprueba sin el checklist §14.
> Promoción a `frozen` (🟢) con la batería T1–T15 verde. ADR formal: ADR-0007. Cambios
> vía RFC/ADR.
