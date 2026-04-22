# 05 — Terraform Reference

**Version:** 3.0  
**Last Updated:** 2026-04-22  
**Terraform:** `>= 1.6`  
**Provider AWS:** `>= 5.x`

---

## 1. Repository Structure

```
infraestructure/terraform/
├── bootstrap/
│   └── main.tf
├── modules/
│   ├── alb/
│   ├── ecr/
│   ├── ecs/
│   ├── iam/
│   ├── monitoring/
│   ├── secrets/
│   ├── rds/          # NEW
│   └── bastion/      # NEW
└── environments/
    ├── dev/
    └── prod/
```

---

## 2. Backend & Bootstrap

El backend remoto (S3 + DynamoDB lock) se mantiene.  
`bootstrap/main.tf` ahora también administra:
- Permission boundary extendida para SSM/EC2Messages/RDS/KMS.
- Políticas del rol OIDC `github_actions` para RDS, KMS, bastion, ECS run-task migrator y EventBridge scheduler.

---

## 3. Providers

Se mantiene el provider AWS con tags por entorno y el provider `random` para generación de credenciales de DB.

---

## 4. Root Variables (nuevas relevantes)

Nuevas variables de entorno:
- `db_subnet_ids` (`list(string)`, default `[]`, fallback a `private_subnet_ids`)
- `enable_bastion` (`bool`)
- `db_pool_size` (`number`)
- `db_pool_max_overflow` (`number`)
- `db_allocated_storage`, `db_instance_class`, `db_multi_az` (si se parametriza por entorno)

Además de las existentes (`vpc_id`, subnets, `certificate_arn`, secretos, etc.).

---

## 5. Module: RDS (nuevo)

Ruta: `infraestructure/terraform/modules/rds/`

Recursos principales:
- `aws_kms_key` + alias `alias/marketer-{env}-rds`
- `aws_db_subnet_group`
- `aws_db_parameter_group` (`postgres17`) con:
  - `rds.force_ssl=1`
  - `shared_preload_libraries=pg_stat_statements,pgaudit`
  - `pgaudit.log=ddl,role`
  - `max_connections` por entorno
- `aws_security_group` (`5432` solo desde `sg-ecs` y `sg-bastion`)
- `random_password`
- `aws_secretsmanager_secret` `marketer/{env}/database-url` con JSON:
  - `username`, `password`, `host`, `port`, `dbname`, `url`
- `aws_iam_role` enhanced monitoring
- `aws_db_instance` (`engine=postgres`, `engine_version=17.9`, `gp3`, Multi-AZ prod)
- `aws_cloudwatch_log_group` export de logs de PostgreSQL

Outputs:
- `instance_id`, `endpoint`, `port`, `security_group_id`
- `database_url_secret_arn`
- `kms_key_arn`

---

## 6. Module: Bastion (nuevo)

Ruta: `infraestructure/terraform/modules/bastion/`

Recursos principales:
- `aws_security_group` sin ingress, egress `443` y `5432` a RDS
- IAM role + instance profile:
  - `AmazonSSMManagedInstanceCore`
  - lectura restringida de `database-url`
- `aws_instance` AL2023 ARM (`t4g.nano`), IMDSv2 requerido
- KMS para EBS
- `user_data` con cliente postgres
- scheduler opcional (EventBridge) para auto-stop en dev

Outputs:
- `instance_id`
- `security_group_id`
- comando `ssm_start_command`

---

## 7. Module: IAM (actualizado)

`modules/iam` mantiene roles de ECS service y execution, y agrega:
- `kms:Decrypt` sobre CMK de RDS (para leer secret cifrado)
- confirmación de acceso a secret `marketer/{env}/database-url`

Separación de responsabilidades:
- `task_execution`: pull + secrets + decrypt
- `task` (runtime): permisos mínimos
- `migrator-task`: en módulo ECS (rol separado)
- `bastion`: en módulo bastion

---

## 8. Module: Secrets (actualizado)

Además de `gemini/inbound/callback`, se añade secret generado por RDS:
- `marketer/{env}/database-url`

