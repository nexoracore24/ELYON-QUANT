# ELYON QUANT

**Plataforma profesional de trading algorítmico.** No es un bot de MT5: es un
ecosistema completo para diseñar, validar, ejecutar, monitorizar y monetizar
estrategias cuantitativas sobre múltiples brokers y exchanges, con estándares
de ingeniería de nivel Stripe / Palantir / Google / OpenAI.

> Estado del proyecto: **Núcleo en construcción.**
> La arquitectura está congelada (`v1.0-rc1`) y el primer código del motor ya
> corre: datos de mercado, detectores Smart Money, riesgo y scoring, con **157
> tests** verdes.
>
> ```bash
> make test    # suite completa
> make demo    # el pipeline decidiendo, y explicándose
> ```
>
> 📋 [Plan Maestro de Documentación](docs/00-governance/documentation-master-plan.md) ·
> 🧊 [Core Architecture Review v1.0](docs/architecture/core-architecture-review-v1.0.md) ·
> 🔒 [Core Contracts v1.0](docs/06-api/core-contracts-v1.0.md)

---

## Estado del código

| Módulo | Qué hace | Estado |
|--------|----------|--------|
| `shared_kernel/edcs` | Decimal canónico, cuantización, JSON canónico, hashing, IDs estables | ✅ |
| `market_data` | Ticks → velas (FORMING/CONFIRMED), watermark, ATR, Efficiency Ratio | ✅ |
| `smart_money` | Displacement, swings, estructura, BOS/CHoCH/MSS, liquidez, sweeps, FVG, order blocks, premium/discount, OTE, Fibonacci | ✅ |
| `risk` | Presupuesto con reserva atómica (CAS), position sizing, riesgo dinámico | ✅ |
| `trading` | Scoring Engine, DecisionRecord, explicabilidad | ✅ |
| `execution` | OMS: ciclo de vida de la orden, idempotencia, recovery | ⬜ especificado |
| `market_context` | Gate de contexto, Market DNA | ⬜ especificado |
| `backtesting` | Reproducibilidad y calibración | ⬜ especificado |

**Garantías demostradas por tests, no prometidas:** determinismo bit a bit ·
no-repaint (una vela confirmada nunca muta) · orden de llegada irrelevante ·
un veto vence a cualquier score · el riesgo tiene la última palabra ·
toda decisión se explica desde su propio registro.

---

## Qué es ELYON QUANT

Un ecosistema SaaS multi-tenant que cubre el ciclo de vida completo del trading
cuantitativo:

1. **Strategy Lab** — autoría, versionado y validación de estrategias.
2. **Market Data Platform** — ingesta y normalización de datos de mercado
   (tick, OHLCV, order book) en tiempo real e histórico.
3. **Backtesting Engine** — simulación histórica reproducible y de alta fidelidad.
4. **Paper Trading** — simulación en vivo sin capital real.
5. **Execution & OMS** — gestión del ciclo de vida de órdenes y ruteo.
6. **Risk Management** — control de riesgo pre-trade / post-trade y *kill-switch*.
7. **Portfolio & Analytics** — posiciones, PnL, métricas y *tearsheets*.
8. **Marketplace** — publicación, suscripción y monetización de estrategias.
9. **Connectivity** — adaptadores a MT5, Interactive Brokers, Binance, etc.
10. **Decision Replay Engine** — registro de **todas** las decisiones (ejecutadas y
    descartadas) y reproducción paso a paso de cualquier señal.
11. **Market Context Engine** — **primer motor que se ejecuta**: determina el
    contexto/régimen del mercado y emite un **Context Score (0–100)**; si no supera
    el mínimo, el resto del motor **ni busca entradas**. Incluye **Market DNA**
    (perfil por activo que adapta filtros, no reglas).

El motor SMC incluye **Fibonacci Institucional** (anclado a estructura, nunca
indicador independiente; provee la OTE) y es **explicable por diseño**: nunca
responde *"entró porque sí"*. El pipeline se abre siempre con el **gate de
contexto** (Market Context Engine).

---

## Principios de ingeniería

- **Clean Architecture** — dependencias apuntando hacia el dominio.
- **Domain-Driven Design (DDD)** — diseño estratégico + táctico.
- **SOLID + Clean Code** — código legible, testeable y mantenible.
- **Event-Driven** donde aporta valor (integración entre *bounded contexts*).
- **Modular Monolith** listo para evolucionar a **microservicios**.
- **Secure by design** y **Scalable by design**.
- **Explainable by design** — toda decisión de trading es trazable y explicable
  (qué detectó, confirmó, descartó; pesos; score; reglas y vetos).
- **Fully replayable** — cada decisión, ejecutada o descartada, es reproducible.

---

## Índice de documentación de arquitectura

| # | Documento | Contenido |
|---|-----------|-----------|
| 00 | [Visión y Arquitectura General](docs/architecture/00-vision-y-arquitectura-general.md) | Visión, C4, estilo arquitectónico |
| 01 | [Organización del Repositorio](docs/architecture/01-organizacion-repositorio.md) | Monorepo, carpetas, estructura por capas |
| 02 | [Módulos y Bounded Contexts](docs/architecture/02-modulos-y-bounded-contexts.md) | Subdominios, agregados, context map |
| 03 | [Dependencias entre Módulos](docs/architecture/03-dependencias-entre-modulos.md) | Reglas, grafo de dependencias, contratos |
| 04 | [Stack Tecnológico](docs/architecture/04-stack-tecnologico.md) | Tecnologías recomendadas y por qué |
| 05 | [Roadmap Técnico](docs/architecture/05-roadmap-tecnico.md) | Fases, hitos, evolución a microservicios |
| 06 | [Convenciones de Desarrollo](docs/architecture/06-convenciones-desarrollo.md) | Estilo, errores, logging, DI |
| 07 | [Naming Convention](docs/architecture/07-naming-convention.md) | Nomenclatura por lenguaje y capa |
| 08 | [Git Strategy](docs/architecture/08-git-strategy.md) | Trunk-based, PRs, versionado, releases |
| 09 | [Testing Strategy](docs/architecture/09-testing-strategy.md) | Pirámide de tests, contract testing |
| 10 | [Deployment Strategy](docs/architecture/10-deployment-strategy.md) | CI/CD, GitOps, entornos, progresivo |
| 11 | [Seguridad](docs/architecture/11-seguridad.md) | AuthN/Z, secretos, cumplimiento |
| 12 | [Escalabilidad](docs/architecture/12-escalabilidad.md) | Escalado horizontal, datos, resiliencia |
| 13 | [Plan de Ejecución paso a paso](docs/architecture/13-plan-de-ejecucion.md) | Cómo construirlo incrementalmente |

**Decisiones de arquitectura (ADR):** ver [`docs/adr/`](docs/adr/).

---

## Lectura recomendada

1. Empieza por **[00 — Visión](docs/architecture/00-vision-y-arquitectura-general.md)**.
2. Sigue con **[02 — Bounded Contexts](docs/architecture/02-modulos-y-bounded-contexts.md)**.
3. Cuando quieras empezar a construir, ve directo a
   **[13 — Plan de Ejecución](docs/architecture/13-plan-de-ejecucion.md)**.
