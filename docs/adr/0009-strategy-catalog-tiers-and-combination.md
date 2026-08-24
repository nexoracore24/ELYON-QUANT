# ADR-0009: Catálogo de estrategias — tiers por evidencia y reglas de combinación

- **Estado:** Accepted
- **Fecha:** 2026-08-24
- **Decisores:** CTO/Principal Architect, Quant Research Director, Risk Lead
- **Relacionado:** ENG-002 (Smart Money Engine Bible), ENG-005 (Risk),
  ENG-009 (Decision Replay), [ADR-0006](0006-deterministic-computing.md),
  [ADR-0008](0008-position-ownership-and-scoring-boundaries.md)

## Contexto

El producto necesita un **catálogo de estrategias** (Smart Money / ICT) que el
usuario pueda activar, desactivar y combinar, con una indicación visible de
cuáles son de alta, media y baja probabilidad.

Es un requisito de producto razonable que esconde tres trampas de ingeniería,
todas capaces de convertir el catálogo en un destructor de capital:

1. **El tier como literal.** Si la etiqueta «alta probabilidad» es una constante
   que escribió el autor de la estrategia, el motor está dimensionando posiciones
   contra una opinión. Es la forma más cara de confundir confianza con evidencia.
2. **La confluencia ingenua.** Si «N estrategias de acuerdo» sube la convicción
   contando estrategias, el catálogo fabrica certeza simplemente creciendo:
   cinco plays que leen el mismo FVG son *una* evidencia vista cinco veces.
3. **El desacuerdo promediado.** Si dos estrategias en vivo quieren lados
   opuestos y el sistema promedia, abre una posición pequeña en el lado que
   gritara más y oculta que no tenía lectura alguna.

Además hay un círculo vicioso operativo: una estrategia necesita datos de
calibración para que se le confíe capital, y necesita ejecutarse para producir
datos de calibración.

## Decisión

### 1. El tier se gana con calibración, nunca se declara

`StrategyProfile` lleva dos campos separados:

- `declared_tier` — la hipótesis del autor. **Solo se muestra**, nunca se obedece.
- `effective_tier` — derivado de un `Calibration(sample_size, wins, expectancy_r)`.

Sin calibración, `effective_tier` es `UNPROVEN`. Las reglas de derivación:

| Condición | Tier |
|---|---|
| `sample_size < 30` | ⚪ UNPROVEN |
| `expectancy_r <= 0` | 🔴 LOW |
| `expectancy_r >= 0.35` | 🟢 HIGH |
| `expectancy_r >= 0.15` | 🟡 MEDIUM |
| resto | 🔴 LOW |

**Expectancy manda, no el win rate.** Una estrategia con 90% de aciertos y
expectancy negativa es LOW: gana a menudo y pierde dinero, que son afirmaciones
distintas y solo la segunda paga. La expectancy se mide en R (múltiplos del
riesgo) para que sea comparable entre instrumentos y tamaños.

El catálogo se publica con **todos los tiers efectivos en ⚪**. Llenarlos es
trabajo del Backtesting Engine, y la distancia entre las dos columnas
(`tier_drift`) es el backlog de research.

### 2. Activación tri-estado: OFF / SHADOW / LIVE

Un booleano deja el círculo vicioso sin salida. `SHADOW` evalúa la estrategia en
cada vela y registra su señal, pero esa señal **nunca** llega a la operación. Es
el mecanismo por el que una estrategia acumula la evidencia que necesita para
dejar de ser `UNPROVEN`.

El estado completo de activación se hashea (`registry.config_hash`) y viaja con
cada decisión, para que un replay pueda probar qué estaba activo.

### 3. El tier decide quién puede operar solo

| Tier | Familias corroborantes requeridas |
|---|---|
| 🟢 HIGH | 0 — ha ganado el derecho a actuar sola |
| 🟡 MEDIUM | 1 |
| 🔴 LOW | 2 |
| ⚪ UNPROVEN | nunca sola; solo puede corroborar |

Esta regla es lo que hace **seguro publicar trece estrategias a la vez**.

### 4. La confluencia cuenta familias, no estrategias

Cada estrategia declara una `StrategyFamily` (LIQUIDITY_RAID, STRUCTURE_SHIFT,
IMBALANCE, BLOCK_MITIGATION, PREMIUM_DISCOUNT, SESSION_TIMING, CORRELATION).
Dentro de una familia, **la señal más fuerte representa a la familia**;
promediar dejaría que un duplicado débil diluyera una lectura fuerte, y sumar
pagaría por la duplicación.

Propiedad invariante, cubierta por test: **añadir una estrategia nunca puede,
por sí sola, hacer que un setup existente parezca mejor.**

El bonus por confluencia es **decreciente y con tope** (4 familias): un gráfico
concurrido no es una certeza.

### 5. El desacuerdo es un veto

Política por defecto `VETO`: si hay señales en vivo en ambos sentidos, el motor
se planta y emite `Veto.STRATEGY_CONFLICT`. Alternativas configurables
(`STRONGEST_WINS` con margen de dominancia, `MAJORITY` por familias) existen
para quien las quiera, pero ninguna es el default.

### 6. Las killzones se definen en hora de Nueva York

Los modelos de sesión (Silver Bullet, Judas, Power of 3, Asian Range) están
definidos en hora local de Nueva York. Fijarlos en UTC los deja desplazados una
hora la mitad del año. Se convierte con `zoneinfo`, y la zona se registra en la
provenance (`sessionTimezone`) por la dependencia de tzdata.

## Alternativas descartadas

- **Tier como constante editable en config.** Es exactamente la trampa 1 con un
  paso de indirección: mueve la opinión de un fichero a otro.
- **Ponderar por win rate.** Premia estrategias que ganan mucho y pequeño y
  pierden poco y grande. Es el perfil de riesgo que revienta cuentas.
- **Confluencia por conteo de estrategias.** Trampa 2. Incentiva añadir plays
  redundantes para inflar la convicción.
- **Netear señales opuestas.** Trampa 3. Produce una posición pequeña con
  convicción cero y sin registro de que hubo desacuerdo.
- **Publicar el catálogo con todo en LIVE.** Sería un maximizador de número de
  operaciones disfrazado de catálogo.
- **Aproximar SMT Divergence desde un solo símbolo.** Daría una estrategia que
  dispara con ruido y lo llama divergencia. Se declara `requires_correlated_feed`
  y el registro **rechaza** ponerla en LIVE; en SHADOW se abstiene diciendo por qué.

## Consecuencias

**Positivas.** El sistema es honesto desde el día uno: no opera con nada que no
haya medido, y lo dice con un motivo concreto en vez de fingir. El catálogo puede
crecer sin aumentar el riesgo, porque crecer no aumenta la convicción. Cada
decisión es reproducible incluyendo qué estrategias estaban activas.

**Negativas / coste asumido.** Recién instalado, **el bot no opera**: todo está
en ⚪ y el gate lo rechaza. Es deliberado, y hay que comunicarlo en el onboarding
o se leerá como un fallo. Desbloquearlo exige ejecutar el Backtesting Engine
(aún no implementado), que queda en el camino crítico del producto.

**Deuda registrada.** Los `declared_tier` del catálogo no están validados:
son la hipótesis de partida para la calibración, no una recomendación.
