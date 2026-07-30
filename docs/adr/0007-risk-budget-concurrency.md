# ADR-0007: Concurrencia del presupuesto de riesgo (reserva + agregado por cuenta + CAS)

- **Estado:** Accepted
- **Fecha:** 2026-07-29
- **Decisores:** Risk Lead, CTO/Principal Architect, Execution Lead, Platform/Data Lead
- **Relacionado:** [Risk Budget Concurrency Standard](../08-engineering/risk-budget-concurrency-standard.md),
  [Core Architecture Review v1.0 §P0-C](../architecture/core-architecture-review-v1.0.md),
  ENG-005 §2/§34, [Core Contracts v1.0](../06-api/core-contracts-v1.0.md), [EDCS](../08-engineering/deterministic-computing-standard.md)

## Contexto

El `RiskState` de una cuenta (pérdida diaria/semanal/mensual, exposición, riesgo por
símbolo/estrategia/correlación) es **estado global compartido**. El pre-trade
concurrente (dos señales, estrategias o símbolos a la vez) puede leer "hay
presupuesto", **ambos** aprobar y **juntos** violar un límite (carrera check-then-act).
La Architecture Review lo marcó como bloqueador **P0-C**. Necesitamos coordinación sin
sacrificar determinismo ni escalabilidad.

## Opciones consideradas

1. **Sin reserva (consultar y aprobar)** — inseguro: es exactamente el double-spend.
2. **Cerrojo global de riesgo** — cuello de botella; serializa cuentas independientes.
3. **Cerrojo pesimista de BD por fila** — contención y riesgo de deadlock multi-dimensión.
4. **Gestor de locks distribuido** — complejidad + nuevo SPOF, innecesario.
5. **Consistencia eventual del presupuesto** — ventanas de double-spend, inaceptable.
6. **Presupuesto reservable con agregado por cuenta + optimistic concurrency/CAS,
   event-sourced, reserva en dos fases (reserve→commit/release) con TTL.**

## Decisión

Adoptar la **opción 6**. El riesgo es un **presupuesto reservable**: la reserva es un
**check-and-act atómico all-or-nothing** sobre el `RiskBudgetAggregate` de la cuenta
(`tenantId+accountId`), protegido por **compare-and-swap** sobre un `version`
(optimistic concurrency), **idempotente** por `intentId`, con **TTL/expiración** que
impide fugas y **reconciliación** con el OMS (autoridad del estado real). Ciclo:
`AVAILABLE → RESERVED → COMMITTED → RELEASED/EXPIRED`. La especificación completa vive
en el Risk Budget Concurrency Standard; su cumplimiento es obligatorio (checklist §14).

## Consecuencias

- **Positivas:** elimina el double-spend por diseño (invariante `Reserved+Committed ≤
  Total`); contención acotada a la cuenta → escala por partición; **sin deadlock** (un
  solo agregado cubre todas las dimensiones); determinismo preservado (orden serializado
  registrado) y reproducible (event sourcing).
- **Negativas / trade-offs:** reintentos CAS añaden latencia bajo alta contención de
  una misma cuenta (mitigado: la operativa de una cuenta es casi secuencial); requiere
  disciplina de TTL/expiración y reconciliación; añade eventos y estado.
- **Contratos:** `risk.v1`→1.1.0 (`reservationId` + eventos de reserva) y
  `execution.v1`→1.1.0 (transporta `reservationId`); adiciones **MINOR** compatibles.
- **Seguimiento:** vigilar `reservation_conflicts`/`reservation_latency`; particionar el
  presupuesto por sub-cuenta/estrategia si una cuenta enterprise sufre contención alta.
