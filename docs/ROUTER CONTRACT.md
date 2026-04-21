# Contratos oficiales Plinng Orchestrator (guia para developers)

Documento para **desarrolladores de agentes y servicios iniciadores** (conversacional, front, backends externos). Es el contrato unico de entrada y salida para integrarse con el orquestador/router. Fuente de verdad en codigo: `application/dto/job_dto.py`, `application/services/job_service.py`, `infrastructure/messaging/http_agent_dispatcher.py`, `adapters/api/routes/*`.

> Version: **v1 (comportamiento actual del codigo)** con una sub-seccion opcional para los nuevos campos **v2** descritos en `adr-payload-incremental-y-evolucion.md` (dependencias declarativas + embed filtrado). Mientras la v2 no este habilitada por configuracion, aplica v1.

## Indice

1. Mapa mental del flujo.
2. Contrato A - Iniciador -> Orquestador (crear un job).
3. Contrato B - Orquestador -> Agente (dispatch HTTP).
4. Contrato C - Agente -> Orquestador (callback de estado).
5. Contrato D - Orquestador -> Iniciador (notificacion terminal).
6. Contrato E - Consulta de estado del job (pull).
7. Codigos de error HTTP + ejemplos.
8. Activar/desactivar info con `depends_on_*` y proyecciones (v2).
9. Checklists de integracion para dev de agente.
10. Apendice - Headers, auth, idempotencia, correlacion.

---

## 1. Mapa mental

```
Iniciador (conversacional / front / otro)
    |  [Contrato A]
    v
+-------------------------+
| Orquestador (router)     |
|  - action_prerequisites  |  validacion estatica
|  - action_execution_gates|  llama microservicios verificadores
|  - agent_sequence        |  orden de pasos
+-------------------------+
    |  [Contrato B] POST {agent_endpoint}/tasks
    v
+-------------------------+
| Agente (N veces, en orden) |
+-------------------------+
    |  [Contrato C] PATCH /api/v1/tasks/{task_id}/callback
    v
+-------------------------+
| Orquestador actualiza estado, despacha siguiente paso |
+-------------------------+
    |  [Contrato D] cuando el job llega a terminal (si initiator_callback_url)
    v
+-------------------------+
| Iniciador recibe resultado final |
+-------------------------+
```

Pull alternativo: `GET /api/v1/jobs/{job_id}` (Contrato E).

---

## 2. Contrato A - Iniciador -> Orquestador

Endpoint: `POST {API_BASE}/api/v1/jobs`

Auth: `X-API-Key: <token_del_iniciador>` (configurado en orquestador).

Headers recomendados:

| Header | Uso |
|--------|-----|
| `X-API-Key` | autenticacion |
| `X-Correlation-ID` | trazabilidad end-to-end, opcional |

**Body (request) - `CreateJobRequest`:**

| Campo | Tipo | Obligatorio | Notas |
|-------|------|-------------|-------|
| `action` | string (1..100) | si | `action_code` del catalogo (ej. `create_ig_post`). |
| `client_request` | object | si (puede ser `{}`) | Datos funcionales del pedido. Los `action_prerequisites` con `field_location=client_request` se validan aqui. |
| `context` | object | si (puede ser `{}`) | Contexto (cuenta, cliente, plataforma, ...). Incluye idealmente `account_uuid`. Los `action_prerequisites` con `field_location=context` se validan aqui. |
| `idempotency_key` | string (<=255) | no | Unica por alta de job. Reintentos con misma key -> **409**. |
| `priority` | int 0..2 | no | 0=default, 2=alta. |
| `correlation_id` | string (<=100) | no | Si no viene, el orquestador genera uno. |
| `initiator_callback_url` | string (<=500) | no | Si presente, el orquestador hara POST al llegar el job a estado terminal (Contrato D). |

**Limite:** `action + client_request + context` serializados no pueden exceder **1 048 576 bytes (1 MB)**.

**Respuesta (status 202):** `EnqueueJobResponse`

```json
{
  "receipt_id": "sqs-msg-abc123",
  "correlation_id": "ig-20260420-007",
  "status": "QUEUED",
  "enqueued_at": "2026-04-20T10:00:00+00:00"
}
```

