# Databricks Access Grants Needed

The control plane can authenticate to workspace `2125342406251669` and can execute SQL on warehouse `2d31dd70fb84fd28` (`Starter Endpoint`).

Current service principal/user observed by the connectivity check:

`3c420467-5f20-431b-b1b4-be6d8738b05d`

## Current blocker

The configured service principal does not have Unity Catalog access to the source catalogs needed for the U.S. News survey:

- `bronze`
- `production`

Observed errors:

- `PERMISSION_DENIED: User does not have USE CATALOG on Catalog 'bronze'.`
- `PERMISSION_DENIED: User does not have USE CATALOG on Catalog 'production'.`
- `PERMISSION_DENIED: User does not have BROWSE on Catalog 'production'.`

## Minimum grants to request

Ask a Databricks workspace/admin or catalog owner to grant the service principal:

```sql
GRANT USE CATALOG ON CATALOG bronze TO `<service-principal-or-group>`;
GRANT USE SCHEMA ON SCHEMA bronze.cms TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_aid_atrbt TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_awards TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_awd_per TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_awrd_actv TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_awrd_disb TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE bronze.cms.ps_stdnt_fa_term TO `<service-principal-or-group>`;

GRANT USE CATALOG ON CATALOG production TO `<service-principal-or-group>`;
GRANT USE SCHEMA ON SCHEMA production.silver TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.erss TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.ersa TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.serss TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.ersd TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.ersd_supplemental TO `<service-principal-or-group>`;
GRANT SELECT ON TABLE production.silver.ira_faculty TO `<service-principal-or-group>`;
```

Also confirm the service principal has **Can Use** permission on SQL warehouse `2d31dd70fb84fd28`.

## Validation command

After grants are applied:

```bash
cd survey-automation
.venv/bin/python infra/scripts/probe_databricks_sources.py
```

The script only prints table accessibility, column names, and row counts. It does not print student-level sample rows.
