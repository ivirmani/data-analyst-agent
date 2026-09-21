"""Generate a small fake sales dataset so we have something to analyze."""

import random

import pandas as pd

# Using the same "seed" means the random numbers come out identical every run,
# so your sales.csv matches mine exactly.
random.seed(42)

REGIONS = ["North", "South", "East", "West"]
PRODUCTS = ["Widget", "Gadget", "Doohickey"]

# "2024-01" through "2024-12". The :02d pads single digits with a zero.
MONTHS = [f"2024-{month:02d}" for month in range(1, 13)]

# How much each region grows per month. South booms, West shrinks.
MONTHLY_GROWTH = {"North": 0.01, "South": 0.08, "East": 0.03, "West": -0.02}

# What each region sells in January, before any growth is applied.
STARTING_REVENUE = {"North": 50000, "South": 20000, "East": 35000, "West": 60000}

# What slice of a region's revenue each product accounts for (adds up to 1.0).
PRODUCT_SHARE = {"Widget": 0.5, "Gadget": 0.3, "Doohickey": 0.2}

rows = []

# One row for every combination of region, product, and month.
for region in REGIONS:
    for product in PRODUCTS:
        for months_elapsed, month in enumerate(MONTHS):
            # Compound growth: start * (1 + rate) raised to the number of months.
            base = STARTING_REVENUE[region] * (1 + MONTHLY_GROWTH[region]) ** months_elapsed

            # Add a random wobble of plus or minus 10% so it doesn't look synthetic.
            wobble = random.uniform(0.9, 1.1)

            revenue = round(base * PRODUCT_SHARE[product] * wobble, 2)

            rows.append(
                {"region": region, "product": product, "month": month, "revenue": revenue}
            )

# Turn our list of rows into a table, then save it as a CSV file.
df = pd.DataFrame(rows)
df.to_csv("sales.csv", index=False)

print(f"Wrote sales.csv with {len(df)} rows")
print()
print(df.head())
