with customers as (

    select * from {{ ref('stg_customer') }}

),

nations as (

    select * from {{ ref('stg_nation') }}

),

regions as (

    select * from {{ ref('stg_region') }}

),

orders_lineitems as (

    select * from {{ ref('int_orders_lineitems') }}

),

customer_enriched as (

    select
        c.customer_id,
        c.customer_name,
        c.market_segment,
        c.account_balance,
        n.nation_name,
        r.region_name

    from customers c
    left join nations n
        on c.nation_id = n.nation_id
    left join regions r
        on n.region_id = r.region_id

),

final as (

    select
        ce.customer_id,
        ce.customer_name,
        ce.market_segment,
        ce.account_balance,
        ce.nation_name,
        ce.region_name,
        ol.order_id,
        ol.order_status,
        ol.order_date,
        ol.order_priority,
        ol.line_number,
        ol.part_id,
        ol.supplier_id,
        ol.quantity,
        ol.extended_price,
        ol.discount,
        ol.tax,
        ol.net_revenue,
        ol.ship_date,
        ol.ship_mode

    from customer_enriched ce
    inner join orders_lineitems ol
        on ce.customer_id = ol.customer_id

)

select * from final