Shape:
```json
{
  "username": "marketer",
  "password": "...",
  "host": "...",
  "port": 5432,
  "dbname": "marketer",
  "url": "postgresql+asyncpg://marketer:...@...:5432/marketer?ssl=require"
}
```

El container consume `DATABASE_URL` con selector JSON key:
`"${secret_arn}:url::"`.

---

## 9. Module: ALB

Sin cambios estructurales.  
Se aprovecha `target_group_arn_suffix` y `alb_arn_suffix` para escalado `ALBRequestCountPerTarget` y dashboards.

---

## 10. Module: ECS (actualizado)

Cambios clave:
- Regla de egress `5432` de `sg-ecs` a `sg-rds`.
- Inyección de `DATABASE_URL` desde secret JSON key.
- Variables de pool (`DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`).
- Nueva task definition `migrator` (`alembic upgrade head`).
- Rol `migrator-task` separado.
- Escalado híbrido:
  - CPU target tracking
  - Memory target tracking
  - `ALBRequestCountPerTarget`
  - step scaling por CPU burst
- `deployment_minimum_healthy_percent=100`
- `deployment_maximum_percent=200`

Outputs nuevos:
- `migrator_task_definition_arn`
- `migrator_log_group_name`

---

## 11. Module: Monitoring (actualizado)

Nuevos recursos:
- Alarmas RDS:
  - CPU
  - FreeableMemory
  - FreeStorageSpace
  - DatabaseConnections
  - Read/Write latency
- `aws_db_event_subscription`
- alarma ECS de memoria
- widgets RDS en dashboard

Variables nuevas:
- `rds_instance_id`
- `rds_enabled`

---

## 12. Environment Wiring (prod/dev)

### Prod

`environments/prod/main.tf` incorpora:
- `module "rds"`:
  - `instance_class="db.t4g.small"`
  - `multi_az=true`
  - `backup_retention_days=14`
  - `deletion_protection=true`
- `module "bastion"`:
  - `enabled=var.enable_bastion`
  - `auto_stop_enabled=false`
- `module "ecs"` recibe:
  - `rds_security_group_id`
  - `database_url_secret_arn`
  - datos de ALB suffix para scaling policy de requests
- `module "monitoring"` recibe:
  - `rds_instance_id`
  - `rds_enabled=true`

### Dev

`environments/dev/main.tf` incorpora:
- `instance_class="db.t4g.micro"`
- `multi_az=false`
- `backup_retention_days=7`
- `deletion_protection=false`
- bastion habilitado con `auto_stop_enabled=true`

---

## 13. Migrations in CI/CD (nuevo)

`deploy.yml` agrega job `migrate`:
1. `aws ecs run-task` sobre `marketer-{env}-migrator`
2. esperar `tasks-stopped`
3. validar `containers[0].exitCode == 0`
4. `deploy` depende de `migrate`

Si falla migración, no hay rollout.

---

## 14. Connecting to DB via SSM (nuevo)

```bash
aws ssm start-session \
  --target <bastion_instance_id> \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<rds_endpoint>"],"portNumber":["5432"],"localPortNumber":["15432"]}'
```

Luego:
```bash
aws secretsmanager get-secret-value \
  --secret-id marketer/prod/database-url \
  --query SecretString --output text | jq -r .url
```

---

## 15. Terraform Workflows

### `terraform.yml`

Además de `fmt/validate/plan/apply`, se recomienda:
- `tflint`
- `checkov` en modo inicial `soft_fail`

### `ci.yml` (nuevo)

Incluye:
- `ruff check .`
- `ruff format --check .`
- `mypy src tests`
- `pytest` con servicio `postgres:17-alpine`
- `alembic upgrade head`
- chequeo de linaje de migrations

---

## 16. Secrets Rotation

Se mantiene rotación manual inmediata para secretos existentes.  
Para `database-url`, rotación automática queda como fase 2 (Lambda de rotación asistida).

---

## 17. Destroy & Safety

Reglas:
- no destruir prod sin change approval.
- en prod, desactivar `deletion_protection` y confirmar final snapshot antes de destroy.