Nota: se responde **202 Accepted** porque el job va primero a cola SQS. El trabajo de validar prerequisites/gates y crear el Job con status `PENDING` ocurre en el consumer. Para seguir el estado, usar Contrato E o esperar Contrato D.

**Ejemplo completo (Instagram):**

```json
{
  "action": "create_ig_post",
  "context": {
    "account_uuid": "8a095bf8-f9b7-47a5-9d4a-5933983ba95f",
    "client_name": "Casa Maruja",
    "platform": "instagram"
  },
  "client_request": {
    "description": "Crea un post destacando el plato estrella de esta semana",
    "attachments": []
  },
  "idempotency_key": "ig-20260420-007",
  "correlation_id": "ig-20260420-007",
  "initiator_callback_url": "https://conversational.example.com/orchestrator/callback"
}
```

---

## 3. Contrato B - Orquestador -> Agente (dispatch HTTP)

Endpoint (hacia el agente): `POST {agent_endpoint_base}/tasks`

`agent_endpoint_base` lo define el propio agente y se registra en la tabla `agents` (o en `agent_sequence.endpoint` si el paso usa uno distinto). El orquestador concatena `/tasks` siempre.

**Headers que recibe el agente:**

| Header | Uso |
|--------|-----|
| `Content-Type: application/json` | fijo |
| `X-Task-Id` | UUID de la task, redundante con el body |
| `X-Correlation-Id` | trazabilidad |
| `X-Callback-Url` | URL que el agente debe llamar al terminar (Contrato C) |
| `Authorization: Bearer <auth_token>` | solo si el agente registro `auth_token` en la tabla `agents` |

**Body (request) - shape canonico:**

```json
{
  "task_id": "<uuid>",
  "job_id": "<uuid>",
  "action_code": "<code>",
  "action_id": "<uuid|null>",
  "correlation_id": "<str>",
  "payload": {
    "client_request": { "...": "..." },
    "context": { "...": "..." },
    "action_execution_gates": {
      "<gate_code>": {
        "passed": true,
        "reason": "<str>",
        "status_code": 200,
        "response": { "...": "..." }
      }
    },
    "agent_sequence": {
      "current": {
        "step_code": "<str>",
        "step_order": 1,
        "task_id": "<uuid>",
        "agent_id": "<uuid>",
        "agent_name": "<str>",
        "endpoint": "<url>/tasks",
        "http_method": "POST",
        "is_mandatory": true,
        "timeout_seconds": 60,
        "retry_count": 0
      },
      "previous": {
        "<step_code_prev>": {
          "status": "COMPLETED",
          "sequence_order": 1,
          "output_schema": "<schema|null>",
          "output_data": { "...": "..." },
          "retry_count": 0,
          "started_at": "2026-04-20T11:00:09+00:00",
          "completed_at": "2026-04-20T11:00:20+00:00",
          "duration_ms": 11000
        }
      }
    }
  },
  "callback_url": "<url>/api/v1/tasks/<task_id>/callback"
}
```

**Reglas v1 (actual):**
- `action_execution_gates` incluye **todos** los gates activos obligatorios.
- `agent_sequence.previous` incluye **todos** los pasos anteriores (con su `output_data`).
- `client_request` y `context` son copia del request original.

**Reglas v2 (con `depends_on_*` activado):**
- `action_execution_gates` incluye **solo** los `gate_code` declarados en `depends_on_gates` del paso actual.
- `agent_sequence.previous` incluye **solo** los `step_code` declarados en `depends_on_steps`.
- Si hay `gates_projection` o `previous_projection`, se filtran los campos internos.
- Ver seccion 8.

**Respuesta esperada del agente:** HTTP **2xx** sin cuerpo obligatorio. El orquestador considera despachada la task si hay 2xx. No se exige schema. Cualquier cuerpo informativo es aceptado.

**Timeout por dispatch:** 10s (HTTP). Si el agente no responde 2xx en ese tiempo, el orquestador marca la task FAILED/retry. Este timeout es para **aceptar la task**; el tiempo real de procesamiento es asincrono (callback).

---

## 4. Contrato C - Agente -> Orquestador (callback)

Endpoint: `PATCH {API_BASE}/api/v1/tasks/{task_id}/callback`

Auth: `X-API-Key: <token_de_agente>` (los agentes reciben su token al registrarse; puede variar por agente).

