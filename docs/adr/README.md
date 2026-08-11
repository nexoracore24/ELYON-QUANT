# Architecture Decision Records (ADR)

Registramos aquí las **decisiones de arquitectura significativas**: su contexto,
las opciones consideradas, la decisión tomada y sus consecuencias. Un ADR es
inmutable: si una decisión se revierte, se crea un ADR nuevo que **supersede** al
anterior (no se edita el viejo).

Formato basado en Michael Nygard. Plantilla en [`template.md`](template.md).

## Índice

| ADR | Título | Estado |
|-----|--------|--------|
| [0001](0001-record-architecture-decisions.md) | Usar ADRs para registrar decisiones | Accepted |
| [0002](0002-modular-monolith-first.md) | Modular Monolith preparado para microservicios | Accepted |
| [0003](0003-primary-backend-language-python.md) | Python como lenguaje principal del backend | Accepted |
| [0004](0004-event-driven-integration.md) | Integración event-driven entre bounded contexts | Accepted |
| [0005](0005-database-per-module.md) | Base de datos (esquema) por módulo | Accepted |
| [0006](0006-deterministic-computing.md) | Computación determinista (decimal-first, EDCS) | Accepted |
| [0007](0007-risk-budget-concurrency.md) | Concurrencia del presupuesto de riesgo (reserva + CAS) | Accepted |
| [0008](0008-position-ownership-and-scoring-boundaries.md) | Frontera de gestión de posición (Trading↔Execution) y separación Context/Entry Score | Accepted |

## Cuándo escribir un ADR

- Elección de un estilo arquitectónico, tecnología estructural o patrón
  transversal.
- Cualquier decisión difícil de revertir o con impacto en varios equipos.
- Cuando alguien pregunte "¿por qué se hizo así?" y la respuesta merezca durar.
