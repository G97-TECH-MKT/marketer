# Guia de conexion del Orchestrator (integracion entre componentes)

Esta guia documenta el contrato real del sistema para que cualquier equipo pueda conectarse al orchestrator sin depender de conocimiento interno del codigo.

Se basa en el estado actual de `src/orchestrator` y en los documentos de `docs/`.

---

## 1. Arquitectura de integracion

El orchestrator opera en 2 etapas:

1. **Ingreso** de solicitudes (API o SQS inbox).
2. **Procesamiento async** (validaciones, creacion de job/task, dispatch HTTP al agente, callback, cierre de job).

Flujo operativo actual:

```text
Cliente integrador
  -> POST /api/v1/jobs (X-API-Key)
  -> Orchestrator encola mensaje en SQS inbox
  -> SQSInboxConsumer consume mensaje
  -> Valida action/prerequisitos/gates
  -> Crea jobs + job_tasks + execution_log
  -> DispatchWorker toma task QUEUED
  -> HTTP POST {agent.endpoint_url}/tasks
  -> Agente hace PATCH /api/v1/tasks/{task_id}/callback
  -> Orchestrator actualiza estados y logs
  -> (opcional) webhook al iniciador via initiator_callback_url
```

---

## 2. URL de ingreso y autenticacion

## Base URL

- **Backend API**: `https://<tu-dominio-orchestrator>`
- **Versionado API**: `/api/v1`

## Headers de seguridad

Todos los endpoints de negocio requieren:

- Header: `X-API-Key: <ORCH_API_KEY>`

Si falta o es invalido, responde `401 Invalid or missing API key`.

## Endpoints de salud

- `GET /health` -> `{ "status": "healthy" }`
- `GET /ready` -> `{ "status": "ready" }` o `{ "status": "unhealthy", "detail": "database unreachable" }`

---

## 3. Variables de entorno clave para conectividad

- `ORCH_DATABASE_URL`: conexion PostgreSQL (obligatoria).
- `ORCH_API_KEY`: API key para consumo de endpoints protegidos.
- `ORCH_CALLBACK_BASE_URL`: base publica que se usa para construir callback URL enviada a agentes.
- `ORCH_AWS_REGION`: region para SQS.
- `ORCH_SQS_INBOX_QUEUE_URL`: cola inbox; si no existe, `POST /api/v1/jobs` devolvera `503`.
- `ORCH_DISPATCH_WORKER_INTERVAL_SECONDS` (default `5`): polling de tasks `QUEUED`.
- `ORCH_SQS_POLL_WAIT_SECONDS` (default `20`): long polling SQS.
- `ORCH_AGENT_DISPATCH_TIMEOUT_SECONDS` (default `10.0`): timeout HTTP al agente.
- `ORCH_CALLBACK_HMAC_SECRET`: existe en settings, pero la verificacion HMAC no esta conectada al endpoint callback actualmente.

---

## 4. Contrato de entrada principal: crear solicitud de trabajo

## Endpoint

`POST /api/v1/jobs`

## Comportamiento real actual

Este endpoint **encola** la solicitud en SQS inbox (no crea inmediatamente el job en BD).

## Body de entrada

```json
{
  "action": "create_web",
  "client_request": {
    "description": "Quiero una web para mi panaderia",
    "attachments": []
  },
  "context": {
    "client_id": "550e8400-e29b-41d4-a716-446655440000",
    "client_name": "Panaderia El Buen Pan"
  },
  "idempotency_key": "create-web-client-550e8400-turn-001",
  "priority": 1,
  "correlation_id": "chat-session-abc123",
  "initiator_callback_url": "https://mi-servicio.com/webhooks/orchestrator"
}
```

## Reglas importantes de entrada

- `action`: obligatorio, max 100 chars.
- `priority`: `0..2`.
- `correlation_id`: opcional, max 100.
- `initiator_callback_url`: opcional, max 500.
- `payload` maximo: `1_048_576` bytes (accion + client_request + context).

## Respuesta exitosa (enqueue)

```json
{
  "receipt_id": "9b6f6a7e-....",
  "correlation_id": "chat-session-abc123",
  "status": "QUEUED",
  "enqueued_at": "2026-04-17T10:15:00.123Z"
}
```

## Errores tipicos

- `503`: SQS inbox no configurada (`ORCH_SQS_INBOX_QUEUE_URL` vacia).
- `422`: validacion pydantic del payload.
- `401`: API key invalida.

---

## 5. Procesamiento interno (SQS -> job real)

Cuando `SQSInboxConsumer` procesa el mensaje, se ejecutan estas validaciones:

1. Idempotencia por `jobs.idempotency_key`.
2. Existencia de `action_catalog.action_code`.
3. `action_catalog.is_active = true`.
4. Agente asociado existe, activo y con `endpoint_url`.
5. Prerequisitos (`action_prerequisites`).
6. Gates de ejecucion (`action_execution_gates`), incluyendo notificacion opcional por `on_fail_action=notify`.
7. Creacion de `jobs`, `job_tasks`, `job_events`, `execution_log`.

