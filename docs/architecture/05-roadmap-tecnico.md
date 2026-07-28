# 05 — Roadmap Técnico

> El roadmap se organiza en **fases con salidas verificables**, no en fechas
> rígidas. Cada fase termina con algo desplegable y medible. La regla es:
> **construir el esqueleto vertical antes que rellenar horizontalmente**.

## Fase 0 — Fundaciones (actual)
**Objetivo:** que el proyecto pueda crecer sin fricción.

- Diseño de arquitectura (este documento) ✅
- Monorepo, tooling, `Makefile`, `docker-compose` local.
- CI base (lint, type-check, test, build), pre-commit hooks.
- Contratos base (`/contracts`), generación de tipos.
- `platform/` (config, DI, bus, telemetry, security) + `shared_kernel`.
- Un módulo *walking skeleton* (`iam`) atravesando las 4 capas end-to-end.
- Observabilidad mínima (tracing + métricas + logs).

**Salida:** despliegue de `platform-api` con `iam` funcional y un pipeline verde.

## Fase 1 — MVP de trading vertical (single-broker)
**Objetivo:** cerrar el circuito **estrategia → backtest → paper → live** con un
solo broker (MT5) y un puñado de instrumentos.

- `market_data`: catálogo + ingesta OHLCV histórica y tiempo real (1 feed).
- `strategy_lab`: definir/versionar estrategias (parámetros, indicadores).
- `backtesting`: motor determinista + `quant-workers` (Temporal) + tearsheet.
- `execution` + `execution-gateway`: OMS básico, modo *paper* y *live* (MT5 ACL).
- `risk`: límites pre-trade esenciales + kill-switch.
- `portfolio`: posiciones y PnL.
- `iam` + `billing` (Stripe) + `notifications` (email) para operar como SaaS.
- Frontend: dashboard con estrategias, backtests y operativa en vivo.

**Salida:** un usuario puede registrarse, crear una estrategia, backtestearla,
pasarla a paper y activarla en real sobre MT5, con control de riesgo.

## Fase 2 — Producto SaaS robusto y multi-broker
**Objetivo:** fiabilidad, más conectividad y analítica seria.

- Conectividad adicional: **Interactive Brokers**, **Binance** (nuevos ACLs
  sin tocar el dominio de `execution`).
- `analytics`: métricas avanzadas, atribución, comparativas, reporting.
- Datos: TimescaleDB/ClickHouse a escala, calidad de datos, más timeframes.
- Riesgo avanzado: exposición por cartera, VaR, límites por org.
- Resiliencia: outbox/inbox en todos los módulos, idempotencia, DLQs.
- Multi-tenant endurecido: RLS, aislamiento, cuotas por plan.
- SLOs formales y *error budgets*; on-call y runbooks.

**Salida:** plataforma multi-broker fiable con analítica de nivel profesional.

## Fase 3 — Ecosistema y monetización
**Objetivo:** efectos de red y nuevas fuentes de ingreso.

- `marketplace`: publicar, descubrir, suscribir y monetizar estrategias;
  *revenue share*, ratings, *copy-trading*.
- API pública para clientes (contratos versionados, rate-limits por plan).
- SDKs (Python/TS) para autoría de estrategias externas.
- Backtesting a escala (paralelismo masivo, *walk-forward*, optimización).
- Programa de *paper trading* competitivo / *leaderboards*.

**Salida:** ELYON QUANT como ecosistema con creadores externos y marketplace.

## Fase 4 — Escala internacional y evolución a microservicios
**Objetivo:** escalar organización y sistema.

- **Extracción selectiva de servicios** (empezando por `execution`,
  `market_data`, `backtesting`) siguiendo el criterio de extracción (abajo).
- Multi-región (latencia y residencia de datos), *data locality*.
- Cumplimiento formal (SOC 2, ISO 27001; según jurisdicción, MiFID/otros).
- Service mesh (mTLS), *cell-based architecture* para aislamiento de blast radius.
- Optimización de costes (autoscaling fino, *spot*, tiering de datos).

**Salida:** plataforma internacional, cumplida y operada por múltiples equipos.

---

## Criterio de extracción a microservicio

Un módulo se extrae **solo** cuando cumple ≥2 de estos disparadores. No antes.

1. **Escalado divergente**: necesita escalar (o desplegarse) a un ritmo distinto
   al del resto (p.ej. `market_data` en picos de mercado).
2. **Perfil no-funcional distinto**: latencia, cómputo o disponibilidad muy
   diferentes (p.ej. `execution`).
3. **Propiedad de equipo**: un equipo dedicado necesita autonomía de despliegue.
4. **Aislamiento de fallo**: su caída no debe arrastrar al resto.

Como las fronteras ya existen (Fase 0-1), extraer = envolver el módulo en un
*shell* de servicio y cambiar el *transport* del contrato. El dominio no se toca.

---

## Principios que gobiernan el roadmap

- **Vertical slices, no capas horizontales**: cada fase entrega valor de punta
  a punta, no "toda la capa de datos primero".
- **Deuda técnica presupuestada**: se admite deuda deliberada, registrada y con
  fecha de pago (no deuda accidental).
- **Nada de *big rewrites***: la arquitectura permite evolucionar sin reescribir.
- **Medir antes de optimizar**: escalado y latencia se abordan con datos de
  observabilidad, no por intuición.