**Body - `TaskCallbackRequest`:**

| Campo | Tipo | Obligatorio | Notas |
|-------|------|-------------|-------|
| `status` | `QUEUED\|DISPATCHED\|IN_PROGRESS\|COMPLETED\|FAILED\|TIMEOUT` | si | Estado nuevo reportado. Solo son utiles: `IN_PROGRESS`, `COMPLETED`, `FAILED`, `TIMEOUT`. |
| `output_data` | object \| null | si `status=COMPLETED` | Datos funcionales para el siguiente paso. Si el paso tiene `output_schema`, se valida. |
| `error_message` | string \| null | si `status=FAILED` o `TIMEOUT` | Mensaje corto. |

**Reglas de transicion (`TASK_STATUS_TRANSITIONS`):**

- `DISPATCHED -> IN_PROGRESS | COMPLETED | FAILED | TIMEOUT`
- `IN_PROGRESS -> COMPLETED | FAILED | TIMEOUT`
- `FAILED -> QUEUED` solo automatico (retry), no via callback.

**Ejemplos de body:**

Progreso (opcional):
```json
{ "status": "IN_PROGRESS" }
```

Exito:
```json
{
  "status": "COMPLETED",
  "output_data": {
    "total_items": 2,
    "client_dna": "Bienvenidos a Casa Maruja...",
    "client_request": "Crea un post destacando el plato estrella...",
    "resources": ["https://i.pinimg.com/736x/00/2f/ef/002fefd0c200e93fd65f823cac70ed05.jpg"]
  }
}
```

Error recuperable o no:
```json
{
  "status": "FAILED",
  "error_message": "OpenAI: rate limit exceeded after 3 retries"
}
```

**Respuesta (status 200):** `TaskCallbackResponse`

```json
{
  "task_id": "<uuid>",
  "job_id": "<uuid>",
  "status": "COMPLETED",
  "job_status": "IN_PROGRESS"
}
```

Codigos de error:
- **404** si `task_id` no existe.
- **409** si la transicion no es valida (ej. callback a task ya `COMPLETED`).
- **422** si hay `output_schema` y `output_data` no valida (cuando se active).

**Retries orquestados:** si el agente reporta `FAILED` o `TIMEOUT` y la task tiene `retry_count < max_retries`, el orquestador **vuelve a despachar** automaticamente con backoff exponencial (30s, 60s, 120s...). El agente no programa retries.

**Idempotencia del callback:** si el agente envia el mismo callback dos veces con el mismo estado terminal, el segundo recibe **409** por transicion invalida. Disenar el agente para evitar callbacks duplicados.

---

## 5. Contrato D - Orquestador -> Iniciador (notificacion terminal)

Solo se activa si el job se creo con `initiator_callback_url`.

Metodo: `POST {initiator_callback_url}`

Headers:
- `Content-Type: application/json`

Body fijo:

```json
{
  "event": "job_terminal",
  "job_id": "<uuid>",
  "action_code": "create_ig_post",
  "status": "COMPLETED",
  "correlation_id": "ig-20260420-007",
  "output_data": {
    "schema": "ig_composed.v1",
    "slots": [ { "index": 1, "image_url": "https://cdn.example/a.jpg", "caption": "..." } ]
  },
  "error_message": null,
  "gate_responses": {
    "brief": { "passed": true, "reason": "ok", "status_code": 200, "response": { "...": "..." } }
  },
  "sequence_responses": {
    "post_planner":   { "status": "COMPLETED", "output_data": { "...": "..." }, "sequence_order": 1 },
    "copy_generator": { "status": "COMPLETED", "output_data": { "...": "..." }, "sequence_order": 2 },
    "content_factory":{ "status": "COMPLETED", "output_data": { "...": "..." }, "sequence_order": 3 }
  },
  "tasks": [
    {
      "task_id": "<uuid>",
      "status": "COMPLETED",
      "agent_id": "<uuid>",
      "sequence_order": 3,
      "step_code": "content_factory",
      "is_mandatory": true,
      "retry_count": 0,
      "error_details": null
    }
  ]
}
```

