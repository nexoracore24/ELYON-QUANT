# ADR-0002: Modular Monolith preparado para microservicios

- **Estado:** Accepted
- **Fecha:** 2026-07-28
- **Decisores:** Arquitectura / CTO
- **Relacionado:** [00-visión](../architecture/00-vision-y-arquitectura-general.md),
  [03-dependencias](../architecture/03-dependencias-entre-modulos.md)

## Contexto

ELYON QUANT debe entregar valor rápido con un equipo inicial pequeño, pero estar
preparado para escalar a organización y sistema de nivel internacional. Los dos
extremos —monolito acoplado o microservicios desde el día 1— tienen costes
conocidos: el primero se vuelve inmantenible; el segundo impone complejidad
operativa (red, observabilidad distribuida, consistencia) que un equipo pequeño
no puede pagar y que además ralentiza la iteración temprana.

## Opciones consideradas

1. **Microservicios desde el inicio** — máxima autonomía, pero coste operativo y
   cognitivo altísimo, *distributed monolith* si las fronteras no están claras.
2. **Monolito tradicional** — rápido al inicio, pero degenera en *big ball of
   mud* sin fronteras internas.
3. **Modular Monolith con fronteras estrictas** — un *deployable* con módulos
   aislados (bounded contexts), comunicación por contratos/eventos, extraíbles a
   servicios cuando se justifique.

## Decisión

Adoptamos **Modular Monolith** para la plataforma transaccional
(`platform-api`), con **fronteras de módulo estrictas** (Clean Architecture por
módulo, esquema de BD por módulo, comunicación por interfaces/eventos). Tres
componentes con perfil no-funcional distinto nacen como **servicios separados**:
`execution-gateway` (latencia), `market-data-ingestor` (throughput),
`quant-workers` (cómputo).

## Consecuencias

- **Positivas:** velocidad inicial de un monolito + disciplina que permite
  extraer servicios sin reescribir el dominio (solo cambia el *transport*).
  Transaccionalidad local simple donde importa.
- **Negativas / trade-offs:** exige **disciplina de fronteras** protegida por
  automatización (import-linters, tests de arquitectura); un despliegue único
  para la plataforma hasta que se extraigan módulos.
- **Condición de revisión:** extraer un módulo a servicio cuando cumpla ≥2
  disparadores del criterio de extracción ([roadmap](../architecture/05-roadmap-tecnico.md)).
