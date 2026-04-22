{
        "task_id": "a84af575-3c36-4672-8151-c6b747335fcd",
        "job_id": "0d75ec10-2642-4b9d-9deb-7d8388dfb594",
        "action_code": "edit_post",
        "action_id": "06e32aac-5dd2-4747-ae59-d0c6c26b4091",
        "correlation_id": "e2e-body-final-1776451754",
        "callback_url": "https://d1lwu6lioovdrb.cloudfront.net/api/v1/tasks/a84af575-3c36-4672-8151-c6b747335fcd/callback",
        "payload": {
            "client_request": {
                "description": "Validacion final completa + body final agente",
                "attachments": [],
            },
            "context": {
                "post_id": "post-body-final-001",
                "account_uuid": "8a095bf8-f9b7-47a5-9d4a-5933983ba95f",
                "platform": "instagram",
                "client_name": "Panaderia La Esperanza",
            },
            "action_execution_gates": {
                "brief": {
                    "passed": True,
                    "reason": "Account with brief retrieved successfully",
                    "status_code": 200,
                    "response": {
                        "status": "success",
                        "message": "Account with brief retrieved successfully",
                        "data": "<respuesta completa del brief (incluida en metadata.gate_responses, tamaño grande)>",
                    },
                }
            },
            "agent_sequence": {
                "current": {
                    "step_code": "content_factory_main",
                    "step_order": 10,
                    "task_id": "a84af575-3c36-4672-8151-c6b747335fcd",
                    "agent_id": "5a258d4e-b01d-48dc-95b1-0e67d81b7f4c",
                    "agent_name": "content_factory",
                    "endpoint": "https://webhook-dev.plinng.com/api/v3/tasks/tasks",
                    "http_method": "POST",
                    "is_mandatory": True,
                    "timeout_seconds": 60,
                    "retry_count": 0,
                },
                "previous": {},
            },
        },
    },
}