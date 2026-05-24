with orders_lineitems as (

    select * from {{ ref('int_orders_lineitems') }}

),

daily_revenue as (

    select
        order_date,
        date_trunc('month', order_date)  as revenue_month,
        date_trunc('year', order_date)   as revenue_year,
        order_status                     as order_status_label,
        count(distinct order_id)         as order_count,
        count(*)                         as line_item_count,
        sum(quantity)                    as total_quantity,
        sum(extended_price)              as gross_revenue,
        sum(net_revenue)                 as net_revenue,
        sum(extended_price * discount)   as total_discount_amount,
        avg(discount)                    as avg_discount_rate

    from orders_lineitems
    group by 1, 2, 3, 4

)

select * from daily_revenue
