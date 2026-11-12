"""Lakeflow pipeline: the datasets of the orders contract, with the contract's rules.

Reads samples.tpch.orders (the `source_table` variable) and renames its columns. The column names
must match the contract, or publish fails on schema drift. The rules are not written here: the deploy
copies the merged contract and contract_expectations.py (from dp-integration) next to this file,
and `rules.apply` turns the contract's row-level rules into expectations on each dataset.
"""
from pyspark import pipelines as dp

from contract_expectations import ContractExpectations

rules = ContractExpectations.from_file(spark.conf.get("contract_path"))  # noqa: F821 (pipeline global)
SOURCE = spark.conf.get("source_table")  # noqa: F821


@dp.materialized_view(name="orders", table_properties=rules.table_properties())
@rules.apply("orders")
def orders():
    return spark.sql(f"""
        SELECT o_orderkey AS order_id, o_custkey AS customer_id, o_orderstatus AS order_status,
               CAST(o_totalprice AS DECIMAL(18,2)) AS total_price, o_orderdate AS order_date,
               o_orderpriority AS order_priority
        FROM {SOURCE}""")