Si hay error de negocio (accion desconocida, prerequisitos faltantes, etc.), el consumer registra traza y elimina el mensaje SQS (no reintenta).

---

## 6. Contrato Orchestrator -> Agentes (dispatch)

## Request del orchestrator al agente

Metodo:

- `POST {agent.endpoint_url}/tasks`

Headers:

- `Content-Type: application/json`
- `X-Task-Id: <task_id>`
- `X-Correlation-Id: <correlation_id>`
- `X-Callback-Url: <callback_url>`
- `Authorization: Bearer <auth_token>` (solo si `agents.auth_token` tiene valor)

Body:

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "action": "create_web",
  "correlation_id": "chat-session-abc123",
  "payload": {
    "client_request": {
      "description": "Quiero una web para mi panaderia"
    },
    "context": {
      "client_id": "550e8400-e29b-41d4-a716-446655440000",
      "client_name": "Panaderia El Buen Pan"
    }
  },
  "callback_url": "https://<ORCH_CALLBACK_BASE_URL>/api/v1/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890/callback"
}
```

## Respuesta esperada del agente

- HTTP 2xx (idealmente `202`) para ACK de recepcion.

Si el agente no responde 2xx, la task falla y puede pasar a retry automatico (backoff exponencial) si aun tiene intentos disponibles.

---

## 7. Contrato Agentes -> Orchestrator (callback)

## Endpoint

`PATCH /api/v1/tasks/{task_id}/callback`

## Headers requeridos

- `X-API-Key: <ORCH_API_KEY>`
- `Content-Type: application/json`
- `X-Correlation-Id` (recomendado para trazabilidad)

## Body de callback

```json
{
  "status": "COMPLETED",
  "output_data": {
    "result_id": "abc123",
    "message": "Proceso terminado"
  },
  "error_message": null
}
```

`status` permitido:

- `IN_PROGRESS`
- `COMPLETED`
- `FAILED`

Transiciones validas:

- `DISPATCHED -> IN_PROGRESS|COMPLETED|FAILED|TIMEOUT`
- `IN_PROGRESS -> COMPLETED|FAILED|TIMEOUT`

Si hay transicion invalida: `409`.
Si `task_id` no existe: `404`.

## Respuesta callback

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "job_id": "f1e2d3c4-b5a6-7890-fedc-ba0987654321",
  "status": "COMPLETED",
  "job_status": "COMPLETED"
}
```

---

## 8. Webhook opcional de cierre al sistema iniciador

Si el request inicial incluye `initiator_callback_url`, cuando el job llega a estado terminal (`COMPLETED|FAILED|TIMEOUT`) el orchestrator hace:

- `POST {initiator_callback_url}`

Payload esperado:

```json
{
  "event": "job_terminal",
  "job_id": "f1e2d3c4-b5a6-7890-fedc-ba0987654321",
  "action_code": "create_web",
  "status": "COMPLETED",
  "correlation_id": "chat-session-abc123",
  "output_data": {},
  "error_message": null,
  "gate_responses": {},
  "sequence_responses": {},
  "tasks": [
    {
      "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "status": "COMPLETED",
      "agent_id": "9f...01",
      "sequence_order": 0,
      "step_code": "step_01",
      "is_mandatory": true,
      "retry_count": 0,
      "error_details": null
    }
  ]
}
```

Nota: en cierres emitidos por el `TimeoutSweeper`, el payload puede no incluir la lista `tasks`. El consumidor del webhook debe tolerar ambos formatos.

---

## 9. API de consulta operativa

- `GET /api/v1/jobs?status=&limit=&offset=`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/execution-log?action_code=&status=&limit=&offset=`
- `GET /api/v1/system/traces?trace_level=&trace_code=&action_code=&correlation_id=&limit=&offset=`

---

## 10. API de catalogo y administracion (conexion entre componentes)

## Agentes

- `GET /api/v1/agents`
- `POST /api/v1/agents`
- `PATCH /api/v1/agents/{agent_id}`
- `DELETE /api/v1/agents/{agent_id}` (soft delete: inactiva)

`POST /api/v1/agents` ejemplo:

```json
{
  "name": "website-builder",
  "description": "Agente constructor de sitios",
  "endpoint_url": "https://agent-website-builder.internal.plinng.com",
  "auth_token": "token-opcional",
  "max_concurrent_tasks": 10,
  "timeout_seconds": 3600,
  "is_active": true
}
```

## Acciones, prerequisitos, gates, secuencias

- `GET|POST|PATCH|DELETE /api/v1/actions`
- `GET|POST|PATCH|DELETE /api/v1/actions/{action_id}/prerequisites`
- `GET|POST|PATCH|DELETE /api/v1/actions/{action_id}/gates`
- `GET|POST|PATCH|DELETE /api/v1/actions/{action_id}/sequence`

Esto permite configurar enrutamiento sin redeploy.

---

## 11. Datos minimos requeridos en base de datos

Para operar el orchestrator, debes tener al menos:

1. **`agents`**: agentes registrados (activos, con `endpoint_url`).
2. **`action_catalog`**: acciones que el sistema permite.
3. **`action_prerequisites`**: reglas minimas por accion.

Opcionales pero recomendados:

- **`action_execution_gates`**: validaciones de negocio previas.
- **`agent_sequence`**: ejecucion multi-step por accion.

Tablas gestionadas automaticamente por runtime:

- `jobs`
- `job_tasks`
- `job_events`
- `execution_log`
- `system_trace_log`

## SQL minimo de ejemplo

```sql
-- 1) Agente
INSERT INTO agents (
  id, name, description, endpoint_url, max_concurrent_tasks, timeout_seconds, is_active, created_at, updated_at
)
VALUES (
  gen_random_uuid(),
  'website-builder',
  'Agente de construccion de sitios',
  'https://agent-website-builder.internal.plinng.com',
  10,
  3600,
  true,
  now(),
  now()
)
ON CONFLICT (name) DO UPDATE
SET endpoint_url = EXCLUDED.endpoint_url, is_active = true, updated_at = now();

