# Lenguaje Ubicuo (Ubiquitous Language)

Glosario vivo del dominio de ELYON QUANT. Es la **fuente de verdad de los
nombres**: el código, las APIs, los eventos y las conversaciones usan estos
términos. Si un término significa cosas distintas en contextos distintos, se
indica explícitamente (eso es DDD correcto, no un error).

> Mantener este documento actualizado es parte de la Definition of Done cuando
> se introduce o cambia un concepto de negocio.

## Términos transversales

| Término | Definición |
|---------|-----------|
| **Tenant** | Organización aislada dentro del SaaS; unidad de multi-tenancy. |
| **Principal** | Actor autenticado (usuario o API key) que ejecuta acciones. |
| **Instrument** | Activo negociable identificado por un `Symbol` (p.ej. `EURUSD`). |
| **Symbol** | Identificador normalizado de un instrumento. |
| **Money** | Cantidad con divisa; value object inmutable. |
| **Kill-switch** | Parada de emergencia que detiene toda la operativa (global o acotada). |

## Strategy Lab

| Término | Definición |
|---------|-----------|
| **Strategy** | Definición lógica que genera señales de trading. |
| **StrategyVersion** | Versión inmutable y reproducible de una estrategia. |
| **Signal** | Salida de una estrategia que puede derivar en órdenes. |
| **Indicator** | Cálculo sobre datos de mercado usado por una estrategia. |
| **ParameterSet** | Conjunto de parámetros que configuran una versión. |
| **Universe** | Conjunto de instrumentos sobre los que opera una estrategia. |

## Backtesting

| Término | Definición |
|---------|-----------|
| **Backtest** | Simulación histórica de una estrategia sobre datos pasados. |
| **BacktestRun** | Ejecución concreta y reproducible de un backtest. |
| **Tearsheet** | Informe estandarizado de performance resultante. |

## Execution

| Término | Definición |
|---------|-----------|
| **Order** | Intención de comprar/vender un instrumento con parámetros. |
| **Fill / Execution Report** | Confirmación (parcial o total) de ejecución de una orden. |
| **TradingSession** | Contexto activo de operativa (paper o live) de una cuenta. |
| **Paper** | Modo de operativa simulada, sin capital real. |
| **Live** | Modo de operativa con capital real en un broker. |
| **Venue** | Broker o exchange donde se rutea y ejecuta una orden. |

## Risk

| Término | Definición |
|---------|-----------|
| **RiskProfile** | Configuración de tolerancia y límites de riesgo de una cuenta/tenant. |
| **RiskLimit** | Restricción concreta (exposición, apalancamiento, buying power…). |
| **Pre-trade risk** | Validación bloqueante antes de rutear una orden. |
| **Post-trade risk** | Vigilancia continua tras la ejecución (drawdown, VaR). |

## Portfolio

| Término | Definición |
|---------|-----------|
| **Portfolio** | Conjunto de posiciones y balances de una cuenta. |
| **Position** | Exposición neta a un instrumento. |
| **PnL** | Profit and Loss; realizado o no realizado. |
| **Balance** | Saldo disponible en una divisa/cuenta. |

## Marketplace / Billing (¡ojo con "Subscription"!)

| Término | Contexto | Definición |
|---------|----------|-----------|
| **Subscription** | `billing` | Plan de pago contratado por un tenant. |
| **Subscription** | `marketplace` | Suscripción de un usuario a una estrategia publicada. |
| **Listing** | `marketplace` | Publicación de una estrategia para descubrimiento/venta. |
| **Payout** | `marketplace` | Pago a un creador por *revenue share*. |
| **Plan** | `billing` | Nivel de servicio con sus cuotas y precio. |

> **Nota:** "Subscription" es deliberadamente **dos conceptos distintos en dos
> bounded contexts distintos**. No se comparten ni se fusionan.
