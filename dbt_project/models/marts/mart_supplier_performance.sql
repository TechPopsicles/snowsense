with lineitems as (

    select * from {{ ref('stg_lineitem') }}

),

suppliers as (

    select * from {{ ref('stg_supplier') }}

),

nations as (

    select * from {{ ref('stg_nation') }}

),

regions as (

    select * from {{ ref('stg_region') }}

),

supplier_enriched as (

    select
        s.supplier_id,
        s.supplier_name,
        s.account_balance,
        n.nation_name,
        r.region_name

    from suppliers s
    left join nations n
        on s.nation_id = n.nation_id
    left join regions r
        on n.region_id = r.region_id

),

supplier_metrics as (

    select
        se.supplier_id,
        se.supplier_name,
        se.nation_name,
        se.region_name,
        se.account_balance,
        count(distinct li.order_id)                                           as orders_supplied,
        count(*)                                                              as line_items_supplied,
        sum(li.quantity)                                                      as total_quantity_supplied,
        sum({{ net_revenue('li.extended_price', 'li.discount', 'li.tax') }}) as total_net_revenue,
        avg(li.discount)                                                      as avg_discount_given,
        avg(datediff('day', li.ship_date, li.receipt_date))                   as avg_delivery_days,
        sum(case when li.return_flag = 'R' then 1 else 0 end)                as returned_items,
        sum(case when li.return_flag != 'R' then 1 else 0 end)               as fulfilled_items

    from supplier_enriched se
    inner join lineitems li
        on se.supplier_id = li.supplier_id

    group by 1, 2, 3, 4, 5

)

select
    *,
    round(
        returned_items / nullif(line_items_supplied, 0) * 100,
        2
    ) as return_rate_pct

from supplier_metrics
