# ADR-0003: Python como lenguaje principal del backend

- **Estado:** Accepted
- **Fecha:** 2026-07-28
- **Decisores:** Arquitectura / CTO
- **Relacionado:** [04-stack](../architecture/04-stack-tecnologico.md)

## Contexto

Necesitamos un lenguaje principal para el monolito modular y los workers de
cómputo. El dominio es trading cuantitativo: backtesting, señales, ML, análisis
numérico. También queremos velocidad de desarrollo y un mercado amplio de
talento, sin renunciar a mantenibilidad a gran escala.

## Opciones consideradas

1. **Python 3.12** — ecosistema quant/ML sin rival (NumPy, pandas/Polars,
   vectorbt, librerías de brokers), tipado gradual maduro, FastAPI/Pydantic para
   backend robusto. Contra: rendimiento bruto y GIL (mitigable: hot path en Rust,
   workers escalables, async I/O).
2. **TypeScript / NestJS** — excelente DI y modularidad, tipos compartidos con
   el frontend. Contra: ecosistema quant/ML pobre; habría que puentear a Python
   igualmente para backtesting.
3. **Go / Java / Kotlin** — rendimiento y robustez, pero afinidad baja con el
   dominio cuant y más *boilerplate* para iterar rápido al inicio.

## Decisión

**Python 3.12** como lenguaje principal del backend transaccional
(`platform-api`) y del cómputo (`quant-workers`, `market-data-ingestor`), con
**FastAPI + Pydantic + SQLAlchemy**. La **latencia crítica** de ejecución se
aísla en `execution-gateway` escrito en **Rust**. El frontend es **TypeScript**.

## Consecuencias

- **Positivas:** máxima afinidad con el dominio; modelos compartidos entre
  backtesting, ejecución y ML; iteración rápida; talento abundante.
- **Negativas / trade-offs:** el rendimiento del hot path no se resuelve en
  Python → se delega a Rust; disciplina de tipado (mypy estricto) obligatoria
  para escalar el código; gestión cuidadosa de async/GIL.
- **Condición de revisión:** si un componente del monolito demuestra ser
  cuello de botella irresoluble en Python, se extrae a servicio en Rust/Go
  (las fronteras de módulo lo permiten sin reescritura del resto).
