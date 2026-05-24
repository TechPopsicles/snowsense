with orders as (

    select * from {{ ref('stg_orders') }}

),

lineitems as (

    select * from {{ ref('stg_lineitem') }}

),

joined as (

    select
        o.order_id,
        o.customer_id,
        o.order_status,
        o.order_date,
        o.order_priority,
        o.clerk,
        o.ship_priority,
        li.line_number,
        li.part_id,
        li.supplier_id,
        li.quantity,
        li.extended_price,
        li.discount,
        li.tax,
        {{ net_revenue('li.extended_price', 'li.discount', 'li.tax') }} as net_revenue,
        li.return_flag,
        li.line_status,
        li.ship_date,
        li.commit_date,
        li.receipt_date,
        li.ship_mode

    from orders o
    inner join lineitems li
        on o.order_id = li.order_id

)

select * from joined
