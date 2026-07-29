# ADR-0006: Computación determinista (decimal-first) — EDCS

- **Estado:** Accepted
- **Fecha:** 2026-07-29
- **Decisores:** CTO/Principal Architect, Quant Lead, Risk Lead, Platform/Data Lead
- **Relacionado:** [EDCS](../08-engineering/deterministic-computing-standard.md),
  [Core Architecture Review v1.0 §P0-A](../architecture/core-architecture-review-v1.0.md),
  [Core Contracts v1.0](../06-api/core-contracts-v1.0.md), ENG-005 §0.2

## Contexto

ELYON QUANT promete reproducibilidad **bit-a-bit** (backtest≡live, replay fiel,
no-repaint). Esa promesa se rompe si la matemática de detectores/derivados (ATR,
Efficiency Ratio, Fibonacci, volatilidad) produce resultados distintos según SO, CPU,
compilador, lenguaje o momento de ejecución. La Architecture Review lo marcó como
bloqueador **P0-A**. Necesitamos una política numérica única y obligatoria.

## Opciones consideradas

1. **IEEE754 binario "controlado"** (fijar redondeo, orden, desactivar FMA/fast-math/
   x87/SIMD, reimplementar transcendentales). — Frágil: depende de flags de compilador
   por lenguaje/plataforma; los transcendentales de `libm` siguen divergiendo; difícil
   de garantizar cross-language.
2. **Tolerancias por epsilon** sobre floats. — Reintroduce ambigüedad y dependencia de
   plataforma justo en las fronteras de decisión; no da bit-a-bit.
3. **Decimal-first con frontera de cuantización** (Decimal `decimal128` canónico para
   todo valor de decisión; float solo *advisory* y cuantizado; serialización canónica;
   transcendentales `libm` prohibidos). — Exacto, cross-language anclable a un estándar
   (IEEE 754-2008 decimal128), verificable por golden vectors.

## Decisión

Adoptar la **opción 3** como **ELYON Deterministic Computing Standard (EDCS)**:
Decimal canónico (`decimal128`, 34 díg., `ROUND_HALF_EVEN`) en el camino de decisión;
cuantización solo en la salida; orden de evaluación fijo; comparación exacta sobre
cuantizado (sin epsilon); prohibición de float binario/transcendentales `libm` en el
camino determinista; Canonical JSON (decimales como string) para serialización y
hashing; stable IDs derivados; comportamiento numérico versionado (`edcs_version`).
El cumplimiento es **obligatorio** y se verifica con el checklist §19 de la EDCS.

## Consecuencias

- **Positivas:** reproducibilidad real cross-platform/language/version; hashes y
  decisiones deterministas; base sólida para replay/backtesting y auditoría.
- **Negativas / trade-offs:** el Decimal es más lento que el float (aceptable fuera del
  gateway; el gateway usa fixed-point equivalente); disciplina de cuantización y de
  golden vectors en cada motor.
- **Seguimiento:** cambios de comportamiento numérico ⇒ `edcs_version++` + recompute de
  datasets; conformance suite cross-platform/language como *gate* de aprobación.
