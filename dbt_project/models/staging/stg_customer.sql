with source as (

    select * from {{ source('tpch', 'customer') }}

),

renamed as (

    select
        c_custkey    as customer_id,
        c_name       as customer_name,
        c_address    as customer_address,
        c_nationkey  as nation_id,
        c_phone      as phone,
        c_acctbal    as account_balance,
        c_mktsegment as market_segment,
        c_comment    as customer_comment

    from source

)

select * from renamed
