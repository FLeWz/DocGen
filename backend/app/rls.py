def tenant_rls(table: str) -> list[str]:
    """SQL for a standard tenant table (has org_id). Use in the table's migration."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} USING (org_id = app_uuid('app.org_id'))",
    ]
