with customer_orders as (

    select * from {{ ref('int_customer_orders') }}

),

customer_lifetime as (

    select
        customer_id,
        customer_name,
        market_segment,
        nation_name,
        region_name,
        account_balance,
        min(order_date)                                         as first_order_date,
        max(order_date)                                         as last_order_date,
        count(distinct order_id)                                as total_orders,
        sum(net_revenue)                                        as customer_ltv,
        avg(net_revenue)                                        as avg_revenue_per_line,
        sum(net_revenue) / nullif(count(distinct order_id), 0)  as avg_order_value,
        datediff('day', min(order_date), max(order_date))       as customer_tenure_days

    from customer_orders
    group by 1, 2, 3, 4, 5, 6

),

with_tiers as (

    select
        *,
        case
            when customer_ltv >= 500000 then 'Platinum'
            when customer_ltv >= 250000 then 'Gold'
            when customer_ltv >= 100000 then 'Silver'
            else 'Bronze'
        end as value_tier

    from customer_lifetime

)

select * from with_tiers
