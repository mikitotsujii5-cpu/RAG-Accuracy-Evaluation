-- Grants required by the Databricks App service principal.
-- Replace <APP_SERVICE_PRINCIPAL_CLIENT_ID> with the client ID generated for
-- the target App before running this file.

GRANT USE CATALOG ON CATALOG rag_accuracy_demo
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT USE SCHEMA ON SCHEMA rag_accuracy_demo.rag_accuracy
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT READ VOLUME, WRITE VOLUME
ON VOLUME rag_accuracy_demo.rag_accuracy.documents
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_projects
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_projects
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_project_members
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_project_members
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_model_catalog
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_model_defaults
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_document_registry
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_document_registry
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_vehicle_master
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_index_variants
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_index_variants
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_sessions
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_sessions
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_messages
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_messages
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_chat_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_prep_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_prep_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_parsed_v2
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_parsed_v2
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;
GRANT MODIFY ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_runs
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_results
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_eval_suggestions
TO `<APP_SERVICE_PRINCIPAL_CLIENT_ID>`;

-- Additional existing Indexes are registered as separate App resource
-- bindings. The App and Jobs do not create Indexes or grant Index permissions.