Notas:
- `status` puede ser `COMPLETED`, `FAILED` o `TIMEOUT`.
- `output_data` aqui es el output del **ultimo task** con output_data. Para agarrar outputs intermedios concretos por paso, usar `sequence_responses[step_code].output_data` (o la tabla `job_step_outputs` en v2).
- El orquestador **no reintenta** esta notificacion si falla. Se registra warning. El iniciador que necesite garantias debe hacer **pull** con Contrato E.
- Timeout del POST: 10s.

**Respuesta esperada del iniciador:** cualquier 2xx. Se ignora el cuerpo.

---

## 6. Contrato E - Consulta de estado del job (pull)

Endpoint: `GET {API_BASE}/api/v1/jobs/{job_id}`

Auth: `X-API-Key`.

Respuesta - `JobDetailResponse`:

```json
{
  "job_id": "<uuid>",
  "action_code": "create_ig_post",
  "correlation_id": "ig-20260420-007",
  "status": "IN_PROGRESS",
  "initiator_service": "api",
  "overall_payload": {
    "action": "create_ig_post",
    "client_request": { "...": "..." },
    "context": { "...": "..." }
  },
  "priority": 0,
  "metadata": {
    "gate_responses": { "...": "..." },
    "sequence_responses": { "...": "..." }
  },
  "tasks": [
    {
      "task_id": "<uuid>",
      "agent_id": "<uuid>",
      "status": "COMPLETED",
      "retry_count": 0,
      "started_at": "...",
      "completed_at": "...",
      "error_details": null
    }
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

Tambien disponible:
- `GET /api/v1/jobs?status=PENDING&limit=50&offset=0`
- `GET /api/v1/jobs/execution-log?action_code=...&status=...`

---

## 7. Codigos de error HTTP (resumen)

| HTTP | Cuando | Excepcion del dominio |
|------|--------|-----------------------|
| 400 | Payload mal formado | validacion Pydantic |
| 401 | Falta/invalida `X-API-Key` | auth |
| 404 | `job_id` o `task_id` no existe | `JobNotFoundError`, `TaskNotFoundError` |
| 409 | Transicion invalida o `idempotency_key` duplicada | `InvalidStateTransitionError`, `DuplicateJobError` |
| 413 | Request > 1 MB | Pydantic validator |
| 422 | Prerequisites faltan, gates fallan, output_schema (v2) invalido | `MissingPrerequisitesError`, `ExecutionGatesFailedError`, `InvalidAgentOutputError` |
| 503 | Cola de encolado no configurada | `JobEnqueueNotConfiguredError` |

Detalle en `../flujos del sistema/catalogo-errores-http.md`.

---

## 8. Activar/desactivar info por paso (mecanismo corto)

El orquestador permite **decirle a cada paso** que partes del JSON necesita. Sin esta configuracion, el paso recibe **todo** (v1).

Tres perillas, todas en la tabla `agent_sequence` (la v2 añade columnas - ver ADR):

1. **`depends_on_gates text[]`** - lista blanca de `gate_code`. Solo esos aparecen en `action_execution_gates`. Vacio = todos (legacy).
2. **`depends_on_steps text[]`** - lista blanca de `step_code` previos. Solo esos aparecen en `agent_sequence.previous` (con su `output_data` embebido). Vacio = todos (legacy).
3. **`previous_projection jsonb`** y **`gates_projection jsonb`** - por cada `step_code` / `gate_code`, lista de rutas a conservar del `output_data` / `response`. NULL = objeto completo.

Ejemplo practico, para el paso final de Instagram (`content_factory`):

```sql
UPDATE agent_sequence
SET
  depends_on_gates = '{}',                              -- no necesita gates aqui
  depends_on_steps = ARRAY['copy_generator'],            -- solo el copy generator
  previous_projection = '{"copy_generator": ["client_dna","client_request","resources"]}'
