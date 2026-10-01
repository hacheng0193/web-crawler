import pandas as pd
from sqlalchemy import create_engine

engine = create_engine("postgresql+psycopg2://metric:metric@172.16.191.1:5433/metricdb")

tables = pd.read_sql("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name
""", engine)
print(tables)