-- 2) Accion
INSERT INTO action_catalog (
  id, action_code, display_name, description, agent_id, payload_schema, is_active, created_at, updated_at
)
SELECT
  gen_random_uuid(),
  'create_web',
  'Crear sitio web',
  'Crea sitio web para cliente',
  a.id,
  '{}'::jsonb,
  true,
  now(),
  now()
FROM agents a
WHERE a.name = 'website-builder'
ON CONFLICT (action_code) DO UPDATE
SET agent_id = EXCLUDED.agent_id, is_active = true, updated_at = now();

-- 3) Prerequisitos minimos
INSERT INTO action_prerequisites (
  id, action_id, field_key, display_name, field_location, is_mandatory, validation_rule, sort_order, created_at
)
SELECT gen_random_uuid(), ac.id, 'description', 'Descripcion cliente', 'client_request', true, 'non_empty', 10, now()
FROM action_catalog ac
WHERE ac.action_code = 'create_web'
ON CONFLICT (action_id, field_key) DO UPDATE
SET validation_rule = EXCLUDED.validation_rule, sort_order = EXCLUDED.sort_order;
```

---

## 12. Orden recomendado para integrar un nuevo agente

1. Crear agente en `agents` (o via `POST /api/v1/agents`).
2. Crear accion en `action_catalog` apuntando al `agent_id`.
3. Definir prerequisitos (`action_prerequisites`).
4. (Opcional) definir gates (`action_execution_gates`).
5. Probar ingreso con `POST /api/v1/jobs`.
6. Verificar que el job llegue por SQS y se cree en BD.
7. Validar dispatch hacia `POST /tasks` del agente.
8. Validar callback `PATCH /api/v1/tasks/{task_id}/callback`.
9. Validar estado final en `GET /api/v1/jobs/{job_id}` y `execution-log`.
10. (Opcional) validar `initiator_callback_url`.

---

## 13. Checklist de conexion E2E

- [ ] `GET /health` y `GET /ready` ok.
- [ ] `ORCH_SQS_INBOX_QUEUE_URL` configurada.
- [ ] API key correcta.
- [ ] Agente activo y con `endpoint_url`.
- [ ] Accion activa y asociada a un agente valido.
- [ ] Prerequisitos configurados para la accion.
- [ ] Agente responde `POST /tasks` con 2xx.
- [ ] Agente puede llamar callback con `X-API-Key`.
- [ ] Estado del job transiciona a terminal.
- [ ] `execution_log` y `system_trace_log` registran evidencia.

---

## 14. Errores frecuentes y como interpretarlos

- `503 SQS inbox queue is not configured`: falta `ORCH_SQS_INBOX_QUEUE_URL`.
- `422 Action '<x>' is not registered`: no existe en `action_catalog`.
- `422 Action '<x>' is disabled`: accion inactiva.
- `422 Agent for action '<x>' not found or inactive`: agente invalido o inactivo.
- `422 missing_prerequisites`: campos obligatorios faltantes/invalidos.
- `422 execution_gates_failed`: negocio no habilitado para ejecutar.
- `409 Job already exists`: `idempotency_key` duplicada.
- `502 Agent dispatch failed`: fallo HTTP al agente o timeout de dispatch.

---

## 15. Scripts utiles del repo para bootstrap y pruebas

- `scripts/bootstrap_routing_contracts.py`: carga/actualiza acciones y prerequisitos base.
- `scripts/seed_demo_data.py`: datos demo.
- `scripts/e2e_mock_agents.py`: agentes mock para validar dispatch/callback end-to-end.

---

## 16. Notas de compatibilidad y madurez

- La documentacion historica del repo incluye etapas antiguas; para integracion usar este documento como referencia principal.
- El flujo API actual es orientado a **enqueue en SQS** y procesamiento asinc.
- HMAC de callbacks esta implementado como funcion utilitaria pero no exigido actualmente en el endpoint.
- Si consumes webhooks de cierre (`initiator_callback_url`), prepara parsing tolerante por variaciones menores entre rutas de cierre.
