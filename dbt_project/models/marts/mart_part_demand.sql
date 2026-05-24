with lineitems as (

    select * from {{ ref('stg_lineitem') }}

),

parts as (

    select * from {{ ref('stg_part') }}

),

orders as (

    select order_id, order_date from {{ ref('stg_orders') }}

),

part_demand as (

    select
        p.part_id,
        p.part_name,
        p.part_type,
        p.brand,
        p.manufacturer,
        p.part_size,
        p.retail_price,
        o.order_date,
        date_trunc('month', o.order_date)                                     as demand_month,
        count(distinct li.order_id)                                           as order_count,
        count(*)                                                              as line_item_count,
        sum(li.quantity)                                                      as total_quantity_demanded,
        sum({{ net_revenue('li.extended_price', 'li.discount', 'li.tax') }}) as total_net_revenue,
        avg(li.discount)                                                      as avg_discount_rate,
        avg(li.extended_price)                                                as avg_line_price

    from parts p
    inner join lineitems li
        on p.part_id = li.part_id
    inner join orders o
        on li.order_id = o.order_id

    group by 1, 2, 3, 4, 5, 6, 7, 8, 9

)

select * from part_demand
