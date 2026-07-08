# Arquitectura — Sistema de Reservas de Entradas

Ver diagrama: [architecture.svg](architecture.svg)

## Componentes

| Componente | Rol | Réplicas | Distribución |
|---|---|---|---|
| `api-gateway` | Punto de entrada REST para clientes | 2 | 1 en Nodo A, 1 en Nodo B (anti-affinity preferida) |
| `reservas-service` | Orquesta la compra (Core) | 2 | 1 en Nodo A, 1 en Nodo B (anti-affinity **requerida**) |
| `inventario-service` | Verifica/descuenta cupo disponible | 2 | 1 en Nodo A, 1 en Nodo B (anti-affinity **requerida**) |
| `pagos-service` | Stub de pasarela de pago | 2 | ambas réplicas preferentemente repartidas |
| `notificaciones-service` | Stub de envío de confirmaciones | 2 | ambas réplicas preferentemente repartidas |
| `postgres` | Persistencia compartida (`inventory`, `reservations`) | 1 | fijado a Nodo A por el PVC (`local-path`) |

`reservas-service` e `inventario-service` usan `podAntiAffinity` con
`requiredDuringSchedulingIgnoredDuringExecution` y `topologyKey: kubernetes.io/hostname`,
de forma que Kubernetes **rechaza** programar las 2 réplicas en el mismo nodo. Con
exactamente 2 nodos en el clúster, esto garantiza 1 réplica por nodo: la caída de
cualquiera de las dos computadoras deja al menos una réplica viva de ambos servicios
críticos.

## Flujo de una reserva (REST)

1. Cliente → `api-gateway` (`POST /api/reservations`).
2. `api-gateway` → `reservas-service` (`POST /reservations`).
3. `reservas-service` → `inventario-service` (`POST /inventory/{event_id}/reserve`):
   descuenta el cupo de forma atómica (`UPDATE ... WHERE available_seats >= qty`).
4. `reservas-service` → `pagos-service` (`POST /charge`): stub con latencia aleatoria
   (100–2500 ms, 5% de "colgadas" de 8s) y ~20% de rechazo.
   - Si el pago falla, `reservas-service` compensa liberando el cupo reservado en el
     paso 3 (patrón saga) y persiste la reserva como `FAILED`.
5. Si el pago es exitoso, se persiste la reserva como `CONFIRMED` en PostgreSQL.
6. `reservas-service` → `notificaciones-service` (`POST /notify`): stub con latencia
   aleatoria (50–1500 ms) y ~15% de fallo. Un fallo aquí **no revierte** la reserva
   ya cobrada; solo queda `notified: false`.

## Por qué dos nodos físicos y no un solo clúster de un nodo

El objetivo de la práctica es experimentar con fallos reales de infraestructura
(caída de un nodo completo). Repartir `reservas-service` e `inventario-service`
entre dos computadoras distintas permite apagar una de ellas y verificar que el
sistema sigue respondiendo con la réplica que sobrevive en la otra, algo que un
clúster de un solo nodo no puede demostrar.

## Limitación conocida

`postgres` corre con una sola réplica y su volumen (`local-path` provisioner de k3s)
queda físicamente en el nodo donde se programó el pod la primera vez. Si ese nodo cae,
la base de datos deja de estar disponible hasta que vuelva a levantarse. Esto es una
limitación aceptada para el alcance de esta Parte I (se documenta como punto de partida
para los mecanismos de resiliencia de las partes siguientes de la práctica).
