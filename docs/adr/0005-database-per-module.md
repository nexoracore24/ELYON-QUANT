# ADR-0005: Base de datos (esquema) por módulo

- **Estado:** Accepted
- **Fecha:** 2026-07-28
- **Decisores:** Arquitectura / CTO
- **Relacionado:** [03-dependencias](../architecture/03-dependencias-entre-modulos.md),
  [12-escalabilidad](../architecture/12-escalabilidad.md)

## Contexto

En un monolito, la tentación es una única base de datos compartida donde
cualquier módulo hace `JOIN` con las tablas de otro. Eso destruye las fronteras:
crea acoplamiento oculto por el esquema, impide evolucionar un módulo sin romper
a otros y bloquea la futura extracción a microservicios.

## Opciones consideradas

1. **BD única compartida, tablas libres** — cómodo al inicio, mortal a medio
   plazo (acoplamiento por datos).
2. **Un servidor de BD distinto por módulo desde el día 1** — aislamiento
   máximo, pero coste operativo alto e innecesario en fase de monolito.
3. **Un esquema por módulo dentro de una misma instancia PostgreSQL**, sin
   `JOIN` entre esquemas de módulos distintos.

## Decisión

**Un esquema por módulo** (`iam.*`, `execution.*`, `portfolio.*`…) en una
instancia PostgreSQL compartida durante la fase de monolito. **Prohibido** el
acceso o `JOIN` a tablas de otro módulo: los datos ajenos se obtienen por la API
de aplicación del módulo dueño o por **proyecciones alimentadas por eventos**
(CQRS). Multi-tenancy reforzada con **Row-Level Security**. Al extraer un módulo
a servicio, su esquema migra a su propia instancia sin cambiar el modelo.

## Consecuencias

- **Positivas:** fronteras reales protegidas por los datos; evolución
  independiente de cada módulo; camino directo a la extracción de servicios;
  aislamiento de tenant con RLS.
- **Negativas / trade-offs:** no hay `JOIN` cruzado → algunas consultas requieren
  composición en aplicación o read models (coste asumido a cambio de
  desacoplamiento); disciplina verificada en CI (revisión de migraciones y tests
  que detectan accesos cruzados).
- **Condición de revisión:** al extraer un módulo, promover su esquema a
  instancia dedicada según necesidades de escala/aislamiento.
