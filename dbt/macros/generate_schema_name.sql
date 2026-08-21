{#
    dbt's default generate_schema_name combines a model's custom
    `+schema` config with the profile's base dataset (target.schema)
    as `<target.schema>_<custom_schema>` — e.g. base dataset `raw` +
    a model's `+schema: marts` becomes the dataset `raw_marts`, not
    the plain `marts` this whole pipeline expects (Terraform creates
    a `marts` dataset directly; quality/*.py and export_marts.py
    query `{project}.marts...` directly).

    This override makes `+schema` the ENTIRE dataset name regardless
    of the profile's base dataset, so `+schema: staging` /
    `intermediate` / `marts` in dbt_project.yml always produce exactly
    those dataset names — matching infrastructure/terraform/main.tf's
    `dataset_ids` and every hardcoded dataset reference elsewhere in
    the project.

    This is dbt's own documented fix for this exact situation:
    https://docs.getdbt.com/docs/build/custom-schemas
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- if custom_schema_name is none -%}

        {{ target.schema }}

    {%- else -%}

        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}