WHERE step_code = 'content_factory';
```

Resultado: el agente recibe en `agent_sequence.previous.copy_generator.output_data` solo `client_dna`, `client_request` y `resources`. Sin `brief`, sin `image_catalog`, sin `post_planner`, sin `total_items`.

**Politica operativa recomendada:**
- Empezar con `depends_on_*` vacios (comportamiento legacy).
- Al agregar un paso nuevo, declarar explicitamente sus dependencias antes de activarlo.
- Las proyecciones solo se usan cuando el `output_data` del predecesor es grande o tiene campos sensibles no necesarios.

---

## 9. Checklists para desarrollador

### Desarrollador de un nuevo **agente**

- [ ] Tu agente expone `POST /tasks` y acepta el body de la seccion 3.
- [ ] Parsea `task_id`, `job_id`, `correlation_id`, `callback_url` y los propaga en logs.
- [ ] Responde **2xx rapido** al dispatch (solo acepta trabajo). El trabajo real va en background.
- [ ] Al terminar, llama `PATCH {callback_url}` con el contrato de seccion 4.
- [ ] En `COMPLETED`, el `output_data` respeta el `output_schema` que te asignaron.
- [ ] En `FAILED/TIMEOUT`, incluye `error_message` util (sin PII).
- [ ] NO programes reintentos propios si esperas que el orquestador retry. Reporta `FAILED` y listo.
- [ ] NO dependas de campos que no esten en `depends_on_*` (v2) - el orquestador podria filtrarlos.
- [ ] Registra idempotencia interna: si recibes el mismo `task_id` dos veces, ignora el segundo.

### Desarrollador del **iniciador** (conversacional/front)

- [ ] Envia `POST /api/v1/jobs` con el body de la seccion 2.
- [ ] Usa `idempotency_key` si el usuario puede mandar el mismo pedido dos veces.
- [ ] Guarda `correlation_id` de la respuesta 202 para debugging cruzado.
- [ ] Dos opciones de cierre (puede combinarlas):
  - **Push**: provee `initiator_callback_url` y recibe Contrato D cuando termine.
  - **Pull**: polling a `GET /api/v1/jobs/{job_id}` (Contrato E) hasta `status.is_terminal`.
- [ ] Maneja 409 por idempotency_key duplicada.
- [ ] Maneja 422: recibes detalle de prerequisitos o gates fallados.

### Dueno de una **`Action`** (quien configura el flujo)

- [ ] Registra `action_catalog` con `action_code` estable.
- [ ] Configura `action_prerequisites` para bloquear entradas invalidas rapido.
- [ ] Configura `action_execution_gates` para datos externos antes del primer agente.
- [ ] Configura `agent_sequence` con `sort_order` explicito.
- [ ] (v2) Configura `depends_on_gates` y `depends_on_steps` por paso.
- [ ] (v2) Declara `output_schema` por paso cuando el agente tenga contrato estable.

---

## 10. Apendice - Headers, auth, idempotencia, correlacion

- **API_BASE**: configurable en deploy. Ej: `https://d1lwu6lioovdrb.cloudfront.net`.
- **Auth**:
  - Iniciadores y consola: `X-API-Key` por servicio (rotable).
  - Agentes al llamar callback: `X-API-Key` de agente.
  - Orquestador al llamar al agente: `Authorization: Bearer <agents.auth_token>` si el agente lo registro.
- **Idempotencia del alta de job**: `idempotency_key` unico en `jobs`. Reintentar con la misma -> 409.
- **Correlacion**: `correlation_id` atraviesa todo (request -> SQS -> worker -> dispatch -> callback -> notificacion). Si el iniciador no lo manda, el orquestador genera uno y lo devuelve.
- **Timeouts relevantes**:
  - Alta HTTP (`POST /jobs`): respuesta 202 inmediata tras encolar.
  - Dispatch al agente: 10s para 2xx.
  - Gate HTTP: 10s.
  - Notificacion al iniciador: 10s, sin retry.
  - Timeout de ejecucion del agente: `timeout_seconds` del paso o del agente; si se excede, `TimeoutSweeper` marca `TIMEOUT` y aplica retry/block.
- **Payload maximo en `POST /jobs`**: 1 MiB (`MAX_PAYLOAD_BYTES = 1_048_576`).

---

## Referencias internas

- Flujo completo: `flujo-router-prerequisitos-gates-secuencia.md`.
- Contrato detallado del dispatch: `contrato-payload-dispatch-agente.md`.
- Ejemplo canonico: `ejemplo-json-body-agente-y-gates.md`.
- Cambios planificados (dependencias, proyecciones): `adr-payload-incremental-y-evolucion.md`.
- Catalogo de errores HTTP: `../flujos del sistema/catalogo-errores-http.md`.
- Modelo de datos: `modelo-de-datos-erd.md`.