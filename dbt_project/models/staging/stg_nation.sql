with source as (

    select * from {{ source('tpch', 'nation') }}

),

renamed as (

    select
        n_nationkey as nation_id,
        n_name      as nation_name,
        n_regionkey as region_id,
        n_comment   as nation_comment

    from source

)

select * from renamed
