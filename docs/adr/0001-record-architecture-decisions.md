# ADR-0001: Usar ADRs para registrar decisiones de arquitectura

- **Estado:** Accepted
- **Fecha:** 2026-07-28
- **Decisores:** Arquitectura / CTO

## Contexto

Un producto que aspira a ser una empresa internacional, con múltiples equipos a
lo largo de años, necesita preservar el **porqué** de sus decisiones. El
conocimiento en la cabeza de las personas se pierde; los PRs no explican
contexto estratégico; las decisiones se re-litigan sin memoria.

## Opciones consideradas

1. **ADRs en el repo (Markdown)** — versionados junto al código, revisados por PR.
2. **Wiki externa** — fácil de editar, pero se desincroniza del código y se pudre.
3. **Sin registro formal** — el conocimiento vive en la gente y en el chat.

## Decisión

Adoptamos **Architecture Decision Records** en `docs/adr/`, formato Nygard,
inmutables y versionados con el código. Toda decisión estructural significativa
lleva su ADR, revisado por PR.

## Consecuencias

- **Positivas:** trazabilidad, onboarding más rápido, menos re-debates,
  decisiones auditables.
- **Negativas:** disciplina requerida para escribirlos.
- **Seguimiento:** revisar en retros si se están escribiendo cuando corresponde.
