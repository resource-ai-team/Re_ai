"""PostgreSQL snapshots and committed execution claims, including failed operations."""

from copy import deepcopy
from threading import RLock
from uuid import uuid4

from psycopg.types.json import Jsonb

from resource_ai.knowledge_graph.ontology import digest


class WorkflowError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class PostgresCaseStore:
    def __init__(self, database):
        self.database = database

    def claim_provider(self, scope):
        # Shared across API workers; commit BEFORE the provider call.
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                """INSERT INTO workflow_provider_slots(scope,last_started,active_until)
                   VALUES(%s,now(),now()+interval '180 seconds')
                   ON CONFLICT(scope) DO UPDATE SET last_started=now(),
                       active_until=now()+interval '180 seconds'
                   WHERE workflow_provider_slots.last_started < now()-interval '60 seconds'
                     AND workflow_provider_slots.active_until < now()
                   RETURNING scope""",
                (scope,),
            ).fetchone()
        return row is not None

    def release_provider(self, scope):
        with self.database.connect(register_vector_types=False) as c:
            c.execute(
                "UPDATE workflow_provider_slots SET active_until=now() WHERE scope=%s", (scope,)
            )

    def list_cases(self, tenant):
        with self.database.connect(register_vector_types=False) as c:
            rows = c.execute(
                """SELECT case_id,revision,updated_at,state->>'status' FROM workflow_cases
                   WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT 30""",
                (tenant,),
            ).fetchall()
        return [
            {"case_id": str(r[0]), "revision": r[1], "updated_at": r[2].isoformat(), "status": r[3]}
            for r in rows
        ]

    def claim_ocr(self, tenant, case_id, checksum):
        self.get(tenant, case_id)
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                """INSERT INTO workflow_ocr_claims(tenant_id,case_id,document_sha256)
                               VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING case_id""",
                (tenant, case_id, checksum),
            ).fetchone()
        return row is not None

    def create(self, tenant, state):
        case_id = str(uuid4())
        state = {**state, "case_id": case_id, "revision": 1}
        with self.database.connect(register_vector_types=False) as c:
            c.execute(
                "INSERT INTO workflow_cases(tenant_id,case_id,revision,state) VALUES(%s,%s,1,%s)",
                (tenant, case_id, Jsonb(state)),
            )
            self._snapshot(c, tenant, state)
        return state

    @staticmethod
    def _snapshot(c, tenant, state):
        c.execute(
            """INSERT INTO workflow_revisions(tenant_id,case_id,revision,state,state_sha256)
                     VALUES(%s,%s,%s,%s,%s)""",
            (tenant, state["case_id"], state["revision"], Jsonb(state), digest(state)),
        )

    def get(self, tenant, case_id):
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                "SELECT state, active_operation FROM workflow_cases WHERE tenant_id=%s AND case_id=%s",
                (tenant, case_id),
            ).fetchone()
        if row is None:
            raise WorkflowError("case-not-found", 404)
        return {**row[0], "active_operation": str(row[1]) if row[1] else None}

    def history(self, tenant, case_id):
        self.get(tenant, case_id)
        with self.database.connect(register_vector_types=False) as c:
            rows = c.execute(
                """SELECT revision,state_sha256,created_at FROM workflow_revisions
                                WHERE tenant_id=%s AND case_id=%s ORDER BY revision""",
                (tenant, case_id),
            ).fetchall()
        return [
            {"revision": r[0], "state_sha256": r[1], "created_at": r[2].isoformat()} for r in rows
        ]

    def reserve(self, tenant, case_id, revision, key, payload):
        checksum, op_id = digest(payload), str(uuid4())
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                """SELECT state,revision,active_operation FROM workflow_cases
                                WHERE tenant_id=%s AND case_id=%s FOR UPDATE""",
                (tenant, case_id),
            ).fetchone()
            if row is None:
                raise WorkflowError("case-not-found", 404)
            previous = c.execute(
                """SELECT payload_sha256,status,result FROM workflow_operations
                                     WHERE tenant_id=%s AND case_id=%s AND idempotency_key=%s""",
                (tenant, case_id, key),
            ).fetchone()
            if previous:
                if previous[0] != checksum:
                    raise WorkflowError("idempotency-key-reused-with-different-input")
                if previous[1] != "completed":
                    raise WorkflowError("operation-" + previous[1] + "-no-automatic-retry")
                return None, previous[2], True
            if row[1] != revision:
                raise WorkflowError("revision-conflict")
            if row[2]:
                raise WorkflowError("case-operation-in-progress")
            c.execute(
                """INSERT INTO workflow_operations(operation_id,tenant_id,case_id,
                          idempotency_key,payload_sha256,status) VALUES(%s,%s,%s,%s,%s,'running')""",
                (op_id, tenant, case_id, key, checksum),
            )
            c.execute(
                "UPDATE workflow_cases SET active_operation=%s WHERE tenant_id=%s AND case_id=%s",
                (op_id, tenant, case_id),
            )
        # Claim is committed BEFORE any external request. A crash cannot invisibly repeat a call.
        return op_id, row[0], False

    def complete(self, tenant, op_id, state):
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                """SELECT revision FROM workflow_cases WHERE tenant_id=%s
                                AND case_id=%s AND active_operation=%s FOR UPDATE""",
                (tenant, state["case_id"], op_id),
            ).fetchone()
            if row is None or row[0] != state["revision"]:
                raise WorkflowError("operation-claim-lost")
            state = {**state, "revision": state["revision"] + 1}
            c.execute(
                """UPDATE workflow_cases SET state=%s,revision=%s,active_operation=NULL,updated_at=now()
                         WHERE tenant_id=%s AND case_id=%s""",
                (Jsonb(state), state["revision"], tenant, state["case_id"]),
            )
            self._snapshot(c, tenant, state)
            c.execute(
                """UPDATE workflow_operations SET status='completed',result=%s,finished_at=now()
                         WHERE operation_id=%s AND tenant_id=%s""",
                (Jsonb(state), op_id, tenant),
            )
        return state

    def fail(self, tenant, case_id, op_id):
        with self.database.connect(register_vector_types=False) as c:
            c.execute(
                """UPDATE workflow_operations SET status='failed',error_code='execution-failed',finished_at=now()
                         WHERE operation_id=%s AND tenant_id=%s AND status='running'""",
                (op_id, tenant),
            )
            c.execute(
                """UPDATE workflow_cases SET active_operation=NULL
                         WHERE tenant_id=%s AND case_id=%s AND active_operation=%s""",
                (tenant, case_id, op_id),
            )

    def put_document(self, tenant, case_id, document_id, media_type, content, sha256):
        with self.database.connect(register_vector_types=False) as c:
            c.execute(
                """INSERT INTO workflow_documents(tenant_id,case_id,document_id,media_type,content,sha256)
                         VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (tenant, case_id, document_id, media_type, content, sha256),
            )

    def get_document(self, tenant, case_id, document_id):
        self.get(tenant, case_id)
        with self.database.connect(register_vector_types=False) as c:
            row = c.execute(
                """SELECT content,media_type FROM workflow_documents
                                WHERE tenant_id=%s AND case_id=%s AND document_id=%s""",
                (tenant, case_id, document_id),
            ).fetchone()
        if row is None:
            raise WorkflowError("document-not-found", 404)
        return bytes(row[0]), row[1]


class MemoryCaseStore:
    """Tests only; never selected as a silent production fallback."""

    def __init__(self):
        self.rows, self.operations, self.snapshots, self.documents = {}, {}, {}, {}
        self.ocr_claims = set()
        self.lock = RLock()

    def claim_ocr(self, tenant, case_id, checksum):
        with self.lock:
            self.get(tenant, case_id)
            key = (tenant, case_id, checksum)
            if key in self.ocr_claims:
                return False
            self.ocr_claims.add(key)
            return True

    def list_cases(self, tenant):
        return [
            {
                "case_id": case,
                "revision": state["revision"],
                "status": state.get("status"),
                "updated_at": "test-only",
            }
            for (owner, case), state in self.rows.items()
            if owner == tenant
        ][-30:]

    def create(self, tenant, state):
        with self.lock:
            state = {**deepcopy(state), "case_id": str(uuid4()), "revision": 1}
            self.rows[tenant, state["case_id"]] = state
            self.snapshots[tenant, state["case_id"]] = [deepcopy(state)]
            return deepcopy(state)

    def get(self, tenant, case_id):
        if (tenant, case_id) not in self.rows:
            raise WorkflowError("case-not-found", 404)
        return deepcopy(self.rows[tenant, case_id])

    def history(self, tenant, case_id):
        self.get(tenant, case_id)
        return [
            {"revision": x["revision"], "state_sha256": digest(x)}
            for x in self.snapshots[tenant, case_id]
        ]

    def reserve(self, tenant, case_id, revision, key, payload):
        with self.lock:
            state = self.get(tenant, case_id)
            previous = self.operations.get((tenant, case_id, key))
            if previous:
                if previous["checksum"] != digest(payload):
                    raise WorkflowError("idempotency-key-reused-with-different-input")
                if previous["status"] != "completed":
                    raise WorkflowError("operation-" + previous["status"] + "-no-automatic-retry")
                return None, deepcopy(previous["result"]), True
            if state["revision"] != revision:
                raise WorkflowError("revision-conflict")
            if state.get("active_operation"):
                raise WorkflowError("case-operation-in-progress")
            op_id = str(uuid4())
            self.operations[tenant, case_id, key] = {
                "checksum": digest(payload),
                "id": op_id,
                "status": "running",
            }
            self.rows[tenant, case_id]["active_operation"] = op_id
            return op_id, state, False

    def complete(self, tenant, op_id, state):
        with self.lock:
            row = self.rows[tenant, state["case_id"]]
            if row.get("active_operation") != op_id or row["revision"] != state["revision"]:
                raise WorkflowError("operation-claim-lost")
            state = {**deepcopy(state), "revision": state["revision"] + 1, "active_operation": None}
            self.rows[tenant, state["case_id"]] = state
            self.snapshots[tenant, state["case_id"]].append(deepcopy(state))
            for (owner, _, _), op in self.operations.items():
                if owner == tenant and op["id"] == op_id:
                    op.update(status="completed", result=deepcopy(state))
            return deepcopy(state)

    def fail(self, tenant, case_id, op_id):
        with self.lock:
            self.rows[tenant, case_id]["active_operation"] = None
            for (owner, _, _), op in self.operations.items():
                if owner == tenant and op["id"] == op_id:
                    op["status"] = "failed"

    def put_document(self, tenant, case_id, document_id, media_type, content, sha256):
        self.documents[tenant, case_id, document_id] = (content, media_type)

    def get_document(self, tenant, case_id, document_id):
        self.get(tenant, case_id)
        try:
            return self.documents[tenant, case_id, document_id]
        except KeyError:
            raise WorkflowError("document-not-found", 404) from None
