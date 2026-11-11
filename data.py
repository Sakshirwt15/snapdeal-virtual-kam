"""Mock seller data for the Virtual KAM demo.

Numbers in SELLER_METRICS come straight from the assignment brief.
Everything else (SKU list, cancellation reasons, visibility, competitor prices)
is an ASSUMPTION made up for the demo and is labelled as such in the README.
"""

SELLER = {
    "seller_id": "SD-48213",
    "name": "Rajesh Kumar",
    "store": "Rajesh Mobile & Accessories",
    "category": "Mobile accessories",
    "city": "Jaipur",
    "phone": "+91-98xxxxxx21",
}

# --- From the brief -------------------------------------------------------
SELLER_METRICS = {
    "last_month": {"sales": 200000, "orders": 400, "cancellation_rate": 8, "asp": 500, "oos_products": 5},
    "this_month": {"sales": 135000, "orders": 270, "cancellation_rate": 14, "asp": 500, "oos_products": 18},
}

# --- ASSUMPTIONS (not in the brief) ----------------------------------------
# 18 currently out-of-stock SKUs. units_last_month = how many each sold last month.
OOS_PRODUCTS = [
    {"sku": "SD-TWS-01", "name": "Bluetooth Earbuds TWS (Black)", "price": 799, "units_last_month": 18, "oos_since_days": 9},
    {"sku": "SD-CHG-02", "name": "20W Fast Charger", "price": 449, "units_last_month": 16, "oos_since_days": 12},
    {"sku": "SD-CBL-03", "name": "Type-C Cable 1m (Braided)", "price": 199, "units_last_month": 15, "oos_since_days": 7},
    {"sku": "SD-PBK-04", "name": "10000mAh Power Bank", "price": 899, "units_last_month": 11, "oos_since_days": 14},
    {"sku": "SD-CAS-05", "name": "Silicone Back Case (Galaxy M-series)", "price": 249, "units_last_month": 10, "oos_since_days": 5},
    {"sku": "SD-NBD-06", "name": "Neckband Bluetooth Headset", "price": 699, "units_last_month": 9, "oos_since_days": 11},
    {"sku": "SD-TGL-07", "name": "Tempered Glass (Pack of 2)", "price": 149, "units_last_month": 9, "oos_since_days": 4},
    {"sku": "SD-CAR-08", "name": "Car Phone Holder", "price": 349, "units_last_month": 7, "oos_since_days": 20},
    {"sku": "SD-SPK-09", "name": "Mini Bluetooth Speaker", "price": 999, "units_last_month": 6, "oos_since_days": 16},
    {"sku": "SD-OTG-10", "name": "OTG Adapter Type-C", "price": 99, "units_last_month": 5, "oos_since_days": 25},
    {"sku": "SD-SMW-11", "name": "Smart Watch Strap (Silicone)", "price": 299, "units_last_month": 4, "oos_since_days": 8},
    {"sku": "SD-RNG-12", "name": "Selfie Ring Light", "price": 549, "units_last_month": 4, "oos_since_days": 18},
    {"sku": "SD-MCH-13", "name": "Micro-USB Cable 1m", "price": 129, "units_last_month": 3, "oos_since_days": 22},
    {"sku": "SD-EAR-14", "name": "Wired Earphones with Mic", "price": 249, "units_last_month": 3, "oos_since_days": 6},
    {"sku": "SD-STD-15", "name": "Foldable Mobile Stand", "price": 199, "units_last_month": 2, "oos_since_days": 30},
    {"sku": "SD-AUX-16", "name": "AUX Cable 3.5mm", "price": 99, "units_last_month": 2, "oos_since_days": 28},
    {"sku": "SD-PCH-17", "name": "Waterproof Phone Pouch", "price": 179, "units_last_month": 1, "oos_since_days": 35},
    {"sku": "SD-LNK-18", "name": "Lightning Cable 1m", "price": 249, "units_last_month": 1, "oos_since_days": 33},
]
# SKUs that were ALREADY out of stock last month (5 of the 18) -> so 13 are newly OOS.
ALREADY_OOS_LAST_MONTH = ["SD-STD-15", "SD-AUX-16", "SD-PCH-17", "SD-LNK-18", "SD-OTG-10"]

# Of the ~44 cancellations this month (gross 314 orders x 14%)
CANCELLATION_REASONS = {
    "seller_cancelled_out_of_stock": 27,
    "customer_requested": 9,
    "address_or_courier_issue": 8,
}

# Visibility funnel (assumed). CTR and conversion roughly flat -> the problem is fewer eyeballs, not worse pages.
VISIBILITY = {
    "last_month": {"impressions": 120000, "ctr_pct": 2.1, "conversion_pct": 15.5},
    "this_month": {"impressions": 84000, "ctr_pct": 2.1, "conversion_pct": 15.2},
    "note": "Out-of-stock listings are down-ranked in search, so impressions fell ~30% while CTR and conversion stayed flat.",
}

# Competitor price check for 2 top SKUs (assumed): a minor factor.
PRICING_CHECK = [
    {"sku": "SD-TWS-01", "your_price": 799, "market_low": 749, "gap_pct": -6.3},
    {"sku": "SD-CHG-02", "your_price": 449, "market_low": 429, "gap_pct": -4.5},
]
